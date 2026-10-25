"""Curator — keeps the family wiki fresh without anyone running a command.

Runs as the `stack-memory-curator` sidecar (own slim image, see
`curator.Dockerfile`). The curator is the vault keeper: it owns the
`git pull` that keeps the working copy in sync with Forgejo (the wiki
container is a pure view that just watches the files). Jobs:

1. **Views follow their cards, in two speeds** (ADR-016 §5). New
   commits land — an archivist filing, a learn commit, a hand edit
   pushed from Obsidian — and Forgejo tells the curator of the push
   (`POST /push` on the cards API; the poll stays as the fallback).
   The views to compile again are exactly those whose `derived_from`
   names a changed card, before or after the change, plus the ones a
   new card would now be in (its persons, its topic, its correspondent,
   its diary month). At once, with the mirror, they are compiled
   without the model: which cards belong, list items, dates and links
   are current within seconds. Once the burst has settled for the
   quiet window, every view it touched is compiled with the model,
   once: "I filed Homer's letter, Homer's page knows", for one model
   call per view. That compile runs in the background, one at a time,
   and the loop goes on: a push during the model's turn, which takes
   minutes on a real model, is compiled at once all the same. The
   model's text is put over each view as it is when written, and every
   writer of the brain takes its writer lock (`brain_lock`).
2. **Nightly full sweep.** Home, every member, every topic, at
   WIKI_NIGHTLY local time. This is the self-healing catch-all:
   cross-references go stale invisibly during the day and are corrected
   while the GPU is idle. It also means the incremental person/topic
   mapping only has to be *helpful*, never load-bearing. Worst case
   for a missed page is "stale until tonight".
3. **Card ids, on every push.** A file pushed without a `card_id`, or
   with one another file carries (a note copied by hand), gets its id
   from the memory bot in one commit through the card store
   (`give_ids_since`, with `memory.lib.card_ids_for`, the rule migration
   0002 applied to the files that were there before).

Rebuilds run the CLI entrypoint as a subprocess (same code path as
`stack memory wiki` and `diary pages`), so a wedged LLM call dies with
the child, not inside this loop. The wiki's own publish commits are filtered by
their subject prefix (`wiki.COMMIT_PREFIX`) — the rebuild must not
trigger itself. The last-rebuilt SHA persists across restarts, so an
interrupted rebuild is simply redone.

Failure shape: a failed rebuild keeps the SHA where it was and
retries after another quiet window — the trigger is never lost, and
`stack memory wiki update` always works as the manual override. If the
curator is down, the vault (and therefore the wiki) goes stale
together — one component, one failure story. Disabling
`wiki_auto_rebuild` stops the rebuilds, NOT the pull: the wiki must
not rot just because the automation is off.

Config surface (rendered from stack.toml by the runtime):
    WIKI_AUTO_REBUILD        "true"/"false" — [memory] wiki_auto_rebuild
    WIKI_REBUILD_QUIET_SECS  quiet window — [memory] wiki_rebuild_quiet_secs
    WIKI_NIGHTLY             "HH:MM" local, "" disables — [memory] wiki_nightly
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

# The framework is in `/app` (the image mounts `lib/stack` there). The
# curator is the container's main script, so it says once where the
# stacklets are, and imports memory's modules as the package
# `stacklets.memory` (`stack.stacklet_code`).
sys.path.insert(0, "/app")  # stack.* framework
from stack.stacklet_code import import_stacklets  # noqa: E402

import_stacklets([Path(__file__).resolve().parents[2]])

from loguru import logger  # noqa: E402
from stacklets.memory.lib import (  # noqa: E402
    MIRROR_POLL_INTERVAL,
    MIRROR_SHA_NAME,
    MIRROR_WAIT_SECS,
    MIRROR_TRIGGER_NAME,
    NIGHTLY_RUN_NAME,
    NIGHTLY_TRIGGER_NAME,
    REBUILT_SHA_NAME,
    SEED_TITLE,
    SyncResult,
    _parse_frontmatter,
    authenticated_remote,
    card_ids_for,
    declares_generated,
    ensure_vault_cloned,
    record_failed_rebuild,
    report_progress,
    brain_remote_url,
    is_auth_failure,
    reconcile_with_remote,
    run_git,
    seed_files,
    vault_remote_url,
)
from stacklets.memory.card_check import CardCheck  # noqa: E402
from stacklets.memory.cards import CardStore, Put, Remove, StoreAPI, StoreError, Sync  # noqa: E402
from stack.cards import card_id_from, is_view, view_names  # noqa: E402
from stack.cards.commits import MEMORY_BOT  # noqa: E402
from stack.migrations import Migrations, until_migrated  # noqa: E402
from stack.lists import LISTS_DIR, ListError, read_list  # noqa: E402
from stack.vault import DEFAULT_SHARED_BUCKET, TOPIC_CARD, correspondents_dir  # noqa: E402

from stacklets.memory.bot import brain_lock  # noqa: E402
from stacklets.memory.bot.cli.wiki import COMMIT_PREFIX  # noqa: E402
from stack.people import PEOPLE_DIR  # noqa: E402

POLL_SECS = int(os.environ.get("CURATOR_POLL_SECS", "30"))
# How often a curator held for memory's migrations looks again.
MIGRATIONS_POLL_SECS = 5
# The memory stacklet's files, and its data dir as the container mounts it,
# where `.migrations.json` records what ran.
MEMORY_STACKLET = Path(__file__).resolve().parents[1]
QUIET_SECS = int(os.environ.get("WIKI_REBUILD_QUIET_SECS", "180"))
NIGHTLY = os.environ.get("WIKI_NIGHTLY", "03:30").strip()

ENTRYPOINT = "/stacklets/memory/bot/cli_entrypoint.py"

# Identity stamped on the curator's brain commits. Brain is a machine-
# owned projection, so its commit log names the curator, not a human.
_BRAIN_AUTHOR_NAME = "memory-curator"
_BRAIN_AUTHOR_EMAIL = "memory-curator@local"

# The memory commit up to which every pushed card has its id.
CARD_IDS_SHA_NAME = "last-card-ids-sha"

# Subject line for the curator's per-cycle brain commit. One commit
# carries the whole cycle: mirrored source plus any regenerated pages.
BRAIN_COMMIT_PREFIX = "brain: project"

# Hard ceiling on one rebuild pass. A vault-wide sweep on a slow local
# model stays well under this; anything longer means a wedged LLM call
# and the retry path is cheaper than waiting forever.
REBUILD_TIMEOUT_SECS = 3600

# Vault-root entries that never belong to a member or the shared
# bucket. Mirrors the wiki's `_NON_MEMBER_DIRS` plus git itself.
_SKIP_TOP = {".git", ".obsidian", "wiki", "private", "templates", "_shared"}

# Legacy generated page filenames. Live files are classified by their
# view frontmatter (`stack.cards.is_view`). Delete/rename diffs cannot
# read frontmatter from a gone file, so these names remain authoritative
# for removal operations.
_GENERATED_NAMES = {"about.md", "index.md"}

# Both ends of the trigger protocol live in `memory.lib`: every writer
# (the write seam on its way out, `stack memory sync` by hand) asks
# there, and this loop is what answers. The curator stays brain's only
# writer; callers only ask and wait (one-writer invariant, ADR-011).
TRIGGER_NAME = MIRROR_TRIGGER_NAME

# Left by Forgejo's notice of a push to the memory repo (`POST /push` on
# the cards API): the next cycle starts at once, but unlike the trigger
# above it asks for no compile with the model before the quiet window.
PUSH_TRIGGER_NAME = "pushed"

# The curator's own git remote. `origin` belongs to the host plane
# (hooks and the host CLI reach Forgejo on a localhost/LAN port); this
# container reaches it on the stack network. One working copy serves
# two network planes, so each plane gets its own remote instead of
# fighting over origin's URL.
CURATOR_REMOTE = "curator"


# ── Pure decision logic (tested in tests/unit/stacklets) ──────────────────────

def only_own_commits(subjects: list[str]) -> bool:
    """True when every commit subject is a wiki publish of our own.

    An empty list is NOT ours: HEAD moved but the range shows no
    commits, which means a history rewrite — the caller treats that
    as a rebuild trigger, never as something to skip.
    """
    return bool(subjects) and all(
        s.startswith(COMMIT_PREFIX) for s in subjects
    )


class Debounce:
    """Quiet-window tracker: `observe(head, now)` returns True once the
    same head has been seen unchanged for the current window.

    The window is `quiet_secs` while rebuilds are succeeding, and doubles
    with each consecutive failure up to `max_backoff_secs`. That second
    part exists because the usual reason a rebuild fails is the AI
    endpoint being down, and being down is a condition that outlives one
    quiet window: retrying on the flat window spends a subprocess and an
    LLM call every three minutes, all day, and prints a line each time
    that is indistinguishable from a healthy heartbeat.
    """

    def __init__(self, quiet_secs: float, max_backoff_secs: float = 3600.0):
        self.quiet_secs = quiet_secs
        self.max_backoff_secs = max_backoff_secs
        self.failures = 0
        self._head: str | None = None
        self._since = 0.0

    def window(self) -> float:
        """The gap currently being waited out. Reported in the log."""
        return min(self.quiet_secs * (2 ** self.failures), self.max_backoff_secs)

    def observe(self, head: str, now: float) -> bool:
        if head != self._head:
            self._head, self._since = head, now
            return False
        return (now - self._since) >= self.window()

    def reset(self) -> None:
        """Nothing pending, or a rebuild worked: back to the flat window."""
        self._head = None
        self.failures = 0

    def retry_later(self, now: float) -> None:
        """Failed rebuild: keep the head, restart the window, wider."""
        self.failures += 1
        self._since = now


def _is_generated_name(path: str) -> bool:
    parts = [p for p in path.split("/") if p]
    return bool(parts) and parts[-1] in _GENERATED_NAMES


def member_selection(
    paths: list[str],
    fm_reader,
    *,
    shared_bucket: str,
    list_texts=lambda path: [],
) -> list[str]:
    """Map changed vault paths to an incremental `wiki` selection argv.

    Member and topic pages are where freshness is felt. Cross-refs
    heal on the nightly sweep.
    Collects the bucket owner for personal captures plus every name in
    `persons:` frontmatter (`fm_reader(path) -> dict`, fed by
    `git show` so the mapping matches the commit), and adds topic
    folders touched by captures or whose card changed, and the
    correspondent a document names or whose card changed. Returns e.g.
    `["--home", "--member", "Homer Simpson", "--topic", "camping"]`, or
    `[]` when nothing
    relevant changed (only generated pages or skipped dirs). Home is
    included whenever anything relevant changed at all. A change to a
    person file adds `--members`: who the family knows changed, so every
    person page is rebuilt and pages for people no file declares are
    retired. A changed list adds the topics it names and the people its
    open items are assigned to, before and after the change
    (`list_texts(path) -> [text, ...]`), since those pages show its
    open items.
    """
    members: list[str] = []
    topics: list[str] = []
    correspondents: list[str] = []
    relevant = False
    people_changed = False

    for path in paths:
        parts = [p for p in path.split("/") if p]
        if not parts or parts[0] in _SKIP_TOP:
            continue
        fm = fm_reader(path) if path.endswith(".md") else {}
        if is_view(fm):
            continue
        relevant = True
        if path.startswith(f"{shared_bucket}/{PEOPLE_DIR}/"):
            people_changed = True
            continue
        if parts[0] != shared_bucket and len(parts) > 1 and parts[0] not in members:
            members.append(parts[0])
        topic = (_topic_slug_from_capture_path(parts, shared_bucket=shared_bucket)
                 or _topic_of_card(parts))
        if topic and topic not in topics:
            topics.append(topic)
        named = fm.get("correspondent") if path.endswith(".md") else ""
        if path.startswith(f"{correspondents_dir(shared_bucket)}/") and len(parts) == 3:
            named = parts[2].removesuffix(".md")
        if isinstance(named, str) and named.strip() and named.strip() not in correspondents:
            correspondents.append(named.strip())
        if _is_list_path(parts):
            people, named = list_reach(list_texts(path), path)
            members += [p for p in people if p not in members]
            topics += [t for t in named if t not in topics]
        if path.endswith(".md"):
            persons = fm.get("persons") or fm.get("person") or []
            if isinstance(persons, str):
                persons = [persons]
            for person in persons:
                if isinstance(person, str) and person.strip() and person not in members:
                    members.append(person)

    if not relevant:
        return []
    argv = ["--home"]
    if people_changed:
        argv.append("--members")
    for member in members:
        argv += ["--member", member]
    for topic in topics:
        argv += ["--topic", topic]
    for name in correspondents:
        argv += ["--correspondent", name]
    return argv


def _is_list_path(parts: list[str]) -> bool:
    """`<bucket>/lists/<slug>.md`, not a file in the lists' archive."""
    return len(parts) == 3 and parts[1] == LISTS_DIR and parts[2].endswith(".md")


