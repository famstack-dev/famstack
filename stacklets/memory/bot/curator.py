"""Curator — keeps the family wiki fresh without anyone running a command.

Runs as the `stack-memory-curator` sidecar (own slim image, see
`curator.Dockerfile`). The curator is the vault keeper: it owns the
`git pull` that keeps the working copy in sync with Forgejo (the wiki
container is a pure view that just watches the files). Jobs:

1. **Incremental, after filings settle.** New commits land — an
   archivist filing, a learn commit, a hand edit pushed from Obsidian.
   The curator debounces the burst, reads which persons the changed
   files involve (`persons:` frontmatter plus the bucket owner), and
   regenerates exactly those member pages plus home. A typical burst
   costs 2-3 LLM calls, so freshness where it's felt — "I filed
   Homer's letter, Homer's page knows" — is nearly free.
2. **Nightly full sweep.** Home, every member, every topic, at
   WIKI_NIGHTLY local time. This is the self-healing catch-all: topic
   pages and cross-references go stale invisibly during the day and
   are corrected while the GPU is idle. It also means the incremental
   person mapping only has to be *helpful*, never load-bearing —
   worst case for a missed page is "stale until tonight".

Rebuilds run the CLI entrypoint as a subprocess (same code path as
`stack memory wiki`), so a wedged LLM call dies with the child, not
inside this loop. The wiki's own publish commits are filtered by
their subject prefix (`wiki.COMMIT_PREFIX`) — the rebuild must not
trigger itself. The last-rebuilt SHA persists across restarts, so an
interrupted rebuild is simply redone.

Failure shape: a failed rebuild keeps the SHA where it was and
retries after another quiet window — the trigger is never lost, and
`stack memory wiki` always works as the manual override. If the
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
import os
import subprocess
import sys
import time
from pathlib import Path

# Sibling imports. The wiki module is loaded straight off its directory
# (not as `cli.wiki`) — the docs stacklet ships a `cli` package too,
# and the bare package name collides when both bot dirs share a
# sys.path (which the test suite's single process does).
sys.path.insert(0, str(Path(__file__).parent / "cli"))  # wiki module
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # memory.lib
sys.path.insert(0, "/app")  # stack.* framework

from loguru import logger  # noqa: E402
from memory.lib import _parse_frontmatter  # noqa: E402

from wiki import COMMIT_PREFIX  # noqa: E402

POLL_SECS = int(os.environ.get("CURATOR_POLL_SECS", "30"))
QUIET_SECS = int(os.environ.get("WIKI_REBUILD_QUIET_SECS", "180"))
NIGHTLY = os.environ.get("WIKI_NIGHTLY", "03:30").strip()

ENTRYPOINT = "/stacklets/memory/bot/cli_entrypoint.py"

# Identity stamped on the curator's brain commits. Brain is a machine-
# owned projection, so its commit log names the curator, not a human.
_BRAIN_AUTHOR_NAME = "memory-curator"
_BRAIN_AUTHOR_EMAIL = "memory-curator@local"

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

# Generated page filenames. Changes to these are the wiki's own output
# (or a human editing around the splice markers) — never a rebuild
# trigger, or the curator would chase its own tail.
_GENERATED_NAMES = {"about.md", "index.md"}


# ── Pure decision logic (tested in tests/stacklets) ──────────────────────

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
    same head has been seen unchanged for `quiet_secs`."""

    def __init__(self, quiet_secs: float):
        self.quiet_secs = quiet_secs
        self._head: str | None = None
        self._since = 0.0

    def observe(self, head: str, now: float) -> bool:
        if head != self._head:
            self._head, self._since = head, now
            return False
        return (now - self._since) >= self.quiet_secs

    def reset(self) -> None:
        self._head = None

    def retry_later(self, now: float) -> None:
        """Failed rebuild: keep the head, restart the quiet window."""
        self._since = now


