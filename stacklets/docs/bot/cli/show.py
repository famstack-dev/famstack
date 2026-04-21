"""`stack docs show <id>` — print a doc's current Paperless state."""

from __future__ import annotations

from pipeline import Classifier, PaperlessAPI

from cli._shared import err, parse_doc_id


async def run(paperless: PaperlessAPI, classifier: Classifier,
              argv: list[str]) -> int:
    full = "--content" in argv
    positional = [a for a in argv if a != "--content"]
    if not positional:
        err("Usage: show <id> [--content]")
        return 2
    doc_id = parse_doc_id(positional[0])
    if doc_id is None:
        return 2

    doc = await paperless.get_doc(doc_id)
    if not doc:
        err(f"Document #{doc_id} not found")
        return 1

    tags = await paperless.get_tags()
    doc_types = await paperless.get_doc_types()
    correspondents = await paperless.get_correspondents()
    _render(doc, tags, doc_types, correspondents, full=full)
    return 0


def _render(doc: dict, tags: dict, doc_types: dict,
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
