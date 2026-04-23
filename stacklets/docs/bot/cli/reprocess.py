"""`stack docs reprocess <id>...` — full pipeline on filed documents.

Honours the archivist's bot.toml [settings] (reformat / mirror_to_git)
so the CLI behaves like the bot on a new upload. `--no-reformat`,
`--no-mirror`, `--dry-run` opt out for a single invocation.

Also exposes `run_one()` so the `classify` subcommand can run a
classification-only pass by calling in with `reformat=False, mirror=None`.
"""

from __future__ import annotations

from pipeline import (
    Classifier,
    EnrichResult,
    PaperlessAPI,
    enrich_document,
    reformat_document,
)

from cli._dry_run import DryRunPaperless
from cli._mirror import (
    build_mirror_like_bot,
    publish_enriched,
    read_bot_toml_settings,
)
from cli._shared import _DRY_FLAGS, err, is_dry, parse_doc_id


async def run(paperless: PaperlessAPI, classifier: Classifier,
              argv: list[str]) -> int:
    dry_run = is_dry(argv)

    # Defaults come from bot.toml so the CLI behaves like a new upload.
    # Explicit flags override.
    settings = read_bot_toml_settings()
    reformat = settings.get("reformat", True)
    if "--no-reformat" in argv:
        reformat = False
    elif "--reformat" in argv:
        reformat = True
    mirror_enabled = settings.get("mirror_to_git", False)
    if "--no-mirror" in argv:
        mirror_enabled = False
    elif "--mirror" in argv:
        mirror_enabled = True

    flag_tokens = {*_DRY_FLAGS, "--reformat", "--no-reformat",
                   "--mirror", "--no-mirror"}
    positional = [a for a in argv if a not in flag_tokens]
    unknown = [a for a in argv if a.startswith("--") and a not in flag_tokens]
    if unknown:
        err(f"Unknown flag(s): {' '.join(unknown)}")
        return 2
    if not positional:
        err("Usage: reprocess <id> [<id>...] [--[no-]reformat] [--[no-]mirror] [--dry|--dry-run]")
        return 2

    doc_ids: list[int] = []
    for p in positional:
        parsed = parse_doc_id(p)
        if parsed is None:
            return 2
        doc_ids.append(parsed)

    mirror = build_mirror_like_bot() if mirror_enabled else None
    if mirror_enabled and mirror is None:
        err("Mirror enabled but required env (CODE_URL / admin creds) is missing. "
            "Bring up `code` or pass --no-mirror.")
        return 1

    successes = 0
    failures = 0
    for doc_id in doc_ids:
        ok = await run_one(
            paperless, classifier, mirror,
            doc_id=doc_id, reformat=reformat, dry_run=dry_run,
        )
        if ok:
            successes += 1
        else:
            failures += 1

    _print_summary(successes, failures, dry_run=dry_run)
    return 0 if failures == 0 else 1


async def run_one(
    paperless: PaperlessAPI, classifier: Classifier, mirror,
    *, doc_id: int, reformat: bool, dry_run: bool,
) -> bool:
    """Re-enrich one Paperless doc. Shared with the `classify` command,
    which calls in with reformat=False, mirror=None to scope the action
    to classification only."""
    doc = await paperless.get_doc(doc_id)
    if not doc:
        err(f"Document #{doc_id} not found")
        return False

    tags = await paperless.get_tags()
    doc_types = await paperless.get_doc_types()
    correspondents = await paperless.get_correspondents()
    before = _snapshot_doc(doc, tags, doc_types, correspondents)

    pipeline_paperless = DryRunPaperless(paperless) if dry_run else paperless
    result = await enrich_document(
        paperless=pipeline_paperless, classifier=classifier, doc=doc,
    )
    if result.llm_error:
        kind, detail = result.llm_error
        err(f"#{doc_id}: LLM {kind} — {detail}")
        return False
    if not result.classification:
        err(f"#{doc_id}: classifier returned nothing")
        return False

    # Reformat — only meaningful on binary-origin docs; Paperless doesn't
    # distinguish, so we always offer it as opt-in and trust the user.
    formatted: str | None = None
    if reformat:
        ocr_text = (doc.get("content") or "").strip()
        if dry_run:
            formatted = await classifier.reformat(ocr_text)
            if formatted and len(formatted) <= 20:
                formatted = None
        else:
            formatted = await reformat_document(
                paperless=paperless, classifier=classifier,
                doc_id=doc_id, ocr_text=ocr_text,
            )

    # Mirror — refetch to get the post-PATCH state; skip for dry-run.
    mirror_path: str | None = None
    if mirror and not dry_run:
        refreshed = await paperless.get_doc(doc_id) or doc
        mirror_path = await publish_enriched(
            mirror, refreshed, result, formatted=formatted,
        )

    _print_diff(
        doc_id=doc_id, before=before, result=result,
        reformatted=bool(formatted), mirror_path=mirror_path,
        mirror_enabled=mirror is not None, dry_run=dry_run,
    )
    return True


