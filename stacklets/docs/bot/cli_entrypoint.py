"""Docs CLI commands — executed inside stack-core-bot-runner.

The host-side `stack docs <cmd>` dispatchers `docker exec` into the
bot-runner container and invoke this entry point. The container already
has aiohttp, loguru, yaml, and the rendered Paperless / AI env vars —
so the host CLI stays stdlib-only while the pipeline logic is shared
verbatim with the archivist bot.

The pattern is deliberately reusable: any stacklet CLI that needs
non-stdlib deps can grow its own `cli_entrypoint.py` here and a thin
dispatcher on the host. Beats either "install aiohttp on the host" or
"duplicate HTTP code in urllib".

Commands:
    show <id> [--content]       pretty-print Paperless state
    classify <id> [--json]      dry classify — LLM JSON output, no writes
    reformat <id> [--raw]       dry reformat — clean markdown, no writes
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))  # pipeline, matching
sys.path.insert(0, "/app")  # stack.resolve_model, stack.prompt

import aiohttp

from pipeline import Classifier, PaperlessAPI


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


async def main(argv: list[str]) -> int:
    if not argv:
        _usage()
        return 2

    cmd, *rest = argv
    handlers = {
        "show": _show,
        "classify": _classify,
        "reformat": _reformat,
    }
    fn = handlers.get(cmd)
    if not fn:
        _err(f"Unknown command: {cmd}")
        _usage()
        return 2

    paperless_url = os.environ.get("PAPERLESS_URL", "")
    paperless_token = os.environ.get("PAPERLESS_TOKEN", "")
    if not paperless_url or not paperless_token:
        _err("PAPERLESS_URL / PAPERLESS_TOKEN not set — bot-runner env missing docs creds.")
        return 1

    async with aiohttp.ClientSession() as http:
        paperless = PaperlessAPI(http, paperless_url, paperless_token)
        classifier = Classifier(
            http,
            os.environ.get("OPENAI_URL", ""),
            os.environ.get("OPENAI_KEY", ""),
        )
        return await fn(paperless, classifier, rest)


def _usage() -> None:
    _err(__doc__.rstrip())


# ── show ────────────────────────────────────────────────────────────────

async def _show(paperless: PaperlessAPI, classifier: Classifier,
                argv: list[str]) -> int:
    full = "--content" in argv
    positional = [a for a in argv if a != "--content"]
    if not positional:
        _err("Usage: show <id> [--content]")
        return 2
    doc_id = _parse_doc_id(positional[0])
    if doc_id is None:
        return 2

    doc = await paperless.get_doc(doc_id)
    if not doc:
        _err(f"Document #{doc_id} not found")
        return 1

    tags = await paperless.get_tags()
    doc_types = await paperless.get_doc_types()
    correspondents = await paperless.get_correspondents()
    _render_show(doc, tags, doc_types, correspondents, full=full)
    return 0


def _render_show(doc: dict, tags: dict, doc_types: dict,
                 correspondents: dict, *, full: bool) -> None:
    from stack.prompt import BOLD, DIM, ORANGE, RESET, TEAL

    # Flip name→id lookups so numeric Paperless fields print as human names.
    tag_name = {tid: name for name, tid in tags.items()}
    type_name = {tid: name for name, tid in doc_types.items()}
    corr_name = {tid: name for name, tid in correspondents.items()}

    doc_id = doc.get("id")
    title = doc.get("title") or "(no title)"
    created = (doc.get("created") or "")[:10]
    tag_ids = doc.get("tags") or []
    tag_names = sorted(tag_name.get(t, f"#{t}") for t in tag_ids)
    content = doc.get("content") or ""

    print()
    print(f"  {ORANGE}#{doc_id}{RESET}  {BOLD}{title}{RESET}")
    print(f"  {DIM}{'─' * 60}{RESET}")
    print(f"  {DIM}{'Date':<14}{RESET}  {TEAL}{created or '—'}{RESET}")
    print(f"  {DIM}{'Type':<14}{RESET}  {TEAL}{type_name.get(doc.get('document_type'), '—')}{RESET}")
    print(f"  {DIM}{'Correspondent':<14}{RESET}  {TEAL}{corr_name.get(doc.get('correspondent'), '—')}{RESET}")
    print(f"  {DIM}{'Tags':<14}{RESET}  {TEAL}{', '.join(tag_names) or '—'}{RESET}")
    print(f"  {DIM}{'Content':<14}{RESET}  {len(content):,} chars")
    print()

    if not content.strip():
        print(f"  {DIM}(no OCR text){RESET}\n")
        return

    if full:
        print(content)
        print()
        return

    for line in content[:500].strip().splitlines():
        print(f"    {line}")
    if len(content) > 500:
        print(f"    {DIM}... ({len(content) - 500:,} more chars — pass --content for full body){RESET}")
    print()


# ── classify ────────────────────────────────────────────────────────────

async def _classify(paperless: PaperlessAPI, classifier: Classifier,
                    argv: list[str]) -> int:
    raw_json = "--json" in argv
    positional = [a for a in argv if a != "--json"]
    if not positional:
        _err("Usage: classify <id> [--json]")
        return 2
    doc_id = _parse_doc_id(positional[0])
    if doc_id is None:
        return 2

    doc = await paperless.get_doc(doc_id)
    if not doc:
        _err(f"Document #{doc_id} not found")
        return 1

    ocr_text = (doc.get("content") or "").strip()
    if len(ocr_text) < 10:
        _err(f"Document #{doc_id} has no usable OCR text ({len(ocr_text)} chars)")
        return 1

    tags = await paperless.get_tags()
    doc_types = await paperless.get_doc_types()
    correspondents = await paperless.get_correspondents()

    if not raw_json:
        _print_classify_header(doc, ocr_text)

    try:
        result = await classifier.classify(
            ocr_text=ocr_text, tags=tags,
            doc_types=doc_types, correspondents=correspondents,
        )
    except Exception as e:
        _err(f"Classifier failed: {e}")
        return 1

    if raw_json:
        json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
        print()
    else:
        _print_classification(result)
    return 0


def _print_classify_header(doc: dict, ocr_text: str) -> None:
    from stack.prompt import BOLD, DIM, ORANGE, RESET
    print()
    print(f"  {ORANGE}#{doc.get('id')}{RESET}  {BOLD}{doc.get('title') or '(no title)'}{RESET}")
    print(f"  {DIM}Classifying {len(ocr_text):,} chars of OCR text...{RESET}")
    print()


def _print_classification(result: dict) -> None:
    from stack.prompt import DIM, GREEN, RESET, TEAL
    if not result:
        print(f"  {DIM}LLM returned no classification (empty dict).{RESET}\n")
        return
    print(f"  {GREEN}✓{RESET}  Classification:\n")
    for line in json.dumps(result, indent=2, ensure_ascii=False).splitlines():
        print(f"    {TEAL}{line}{RESET}")
    print(f"\n  {DIM}--dry-run: no changes made to Paperless.{RESET}\n")


# ── reformat ────────────────────────────────────────────────────────────

async def _reformat(paperless: PaperlessAPI, classifier: Classifier,
                    argv: list[str]) -> int:
    raw = "--raw" in argv
    positional = [a for a in argv if a != "--raw"]
    if not positional:
        _err("Usage: reformat <id> [--raw]")
        return 2
    doc_id = _parse_doc_id(positional[0])
    if doc_id is None:
        return 2

    doc = await paperless.get_doc(doc_id)
    if not doc:
        _err(f"Document #{doc_id} not found")
        return 1

    ocr_text = (doc.get("content") or "").strip()
    if len(ocr_text) < 10:
        _err(f"Document #{doc_id} has no usable OCR text ({len(ocr_text)} chars)")
        return 1

    if not raw:
        _print_reformat_header(doc, ocr_text)

    formatted = await classifier.reformat(ocr_text)

    if raw:
        if formatted:
            sys.stdout.write(formatted)
            sys.stdout.write("\n")
        return 0 if formatted else 1

    _print_reformat_result(formatted, len(ocr_text))
    return 0 if formatted else 1


def _print_reformat_header(doc: dict, ocr_text: str) -> None:
    from stack.prompt import BOLD, DIM, ORANGE, RESET
    print()
    print(f"  {ORANGE}#{doc.get('id')}{RESET}  {BOLD}{doc.get('title') or '(no title)'}{RESET}")
    print(f"  {DIM}Reformatting {len(ocr_text):,} chars of OCR text...{RESET}")
    print()


def _print_reformat_result(formatted: str | None, source_chars: int) -> None:
    from stack.prompt import DIM, GREEN, ORANGE, RESET
    if not formatted:
        print(f"  {ORANGE}✗{RESET}  Reformat returned nothing — LLM may be down or too short a response.\n")
        return
    print(f"  {GREEN}✓{RESET}  Reformatted ({source_chars:,} → {len(formatted):,} chars):")
    print(f"  {DIM}{'─' * 60}{RESET}\n")
    print(formatted)
    print(f"\n  {DIM}{'─' * 60}{RESET}")
    print(f"  {DIM}--dry-run: no changes made to Paperless.{RESET}\n")


# ── Helpers ─────────────────────────────────────────────────────────────

def _parse_doc_id(raw: str) -> int | None:
    try:
        return int(raw)
    except ValueError:
        _err(f"Invalid document id: {raw!r} (must be an integer)")
        return None


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