def list_reach(texts: list[str], path: str) -> tuple[list[str], list[str]]:
    """The people and the topics whose pages show the open items of a list.

    `texts` are versions of one list file; the reach is their union, so
    a person whose item was ticked and a topic the list stopped naming
    still have their page rebuilt. A version that is not a list adds
    nothing.
    """
    people: list[str] = []
    topics: list[str] = []
    for text in texts:
        try:
            lst = read_list(text, path=path)
        except ListError:
            continue
        topics += [t for t in lst.topics if t not in topics]
        for item in lst.open_items:
            people += [p for p in item.people if p not in people]
    return people, topics


def _topic_slug_from_capture_path(parts: list[str], *, shared_bucket: str) -> str:
    """Return the topic slug for a capture path, or "" for non-topic paths."""
    capture_dirs = {"notes", "bookmarks", "documents"}
    if len(parts) >= 3 and parts[0] == shared_bucket and parts[2] in capture_dirs:
        return parts[1]
    if len(parts) >= 4 and parts[0] != shared_bucket and parts[2] in capture_dirs:
        return parts[1]
    return ""


def _topic_of_card(parts: list[str]) -> str:
    """The topic whose own card is at `parts` (`<bucket>/<topic>/topic.md`), else ""."""
    return parts[1] if len(parts) == 3 and parts[2] == TOPIC_CARD else ""


# ── Views to compile again (ADR-016 §5) ──────────────────────────────────
#
# A view names the cards it was compiled from (`derived_from`). When a card
# is pushed, the views that name it, before or after the change, are the
# ones to compile again; `member_selection` adds the views a new card would
# now be in, which name nothing yet. Both arrive as the arguments the wiki
# and the diary commands take.

@dataclass
class Selection:
    """The views to compile again: `wiki` arguments, and diary months (`2026-10`)."""

    wiki: list[str] = field(default_factory=list)
    months: list[str] = field(default_factory=list)

    def add(self, argv: list[str], month: str = "") -> None:
        """Take `argv` (`--home`, `--member homer`, ...) and `month`, each once."""
        have = set(_pairs(self.wiki))
        for pair in _pairs(argv):
            if pair not in have:
                have.add(pair)
                self.wiki += [p for p in pair if p]
        if month and month not in self.months:
            self.months.append(month)

    def update(self, other: "Selection") -> None:
        self.add(other.wiki)
        for month in other.months:
            self.add([], month)

    def __bool__(self) -> bool:
        return bool(self.wiki or self.months)


def _pairs(argv: list[str]) -> list[tuple[str, str]]:
    """`argv` as (flag, value) pairs; a flag that takes no value pairs with ""."""
    pairs, i = [], 0
    while i < len(argv):
        takes_value = argv[i] in _VALUE_FLAGS and i + 1 < len(argv)
        pairs.append((argv[i], argv[i + 1] if takes_value else ""))
        i += 2 if takes_value else 1
    return pairs


_VALUE_FLAGS = {"--member", "--topic", "--correspondent"}


def view_args(path: str, fields: dict, *, shared_bucket: str) -> tuple[list[str], str]:
    """How the view at brain `path` is compiled: (wiki arguments, diary month).

    A folder listing is compiled with the page of the folder it lists, a
    diary year and the diary's front page with any of their months.
    """
    parts = path.removesuffix(".md").split("/")
    kind = str(fields.get("view") or "")
    if kind == "home":
        return ["--home"], ""
    if kind == "person":
        return ["--member", parts[0]], ""
    if kind == "topic" and len(parts) >= 3:
        return ["--topic", parts[-2]], ""
    if kind == "correspondent" and len(parts) >= 4:
        return ["--correspondent", parts[-2]], ""
    if kind == "diary-month" and len(parts) >= 2:
        return [], f"{parts[-2]}-{parts[-1]}"
    owner = parts[:-2]
    if kind == "folder" and owner == [shared_bucket]:
        return ["--home"], ""
    if kind == "folder" and len(owner) == 1:
        return ["--member", owner[0]], ""
    if kind == "folder" and len(owner) == 2:
        return ["--topic", owner[1]], ""
    return [], ""


