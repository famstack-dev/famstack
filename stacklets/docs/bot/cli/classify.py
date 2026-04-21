"""`stack docs classify <id>` — classify + apply, or preview with `--dry`.

Defaults to applying the LLM's classification to Paperless. `--dry-run`
runs the plan through the same code path but swaps in `DryRunPaperless`
so nothing is written. `--json` prints raw LLM output for pipes (implies
dry).
"""

from __future__ import annotations

import json
import sys

from pipeline import Classifier, PaperlessAPI

from cli._shared import _DRY_FLAGS, err, is_dry, parse_doc_id
from cli.reprocess import run_one


async def run(paperless: PaperlessAPI, classifier: Classifier,
              argv: list[str]) -> int:
    raw_json = "--json" in argv
    dry_run = is_dry(argv) or raw_json  # --json implies no-writes

    flag_tokens = {"--json", *_DRY_FLAGS}
    positional = [a for a in argv if a not in flag_tokens]
    unknown = [a for a in argv if a.startswith("--") and a not in flag_tokens]
    if unknown:
        err(f"Unknown flag(s): {' '.join(unknown)}")
        return 2
    if not positional:
        err("Usage: classify <id> [--dry|--dry-run] [--json]")
        return 2
    doc_id = parse_doc_id(positional[0])
    if doc_id is None:
        return 2

    if raw_json:
        return await _raw_json(paperless, classifier, doc_id)

    # Default / --dry-run: classify + apply (or plan) via the shared
    # pipeline, rendered as a before/after diff. No reformat, no mirror
    # — classify is scoped to classification only.
    ok = await run_one(
        paperless, classifier, mirror=None,
        doc_id=doc_id, reformat=False, dry_run=dry_run,
    )
    if ok:
        from stack.prompt import DIM, GREEN, RESET
        verb = "would classify" if dry_run else "classified"
        print(f"  {GREEN}✓{RESET} {verb}")
        if dry_run:
            print(f"  {DIM}--dry-run: no changes made to Paperless.{RESET}")
        print()
    return 0 if ok else 1


async def _raw_json(paperless: PaperlessAPI, classifier: Classifier,
                    doc_id: int) -> int:
    """--json mode: call the classifier directly and dump raw JSON. No writes."""
    doc = await paperless.get_doc(doc_id)
    if not doc:
        err(f"Document #{doc_id} not found")
        return 1
    ocr_text = (doc.get("content") or "").strip()
    if len(ocr_text) < 10:
        err(f"Document #{doc_id} has no usable OCR text ({len(ocr_text)} chars)")
        return 1
    tags = await paperless.get_tags()
    doc_types = await paperless.get_doc_types()
    correspondents = await paperless.get_correspondents()
    try:
        result = await classifier.classify(
            ocr_text=ocr_text, tags=tags,
            doc_types=doc_types, correspondents=correspondents,
        )
    except Exception as e:
        err(f"Classifier failed: {e}")
        return 1
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    print()
    return 0
