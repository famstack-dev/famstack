"""Curator — keeps the family wiki fresh without anyone running a command.

Runs as the `stack-memory-curator` sidecar (own slim image, see
`curator.Dockerfile`) and watches the vault working copy that the wiki
container keeps in sync with Forgejo (a `git pull` loop, 20s
interval). Two jobs:

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
`stack memory wiki` always works as the manual override. If the wiki
container is down nothing pulls the vault, so the curator simply
sees no new commits until it returns.

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

    def frontmatter_at(self, rev: str, path: str) -> dict:
        """Frontmatter of `path` at `rev` ({} for deleted/binary files)."""
        out = self._run("show", f"{rev}:{path}")
        return _parse_frontmatter(out) if out else {}


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
    if os.environ.get("WIKI_AUTO_REBUILD", "true").lower() != "true":
        logger.info("[curator] wiki_auto_rebuild is off — idling")
        await asyncio.Event().wait()

    vault_dir = Path(os.environ.get("MEMORY_VAULT_DIR", "/data/memory/vault"))
    shared_bucket = os.environ.get("SHARED_BUCKET", "family")
    state_dir = Path(os.environ.get("CURATOR_STATE_DIR", "/data/memory/curator"))
    sha_file = state_dir / "last-rebuilt-sha"
    nightly_file = state_dir / "last-nightly-date"

    while not (vault_dir / ".git").exists():
        logger.info("[curator] waiting for vault at {}", vault_dir)
        await asyncio.sleep(POLL_SECS)

    vault = Vault(vault_dir)
    debounce = Debounce(QUIET_SECS)

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

    logger.info(
        "[curator] watching {} (poll {}s, quiet {}s, nightly {}) from {}",
        vault_dir, POLL_SECS, QUIET_SECS, NIGHTLY or "off", last[:10] or "(empty repo)",
    )

    while True:
        await asyncio.sleep(POLL_SECS)
        head = await vault.head()
        if not head:
            continue
        if not last:
            last = _write(sha_file, head)
            continue

        # ── Nightly full sweep ────────────────────────────────────────
        # Runs regardless of pending changes — it covers everything an
        # incremental pass would, so it also clears the debounce. The
        # date is recorded even on failure: one attempt per night, the
        # incremental path and the manual CLI cover the gap.
        if nightly_due(NIGHTLY, _read(nightly_file), time.localtime()):
            _write(nightly_file, time.strftime("%Y-%m-%d", time.localtime()))
            if await rebuild([]):
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
            last = _write(sha_file, head)
            debounce.reset()
        else:
            debounce.retry_later(time.monotonic())


if __name__ == "__main__":
    asyncio.run(main())
