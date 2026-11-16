"""`stack memory diary pages`: the month, year and index pages, from the cards on file.

    rc = await pages(llm, ["--month", "2026-10"])
    rc = await project(records, llm, summaries_cache, room_id=room_id, ...)

The pages are a projection of the cards: the compile builds them after
it wrote the cards, and the curator runs `diary pages` after a push, a
hand edit or a correction. Without `--fast` the month summaries are
written first.
"""

from __future__ import annotations

import os
from pathlib import Path

from stack.ai import transcripts

from ... import brain_lock, diary_store
from ...diary.pages import DIARY_DIR, pages_for
from ...diary.sources import media_of
from ...diary.texts import configure_language
from .. import wiki
from .room import remembered_room
from .store import entries_on_file, records_on_file
from .summary import SUMMARY_PROMPT, month_summaries


async def project(records, llm, summaries_cache, *, room_id: str,
                   bucket: str, rebuild: bool, dry_run: bool,
                   only: "list[str] | None" = None) -> int:
    """Build and publish the diary pages from the records in the vault.

    `records` maps each record's path to its content, as the vault holds
    it after this run's writes. The pages are a view of these and of
    nothing else, so every correction and hand edit shows.

    Without `llm`, a month whose entries changed keeps the paragraph last
    written for it. `only` (months, `2026-10`) publishes those months'
    pages, their years' and the front page, and asks the model about no
    other month; None publishes every page.
    """
    on_file, entries = entries_on_file(records)
    summaries = await month_summaries(entries, llm, summaries_cache, bucket=bucket,
                                 rebuild=rebuild, only=only)

    pages = pages_for(entries, bucket=bucket, room_id=room_id, summaries=summaries,
                            media=media_of(on_file))
    if only is not None:
        wanted = {f"{DIARY_DIR}/about.md"}
        for month in only:
            year, _, number = month.partition("-")
            wanted |= {f"{DIARY_DIR}/{year}/about.md", f"{DIARY_DIR}/{year}/{number}.md"}
        pages = [page for page in pages if page.path in wanted]
    return publish_pages(pages, bucket=bucket, dry_run=dry_run)


async def pages(llm, argv: list[str]) -> int:
    """`diary pages [--fast] [--month <YYYY-MM>]...`: the diary pages, from the records on file.

    Reads the diary records from the memory repo's working copy, not the
    room: what the curator runs when a diary card was pushed, a hand edit
    or a correction. `--fast` asks no model; a month whose entries changed
    keeps its paragraph until the next compile with one.

    With the model, the month's paragraph is written first and the pages
    after, from the records as they are by then, under the brain's writer
    lock: the model's turn can take minutes, and a compile without the
    model may have written newer entries meanwhile.
    """
    bucket = os.environ.get("SHARED_BUCKET", "family")
    configure_language(os.environ.get("LANGUAGE", ""))
    vault = Path(os.environ.get("MEMORY_VAULT_DIR", ""))
    _, summaries_cache = diary_store.open_stores(
        summary_fingerprint=transcripts.fingerprint(SUMMARY_PROMPT))
    months = [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == "--month"] or None
    if "--fast" not in argv:
        _, entries = entries_on_file(records_on_file(vault, bucket))
        await month_summaries(entries, llm, summaries_cache, bucket=bucket, rebuild=False,
                         only=months)
    with brain_lock.held(Path(os.environ.get("BRAIN_REPO_DIR", ""))):
        records = records_on_file(vault, bucket)
        return await project(records, None, summaries_cache, room_id=remembered_room(),
                              bucket=bucket,
                              rebuild=False, dry_run="--dry-run" in argv, only=months)


def publish_pages(pages, *, bucket: str, dry_run: bool) -> int:
    """Write the compiled pages into the brain working copy.

    One publisher for both exits: a full compile and the empty diary
    reach the wiki the same way, so the page a family lands on has the
    same frontmatter and the same splice contract either way. Each page
    is a view of the entries it shows.
    """
    if dry_run:
        for page in pages:
            print(f"\n{'=' * 70}\n{bucket}/{page.path}\n{'=' * 70}\n{page.body}")
        return 0

    rc = 0
    for page in pages:
        rc |= wiki._publish(
            page.body, target_path=f"{bucket}/{page.path}",
            view=wiki.ViewFields(page.view, title=page.title,
                                 sources=[(e.card_id, e.version) for e in page.entries]),
        )
    return rc