def views_naming(brain: Path, ids: set[str]) -> list[tuple[str, dict]]:
    """Every view in `brain` whose `about` or `derived_from` names one of `ids`."""
    if not ids:
        return []
    found = []
    for md in sorted(brain.rglob("*.md")):
        rel = md.relative_to(brain).as_posix()
        if rel.startswith(".git/"):
            continue
        try:
            text = md.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        # A view's frontmatter opens with its kind (`wiki.ViewFields`), so
        # the cards mirrored beside the views are passed over unparsed.
        if not text.startswith("---\nview:"):
            continue
        fields = _parse_frontmatter(text, rel)
        if view_names(fields) & ids:
            found.append((rel, fields))
    return found


def diary_month_of(path: str) -> str:
    """The month of a diary record at `path` (`2026-10`), else ""."""
    parts = path.split("/")
    if len(parts) == 6 and parts[1:3] == ["diary", "entries"] and parts[5].endswith(".md"):
        return f"{parts[3]}-{parts[4]}"
    return ""


def select_views(paths: list[str], fm_reader, *, shared_bucket: str,
                 list_texts=lambda path: [], ids_of=lambda path: set(),
                 named=lambda ids: []) -> Selection:
    """The views a change to the vault `paths` compiles again.

    The views that name a changed card (`ids_of(path)`: its card ids
    before and after the change; `named(ids)`: the views naming them, as
    `views_naming` finds them), and the views the changed cards would now
    be in (`member_selection`, and a diary record's month).
    """
    selection = Selection()
    selection.add(member_selection(paths, fm_reader, shared_bucket=shared_bucket,
                                   list_texts=list_texts))
    for path in paths:
        selection.add([], diary_month_of(path))
    ids = set().union(*(ids_of(path) for path in paths)) if paths else set()
    for rel, fields in named(ids):
        selection.add(*view_args(rel, fields, shared_bucket=shared_bucket))
    return selection


async def views_pushed(vault: "Vault", brain: Path, since: str, until: str, *,
                       shared_bucket: str) -> Selection:
    """The views the commits `since..until` of the memory repo touched (`select_views`)."""
    paths = await vault.changed_paths(since, until)

    def ids_of(path: str) -> set[str]:
        if not path.endswith(".md"):
            return set()
        return {card_id_from(vault.frontmatter_at(rev, path)) for rev in (since, until)} - {""}

    return await asyncio.to_thread(
        select_views, paths, lambda p: vault.frontmatter_at(until, p),
        shared_bucket=shared_bucket,
        list_texts=lambda p: [vault.text_at(since, p), vault.text_at(until, p)],
        ids_of=ids_of, named=lambda ids: views_naming(brain, ids))


class Recompile:
    """When the views a push touched are compiled again, in two speeds.

    `pushed` runs on every cycle the memory repo moved: the views it
    touched are compiled at once without the model, so which cards belong,
    list items, dates and links are current within seconds. `settle` runs
    on every cycle after: once the head has stood still for the quiet
    window, it starts the compile with the model of every view the burst
    touched, in the background, one at a time. Three pushes in a row cost
    one model call per view.

    The model's turn can take minutes, and the loop does not wait for it:
    a push meanwhile is compiled at once all the same, and joins the next
    compile with the model, which `settle` starts after the next quiet
    window. The compile with the model renders its text over the views as
    they are when it writes them (`wiki._compile`), so the facts of the
    quick compile stay. `finished` says when it has ended.

        recompile = Recompile(select, compile, Debounce(180))
        await recompile.pushed(before, head)           # at once, no model
        await recompile.settle(last, head, now)        # after the quiet window
        recompile.finished(now)                        # (head, ok) once it ended

    `select(since, until)` returns the `Selection` for a range of the
    memory repo; `compile(selection, fast=)` compiles it, True on success.
    """

    def __init__(self, select, compile, debounce: Debounce):
        self.select = select
        self.compile = compile
        self.debounce = debounce
        # What the pushes since the last compile with the model touched. A
        # view a push takes a card out of no longer names it once compiled
        # at once, so the model's turn cannot find it again by
        # `derived_from`.
        self.pending = Selection()
        self._running: asyncio.Task | None = None
        self._selection = Selection()
        self._head = ""
        # Asked for at once (`stack memory sync`) while one was running.
        self._forced = False

    @property
    def busy(self) -> bool:
        """A compile with the model is running, or has ended and not been collected."""
        return self._running is not None

    @property
    def ended(self) -> bool:
        return self._running is not None and self._running.done()

    async def pushed(self, since: str, until: str) -> bool:
        """Compile at once, without the model, what `since..until` touched."""
        selection = await self.select(since, until)
        self.pending.update(selection)
        return await self.compile(selection, fast=True) if selection else True

    async def settle(self, since: str, head: str, now: float, *, forced: bool = False) -> None:
        """Start the compile with the model of what touched views since `since`, once quiet.

        Never waits for it. While one runs, nothing else starts; one asked
        for at once meanwhile starts as soon as it has ended.
        """
        if self.busy:
            self._forced = self._forced or forced
            return
        forced, self._forced = forced or self._forced, False
        if not forced and not self.debounce.observe(head, now):
            return
        selection = Selection()
        selection.update(self.pending)
        selection.update(await self.select(since, head))
        self.pending = Selection()
        self._selection, self._head = selection, head
        self._running = asyncio.create_task(self._compile(selection))

    async def _compile(self, selection: Selection) -> bool:
        return await self.compile(selection, fast=False) if selection else True

    def finished(self, now: float) -> tuple[str, bool] | None:
        """(the head it compiled, whether it worked) once a compile with the model ended.

        None while it runs or when none ran. A failed one gives its views
        back to the next, after a wider window.
        """
        task = self._running
        if task is None or not task.done():
            return None
        self._running = None
        try:
            ok = bool(task.result())
        except Exception as e:  # a crash in the compile is a failed compile
            logger.warning("[curator] the compile with the model crashed: {}", e)
            ok = False
        if ok:
            self.debounce.reset()
        else:
            self.pending.update(self._selection)
            self.debounce.retry_later(now)
        return self._head, ok

    async def wait(self, now: float) -> tuple[str, bool] | None:
        """Wait for a running compile with the model to end, then `finished`."""
        if self._running is not None:
            await asyncio.wait({self._running})
        return self.finished(now)


# ── Source mirror (memory -> brain projection) ───────────────────────────
#
# The curator polls memory (source) and writes brain (projection). The
# mirror replays memory's git diff onto brain's working copy: a new or
# edited capture is copied in, a deleted one removed, a rename moved.
# Generation then writes its pages on top (slice 4), and the whole tree
# is committed to brain as one commit per cycle.
#
# `is_source_path` is the guard that keeps generation's own output from
# being treated as source to mirror: generated pages carry
# view frontmatter (`stack.cards.is_view`), and `.git` internals are
# always skipped.

# Git diff status letters the mirror acts on. `A`dded and `M`odified
# copy the file in; `D`eleted removes it; `R`enamed moves it (old path
# removed, new path copied). `C`opied is treated like an add of the new
# path. `T` (type change) is treated as a modify.
_COPY_STATUSES = {"A", "M", "C", "T"}


def is_source_path(path: str, frontmatter: dict | None = None) -> bool:
    """True when a vault path is source the mirror should replay to brain.

    Excludes git internals and views (`stack.cards.is_view`).
    Filename is not source of truth for live files: a hand-written
    `about.md` with no generated marker is source and must be mirrored
    into brain. The generation step sees that source page and skips
    composing over it.
    """
    parts = [p for p in path.split("/") if p]
    if not parts or parts[0] == ".git":
        return False
    if is_view(frontmatter or {}):
        return False
    return True


def is_source_path_for_gone_file(path: str) -> bool:
    """Source decision for delete/rename paths whose content is gone.

    Git only gives the path for `D` and the old side of `R`, so the
    curator cannot read the old frontmatter marker. The legacy generated
    filenames remain authoritative here to avoid deleting generated
    projection pages from brain during a transition.
    """
    parts = [p for p in path.split("/") if p]
    if not parts or parts[0] == ".git":
        return False
    if _is_generated_name(path):
        logger.warning(
            "[curator] treating {} as generated by legacy filename backstop; "
            "missing generated frontmatter marker on delete/rename path",
            path,
        )
        return False
    return True