def member_selection(
    paths: list[str],
    fm_reader,
    *,
    shared_bucket: str,
) -> list[str]:
    """Map changed vault paths to an incremental `wiki` selection argv.

    Persons-only by design: member pages are where freshness is felt,
    everything else (topics, cross-refs) heals on the nightly sweep.
    Collects the bucket owner for personal captures plus every name in
    `persons:` frontmatter (`fm_reader(path) -> dict`, fed by
    `git show` so the mapping matches the commit). Returns e.g.
    `["--home", "--member", "Homer Simpson"]`, or `[]` when nothing
    relevant changed (only generated pages or skipped dirs) — home is
    included whenever anything relevant changed at all.
    """
    members: list[str] = []
    relevant = False

    for path in paths:
        parts = [p for p in path.split("/") if p]
        if not parts or parts[0] in _SKIP_TOP:
            continue
        if parts[-1] in _GENERATED_NAMES:
            continue
        relevant = True
        if parts[0] != shared_bucket and len(parts) > 1 and parts[0] not in members:
            members.append(parts[0])
        if path.endswith(".md"):
            fm = fm_reader(path) or {}
            persons = fm.get("persons") or fm.get("person") or []
            if isinstance(persons, str):
                persons = [persons]
            for person in persons:
                if isinstance(person, str) and person.strip() and person not in members:
                    members.append(person)

    if not relevant:
        return []
    argv = ["--home"]
    for member in members:
        argv += ["--member", member]
    return argv


# ── Source mirror (memory -> brain projection) ───────────────────────────
#
# The curator polls memory (source) and writes brain (projection). The
# mirror replays memory's git diff onto brain's working copy: a new or
# edited capture is copied in, a deleted one removed, a rename moved.
# Generation then writes its pages on top (slice 4), and the whole tree
# is committed to brain as one commit per cycle.
#
# `is_source_path` is the guard that keeps generation's own output from
# being treated as source to mirror: brain carries generated pages
# (about.md, folder index.md) that memory never has, so a stray diff
# entry naming one is ignored. `.git` internals are always skipped.

# Git diff status letters the mirror acts on. `A`dded and `M`odified
# copy the file in; `D`eleted removes it; `R`enamed moves it (old path
# removed, new path copied). `C`opied is treated like an add of the new
# path. `T` (type change) is treated as a modify.
_COPY_STATUSES = {"A", "M", "C", "T"}


def is_source_path(path: str) -> bool:
    """True when a vault path is source the mirror should replay to brain.

    Excludes git internals and the generated page filenames the
    projection owns (about.md, folder index.md). Memory never contains
    those, so this is a defensive belt-and-braces filter: even a
    hand-edited memory file named `about.md` would not be mirrored as
    source and then clobbered by generation.
    """
    parts = [p for p in path.split("/") if p]
    if not parts or parts[0] == ".git":
        return False
    if parts[-1] in _GENERATED_NAMES:
        return False
    return True


def diff_to_fileops(name_status_lines: list[str]) -> list[tuple[str, str, str]]:
    """Map a `git diff --name-status -M` block to brain file operations.

    Each output op is `(action, path, from_path)`:

      - `("copy", new, src)`  — copy `src` from memory into brain at
        `new` (an add, modify, or the destination half of a rename).
        For a plain add/modify `src == new`.
      - `("rm", old, "")`     — remove `old` from brain (a delete, or
        the source half of a rename).

    Renames (`R<score>\\told\\tnew`) become an `rm old` + `copy new`
    pair, so a re-slugged capture lands at its new path with no stale
    file left behind. Lines naming a non-source path (generated pages,
    git internals) are dropped on whichever side fails `is_source_path`:
    a rename out of source degrades to a delete, a rename into source to
    an add. Blank and malformed lines are skipped.
    """
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
            if is_source_path(old):
                ops.append(("rm", old, ""))
            if is_source_path(new):
                ops.append(("copy", new, new))
        elif code == "D":
            if len(fields) < 2:
                continue
            old = fields[1]
            if is_source_path(old):
                ops.append(("rm", old, ""))
        elif code in _COPY_STATUSES:
            if len(fields) < 2:
                continue
            new = fields[1]
            if is_source_path(new):
                ops.append(("copy", new, new))
        # Unknown status (e.g. `U` unmerged) is skipped — the nightly
        # reconcile is the catch-all for any state the incremental path
        # can't classify.
    return ops


def reconcile_fileops(
    memory_paths: list[str], brain_paths: list[str],
) -> list[tuple[str, str, str]]:
    """Full reconcile: make brain's source files exactly match memory's.

    `memory_paths` is every tracked file in the memory clone;
    `brain_paths` is every tracked file in the brain working copy. Both
    are filtered to source paths, then:

      - every memory source file is copied into brain (overwrites, so an
        edit missed by the incremental path is healed), and
      - every brain source file that memory no longer has is removed.

    Generated pages in brain (about.md, folder index.md) are not source,
    so they never appear on either side — the reconcile leaves them
    untouched for generation to manage. This is the nightly self-heal,
    rsync `--delete` semantics scoped to source.
    """
    mem = [p for p in memory_paths if is_source_path(p)]
    brain_src = {p for p in brain_paths if is_source_path(p)}
    ops: list[tuple[str, str, str]] = []
    for path in sorted(mem):
        ops.append(("copy", path, path))
    for path in sorted(brain_src - set(mem)):
        ops.append(("rm", path, ""))
    return ops


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


