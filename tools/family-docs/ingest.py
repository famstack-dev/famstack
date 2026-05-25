#!/usr/bin/env python3
"""ingest.py — file the rendered demo documents into a running stack.

Posts each rendered document into the Matrix `documents` room as the
family member who would plausibly have uploaded it, by driving the real
`stack messages upload` CLI. The archivist watches that room, so each
document flows through the exact path a family uses: upload to Matrix ->
OCR -> classify -> mirror to the memory wiki. Matrix stays the ledger;
nothing here talks to Paperless or the pipeline directly.

    # see what would be posted, by whom (no network)
    python tools/family-docs/ingest.py --dry-run

    # actually file them into the running instance
    python tools/family-docs/ingest.py

    # slower, to watch each one land in Element
    python tools/family-docs/ingest.py --delay 12

Requires the stack to be up (`stack up messages docs`) and the family
accounts to exist with passwords in the secrets store. Only Homer,
Marge, Bart and Lisa have accounts; documents that belong to Maggie (or
the household) are uploaded by Marge, the same as in real life.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
STACK = REPO_ROOT / "stack"

# Who uploads what. Personal documents come from their owner; household,
# baby, and school paperwork come from Marge, who runs the household.
# Bart proudly posts his own drawing; Lisa, her straight-A report card.
SENDERS = {
    "homer-payslip": "homer",
    "homer-employment-contract": "homer",
    "homer-car-insurance-policy": "homer",
    "homer-bank-statement": "homer",
    "homer-prescription": "homer",
    "homer-tax-assessment": "homer",
    "homer-birth-certificate": "homer",
    "springfield-mortgage-agreement": "homer",
    "slh-vet-invoice": "homer",
    "kwik-e-mart-receipt": "homer",
    "moes-tavern-tab": "homer",
    "texxon-gas-receipt": "homer",
    "springfield-power-electricity-bill": "marge",
    "marge-hospital-invoice": "marge",
    "marge-recipe-card": "marge",
    "marge-birth-certificate": "marge",
    "maggie-vaccination-record": "marge",
    "maggie-birth-certificate": "marge",
    "lisa-birth-certificate": "marge",
    "bart-birth-certificate": "marge",
    "bart-behavior-letter": "marge",
    "pta-meeting-minutes": "marge",
    "lisa-report-card": "lisa",
    "bart-drawing": "bart",
    "krusty-burger-receipt": "bart",
}
DEFAULT_SENDER = "marge"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="File rendered demo documents via Matrix.")
    ap.add_argument("--locale", default="en", help="which out/<locale> to ingest (default: en)")
    ap.add_argument("--room", default="documents", help="target Matrix room (default: documents)")
    ap.add_argument("--delay", type=float, default=6.0,
                    help="seconds to wait between uploads (default: 6)")
    ap.add_argument("--only", help="ingest only files whose name contains this substring")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, post nothing")
    args = ap.parse_args(argv)

    out_dir = HERE / "out" / args.locale
    if not out_dir.is_dir():
        sys.exit(f"no rendered documents at {out_dir} — run generate.py first")

    files = sorted(p for p in out_dir.iterdir()
                   if p.suffix.lower() in (".pdf", ".png", ".jpg", ".jpeg"))
    if args.only:
        files = [p for p in files if args.only in p.stem]
    if not files:
        sys.exit("no documents to ingest")

    print(f"{'(dry run) ' if args.dry_run else ''}filing {len(files)} documents "
          f"into '{args.room}':\n")

    failures = 0
    for i, path in enumerate(files):
        sender = SENDERS.get(path.stem, DEFAULT_SENDER)
        print(f"  {sender:>6}  →  {path.name}")
        if args.dry_run:
            continue
        result = subprocess.run(
            [str(STACK), "messages", "upload", args.room, str(path), "--as", sender],
            cwd=REPO_ROOT,
        )
        if result.returncode != 0:
            failures += 1
            print(f"          ! upload failed for {path.name}")
        # Space uploads out so the archivist files them one at a time and
        # the timeline stays ordered. Skip the wait after the last one.
        if args.delay and i < len(files) - 1:
            time.sleep(args.delay)

    if args.dry_run:
        print(f"\n(dry run) {len(files)} documents would be filed. "
              f"Re-run without --dry-run to post them.")
        return 0
    print(f"\n{len(files) - failures}/{len(files)} documents filed into '{args.room}'.")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