def diff_to_fileops(
    name_status_lines: list[str],
    fm_reader=None,
) -> list[tuple[str, str, str]]:
    """Map a `git diff --name-status -M` block to brain file operations.

    Each output op is `(action, path, from_path)`:

      - `("copy", new, src)`  — copy `src` from memory into brain at
        `new` (an add, modify, or the destination half of a rename).
        For a plain add/modify `src == new`.
      - `("rm", old, "")`     — remove `old` from brain (a delete, or
        the source half of a rename).

    Renames (`R<score>\\told\\tnew`) become an `rm old` + `copy new`
    pair, so a re-slugged capture lands at its new path with no stale
    file left behind. Added/copied/modified files are filtered by their
    view frontmatter. Delete and old-rename paths use the legacy
    filename backstop because git cannot provide frontmatter for a gone
    file. Blank and malformed lines are skipped.
    """
    def _fm(path: str) -> dict:
        if fm_reader is None or not path.endswith(".md"):
            return {}
        return fm_reader(path) or {}

    ops: list[tuple[str, str, str]] = []
    for raw in name_status_lines:
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        fields = line.split("\t")
        status = fields[0].strip()
        code = status[:1]
        if code == "R" or code == "C":
            if len(fields) < 3:
                continue
            old, new = fields[1], fields[2]
            if is_source_path_for_gone_file(old):
                ops.append(("rm", old, ""))
            if is_source_path(new, _fm(new)):
                ops.append(("copy", new, new))
        elif code == "D":
            if len(fields) < 2:
                continue
            old = fields[1]
            if is_source_path_for_gone_file(old):
                ops.append(("rm", old, ""))
        elif code in _COPY_STATUSES:
            if len(fields) < 2:
                continue
            new = fields[1]
            if is_source_path(new, _fm(new)):
                ops.append(("copy", new, new))
        # Unknown status (e.g. `U` unmerged) is skipped — the nightly
        # reconcile is the catch-all for any state the incremental path
        # can't classify.
    return ops


def reconcile_fileops(
    memory_paths: list[str], brain_paths: list[str],
    memory_fm_reader=None, brain_fm_reader=None,
) -> list[tuple[str, str, str]]:
    """Full reconcile: make brain's source files exactly match memory's.

    `memory_paths` is every tracked file in the memory clone;
    `brain_paths` is every tracked file in the brain working copy. Both
    are filtered to source paths, then:

      - every memory source file is copied into brain (overwrites, so an
        edit missed by the incremental path is healed), and
      - every brain source file that memory no longer has is removed.

    Views in brain carry their view frontmatter, so they never
    appear on either side. The reconcile leaves them untouched for
    generation to manage. This is the nightly self-heal, rsync
    `--delete` semantics scoped to source.
    """
    def _fm(reader, path: str) -> dict:
        if reader is None or not path.endswith(".md"):
            return {}
        return reader(path) or {}

    mem = [p for p in memory_paths if is_source_path(p, _fm(memory_fm_reader, p))]
    brain_src = {p for p in brain_paths if is_source_path(p, _fm(brain_fm_reader, p))}
    ops: list[tuple[str, str, str]] = []
    for path in sorted(mem):
        ops.append(("copy", path, path))
    for path in sorted(brain_src - set(mem)):
        ops.append(("rm", path, ""))
    return ops


def consume_trigger(state_dir: Path, name: str = TRIGGER_NAME) -> bool:
    """Consume a pending trigger (mirror-now by default). True when one was there.

    Deletion is the consumption: a concurrent second consumer loses the
    unlink race and correctly reports no trigger.
    """
    try:
        (state_dir / name).unlink()
        return True
    except OSError:
        return False


async def sleep_until_tick(secs: float, state_dir: Path,
                           slice_secs: float = 1.0, wake=lambda: False) -> bool:
    """Sleep up to `secs`, waking early when a trigger lands.

    Returns True when a mirror-now trigger woke it, which asks for the
    model's compile at once too. A push trigger (Forgejo's notice) wakes
    it without that and returns False, as the poll does when `secs` run
    out: the poll is what still finds a push whose notice never came.
    `wake()` true ends it the same way: a compile with the model that has
    ended. The sleep is sliced so either trigger is seen within about a
    second instead of a full poll interval.
    """
    deadline = time.monotonic() + secs
    while time.monotonic() < deadline:
        if consume_trigger(state_dir):
            return True
        if consume_trigger(state_dir, PUSH_TRIGGER_NAME) or wake():
            return False
        await asyncio.sleep(min(slice_secs, max(deadline - time.monotonic(), 0.0)))
    return consume_trigger(state_dir)


def request_push(state_dir: Path) -> None:
    """Leave the push trigger: Forgejo says the memory repo moved."""
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / PUSH_TRIGGER_NAME).write_text(str(time.time()), encoding="utf-8")


def mirror_now(store: CardStore, state_dir: Path, commit: str, *,
               busy: Callable[[], bool] = lambda: False,
               timeout: float = MIRROR_WAIT_SECS, interval: float = MIRROR_POLL_INTERVAL) -> bool:
    """Wake the loop to mirror `commit`, and wait for it; whether the trees
    people read (the wiki, the agent's mount) have it within `timeout`.

    The write route's half of the mirror: the store committed in one of the
    API's threads, the loop mirrors on the tick the push trigger starts and
    records what it mirrored, and the write is answered once that record
    includes `commit`. False is a delay, never a failure: the commit is in
    the clone either way, and the loop mirrors it regardless.

    While `busy()`, the loop is away: held before its first cycle (for the
    migrations, which write through here, or for the brain's clone), or on
    the nightly sweep for minutes, whose own writes come through here too.
    The answer is not yet, at once, and the trigger has the loop mirror it
    when it runs.
    """
    request_push(state_dir)
    if busy():
        return False
    record = state_dir / MIRROR_SHA_NAME
    deadline = time.monotonic() + timeout
    while True:
        mirrored = record.read_text(encoding="utf-8").strip() if record.exists() else ""
        if store.includes(commit, later=mirrored):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def nightly_due(nightly: str, last_run_date: str, now_local: time.struct_time) -> bool:
    """True when the nightly sweep should run: a valid HH:MM is
    configured, we're past it, and today's sweep hasn't happened."""
    if not nightly or ":" not in nightly:
        return False
    try:
        hour, minute = (int(x) for x in nightly.split(":", 1))
    except ValueError:
        return False
    today = time.strftime("%Y-%m-%d", now_local)
    if last_run_date == today:
        return False
    return (now_local.tm_hour, now_local.tm_min) >= (hour, minute)


# ── Remotes and sync reporting ───────────────────────────────────────────


def curator_remote(build_url) -> str | None:
    """Build a container-plane remote URL from the environment, or None.

    Read fresh on every call rather than captured at boot: this is also
    the recovery path for a credential Forgejo has since rejected, and
    a value cached at startup can only ever hand back the rejected one.
    The service name in `CODE_URL` is what makes the container plane
    immune to the DHCP lease that broke the host plane.
    """
    code_url = os.environ.get("CODE_URL", "")
    admin_user = os.environ.get("MATRIX_ADMIN_USER", "")
    admin_password = os.environ.get("MATRIX_ADMIN_PASSWORD", "")
    if not (code_url and admin_user and admin_password):
        return None
    return authenticated_remote(build_url(code_url), admin_user, admin_password)


# What a reconcile outcome means for whoever is reading the logs. The
# healthy statuses say nothing at all; everything else is the data
# plane failing to make progress, and this incident is what happens
# when that is filed under DEBUG: a sync that had not worked in weeks,
# retrying quietly every 30 seconds, with no line anywhere to show it.
_SYNC_REPORT = {
    "up_to_date":          (None, ""),
    "fast_forwarded":      (None, ""),
    "ahead":               (None, ""),
    "reset_to_remote":     ("warning", "{label}: realigned to the remote (projection, rebuilt from source)"),
    "unreachable":         ("warning", "{label}: Forgejo unreachable, sync is not making progress ({detail})"),
    "auth_failed":         ("error", "{label}: Forgejo rejected the credentials, sync is stuck until they are refreshed ({detail})"),
    "failed":              ("error", "{label}: git refused the recovery ({detail})"),
}


