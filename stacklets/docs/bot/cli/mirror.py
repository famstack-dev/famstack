"""`stack docs mirror <id>...` — backfill existing docs into the Forgejo mirror.

Uses the doc's current Paperless state (title, tags, correspondent,
content). No LLM call. Fails fast when `mirror_to_git = false` in
bot.toml.
"""

from __future__ import annotations

from pipeline import Classifier, PaperlessAPI

from cli._mirror import (
    build_mirror_like_bot,
    publish_current_state,
    read_bot_toml_settings,
)
from cli._shared import _DRY_FLAGS, err, is_dry, parse_doc_id


async def run(paperless: PaperlessAPI, classifier: Classifier,
              argv: list[str]) -> int:
    dry_run = is_dry(argv)
    flag_tokens = set(_DRY_FLAGS)
    positional = [a for a in argv if a not in flag_tokens]
    unknown = [a for a in argv if a.startswith("--") and a not in flag_tokens]
    if unknown:
        err(f"Unknown flag(s): {' '.join(unknown)}")
        return 2
    if not positional:
        err("Usage: mirror <id> [<id>...] [--dry|--dry-run]")
        return 2

    doc_ids: list[int] = []
    for p in positional:
        parsed = parse_doc_id(p)
        if parsed is None:
            return 2
        doc_ids.append(parsed)

    settings = read_bot_toml_settings()
    if not settings.get("mirror_to_git", False):
        err("Mirror is disabled in stacklets/docs/bot/bot.toml (mirror_to_git = false). "
            "Flip it to true first, then `stack up core` to reboot the bot.")
        return 1

    mirror = build_mirror_like_bot()
    if mirror is None:
        err("Mirror env missing — bring up `code` so CODE_URL / admin creds are set.")
        return 1

    tags = await paperless.get_tags()
    doc_types = await paperless.get_doc_types()
    correspondents = await paperless.get_correspondents()

    successes = 0
    failures = 0
    for doc_id in doc_ids:
        doc = await paperless.get_doc(doc_id)
        if not doc:
            err(f"#{doc_id}: not found")
            failures += 1
            continue

        if dry_run:
            _print_row(doc, path=None, dry_run=True)
            successes += 1
            continue

        path = await publish_current_state(
            mirror, doc, tags, doc_types, correspondents,
        )
        _print_row(doc, path, dry_run=False)
        if path:
            successes += 1
        else:
            failures += 1

    _print_summary(successes, failures, dry_run=dry_run)
    return 0 if failures == 0 else 1


def _print_row(doc: dict, path: str | None, *, dry_run: bool) -> None:
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


def _print_summary(successes: int, failures: int, *, dry_run: bool) -> None:
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
