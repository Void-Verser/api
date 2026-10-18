"""Create the dashboard owner, or reset its password, directly in the DB.

Usage (password is prompted, never passed on the command line):
    DATABASE_URL=... python scripts/create_owner.py --email you@example.com
    DATABASE_URL=... python scripts/create_owner.py --email you@example.com --reset-password
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select  # noqa: E402

from app.auth.owner_auth import MIN_PASSWORD_LENGTH  # noqa: E402
from app.database import get_session_factory, init_db  # noqa: E402
from app.models.owner import Owner, OwnerSession  # noqa: E402
from app.utils.security import hash_password  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--reset-password", action="store_true")
    args = ap.parse_args()
    email = args.email.strip().lower()

    password = getpass.getpass("New owner password: ")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise SystemExit(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if getpass.getpass("Repeat password: ") != password:
        raise SystemExit("Passwords do not match.")

    init_db()
    db = get_session_factory()()
    try:
        owner = db.scalar(select(Owner).where(Owner.email == email))
        if owner and not args.reset_password:
            raise SystemExit("Owner exists. Use --reset-password to change the password.")
        if owner is None and db.scalar(select(Owner.id).limit(1)) is not None and not args.reset_password:
            raise SystemExit("A different owner already exists.")
        if owner is None:
            db.add(Owner(email=email, password_hash=hash_password(password)))
            print(f"Owner {email} created.")
        else:
            owner.password_hash = hash_password(password)
            db.query(OwnerSession).filter(OwnerSession.owner_id == owner.id).delete()
            print(f"Password reset for {email}; existing sessions signed out.")
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