# ── Git plumbing ─────────────────────────────────────────────────────────

class Vault:
    """Read-only git view of the vault working copy.

    The wiki container owns the `git pull`; we only ever read. The
    bind-mounted repo belongs to the host user, so git's dubious-
    ownership check is satisfied via a private GIT_CONFIG_GLOBAL
    rather than touching any shared config.
    """

    def __init__(self, path: Path):
        self.path = path
        self._env = {**os.environ, "GIT_CONFIG_GLOBAL": "/tmp/curator-gitconfig"}
        Path("/tmp/curator-gitconfig").write_text(
            "[safe]\n\tdirectory = *\n", encoding="utf-8",
        )

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

    async def sync(self) -> None:
        """Fast-forward the working copy from Forgejo.

        Same cheap shape the wiki entrypoint used before the curator
        took ownership: an idle tick is one `ls-remote` ref query, the
        fetch + ff happens only when the remote actually moved. Never
        fatal — Forgejo briefly unreachable means the tick is skipped
        and everything keeps serving what is on disk.
        """
        def _sync() -> None:
            # Same tick shape as the old wiki entrypoint loop: skip on
            # any read failure, compare refs, pull only on real change.
            local = self._run("rev-parse", "HEAD")
            if not local:
                return
            remote = self._run("ls-remote", "origin", "HEAD")
            remote_head = remote.split()[0] if remote and remote.split() else ""
            if not remote_head or local.strip() == remote_head:
                return
            self._run("pull", "--quiet", "--ff-only")

        await asyncio.to_thread(_sync)

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
        out = self._run("show", f"{rev}:{path}")
        return _parse_frontmatter(out) if out else {}


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

    def _run(self, *args: str) -> tuple[int, str]:
        result = subprocess.run(
            ["git", "-C", str(self.path), *args],
            capture_output=True, text=True, env=self._env,
        )
        if result.returncode != 0:
            logger.debug("[curator] brain git {} failed: {}", args[0], result.stderr.strip())
        return result.returncode, result.stdout

    async def tracked_files(self) -> list[str]:
        _, out = await asyncio.to_thread(self._run, "ls-files")
        return [p for p in out.splitlines() if p.strip()]

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
        commit, which is still a success)."""
        self._run("add", "-A")
        # `diff --cached --quiet` exits 1 when staged changes exist.
        code, _ = self._run("diff", "--cached", "--quiet")
        if code == 0:
            return True  # nothing to commit — already in sync
        rc, _ = self._run(
            "-c", f"user.name={_BRAIN_AUTHOR_NAME}",
            "-c", f"user.email={_BRAIN_AUTHOR_EMAIL}",
            "commit", "-m", message,
        )
        if rc != 0:
            return False
        rc, _ = self._run("push", "--quiet")
        return rc == 0

    async def commit_push(self, message: str) -> bool:
        return await asyncio.to_thread(self._commit_push, message)


# ── Rebuild ──────────────────────────────────────────────────────────────

async def rebuild(selection: list[str]) -> bool:
    """One wiki generation pass via the CLI entrypoint — the same code
    path `stack memory wiki` execs, in a subprocess so a wedged LLM
    call dies with the child instead of inside this loop."""
    label = " ".join(selection) if selection else "(full sweep)"
    logger.info("[curator] rebuilding wiki: {}", label)
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, ENTRYPOINT, "wiki", *selection,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out_bytes, _ = await asyncio.wait_for(
            proc.communicate(), timeout=REBUILD_TIMEOUT_SECS,
        )
    except TimeoutError:
        proc.kill()
        logger.warning("[curator] rebuild timed out after {}s", REBUILD_TIMEOUT_SECS)
        return False
    except Exception as e:
        logger.warning("[curator] rebuild failed to start: {}", e)
        return False

    output = out_bytes.decode(errors="replace").strip()
    if proc.returncode != 0:
        tail = "\n".join(output.splitlines()[-5:])
        logger.warning("[curator] wiki generation rc={}: {}", proc.returncode, tail)
        return False
    published = sum(1 for ln in output.splitlines() if ln.startswith("published "))
    logger.info("[curator] wiki refreshed — {} page(s) published", published)
    return True


# ── Main loop ────────────────────────────────────────────────────────────

async def main() -> None:
    rebuild_enabled = os.environ.get("WIKI_AUTO_REBUILD", "true").lower() == "true"

    vault_dir = Path(os.environ.get("MEMORY_VAULT_DIR", "/data/memory/vault"))
    brain_dir = Path(os.environ.get("BRAIN_REPO_DIR", "/data/memory/brain"))
    shared_bucket = os.environ.get("SHARED_BUCKET", "family")
    state_dir = Path(os.environ.get("CURATOR_STATE_DIR", "/data/memory/curator"))
    sha_file = state_dir / "last-rebuilt-sha"
    mirror_file = state_dir / "last-mirrored-sha"
    nightly_file = state_dir / "last-nightly-date"

    while not (vault_dir / ".git").exists():
        logger.info("[curator] waiting for vault at {}", vault_dir)
        await asyncio.sleep(POLL_SECS)
    while not (brain_dir / ".git").exists():
        logger.info("[curator] waiting for brain projection at {}", brain_dir)
        await asyncio.sleep(POLL_SECS)

    vault = Vault(vault_dir)
    brain = Brain(brain_dir, vault_dir)
    debounce = Debounce(QUIET_SECS)

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
    # operator's to backfill (`stack memory wiki`), not something to
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

    async def mirror_incremental(since: str, until: str) -> bool:
        """Replay memory's `since..until` source diff onto brain, then
        commit + push. One commit. Returns True on success (incl. a
        no-op range)."""
        ops = diff_to_fileops(await vault.name_status(since, until))
        await brain.apply(ops)
        return await brain.commit_push(f"{BRAIN_COMMIT_PREFIX} sync source")

    async def mirror_reconcile() -> bool:
        """Full source reconcile: make brain's source files exactly match
        memory's, then commit + push. The first-boot populate and the
        nightly self-heal both use this."""
        ops = reconcile_fileops(
            await vault.tracked_files(), await brain.tracked_files(),
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

    while True:
        await asyncio.sleep(POLL_SECS)
        # The curator owns the pull — the wiki container only watches
        # files. Runs even with rebuilds disabled: the wiki must not
        # rot just because the automation is off.
        await vault.sync()
        head = await vault.head()
        if not head:
            continue

        # ── Source mirror (memory -> brain) ───────────────────────────
        # Data-plane like the pull: brain must always carry memory's
        # current source so Quartz renders fresh captures, even with LLM
        # rebuilds disabled. Cheap (file copies, one commit) so it runs
        # every cycle the source moved, undebounced.
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

        # ── Nightly full sweep ────────────────────────────────────────
        # Runs regardless of pending changes — it covers everything an
        # incremental pass would, so it also clears the debounce. The
        # date is recorded even on failure: one attempt per night, the
        # incremental path and the manual CLI cover the gap. A full
        # source reconcile precedes the regen so brain self-heals any
        # drift the incremental mirror missed.
        if nightly_due(NIGHTLY, _read(nightly_file), time.localtime()):
            _write(nightly_file, time.strftime("%Y-%m-%d", time.localtime()))
            if await mirror_reconcile():
                mirror_sha = _write(mirror_file, head)
            if await rebuild([]):
                # Generation wrote pages into brain's working tree; commit
                # and push them (one commit alongside the reconcile).
                await brain.commit_push(f"{BRAIN_COMMIT_PREFIX} nightly rebuild")
                last = _write(sha_file, head)
                debounce.reset()
            continue

        # ── Incremental: persons + home, debounced ────────────────────
        if head == last:
            debounce.reset()
            continue

        if only_own_commits(await vault.subjects(last, head)):
            last = _write(sha_file, head)
            debounce.reset()
            continue

        if not debounce.observe(head, time.monotonic()):
            continue

        paths = await vault.changed_paths(last, head)
        selection = member_selection(
            paths, lambda p: vault.frontmatter_at(head, p),
            shared_bucket=shared_bucket,
        )
        if not selection:
            last = _write(sha_file, head)
            debounce.reset()
            continue

        if await rebuild(selection):
            # Commit + push the regenerated pages the wiki CLI wrote into
            # brain's working tree.
            await brain.commit_push(f"{BRAIN_COMMIT_PREFIX} incremental rebuild")
            last = _write(sha_file, head)
            debounce.reset()
        else:
            debounce.retry_later(time.monotonic())


if __name__ == "__main__":
    asyncio.run(main())
