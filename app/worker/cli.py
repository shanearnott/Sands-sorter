"""`python -m app.worker poll|summarize|backfill-extractions`."""
from __future__ import annotations

import argparse
import json
import logging
import sys

from app.db import SessionLocal
from app.summary.digest import build_and_emit
from app.worker.tasks import poll_all

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sands-worker")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("poll", help="Poll all enabled sources and process new docs.")
    summarize = sub.add_parser(
        "summarize", help="Build the income/expenses digest and email or save it."
    )
    summarize.add_argument(
        "--cadence",
        choices=("daily", "weekly"),
        help="Override the configured summary cadence.",
    )
    sub.add_parser(
        "backfill-extractions",
        help="Re-run OCR on filed docs missing an extraction row (M5).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)
    with SessionLocal() as db:
        if args.command == "poll":
            result = poll_all(db)
            print(json.dumps(result.__dict__, default=str, indent=2))
            return 0
        if args.command == "summarize":
            outcome = build_and_emit(db, cadence=args.cadence)
            print(json.dumps(outcome, default=str, indent=2))
            return 0
        if args.command == "backfill-extractions":
            from app.reports.backfill import backfill_extractions

            count = backfill_extractions(db)
            print(json.dumps({"backfilled": count}))
            return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
