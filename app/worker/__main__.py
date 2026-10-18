"""python -m app.worker [--once]"""

from __future__ import annotations

import argparse
import logging

from app.config import get_settings
from app.database import init_db
from app.worker.worker import run_worker


def main() -> None:
    ap = argparse.ArgumentParser(description="Media API job worker")
    ap.add_argument("--once", action="store_true", help="drain the queue, then exit (cron mode)")
    args = ap.parse_args()
    logging.basicConfig(level=get_settings().log_level.upper(),
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    init_db()
    n = run_worker(once=args.once)
    logging.getLogger("media_api.worker").info("worker exiting, processed %s job(s)", n)


if __name__ == "__main__":
    main()
