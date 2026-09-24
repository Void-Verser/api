"""Create a client API key directly in the database.

Usage:
    MASTER_API_SECRET is NOT needed here (direct DB access).
    DATABASE_URL=... python scripts/create_key.py --name "Raiden Bot" \
        --permissions search info stream download

Prints the plaintext key ONCE — store it as MEDIA_API_KEY on the client.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

from app.database import get_session_factory, init_db
from app.models.api_key import VALID_PERMISSIONS, ApiKey
from app.utils.security import generate_api_key, hash_api_key


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--permissions", nargs="*", default=["search", "info"])
    ap.add_argument("--rate-limit", type=int, default=None)
    ap.add_argument("--expires-in-days", type=int, default=None)
    args = ap.parse_args()

    perms = sorted({p.lower() for p in args.permissions})
    unknown = [p for p in perms if p not in VALID_PERMISSIONS]
    if unknown:
        raise SystemExit(f"Unknown permissions: {unknown}. Valid: {sorted(VALID_PERMISSIONS)}")

    init_db()
    plaintext = generate_api_key()
    db = get_session_factory()()
    try:
        record = ApiKey(
            name=args.name,
            key_hash=hash_api_key(plaintext),
            key_last3=plaintext[-3:],
            permissions=perms,
            rate_limit=args.rate_limit,
            expires_at=(
                datetime.now(timezone.utc) + timedelta(days=args.expires_in_days)
                if args.expires_in_days
                else None
            ),
        )
        db.add(record)
        db.commit()
        print(f"Name:        {args.name}")
        print(f"Permissions: {', '.join(perms)}")
        print(f"MEDIA_API_KEY={plaintext}")
        print("(shown once — only the hash is stored)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