def _snapshot_doc(doc: dict, tags: dict, doc_types: dict,
                  correspondents: dict) -> dict:
    """Capture the human-readable state of a doc as a flat dict."""
    tag_name = {tid: name for name, tid in tags.items()}
    type_name = {tid: name for name, tid in doc_types.items()}
    corr_name = {tid: name for name, tid in correspondents.items()}

    current_tags = [tag_name.get(t, f"#{t}") for t in (doc.get("tags") or [])]
    topics = sorted(t for t in current_tags if not t.startswith("Person: "))
    persons = sorted(t.replace("Person: ", "") for t in current_tags
                     if t.startswith("Person: "))

    return {
        "title": doc.get("title") or "",
        "topics": topics,
        "persons": persons,
        "correspondent": corr_name.get(doc.get("correspondent")),
        "document_type": type_name.get(doc.get("document_type")),
        "date": (doc.get("created") or "")[:10],
    }


def _print_diff(*, doc_id: int, before: dict, result: EnrichResult,
                reformatted: bool, mirror_path: str | None,
                mirror_enabled: bool, dry_run: bool) -> None:
    from stack.prompt import BOLD, DIM, GREEN, ORANGE, RESET, TEAL

    after_title = result.classification.get("title") or before["title"]
    after_date = result.updates_applied.get("created") or before["date"]

    marker = f"  {DIM}(DRY RUN){RESET}" if dry_run else ""
    print()
    print(f"  {ORANGE}#{doc_id}{RESET}  {BOLD}{after_title}{RESET}{marker}")

    _diff_row("title", before["title"], after_title)
    # Fresh-reprocess semantics: the resolved_* lists ARE the new full
    # state for topics and persons, not additions to the prior set.
    _diff_row("topic", ", ".join(before["topics"]),
              ", ".join(sorted(result.resolved_topics)))
    _diff_row("person", ", ".join(before["persons"]),
              ", ".join(sorted(result.resolved_persons)))
    _diff_row("correspondent", before["correspondent"],
              result.resolved_correspondent)
    _diff_row("document_type", before["document_type"],
              result.resolved_type)
    _diff_row("date", before["date"], after_date)

    if reformatted:
        verb = "would reformat" if dry_run else "reformatted"
        print(f"    {DIM}reformat:{RESET}       {TEAL}{verb}{RESET}")

    if result.summary:
        verb = "would write" if dry_run else "written"
        chars = len(result.summary)
        print(f"    {DIM}summary:{RESET}        {TEAL}{verb} ({chars} chars){RESET}")

    if dry_run:
        if mirror_enabled:
            print(f"    {DIM}mirror:{RESET}         {DIM}skipped (--dry-run){RESET}")
    elif mirror_enabled:
        status = f"{GREEN}{mirror_path}{RESET}" if mirror_path else f"{ORANGE}failed{RESET}"
        print(f"    {DIM}mirror:{RESET}         {status}")

    if result.created_new:
        print(f"    {DIM}created:{RESET}        {TEAL}{', '.join(result.created_new)}{RESET}")


def _diff_row(label: str, before_value, after_value) -> None:
    from stack.prompt import DIM, RESET, TEAL
    before_disp = before_value if before_value else "(none)"
    after_disp = after_value if after_value else "(none)"
    if str(before_disp) == str(after_disp):
        return
    print(f"    {DIM}{label + ':':<15}{RESET} {before_disp}  {DIM}→{RESET}  {TEAL}{after_disp}{RESET}")


def _print_summary(successes: int, failures: int, *, dry_run: bool) -> None:
    from stack.prompt import DIM, GREEN, ORANGE, RESET
    total = successes + failures
    verb = "would reprocess" if dry_run else "reprocessed"
    print()
    icon = f"{GREEN}✓{RESET}" if failures == 0 else f"{ORANGE}!{RESET}"
    print(f"  {icon} {verb} {successes}/{total}" + (
        f" ({failures} failed)" if failures else ""))
    if dry_run:
        print(f"  {DIM}--dry-run: no changes made to Paperless or the mirror.{RESET}")
    print()
