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
    show <id> [--content]           pretty-print Paperless state
    classify <id>   [--dry] [--json]
                                classify + apply (title, tags, type,
                                correspondent, date). --dry skips writes;
                                --json prints raw LLM output (implies --dry).
    reformat <id>   [--dry] [--raw]
                                reformat OCR + apply content to Paperless.
                                --dry skips writes; --raw prints the raw
                                markdown (implies --dry).
    reprocess <id> [<id>...]    full pipeline (classify + reformat + mirror)
                                respecting archivist bot.toml [settings].
                                flags: --[no-]reformat --[no-]mirror --dry
    mirror <id> [<id>...]       push docs to the Forgejo mirror using their
                                current Paperless state (no LLM). Useful for
                                backfilling after enabling mirror_to_git.
                                flags: --dry

All dry flags accept `--dry` and `--dry-run` interchangeably.
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

from pipeline import (
    Classifier,
    EnrichResult,
    PaperlessAPI,
    enrich_document,
    reformat_document,
)


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
        "reprocess": _reprocess,
        "mirror": _mirror_cmd,
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


# ── dry-run flag set (shared by every write-capable command) ───────────

_DRY_FLAGS = ("--dry-run", "--dry")


def _is_dry(argv: list[str]) -> bool:
    return any(f in argv for f in _DRY_FLAGS)


# ── classify ────────────────────────────────────────────────────────────

