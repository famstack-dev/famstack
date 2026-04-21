"""`stack docs reformat <id>` — reformat OCR to clean markdown + apply.

Defaults to applying. `--dry-run` shows the full rewritten markdown
without writing. `--raw` prints just the markdown to stdout (implies dry).
"""

from __future__ import annotations

import sys

from pipeline import Classifier, PaperlessAPI, reformat_document

from cli._dry_run import DryRunPaperless
from cli._shared import _DRY_FLAGS, err, is_dry, parse_doc_id


async def run(paperless: PaperlessAPI, classifier: Classifier,
              argv: list[str]) -> int:
    raw = "--raw" in argv
    dry_run = is_dry(argv) or raw  # --raw implies no-writes

    flag_tokens = {"--raw", *_DRY_FLAGS}
    positional = [a for a in argv if a not in flag_tokens]
    unknown = [a for a in argv if a.startswith("--") and a not in flag_tokens]
    if unknown:
        err(f"Unknown flag(s): {' '.join(unknown)}")
        return 2
    if not positional:
        err("Usage: reformat <id> [--dry|--dry-run] [--raw]")
        return 2
    doc_id = parse_doc_id(positional[0])
    if doc_id is None:
        return 2

    doc = await paperless.get_doc(doc_id)
    if not doc:
        err(f"Document #{doc_id} not found")
        return 1

    ocr_text = (doc.get("content") or "").strip()
    if len(ocr_text) < 10:
        err(f"Document #{doc_id} has no usable OCR text ({len(ocr_text)} chars)")
        return 1

    if raw:
        # Pipe-friendly: direct classifier call, raw markdown, no writes.
        formatted = await classifier.reformat(ocr_text)
        if formatted:
            sys.stdout.write(formatted)
            sys.stdout.write("\n")
        return 0 if formatted else 1

    _print_header(doc, ocr_text, dry_run=dry_run)

    # Apply or plan through reformat_document. DryRunPaperless swallows
    # the PATCH so dry-run and real runs share the same code path.
    pipeline_paperless = DryRunPaperless(paperless) if dry_run else paperless
    formatted = await reformat_document(
        paperless=pipeline_paperless, classifier=classifier,
        doc_id=doc_id, ocr_text=ocr_text,
    )

    _print_result(formatted, len(ocr_text), dry_run=dry_run)
    return 0 if formatted else 1


def _print_header(doc: dict, ocr_text: str, *, dry_run: bool) -> None:
    from stack.prompt import BOLD, DIM, ORANGE, RESET
    marker = f"  {DIM}(DRY RUN){RESET}" if dry_run else ""
    print()
    print(f"  {ORANGE}#{doc.get('id')}{RESET}  {BOLD}{doc.get('title') or '(no title)'}{RESET}{marker}")
    verb = "Would reformat" if dry_run else "Reformatting"
    print(f"  {DIM}{verb} {len(ocr_text):,} chars of OCR text...{RESET}")
    print()


def _print_result(formatted: str | None, source_chars: int,
                  *, dry_run: bool) -> None:
    from stack.prompt import DIM, GREEN, ORANGE, RESET
    if not formatted:
        print(f"  {ORANGE}✗{RESET}  Reformat returned nothing — LLM may be down or too short a response.\n")
        return
    verb = "Would reformat" if dry_run else "Reformatted"
    print(f"  {GREEN}✓{RESET}  {verb} ({source_chars:,} → {len(formatted):,} chars)")
    if dry_run:
        # Show the full markdown so the operator can sanity-check before
        # a non-dry run overwrites the Paperless body.
        print(f"  {DIM}{'─' * 60}{RESET}\n")
        print(formatted)
        print(f"\n  {DIM}{'─' * 60}{RESET}")
        print(f"  {DIM}--dry-run: no changes made to Paperless.{RESET}")
    else:
        # Applied. The new content is already in Paperless — `stack docs
        # show <id> --content` retrieves it if the user wants to inspect.
        print(f"  {DIM}applied to Paperless. `stack docs show <id> --content` to view.{RESET}")
    print()
