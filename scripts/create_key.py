"""Create a client API key directly in the database.

Usage:
    MASTER_API_SECRET is NOT needed here (direct DB access).
    DATABASE_URL=... python scripts/create_key.py --name "Raiden Bot"

Default permissions are "*" (every client operation). Prints the plaintext
key ONCE — store it as MEDIA_API_KEY on the client. Prefer the dashboard's
"Get API Key" button for normal use.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import get_session_factory, init_db  # noqa: E402
from app.models.api_key import VALID_PERMISSIONS  # noqa: E402
from app.services.keys import create_key  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--permissions", nargs="*", default=["*"])
    ap.add_argument("--rate-limit", type=int, default=None)
    ap.add_argument("--expires-in-days", type=int, default=None)
    args = ap.parse_args()

    perms = sorted({p.lower() for p in args.permissions})
    unknown = [p for p in perms if p not in VALID_PERMISSIONS]
    if unknown:
        raise SystemExit(f"Unknown permissions: {unknown}. Valid: {sorted(VALID_PERMISSIONS)}")

    init_db()
    db = get_session_factory()()
    try:
        record, plaintext = create_key(
            db, name=args.name, permissions=perms, rate_limit=args.rate_limit,
            expires_in_days=args.expires_in_days,
        )
        print(f"Name:        {args.name}")
        print(f"Permissions: {', '.join(record.permissions)}")
        print(f"MEDIA_API_KEY={plaintext}")
        print("(shown once — only the hash is stored)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
