"""`stack docs reprocess <id|range>...` — full pipeline on filed documents.

Accepts single ids and inclusive ranges:

    reprocess 42
    reprocess 1-13                 (ids 1 through 13 inclusive)
    reprocess 1-5 7 10-12 --dry    (mixed, with dry-run)

Ids missing from Paperless inside a range are silently skipped — the
end-of-run summary reports the skipped count.

A `--msg "your hint"` flag attaches a user clarification to the
classify prompt, same lever as the Matrix reply-to-reprocess flow:

    reprocess 7 --msg "Der Urlaub ist im Februar 2026"
    reprocess 1-5 --msg "filed for tax year 2025"

Honours the archivist's bot.toml `[settings].reformat` so the CLI
behaves like the bot on a new upload. Memory-vault mirroring is
attempted by default and skipped if the `code` stacklet env is
unavailable. `--no-reformat`, `--no-mirror`, `--dry-run` opt out for
a single invocation.

Also exposes `run_one()` so the `classify` subcommand can run a
classification-only pass by calling in with `reformat=False, mirror=None`.
"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path

# `memory.lib` is imported lazily inside `run_one` — it depends on the
# bot-runner's sys.path (which adds the stacklets parent dir) so a unit
# test that imports `cli.reprocess` for argv-helper coverage doesn't
# need the live container's path setup.


def _extract_msg(argv: list[str]) -> tuple[list[str], str | None]:
    """Strip a `--msg <text>` pair out of argv and return (rest, msg).

    The user hint piggy-backs on the same plumbing as the Matrix
    reply-to-reprocess flow — it lands in the classifier prompt as a
    `User clarification` block. Quoting comes from the shell; argv
    arrives with the message as a single token.

    Returns `(argv_without_msg, msg)`. When `--msg` isn't supplied, msg
    is None and argv is returned unchanged.
    """
    for i, arg in enumerate(argv):
        if arg == "--msg":
            if i + 1 >= len(argv):
                return argv, None  # caller treats trailing --msg as a usage error
            return argv[:i] + argv[i + 2:], argv[i + 1]
        if arg.startswith("--msg="):
            return argv[:i] + argv[i + 1:], arg[len("--msg="):]
    return argv, None

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
from cli._shared import _DRY_FLAGS, err, is_dry, parse_id_specs


class RunResult(Enum):
    """Outcome of a single `run_one()` invocation.

    Three buckets so range-reprocessing can distinguish "the doc isn't
    here" (silent skip) from "the doc is here but processing failed"
    (loud failure). The `classify` subcommand maps NOT_FOUND back to a
    visible error since it runs on one explicit id at a time.
    """
    OK = "ok"
    NOT_FOUND = "not_found"
    FAILED = "failed"


async def run(paperless: PaperlessAPI, classifier: Classifier,
              argv: list[str]) -> int:
    # `--msg "text"` lands in the classify prompt as a `User
    # clarification` block — the same lever the Matrix reply-to-reprocess
    # flow uses. Examples:
    #
    #   stack docs reprocess 7 --msg "Der Urlaub ist im Februar 2026"
    #   stack docs reprocess 1-5 --msg "this is for Sabrina"
    #
    # Stripped from argv before flag/positional parsing so the rest of
    # the parser only sees id specs and known flags.
    argv, user_hint = _extract_msg(argv)
    if "--msg" in argv:
        err("--msg requires a value: --msg \"your hint\"")
        return 2

    dry_run = is_dry(argv)

    # Defaults come from bot.toml so the CLI behaves like a new upload.
    # Explicit flags override.
    settings = read_bot_toml_settings()
    reformat = settings.get("reformat", True)
    if "--no-reformat" in argv:
        reformat = False
    elif "--reformat" in argv:
        reformat = True
    # Mirror is attempted by default; `--no-mirror` skips it for a
    # single invocation. If the env isn't there, `build_mirror_like_bot`
    # returns None and we error out below with a pointer to `code`.
    mirror_enabled = "--no-mirror" not in argv

    flag_tokens = {*_DRY_FLAGS, "--reformat", "--no-reformat",
                   "--no-mirror"}
    positional = [a for a in argv if a not in flag_tokens]
    unknown = [a for a in argv if a.startswith("--") and a not in flag_tokens]
    if unknown:
        err(f"Unknown flag(s): {' '.join(unknown)}")
        return 2
    if not positional:
        err("Usage: reprocess <id|range>... [--msg \"hint\"] [--[no-]reformat] [--no-mirror] [--dry|--dry-run]")
        return 2

    doc_ids = parse_id_specs(positional)
    if doc_ids is None:
        return 2

    mirror = build_mirror_like_bot() if mirror_enabled else None
    if mirror_enabled and mirror is None:
        err("Memory vault writer needs the `code` stacklet up (CODE_URL / admin creds). "
            "Bring up `code` or pass --no-mirror.")
        return 1

    successes = 0
    failures = 0
    skipped = 0
    for doc_id in doc_ids:
        result = await run_one(
            paperless, classifier, mirror,
            doc_id=doc_id, reformat=reformat, dry_run=dry_run,
            user_hint=user_hint,
        )
        if result is RunResult.OK:
            successes += 1
        elif result is RunResult.NOT_FOUND:
            # Silent skip: ranges are expected to span gaps. The summary
            # surfaces the count so missing ids aren't invisible.
            skipped += 1
        else:
            failures += 1

    _print_summary(successes, failures, skipped, dry_run=dry_run)
    return 0 if failures == 0 else 1


async def run_one(
    paperless: PaperlessAPI, classifier: Classifier, mirror,
    *, doc_id: int, reformat: bool, dry_run: bool,
    user_hint: str | None = None,
) -> RunResult:
    """Re-enrich one Paperless doc.

    Shared with the `classify` command, which calls in with
    reformat=False, mirror=None to scope the action to classification
    only. Returns a `RunResult` so callers can distinguish missing ids
    from genuine processing failures — the `reprocess` command treats
    NOT_FOUND as a silent skip when iterating a range; `classify` keeps
    its loud per-id error message.
    """
    doc = await paperless.get_doc(doc_id)
    if not doc:
        return RunResult.NOT_FOUND

    tags = await paperless.get_tags()
    doc_types = await paperless.get_doc_types()
    correspondents = await paperless.get_correspondents()
    before = _snapshot_doc(doc, tags, doc_types, correspondents)

    # Anchor partial-date resolution to Paperless's immutable filing
    # date (`added`). System date would silently shift the anchor to
    # "today" and re-hallucinate years on a reprocess weeks later.
    added = (doc.get("added") or "")[:10] or None
    # Load the same memory ontology the live bot uses so cross-language
    # canonicalisation (and cross-field rejection) applies to CLI
    # reprocess too. The vault path and the household language ride on
    # the same env vars as in the bot. Imported lazily so unit tests
    # that only exercise argv helpers don't need memory.lib's sys.path.
    from memory.lib import get_ontology as _get_memory_ontology
    vault_env = os.environ.get("MEMORY_VAULT_DIR", "")
    ontology = _get_memory_ontology(Path(vault_env) if vault_env else None)
    lang = os.environ.get("LANGUAGE", "en")
    pipeline_paperless = DryRunPaperless(paperless) if dry_run else paperless
    result = await enrich_document(
        paperless=pipeline_paperless, classifier=classifier, doc=doc,
        ontology_section=ontology.classifier_prompt_section(lang),
        ontology=ontology,
        lang=lang,
        is_reprocess=True,
        date_filed=added,
        user_hint=user_hint,
    )
    if result.llm_error:
        kind, detail = result.llm_error
        err(f"#{doc_id}: LLM {kind} — {detail}")
        return RunResult.FAILED
    if not result.classification:
        err(f"#{doc_id}: classifier returned nothing")
        return RunResult.FAILED

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
    return RunResult.OK


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


def _print_summary(successes: int, failures: int, skipped: int,
                   *, dry_run: bool) -> None:
    from stack.prompt import DIM, GREEN, ORANGE, RESET
    total = successes + failures
    verb = "would reprocess" if dry_run else "reprocessed"
    print()
    icon = f"{GREEN}✓{RESET}" if failures == 0 else f"{ORANGE}!{RESET}"
    detail_bits: list[str] = []
    if failures:
        detail_bits.append(f"{failures} failed")
    if skipped:
        detail_bits.append(f"{skipped} skipped (not found)")
    detail = f" ({', '.join(detail_bits)})" if detail_bits else ""
    print(f"  {icon} {verb} {successes}/{total}{detail}")
    if dry_run:
        print(f"  {DIM}--dry-run: no changes made to Paperless or the mirror.{RESET}")
    print()