def report_vault_sync(result: Sync, *, waited: int) -> Sync:
    """Log how the memory's clone stands against Forgejo after a cycle.

    Quiet while all is well. `waited` is how many commits waited before
    the cycle, so a delivery is said once, when it happens.
    """
    count = len(result.pending)
    waiting = f"{count} change{'s' if count != 1 else ''} wait in the clone"
    if result.state == "ok":
        if waited:
            logger.info("[curator] vault: pushed {} change(s) that waited for Forgejo", waited)
    elif result.state == "unreachable" and is_auth_failure(result.detail):
        logger.error("[curator] vault: Forgejo rejected the credentials, {} until they are "
                     "refreshed ({})", waiting, result.detail)
    elif result.state == "unreachable":
        logger.warning("[curator] vault: Forgejo unreachable, {} ({})", waiting, result.detail)
    elif result.state == "refused":
        logger.warning("[curator] vault: Forgejo refused the push, {} ({})", waiting, result.detail)
    elif result.state == "diverged":
        logger.error("[curator] vault: the clone and Forgejo diverged and {} for a person to "
                     "decide; nothing is pushed or reset until then ({})", waiting, result.detail)
    else:
        logger.warning("[curator] vault: {}", result.detail)
    return result


def report_sync(label: str, result: SyncResult) -> SyncResult:
    """Log a reconcile outcome at a level that matches its severity."""
    level, template = _SYNC_REPORT.get(
        result.status, ("warning", "{label}: unexpected sync status {detail}"),
    )
    if level is not None:
        getattr(logger, level)(
            "[curator] "
            + template.format(label=label, detail=result.detail or result.status),
        )
    return result


# ── Git plumbing ─────────────────────────────────────────────────────────

class Vault:
    """The curator's clone of the memory, and the card store that writes it.

    The store is the clone's one writer (ADR-019 §3): every change to the
    memory becomes a commit here, pushed to Forgejo before the writer is
    answered. `sync` is the cycle's half: make the clone when there is none,
    give a new memory its seed, pull what Forgejo has, push what waited.
    The other methods only read. The bind-mounted repo belongs to
    the host user, so git's dubious-ownership check is satisfied via a
    private GIT_CONFIG_GLOBAL rather than touching any shared config.
    """

    def __init__(self, path: Path, *, server_name: str = "",
                 shared_bucket: str = DEFAULT_SHARED_BUCKET, seeds: Path | None = None):
        self.path = path
        # What a new memory is given as its first commit (`seed_files`).
        self.shared_bucket, self.seeds = shared_bucket, seeds
        self._env = {**os.environ, "GIT_CONFIG_GLOBAL": "/tmp/curator-gitconfig"}
        Path("/tmp/curator-gitconfig").write_text(
            "[safe]\n\tdirectory = *\n", encoding="utf-8",
        )
        self.store = CardStore(path, remote=CURATOR_REMOTE, server_name=server_name,
                               git_env={"GIT_CONFIG_GLOBAL": "/tmp/curator-gitconfig"},
                               check=CardCheck(shared_bucket=shared_bucket))

    def _run(self, *args: str) -> str | None:
        result = subprocess.run(
            ["git", "-C", str(self.path), *args],
            capture_output=True, text=True, env=self._env,
        )
        if result.returncode != 0:
            logger.debug("[curator] git {} failed: {}", args[0], result.stderr.strip())
            return None
        return result.stdout

    async def head(self) -> str | None:
        out = await asyncio.to_thread(self._run, "rev-parse", "HEAD")
        return out.strip() if out else None

    async def sync(self) -> Sync:
        """Pull what Forgejo has and push what waited, through the store.

        Never fatal. Forgejo briefly unreachable means the tick is
        skipped and everything keeps serving what is on disk, and the
        writes of the meantime wait in the clone. Anything short of
        progress is logged where an operator sees it.
        """
        return await asyncio.to_thread(self.sync_now)

    def sync_now(self) -> Sync:
        """`sync`, in the calling thread: what the API's `POST /sync` runs."""
        if not (self.path / ".git").exists() and (missing := self._clone()):
            return report_vault_sync(missing, waited=0)
        self._seed_if_new()
        waited = len(self.store.pending())
        result = self.store.sync()
        if (result.state == "unreachable" and is_auth_failure(result.detail)
                and self._refresh_remote()):
            # The rejected credential may simply be the one we cached at
            # boot. Re-derive from the current environment and try once
            # more, then stop: a second failure is a real one.
            result = self.store.sync()
        return report_vault_sync(result, waited=waited)

    def _clone(self) -> Sync | None:
        """Clone the memory from Forgejo; a `Sync` saying why not, None once it is there."""
        url = curator_remote(vault_remote_url)
        if not url:
            return Sync("missing", detail="no Forgejo address or admin credentials here to clone "
                                          "the memory with: `stack up memory` renders them")
        if not ensure_vault_cloned(self.path, url):
            return Sync("missing", detail="the memory's repository is not in Forgejo yet, or "
                                          "cannot be cloned from it: `stack up memory` makes it")
        self.ensure_remote(CURATOR_REMOTE, url)
        logger.info("[curator] vault: cloned the memory to {}", self.path)
        return None

    def _seed_if_new(self) -> None:
        """Give a memory with no commit yet its seed, as its first commit."""
        try:
            if not self.store.is_new():
                return
            seeds = seed_files(self.seeds, shared_bucket=self.shared_bucket)
            written = self.store.commit([Put(path, text, base="") for path, text in seeds.items()],
                                        by=MEMORY_BOT, kind="seed", title=SEED_TITLE)
        except StoreError as e:
            logger.error("[curator] vault: the new memory was not given its seed: {}", e)
            return
        logger.info("[curator] vault: gave the new memory its seed, {} file(s){}", len(seeds),
                    ", waiting for Forgejo" if written.pending else "")

    def _refresh_remote(self) -> bool:
        url = curator_remote(vault_remote_url)
        if not url:
            return False
        self.ensure_remote(CURATOR_REMOTE, url)
        return True

    def ensure_remote(self, name: str, url: str) -> None:
        """Idempotently point the named remote at `url` (add on first boot)."""
        if self._run("remote", "set-url", name, url) is None:
            self._run("remote", "add", name, url)

    async def subjects(self, since: str, until: str) -> list[str]:
        """Commit subjects in `since..until`. A broken range (history
        rewrite) returns [] — which `only_own_commits` treats as not
        ours, so the rebuild still happens."""
        out = await asyncio.to_thread(
            self._run, "log", "--format=%s", f"{since}..{until}",
        )
        return out.splitlines() if out else []

    async def changed_paths(self, since: str, until: str) -> list[str]:
        out = await asyncio.to_thread(
            self._run, "diff", "--name-only", f"{since}..{until}",
        )
        return [p for p in (out or "").splitlines() if p.strip()]

    async def name_status(self, since: str, until: str) -> list[str]:
        """`git diff --name-status -M` lines for the mirror.

        Rename detection (`-M`) surfaces a re-slugged capture as one
        `R<score>\\told\\tnew` line so the mirror moves it instead of
        leaving a stale copy. A broken range returns [] — the nightly
        reconcile heals whatever the incremental pass missed."""
        out = await asyncio.to_thread(
            self._run, "diff", "--name-status", "-M", f"{since}..{until}",
        )
        return [ln for ln in (out or "").splitlines() if ln.strip()]

    async def tracked_files(self) -> list[str]:
        """Every tracked file in the working copy (`git ls-files`)."""
        out = await asyncio.to_thread(self._run, "ls-files")
        return [p for p in (out or "").splitlines() if p.strip()]

    def frontmatter_at(self, rev: str, path: str) -> dict:
        """Frontmatter of `path` at `rev` ({} for deleted/binary files)."""
        out = self.text_at(rev, path)
        return _parse_frontmatter(out, f"{rev}:{path}") if out else {}

    def text_at(self, rev: str, path: str) -> str:
        """The text of `path` at `rev`, "" where it does not exist."""
        return self._run("show", f"{rev}:{path}") or ""