async def _classify(paperless: PaperlessAPI, classifier: Classifier,
                    argv: list[str]) -> int:
    raw_json = "--json" in argv
    dry_run = _is_dry(argv) or raw_json  # --json implies no-writes

    flag_tokens = {"--json", *_DRY_FLAGS}
    positional = [a for a in argv if a not in flag_tokens]
    unknown = [a for a in argv if a.startswith("--") and a not in flag_tokens]
    if unknown:
        _err(f"Unknown flag(s): {' '.join(unknown)}")
        return 2
    if not positional:
        _err("Usage: classify <id> [--dry|--dry-run] [--json]")
        return 2
    doc_id = _parse_doc_id(positional[0])
    if doc_id is None:
        return 2

    if raw_json:
        return await _classify_raw_json(paperless, classifier, doc_id)

    # Default / --dry-run: classify + apply (or plan) via the shared pipeline,
    # rendered as a before/after diff. No reformat, no mirror — classify is
    # scoped to classification only.
    ok = await _reprocess_one(
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


async def _classify_raw_json(paperless: PaperlessAPI, classifier: Classifier,
                             doc_id: int) -> int:
    """--json mode: call the classifier directly and dump raw JSON. No writes."""
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
    try:
        result = await classifier.classify(
            ocr_text=ocr_text, tags=tags,
            doc_types=doc_types, correspondents=correspondents,
        )
    except Exception as e:
        _err(f"Classifier failed: {e}")
        return 1
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    print()
    return 0


# ── reformat ────────────────────────────────────────────────────────────

async def _reformat(paperless: PaperlessAPI, classifier: Classifier,
                    argv: list[str]) -> int:
    raw = "--raw" in argv
    dry_run = _is_dry(argv) or raw  # --raw implies no-writes

    flag_tokens = {"--raw", *_DRY_FLAGS}
    positional = [a for a in argv if a not in flag_tokens]
    unknown = [a for a in argv if a.startswith("--") and a not in flag_tokens]
    if unknown:
        _err(f"Unknown flag(s): {' '.join(unknown)}")
        return 2
    if not positional:
        _err("Usage: reformat <id> [--dry|--dry-run] [--raw]")
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

    if raw:
        # Pipe-friendly: direct classifier call, raw markdown, no writes.
        formatted = await classifier.reformat(ocr_text)
        if formatted:
            sys.stdout.write(formatted)
            sys.stdout.write("\n")
        return 0 if formatted else 1

    _print_reformat_header(doc, ocr_text, dry_run=dry_run)

    # apply or plan through reformat_document (_DryRunPaperless swallows the
    # PATCH so dry-run and real runs share the same code path).
    pipeline_paperless = _DryRunPaperless(paperless) if dry_run else paperless
    formatted = await reformat_document(
        paperless=pipeline_paperless, classifier=classifier,
        doc_id=doc_id, ocr_text=ocr_text,
    )

    _print_reformat_result(formatted, len(ocr_text), dry_run=dry_run)
    return 0 if formatted else 1


def _print_reformat_header(doc: dict, ocr_text: str, *, dry_run: bool) -> None:
    from stack.prompt import BOLD, DIM, ORANGE, RESET
    marker = f"  {DIM}(DRY RUN){RESET}" if dry_run else ""
    print()
    print(f"  {ORANGE}#{doc.get('id')}{RESET}  {BOLD}{doc.get('title') or '(no title)'}{RESET}{marker}")
    verb = "Would reformat" if dry_run else "Reformatting"
    print(f"  {DIM}{verb} {len(ocr_text):,} chars of OCR text...{RESET}")
    print()


def _print_reformat_result(formatted: str | None, source_chars: int,
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




# ── reprocess ──────────────────────────────────────────────────────────

async def _reprocess(paperless: PaperlessAPI, classifier: Classifier,
                      argv: list[str]) -> int:
    dry_run = _is_dry(argv)

    # Defaults come from the archivist's bot.toml so the CLI behaves the
    # same way the bot does for a new upload. Explicit flags override.
    settings = _read_bot_toml_settings()
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
        _err(f"Unknown flag(s): {' '.join(unknown)}")
        return 2
    if not positional:
        _err("Usage: reprocess <id> [<id>...] [--[no-]reformat] [--[no-]mirror] [--dry|--dry-run]")
        return 2

    doc_ids: list[int] = []
    for p in positional:
        parsed = _parse_doc_id(p)
        if parsed is None:
            return 2
        doc_ids.append(parsed)

    mirror = _build_mirror_like_bot() if mirror_enabled else None
    if mirror_enabled and mirror is None:
        _err("Mirror enabled but required env (CODE_URL / admin creds) is missing. "
             "Bring up `code` or pass --no-mirror.")
        return 1

    successes = 0
    failures = 0
    for doc_id in doc_ids:
        ok = await _reprocess_one(
            paperless, classifier, mirror,
            doc_id=doc_id, reformat=reformat, dry_run=dry_run,
        )
        if ok:
            successes += 1
        else:
            failures += 1

    _print_reprocess_summary(successes, failures, dry_run=dry_run)
    return 0 if failures == 0 else 1


async def _reprocess_one(
    paperless: PaperlessAPI, classifier: Classifier, mirror,
    *, doc_id: int, reformat: bool, dry_run: bool,
) -> bool:
    """Re-enrich one Paperless doc. Returns True on success, False on any failure."""
    doc = await paperless.get_doc(doc_id)
    if not doc:
        _err(f"Document #{doc_id} not found")
        return False

    tags = await paperless.get_tags()
    doc_types = await paperless.get_doc_types()
    correspondents = await paperless.get_correspondents()
    before = _snapshot_doc(doc, tags, doc_types, correspondents)

    pipeline_paperless = _DryRunPaperless(paperless) if dry_run else paperless
    result = await enrich_document(
        paperless=pipeline_paperless, classifier=classifier, doc=doc,
    )
    if result.llm_error:
        kind, detail = result.llm_error
        _err(f"#{doc_id}: LLM {kind} — {detail}")
        return False
    if not result.classification:
        _err(f"#{doc_id}: classifier returned nothing")
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
        mirror_path = await _publish_mirror(
            mirror, refreshed, result, formatted=formatted,
        )

    _print_reprocess_diff(
        doc_id=doc_id, before=before, result=result,
        reformatted=bool(formatted), mirror_path=mirror_path,
        mirror_enabled=mirror is not None, dry_run=dry_run,
    )
    return True


# ── reprocess: no-op writer for --dry-run ──────────────────────────────

class _DryRunPaperless:
    """Read-through wrapper that stubs every write so `enrich_document`
    computes its plan without touching Paperless.

    Reads delegate to the real PaperlessAPI. Writes return synthetic ids
    (so `tag_ids.append(new_id)` still works downstream) and True for
    updates. Safe to pass wherever pipeline expects a PaperlessAPI.
    """

    def __init__(self, real: PaperlessAPI):
        self._real = real
        self._fake_id = 10_000_000

    async def get_doc(self, doc_id): return await self._real.get_doc(doc_id)
    async def get_tags(self): return await self._real.get_tags()
    async def get_doc_types(self): return await self._real.get_doc_types()
    async def get_correspondents(self): return await self._real.get_correspondents()

    async def update_doc(self, *a, **kw): return True

    async def create_tag(self, *a, **kw):
        self._fake_id += 1
        return self._fake_id

    async def create_doc_type(self, *a, **kw):
        self._fake_id += 1
        return self._fake_id

    async def create_correspondent(self, *a, **kw):
        self._fake_id += 1
        return self._fake_id


# ── reprocess: mirror bootstrap ────────────────────────────────────────

def _read_bot_toml_settings() -> dict:
    """Read [settings] from the archivist's bot.toml (same file the bot reads)."""
    try:
        import tomllib
    except ModuleNotFoundError:
        from stack._compat import tomllib
    path = Path("/stacklets/docs/bot/bot.toml")
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return data.get("settings", {})


def _mirror_enabled_in_bot_toml() -> bool:
    return bool(_read_bot_toml_settings().get("mirror_to_git", False))


def _build_mirror_like_bot():
    """Build a GitMirror exactly like the archivist bot's `_init_mirror`.

    Reuses the bot's creds file at /data/docs/bot/forgejo-creds.json so
    the CLI and the bot authenticate as the same Forgejo user. Returns
    None when required env is missing (typically code stacklet down).
    """
    code_url = os.environ.get("CODE_URL", "")
    admin_user = os.environ.get("MATRIX_ADMIN_USER", "")
    admin_password = os.environ.get("MATRIX_ADMIN_PASSWORD", "")
    admin_ids = os.environ.get("STACK_ADMIN_USER_IDS", "")
    if not (code_url and admin_user and admin_password):
        return None

    admin_usernames: list[str] = []
    for raw in admin_ids.split(","):
        raw = raw.strip()
        if not raw:
            continue
        name = raw.lstrip("@").split(":", 1)[0]
        if name and name != admin_user:
            admin_usernames.append(name)

    from git_mirror import GitMirror
    settings = _read_bot_toml_settings()
    return GitMirror(
        code_url=code_url,
        admin_user=admin_user,
        admin_password=admin_password,
        admin_usernames=admin_usernames,
        data_dir=Path("/data/docs/bot"),
        org_name=settings.get("mirror_org", "family"),
    )


async def _publish_mirror(
    mirror, doc: dict, result: EnrichResult,
    *, formatted: str | None,
) -> str | None:
    """Publish a mirror entry for the re-enriched doc. Returns path or None."""
    from stack import resolve_model

    ocr_text = doc.get("content") or ""
    if formatted:
        body_text = formatted
        processing = "ai_formatted"
        try:
            model = resolve_model("archivist-bot/reformat")
        except ValueError:
            model = None
    else:
        body_text = ocr_text
        processing = "ocr"
        model = None

    enriched = dict(result.classification) if result.classification else {}
    enriched["topics"] = result.resolved_topics
    enriched["persons"] = result.resolved_persons
    enriched["correspondent"] = result.resolved_correspondent
    enriched["document_type"] = result.resolved_type
    paperless_tags = [
        *result.resolved_topics,
        *(f"Person: {p}" for p in result.resolved_persons),
    ]

    paperless_url = (os.environ.get("PAPERLESS_PUBLIC_URL")
                     or os.environ.get("PAPERLESS_URL", ""))
    try:
        ok = await mirror.publish(
            paperless_id=doc["id"],
            classification=enriched,
            body_text=body_text,
            processing=processing,
            model=model,
            paperless_url=paperless_url,
            tags=paperless_tags,
            fallback_title=doc.get("title"),
        )
    except Exception as e:
        _err(f"#{doc['id']}: mirror publish failed — {e}")
        return None
    return mirror._cache.get(doc["id"]) if ok else None


# ── reprocess: state snapshot + diff rendering ─────────────────────────

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


def _print_reprocess_diff(*, doc_id: int, before: dict, result: EnrichResult,
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


def _print_reprocess_summary(successes: int, failures: int, *, dry_run: bool) -> None:
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


# ── mirror (push existing docs, no LLM) ────────────────────────────────

async def _mirror_cmd(paperless: PaperlessAPI, classifier: Classifier,
                      argv: list[str]) -> int:
    dry_run = _is_dry(argv)
    flag_tokens = set(_DRY_FLAGS)
    positional = [a for a in argv if a not in flag_tokens]
    unknown = [a for a in argv if a.startswith("--") and a not in flag_tokens]
    if unknown:
        _err(f"Unknown flag(s): {' '.join(unknown)}")
        return 2
    if not positional:
        _err("Usage: mirror <id> [<id>...] [--dry|--dry-run]")
        return 2

    doc_ids: list[int] = []
    for p in positional:
        parsed = _parse_doc_id(p)
        if parsed is None:
            return 2
        doc_ids.append(parsed)

    settings = _read_bot_toml_settings()
    if not settings.get("mirror_to_git", False):
        _err("Mirror is disabled in stacklets/docs/bot/bot.toml (mirror_to_git = false). "
             "Flip it to true first, then `stack up core` to reboot the bot.")
        return 1

    mirror = _build_mirror_like_bot()
    if mirror is None:
        _err("Mirror env missing — bring up `code` so CODE_URL / admin creds are set.")
        return 1

    tags = await paperless.get_tags()
    doc_types = await paperless.get_doc_types()
    correspondents = await paperless.get_correspondents()

    successes = 0
    failures = 0
    for doc_id in doc_ids:
        doc = await paperless.get_doc(doc_id)
        if not doc:
            _err(f"#{doc_id}: not found")
            failures += 1
            continue

        if dry_run:
            _print_mirror_row(doc, path=None, dry_run=True)
            successes += 1
            continue

        path = await _mirror_existing(mirror, doc, tags, doc_types, correspondents)
        _print_mirror_row(doc, path, dry_run=False)
        if path:
            successes += 1
        else:
            failures += 1

    _print_mirror_summary(successes, failures, dry_run=dry_run)
    return 0 if failures == 0 else 1


async def _mirror_existing(mirror, doc: dict, tags: dict, doc_types: dict,
                           correspondents: dict) -> str | None:
    """Publish a mirror entry from the doc's current Paperless state.

    No LLM call, no classification run. `processing` is set to "ocr" because
    we're shipping whatever Paperless's content field currently holds —
    could be raw OCR, could be an earlier ai_formatted body. We don't
    track provenance for backfill; the important thing is the mirror entry
    exists and matches Paperless.
    """
    classification = _classification_from_doc(doc, tags, doc_types, correspondents)
    paperless_tag_names = [
        *classification.get("topics", []),
        *(f"Person: {p}" for p in classification.get("persons", [])),
    ]
    body_text = doc.get("content") or ""
    paperless_url = (os.environ.get("PAPERLESS_PUBLIC_URL")
                     or os.environ.get("PAPERLESS_URL", ""))

    try:
        ok = await mirror.publish(
            paperless_id=doc["id"],
            classification=classification,
            body_text=body_text,
            processing="ocr",
            model=None,
            paperless_url=paperless_url,
            tags=paperless_tag_names,
            fallback_title=doc.get("title"),
        )
    except Exception as e:
        _err(f"#{doc['id']}: mirror publish failed — {e}")
        return None
    return mirror._cache.get(doc["id"]) if ok else None


def _classification_from_doc(doc: dict, tags: dict, doc_types: dict,
                              correspondents: dict) -> dict:
    """Reshape a Paperless doc's current fields into classification form.

    Mirror.publish expects topics / persons / correspondent / document_type
    as human-readable strings (not ids) — same shape the LLM produces.
    """
    tag_name = {tid: name for name, tid in tags.items()}
    type_name = {tid: name for name, tid in doc_types.items()}
    corr_name = {tid: name for name, tid in correspondents.items()}

    doc_tag_names = [tag_name[t] for t in (doc.get("tags") or []) if t in tag_name]
    topics = [t for t in doc_tag_names if not t.startswith("Person: ")]
    persons = [t.replace("Person: ", "") for t in doc_tag_names
               if t.startswith("Person: ")]
    date = (doc.get("created") or "")[:10] or None

    return {
        "title": doc.get("title") or "",
        "date": date,
        "topics": topics,
        "persons": persons,
        "correspondent": corr_name.get(doc.get("correspondent")),
        "document_type": type_name.get(doc.get("document_type")),
    }


def _print_mirror_row(doc: dict, path: str | None, *, dry_run: bool) -> None:
    from stack.prompt import BOLD, DIM, GREEN, ORANGE, RESET, TEAL
    title = doc.get("title") or "(no title)"
    marker = f"  {DIM}(DRY RUN){RESET}" if dry_run else ""
    print()
    print(f"  {ORANGE}#{doc.get('id')}{RESET}  {BOLD}{title}{RESET}{marker}")
    if dry_run:
        print(f"    {DIM}mirror:{RESET} {TEAL}would publish{RESET}")
    elif path:
        print(f"    {DIM}mirror:{RESET} {GREEN}{path}{RESET}")
    else:
        print(f"    {DIM}mirror:{RESET} {ORANGE}failed{RESET}")


def _print_mirror_summary(successes: int, failures: int, *, dry_run: bool) -> None:
    from stack.prompt import DIM, GREEN, ORANGE, RESET
    total = successes + failures
    verb = "would mirror" if dry_run else "mirrored"
    icon = f"{GREEN}✓{RESET}" if failures == 0 else f"{ORANGE}!{RESET}"
    print()
    print(f"  {icon} {verb} {successes}/{total}" + (
        f" ({failures} failed)" if failures else ""))
    if dry_run:
        print(f"  {DIM}--dry-run: no commits to Forgejo.{RESET}")
    print()


# ── Helpers ─────────────────────────────────────────────────────────────

def _parse_doc_id(raw: str) -> int | None:
    try:
        return int(raw)
    except ValueError:
        _err(f"Invalid document id: {raw!r} (must be an integer)")
        return None


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
