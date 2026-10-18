"""Result storage: local filesystem or any S3-compatible bucket.

* local: files live under LOCAL_STORAGE_DIR and are served by the API via
  HMAC-signed, expiring /v1/files links. Only valid when the API and the
  worker share a filesystem (single host / docker-compose volume).
* s3: AWS S3, Cloudflare R2, Backblaze B2, MinIO... Uploads and downloads
  use SigV4 presigned URLs (implemented here — no boto3 dependency), so the
  API on serverless only signs URLs and never touches the bytes.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

from app.config import Settings, get_settings
from app.utils.security import sign_value, verify_signature


def _q(value: str, safe: str = "-_.~") -> str:
    return quote(value, safe=safe)


def presign_s3_url(
    *,
    method: str,
    bucket: str,
    key: str,
    region: str,
    access_key: str,
    secret_key: str,
    expires: int,
    endpoint_url: str = "",
    extra_query: dict[str, str] | None = None,
    now: datetime | None = None,
) -> str:
    """AWS Signature V4 query-string presigning (UNSIGNED-PAYLOAD)."""
    now = now or datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date = now.strftime("%Y%m%d")
    region = region or "us-east-1"
    if endpoint_url:  # path-style for custom endpoints (R2, MinIO, B2)
        ep = urlsplit(endpoint_url.rstrip("/"))
        host = ep.netloc
        scheme = ep.scheme or "https"
        path = f"{ep.path}/{bucket}/{key}"
    else:
        host = f"{bucket}.s3.amazonaws.com" if region == "us-east-1" else f"{bucket}.s3.{region}.amazonaws.com"
        scheme = "https"
        path = f"/{key}"
    scope = f"{date}/{region}/s3/aws4_request"
    params = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{access_key}/{scope}",
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": str(int(expires)),
        "X-Amz-SignedHeaders": "host",
        **(extra_query or {}),
    }
    canonical_query = "&".join(f"{_q(k)}={_q(v)}" for k, v in sorted(params.items()))
    canonical_uri = _q(path, safe="/-_.~")
    canonical_request = "\n".join(
        [method, canonical_uri, canonical_query, f"host:{host}", "", "host", "UNSIGNED-PAYLOAD"]
    )
    string_to_sign = "\n".join(
        ["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical_request.encode()).hexdigest()]
    )

    def _h(k: bytes, msg: str) -> bytes:
        return hmac.new(k, msg.encode(), hashlib.sha256).digest()

    k = _h(_h(_h(_h(f"AWS4{secret_key}".encode(), date), region), "s3"), "aws4_request")
    signature = hmac.new(k, string_to_sign.encode(), hashlib.sha256).hexdigest()
    return f"{scheme}://{host}{canonical_uri}?{canonical_query}&X-Amz-Signature={signature}"


class LocalStorage:
    name = "local"

    def __init__(self, settings: Settings):
        self.root = Path(settings.local_storage_dir).resolve()

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root not in p.parents:
            raise ValueError("invalid storage key")
        return p

    def put(self, local_file: str, key: str, content_type: str) -> int:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(local_file, dest)
        return dest.stat().st_size

    def delete(self, key: str) -> None:
        try:
            p = self._path(key)
            p.unlink(missing_ok=True)
            if p.parent != self.root and not any(p.parent.iterdir()):
                p.parent.rmdir()
        except (OSError, ValueError):
            pass

    def open_path(self, key: str) -> Path | None:
        p = self._path(key)
        return p if p.is_file() else None

    def url(self, *, key: str, job_id: str, filename: str, content_type: str,
            expires_at: int, base_url: str) -> str:
        sig = sign_value(f"{job_id}:{expires_at}")
        return f"{base_url.rstrip('/')}/v1/files/{job_id}/{quote(filename)}?expires={expires_at}&sig={sig}"

    def healthy(self) -> tuple[bool, str]:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            return os.access(self.root, os.W_OK), str(self.root)
        except OSError as exc:
            return False, str(exc)


class S3Storage:
    name = "s3"

    def __init__(self, settings: Settings):
        self.s = settings

    def _presign(self, method: str, key: str, expires: int, extra: dict | None = None) -> str:
        return presign_s3_url(
            method=method, bucket=self.s.s3_bucket, key=key, region=self.s.s3_region,
            access_key=self.s.s3_access_key_id, secret_key=self.s.s3_secret_access_key,
            expires=expires, endpoint_url=self.s.s3_endpoint_url, extra_query=extra,
        )

    def put(self, local_file: str, key: str, content_type: str) -> int:
        import httpx

        size = os.path.getsize(local_file)
        url = self._presign("PUT", key, 900)
        with open(local_file, "rb") as fh:
            resp = httpx.put(url, content=fh, headers={"Content-Type": content_type,
                                                       "Content-Length": str(size)}, timeout=300)
        if resp.status_code >= 300:
            raise RuntimeError(f"object storage upload failed: HTTP {resp.status_code}")
        os.unlink(local_file)
        return size

    def delete(self, key: str) -> None:
        import httpx

        try:
            httpx.delete(self._presign("DELETE", key, 300), timeout=30)
        except httpx.HTTPError:
            pass

    def url(self, *, key: str, job_id: str, filename: str, content_type: str,
            expires_at: int, base_url: str) -> str:
        ttl = max(1, min(expires_at - int(time.time()), 7 * 24 * 3600))
        return self._presign("GET", key, ttl, {
            "response-content-disposition": f'attachment; filename="{filename}"',
            "response-content-type": content_type,
        })

    def healthy(self) -> tuple[bool, str]:
        ok = all([self.s.s3_bucket, self.s.s3_access_key_id, self.s.s3_secret_access_key])
        return ok, "configured" if ok else "S3_BUCKET / S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY missing"


def get_storage(settings: Settings | None = None):
    s = settings or get_settings()
    return S3Storage(s) if (s.storage_backend or "local").lower() == "s3" else LocalStorage(s)


def verify_file_link(job_id: str, expires: int, sig: str) -> bool:
    return expires >= int(time.time()) and verify_signature(f"{job_id}:{expires}", sig)