class Brain:
    """The brain projection working copy — the curator owns its git.

    Unlike the memory clone (read-only to the curator), brain is
    written: the mirror applies file ops copied from memory, generation
    writes pages on top (slice 4), and the curator commits everything as
    one commit per cycle and pushes. Quartz mounts this directory.
    """

    def __init__(self, path: Path, source: Path):
        self.path = path
        self.source = source
        self._env = {**os.environ, "GIT_CONFIG_GLOBAL": "/tmp/curator-gitconfig"}

    def _run(self, *args: str) -> tuple[int, str, str]:
        rc, out, err = run_git(self.path, *args, env=self._env)
        if rc != 0:
            logger.debug("[curator] brain git {} failed: {}", args[0], err)
        return rc, out, err

    async def tracked_files(self) -> list[str]:
        _, out, _ = await asyncio.to_thread(self._run, "ls-files")
        return [p for p in out.splitlines() if p.strip()]

    def frontmatter_at(self, path: str) -> dict:
        """Frontmatter of a tracked brain file, or {} when unreadable."""
        try:
            text = (self.path / path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return {}
        return _parse_frontmatter(text, path)

    def _apply(self, ops: list[tuple[str, str, str]]) -> None:
        """Replay file ops onto brain's working tree (no git, no commit).

        A `copy` reads the file from the memory source clone and writes
        it into brain at the same relative path; a `rm` deletes brain's
        copy. Both are idempotent and tolerant of a missing source/target
        — the nightly reconcile is the catch-all for any slip.
        """
        for action, path, src in ops:
            target = self.path / path
            if action == "copy":
                source_file = self.source / (src or path)
                try:
                    data = source_file.read_bytes()
                except OSError:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            elif action == "rm":
                try:
                    target.unlink()
                except OSError:
                    pass

    async def apply(self, ops: list[tuple[str, str, str]]) -> None:
        await asyncio.to_thread(self._apply, ops)

    def _commit_push(self, message: str) -> bool:
        """Stage everything, commit if there is a change, push. Returns
        True when a commit was made and pushed (or there was nothing to
        commit, which is still a success).

        Staging and committing hold the brain's writer lock, so a commit
        never takes a view a compile is writing at that moment.
        """
        with brain_lock.held(self.path):
            if not self._commit(message):
                return False
        return self._push()

    def _commit(self, message: str) -> bool:
        """Stage everything and commit when there is a change; False when git refused."""
        self._run("add", "-A")
        # `diff --cached --quiet` exits 1 when staged changes exist, so its
        # exit code is the answer to "is there anything to commit?" rather
        # than a fault. It calls git directly to stay out of `_run`, which
        # reports every non-zero exit as a failure and would therefore
        # announce a broken mirror on every cycle that had work to do.
        code, _, _ = run_git(self.path, "diff", "--cached", "--quiet", env=self._env)
        if code != 0:
            rc, _, _ = self._run(
                "-c", f"user.name={_BRAIN_AUTHOR_NAME}",
                "-c", f"user.email={_BRAIN_AUTHOR_EMAIL}",
                "commit", "-m", message,
            )
            if rc != 0:
                return False
        return True

    def _push(self) -> bool:
        # Push even with nothing newly committed: a prior cycle may have
        # committed locally and lost the push (Forgejo briefly down), and
        # "nothing to commit" must not report that state as in-sync.
        rc, _, err = self._run("push", "--quiet", CURATOR_REMOTE, "main")
        if rc == 0:
            return True
        if is_auth_failure(err) and self._refresh_remote():
            rc, _, err = self._run("push", "--quiet", CURATOR_REMOTE, "main")
            if rc == 0:
                return True
        # No force-push here any more. Forcing was indiscriminate: it
        # fired on every failure, including the 403 from a `family/brain`
        # that had never been created, where it could not help and its
        # "remote diverged" line actively hid the real cause. A refused
        # push now says why, and a genuinely diverged remote is handled
        # by `sync` at the top of the next cycle, which realigns and
        # re-projects instead of overwriting history.
        logger.error("[curator] brain push failed, projection is not reaching Forgejo: {}", err)
        return False

    def ensure_remote(self, name: str, url: str) -> None:
        """Idempotently point the named remote at `url` (add on first boot)."""
        code, _, _ = self._run("remote", "set-url", name, url)
        if code != 0:
            self._run("remote", "add", name, url)

    def _refresh_remote(self) -> bool:
        url = curator_remote(brain_remote_url)
        if not url:
            return False
        self.ensure_remote(CURATOR_REMOTE, url)
        return True

    async def commit_push(self, message: str) -> bool:
        return await asyncio.to_thread(self._commit_push, message)

    async def sync(self) -> SyncResult:
        """Reconcile the projection with its remote before rebuilding it.

        Brain is machine-owned and regenerable (ADR-011), so it resets:
        if the remote holds commits this copy does not,
        or the repo was re-created and shares no history at all, the
        remote is simply taken as the new base. Nothing is preserved
        because nothing here is irreplaceable; the caller re-projects
        from memory on top.

        Local commits that are merely ahead are left alone — those are
        last cycle's projection waiting on a push, not a divergence.
        """
        return await asyncio.to_thread(self._sync)

    def _sync(self) -> SyncResult:
        result = self._reconcile()
        if result.status == "auth_failed" and self._refresh_remote():
            result = self._reconcile()
        return report_sync("brain", result)

    def _reconcile(self) -> SyncResult:
        return reconcile_with_remote(self.path, CURATOR_REMOTE, env=self._env)


# ── Rebuild ──────────────────────────────────────────────────────────────

async def compile_diary(state_dir: Path | None = None) -> bool:
    """One diary pass over the memories room, on the nightly sweep.

    The compiler re-reads the whole room every time, because a reply or
    an edit arriving tonight can belong to an entry from years back, and
    anything that walked forward from a watermark would never attach it.
    Re-reading is affordable because what each message cost is on file:
    the nightly pays for recordings and readings that are genuinely new
    and reuses the rest.
    """
    return await _run_command("diary", [], "compiling the diary", state_dir)


async def archive_lists(state_dir: Path | None = None) -> bool:
    """Move yesterday's ticked list items into each list's archive."""
    return await _run_command("lists", [], "archiving ticked list items", state_dir)


async def rebuild(selection: list[str], state_dir: Path | None = None) -> bool:
    """One wiki generation pass via the CLI entrypoint — the same code
    path `stack memory wiki update` execs, in a subprocess so a wedged
    LLM call dies with the child instead of inside this loop."""
    label = " ".join(selection) if selection else "every page"
    return await _run_command("wiki", selection, f"regenerating wiki pages: {label}", state_dir)


async def compile_views(selection: Selection, *, fast: bool,
                        state_dir: Path | None = None) -> bool:
    """Compile the views in `selection` through the CLI entrypoint.

    `fast` asks no model: the views show the cards as they are now, in the
    model's last wording. The diary's months are compiled from the diary
    records on file, without reading the room again.
    """
    flag = ["--fast"] if fast else []
    speed = "at once" if fast else "with the model"
    ok = True
    if selection.wiki:
        ok = await _run_command("wiki", [*selection.wiki, *flag],
                                f"compiling {speed}: {' '.join(selection.wiki)}", state_dir)
    if selection.months:
        months = [arg for month in selection.months for arg in ("--month", month)]
        ok = await _run_command("diary", ["pages", *flag, *months],
                                f"compiling {speed}: diary {' '.join(selection.months)}",
                                state_dir) and ok
    return ok


async def _run_command(command: str, selection: list[str], what: str,
                       state_dir: Path | None = None) -> bool:
    """Run one entrypoint command in a child process; True when it succeeded.

    The child's lines (one per page: published, unchanged, skipped) go to
    the log and, with a `state_dir`, to the progress file a waiting
    `stack memory wiki update` prints from, as they come.
    """
    _progress(state_dir, what)
    # Starting the child and waiting on it are separate failure modes, and
    # only the second one has a child to kill. Keeping them in one block
    # left `proc.kill()` reachable on a path where `proc` was never bound.
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, ENTRYPOINT, command, *selection,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except Exception as e:
        logger.warning("[curator] {} failed to start: {}", command, e)
        return False

    lines: list[str] = []

    async def follow() -> None:
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            lines.append(line)
            if line.strip() and not line.startswith("{"):
                _progress(state_dir, line.strip()[:200])
        await proc.wait()

    try:
        await asyncio.wait_for(follow(), timeout=REBUILD_TIMEOUT_SECS)
    except TimeoutError:
        proc.kill()
        logger.warning("[curator] {} timed out after {}s",
                       command, REBUILD_TIMEOUT_SECS)
        return False
    except Exception as e:
        logger.warning("[curator] {} failed: {}", command, e)
        return False

    if proc.returncode != 0:
        tail = "\n".join(lines[-5:])
        logger.warning("[curator] {} rc={}: {}", command, proc.returncode, tail)
        return False
    published = sum(1 for ln in lines if ln.startswith("published "))
    logger.info("[curator] {} done — {} page(s) published", command, published)
    return True


def _progress(state_dir: Path | None, what: str, *, fresh: bool = False) -> None:
    """One step, to the log and, when someone may be waiting, the progress file."""
    logger.info("[curator] {}", what)
    if state_dir is None:
        return
    try:
        report_progress(state_dir, what, fresh=fresh)
    except OSError as e:
        logger.warning("[curator] could not record progress: {}", e)


# ── Card ids ─────────────────────────────────────────────────────────────

def purge_views(store: CardStore) -> list[str]:
    """Take the views an older release compiled into the memory out of it.

    Before the brain projection (B1) the wiki compiled its pages into the
    memory itself. Views are the curator's projection, never cards: a
    markdown file whose frontmatter declares it a view (or `generated:
    true`, as views did before) comes out, wherever it is, in one commit by
    the memory bot through the store. A page that only reserves a
    generated region, such as the seeded `index.md`, and every README stay
    (`declares_generated`). Run once a start; with none left it writes
    nothing. Returns the paths taken out; a refusal is logged, and the next
    start tries again.
    """
    try:
        found = [f for f in store.files() if f.path.endswith(".md")
                 and PurePosixPath(f.path).name != "README.md" and declares_generated(f.text)]
        if found:
            store.commit([Remove(f.path, f.version) for f in found], by=MEMORY_BOT, kind="cards",
                         title="purge generated wiki pages from source")
    except StoreError as e:
        logger.warning("[curator] generated pages not taken out of the memory yet: {}", e)
        return []
    if found:
        logger.info("[curator] took {} generated wiki page(s) out of the memory", len(found))
    return [f.path for f in found]


def give_ids_since(store: CardStore, since: str | None) -> bool:
    """Give the cards pushed after `since` an id of their own; True when done.

    `since` None looks at every file, for a curator with no commit on record.
    The ids are one commit by the memory bot, through the card store.

    False when the store did not write it (a card changed meanwhile, git
    failed), so the next cycle tries again from the same commit. A file the
    rule cannot place is logged once and counts as done: it waits for a
    person, not a retry.
    """
    try:
        found = card_ids_for(store.repo, since=since)
    except OSError as e:
        logger.warning("[curator] card ids not given yet: {}", e)
        return False
    for line in found.left:
        logger.warning("[curator] no card id for {}; left as it is", line)
    if not found.changes:
        return True
    changes = [Put(path, text, base=_version(store, path)) for path, text in sorted(found.changes.items())]
    try:
        store.commit(changes, by=MEMORY_BOT, kind="cards",
                     title=f"give {len(changes)} card(s) an id of their own")
    except StoreError as e:
        logger.warning("[curator] card ids not given yet: {}", e)
        return False
    logger.info("[curator] gave {} card(s) an id: {}", len(changes), ", ".join(sorted(found.changes)))
    return True


def _version(store: CardStore, path: str) -> str:
    current = store.read(path)
    return current.version if current else ""


def _card_editor(store: CardStore, shared_bucket: str, language: str, state_dir: Path):
    """The card write endpoint (`memory/card_edit.py`): it commits a change
    through the card store, as the person it is by, and wakes the loop to
    mirror it. Without the write token a write is refused; without the
    Matrix server's name a person's write is refused, never made under an
    address made up in its place."""
    from stacklets.memory.card_edit import CardEditor
    return CardEditor(store=store, token=os.environ.get("CARDS_WRITE_TOKEN", ""),
                      shared_bucket=shared_bucket, language=language,
                      on_write=lambda: request_push(state_dir))


# ── Main loop ────────────────────────────────────────────────────────────

def _serve_cards(brain_dir: Path, vault_dir: Path, media_dir: Path, shared_bucket: str,
                 state_dir: Path, vault: Vault, *, away: threading.Event) -> None:
    """The cards as JSON for core's card page, on the stack network (`memory/cards_api.py`).

    The curator holds the vault and the brain, so it serves the vault's cards
    with the views it compiled about them, and the media archive's files
    those cards embed. The same server takes the memory's writes through
    the card store (`memory.cards.StoreAPI`, with the card write token) and
    Forgejo's notice of a push to the memory repo; both wake the loop below.
    `away` is set while the loop does not mirror (`mirror_now`).
    A failure to start is logged, never fatal: the wiki is the curator's job.
    """
    from stacklets.memory.cards_api import PORT, CardsAPI, serve
    fonts = Path(__file__).resolve().parents[1] / "quartz" / "static" / "fonts"
    language = os.environ.get("LANGUAGE", "en")
    try:
        serve(CardsAPI(brain=brain_dir, vault=vault_dir, fonts=fonts, media=media_dir,
                       shared_bucket=shared_bucket, language=language),
              on_push=lambda: request_push(state_dir),
              editor=_card_editor(vault.store, shared_bucket, language, state_dir),
              store_api=StoreAPI(vault.store, token=os.environ.get("CARDS_WRITE_TOKEN", ""),
                                 on_write=lambda commit: mirror_now(vault.store, state_dir, commit,
                                                                     busy=away.is_set),
                                 sync=vault.sync_now))
        logger.info("[curator] cards API on :{}", PORT)
    except OSError as e:
        logger.warning("[curator] cards API not started: {}", e)


async def hold_until_migrated() -> None:
    """Return once memory's data has had every migration this release ships.

    `stack up memory` starts this container before it runs memory's
    migrations, and the curator writes the memory too (card ids, card
    edits), so it touches nothing in between. Read from the files and the
    record, without the code host.
    """
    data = Path(os.environ.get("MEMORY_DATA", "/data/memory"))

    def say(keys: list[str]) -> None:
        logger.info("[curator] waiting for the migrations: {}", ", ".join(keys))

    await until_migrated([Migrations("memory", MEMORY_STACKLET, data)], say,
                         MIGRATIONS_POLL_SECS)


async def main() -> None:
    rebuild_enabled = os.environ.get("WIKI_AUTO_REBUILD", "true").lower() == "true"

    vault_dir = Path(os.environ.get("MEMORY_VAULT_DIR", "/data/memory/vault"))
    brain_dir = Path(os.environ.get("BRAIN_REPO_DIR", "/data/memory/brain"))
    shared_bucket = os.environ.get("SHARED_BUCKET", "family")
    state_dir = Path(os.environ.get("CURATOR_STATE_DIR", "/data/memory/curator"))
    sha_file = state_dir / REBUILT_SHA_NAME
    mirror_file = state_dir / MIRROR_SHA_NAME
    card_ids_file = state_dir / CARD_IDS_SHA_NAME
    nightly_file = state_dir / "last-nightly-date"

    vault = Vault(vault_dir, server_name=os.environ.get("MATRIX_SERVER_NAME", ""),
                  shared_bucket=shared_bucket)
    brain = Brain(brain_dir, vault_dir)
    # Set while the loop does not mirror: until its first cycle (held for the
    # migrations, waiting for its clone or the brain's) and for the length of
    # the nightly sweep. The API's threads read it (`mirror_now`).
    away = threading.Event()
    away.set()
    # The API answers from the curator's first moment, before its clone and
    # before the migrations: `stack up memory` is ready once it answers, the
    # install asks it to clone and seed a new memory (`POST /sync`), and the
    # migrations write through it.
    _serve_cards(brain_dir, vault_dir, Path(os.environ.get("MEDIA_ARCHIVE_DIR", "/data/memory/media")),
                 shared_bucket, state_dir, vault, away=away)
    await hold_until_migrated()

    while (await vault.sync()).state == "missing":
        await asyncio.sleep(POLL_SECS)
    await asyncio.to_thread(purge_views, vault.store)
    while not (brain_dir / ".git").exists():
        logger.info("[curator] waiting for brain projection at {}", brain_dir)
        await asyncio.sleep(POLL_SECS)
    debounce = Debounce(QUIET_SECS)

    async def select(since: str, until: str) -> Selection:
        return await views_pushed(vault, brain_dir, since, until, shared_bucket=shared_bucket)

    async def compile(selection: Selection, *, fast: bool) -> bool:
        # The quick compile follows every push, so it reports to the log
        # only; the progress file is for a `stack memory wiki update`
        # waiting on the model's compile.
        if not fast:
            _progress(state_dir, "regenerating the pages new filings touched", fresh=True)
        return await compile_views(selection, fast=fast, state_dir=None if fast else state_dir)

    recompile = Recompile(select, compile, debounce)

    # The working copies' `origin` carries a host-plane URL (hooks and
    # the host CLI set and use it); it is unreachable from inside this
    # container. Give the curator its own remote on the stack network.
    # Auth: the unified stack admin is also Forgejo's admin.
    vault_url = curator_remote(vault_remote_url)
    brain_url = curator_remote(brain_remote_url)
    if vault_url and brain_url:
        vault.ensure_remote(CURATOR_REMOTE, vault_url)
        brain.ensure_remote(CURATOR_REMOTE, brain_url)
    else:
        logger.warning("[curator] no CODE_URL/admin creds — remote sync disabled, serving local state")

    # GIT_CONFIG_GLOBAL with `safe.directory = *` is written by Vault's
    # __init__; brain reuses the same file (both repos are bind-mounted
    # and host-owned). Vault constructed above, so the file exists.

    def _write(path: Path, value: str) -> str:
        state_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
        return value

    def _read(path: Path) -> str:
        return path.read_text(encoding="utf-8").strip() if path.exists() else ""

    # First boot starts from the current HEAD: existing content is the
    # operator's to backfill (`stack memory wiki update --all`), not something to
    # surprise a fresh install with. From then on the SHA survives
    # restarts, so a rebuild interrupted mid-flight is simply redone.
    last = _read(sha_file)
    if not last:
        head = await vault.head()
        if head:
            last = _write(sha_file, head)
    # Same contract for the nightly: with no recorded date, any boot
    # after the configured time would fire an immediate full sweep
    # (found live on the test rig). First boot counts as done-today;
    # the first real sweep runs tomorrow night.
    if not _read(nightly_file):
        _write(nightly_file, time.strftime("%Y-%m-%d", time.localtime()))
    # Card ids follow the same contract: the cards already there are
    # migration 0002's, the curator looks at what is pushed after it.
    card_ids_sha = _read(card_ids_file)
    if not card_ids_sha:
        head = await vault.head()
        if head:
            card_ids_sha = _write(card_ids_file, head)

    async def mirror_incremental(since: str, until: str) -> bool:
        """Replay memory's `since..until` source diff onto brain, compile
        the views it touched without the model, then commit + push. One
        commit. Returns True on success (incl. a no-op range)."""
        ops = diff_to_fileops(
            await vault.name_status(since, until),
            lambda path: vault.frontmatter_at(until, path),
        )
        await brain.apply(ops)
        if rebuild_enabled:
            await recompile.pushed(since, until)
        return await brain.commit_push(f"{BRAIN_COMMIT_PREFIX} sync source")

    async def mirror_reconcile() -> bool:
        """Full source reconcile: make brain's source files exactly match
        memory's, then commit + push. The first-boot populate and the
        nightly self-heal both use this."""
        ops = reconcile_fileops(
            await vault.tracked_files(), await brain.tracked_files(),
            lambda path: vault.frontmatter_at("HEAD", path),
            brain.frontmatter_at,
        )
        await brain.apply(ops)
        return await brain.commit_push(f"{BRAIN_COMMIT_PREFIX} reconcile source")

    # First boot: brain carries only its scaffold, so populate it from
    # memory in full before the incremental loop takes over.
    mirror_sha = _read(mirror_file)
    if not mirror_sha:
        head = await vault.head()
        if head and await mirror_reconcile():
            mirror_sha = _write(mirror_file, head)

    logger.info(
        "[curator] keeping {} in sync (poll {}s, quiet {}s, nightly {}, rebuild {}) from {}",
        vault_dir, POLL_SECS, QUIET_SECS, NIGHTLY or "off",
        "on" if rebuild_enabled else "OFF", last[:10] or "(empty repo)",
    )

    away.clear()
    while True:
        # A mirror-now trigger (from `stack memory sync`) forces this
        # cycle to rebuild the wiki immediately, bypassing the debounce.
        # The debounce exists to batch a burst of filings into one
        # rebuild; an explicit trigger means "I want it current now",
        # which is what the test rig needs for a fast feedback loop.
        forced = await sleep_until_tick(POLL_SECS, state_dir, wake=lambda: recompile.ended)
        if forced:
            logger.info("[curator] mirror-now trigger received")
        # The curator owns the pull — the wiki container only watches
        # files. Runs even with rebuilds disabled: the wiki must not
        # rot just because the automation is off.
        await vault.sync()
        head = await vault.head()
        if not head:
            continue

        # ── Card ids for what was pushed ──────────────────────────────
        # Before the mirror, so a card reaches brain with its id the
        # cycle after; the commit it makes is in the clone at once.
        if card_ids_sha != head and await asyncio.to_thread(
                give_ids_since, vault.store, card_ids_sha or None):
            card_ids_sha = _write(card_ids_file, head)

        # Brain has to start the cycle on top of the remote it is about
        # to push to. Skip this and a remote that moved — or was
        # re-created, which is how this failure actually arrives — turns
        # every push from here on into a rejection. A realignment throws
        # away the local projection, which is regenerable, so the mirror
        # state is cleared with it and the block below rebuilds brain
        # from memory in full.
        if (await brain.sync()).status == "reset_to_remote":
            mirror_sha = ""
            mirror_file.unlink(missing_ok=True)

        # ── Source mirror (memory -> brain) ───────────────────────────
        # Data-plane like the pull: brain must always carry memory's
        # current source so Quartz renders fresh captures, even with LLM
        # rebuilds disabled. Cheap (file copies, one commit) so it runs
        # every cycle the source moved, undebounced, and with it the
        # views the push touched, compiled without the model.
        if mirror_sha and head != mirror_sha:
            if await mirror_incremental(mirror_sha, head):
                mirror_sha = _write(mirror_file, head)
        elif not mirror_sha:
            if await mirror_reconcile():
                mirror_sha = _write(mirror_file, head)

        if not rebuild_enabled:
            continue
        if not last:
            last = _write(sha_file, head)
            continue

        # ── A compile with the model that ended ───────────────────────
        if (ended := recompile.finished(time.monotonic())) is not None:
            compiled_to, ok = ended
            if ok:
                # Commit + push the views the wiki CLI wrote into brain's
                # working tree.
                await brain.commit_push(f"{BRAIN_COMMIT_PREFIX} incremental rebuild")
                last = _write(sha_file, compiled_to)
            else:
                record_failed_rebuild(state_dir, retry_in=debounce.window())
                # Say the shape of the failure, not just that there was one.
                # A per-cycle line at the same interval reads as a
                # heartbeat; a widening gap with a count on it reads as an
                # outage.
                logger.warning(
                    "[curator] rebuild failed {}x in a row; next attempt in {}m "
                    "(source is still mirrored, only generated pages are stale)",
                    debounce.failures, round(debounce.window() / 60),
                )

        # ── Nightly full sweep ────────────────────────────────────────
        # Runs regardless of pending changes — it covers everything an
        # incremental pass would, so it also clears the debounce. The
        # date is recorded even on failure: one attempt per night, the
        # incremental path and the manual CLI cover the gap. A full
        # source reconcile precedes the regen so brain self-heals any
        # drift the incremental mirror missed. `stack memory wiki update --all`
        # asks for the same sweep now; that does not count as tonight's.
        # It waits while a compile with the model runs: one at a time.
        asked = not recompile.busy and consume_trigger(state_dir, NIGHTLY_TRIGGER_NAME)
        if asked or (not recompile.busy
                     and nightly_due(NIGHTLY, _read(nightly_file), time.localtime())):
            if not asked:
                _write(nightly_file, time.strftime("%Y-%m-%d", time.localtime()))
            _progress(state_dir, "rebuilding the whole wiki", fresh=True)
            _progress(state_dir, "reconciling the source vault with the brain")
            if await mirror_reconcile():
                mirror_sha = _write(mirror_file, head)
            away.set()
            try:
                # Lists first: the night's archive commit then reaches brain
                # with the next mirror, and the pages built below read lists
                # that hold only what is open.
                await archive_lists(state_dir)
                # Diary next: it writes pages into the same working tree,
                # and the commit below should carry both nights' work.
                await compile_diary(state_dir)
                rebuilt = await rebuild([], state_dir)
            finally:
                away.clear()
            if rebuilt:
                # Generation wrote pages into brain's working tree; commit
                # and push them (one commit alongside the reconcile).
                await brain.commit_push(f"{BRAIN_COMMIT_PREFIX} nightly rebuild")
                last = _write(sha_file, head)
                debounce.reset()
                recompile.pending = Selection()
            # The record a waiting `stack memory wiki update --all` reads.
            _write(state_dir / NIGHTLY_RUN_NAME,
                   json.dumps({"at": time.time(), "ok": rebuilt, "asked": asked}))
            continue

        # ── Incremental: the views the burst touched, with the model ──
        if head == last:
            if not recompile.busy:
                debounce.reset()
            continue

        if not recompile.busy and only_own_commits(await vault.subjects(last, head)):
            last = _write(sha_file, head)
            debounce.reset()
            continue

        # Starts it in the background; `finished` above collects it.
        await recompile.settle(last, head, time.monotonic(), forced=forced)


if __name__ == "__main__":
    asyncio.run(main())
