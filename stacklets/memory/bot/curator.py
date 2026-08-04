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
   WIKI_NIGHTLY local time. This is the self-healing catch-all:
   cross-references go stale invisibly during the day and are corrected
   while the GPU is idle. It also means the incremental person/topic
   mapping only has to be *helpful*, never load-bearing. Worst case
   for a missed page is "stale until tonight".

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
from memory.lib import (  # noqa: E402
    MIRROR_SHA_NAME,
    MIRROR_TRIGGER_NAME,
    PRESERVE_LOCAL,
    RESET_LOCAL,
    SyncResult,
    _parse_frontmatter,
    authenticated_remote,
    brain_remote_url,
    is_auth_failure,
    reconcile_with_remote,
    run_git,
    vault_remote_url,
)

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

# Legacy generated page filenames. Live files are now classified by the
# `generated: true` frontmatter marker. Delete/rename diffs cannot read
# frontmatter from a gone file, so these names remain authoritative for
# removal operations.
_GENERATED_NAMES = {"about.md", "index.md"}

# Both ends of the trigger protocol live in `memory.lib`: every writer
# (the write seam on its way out, `stack memory sync` by hand) asks
# there, and this loop is what answers. The curator stays brain's only
# writer; callers only ask and wait (one-writer invariant, ADR-011).
TRIGGER_NAME = MIRROR_TRIGGER_NAME

# The curator's own git remote. `origin` belongs to the host plane
# (hooks and the host CLI reach Forgejo on a localhost/LAN port); this
# container reaches it on the stack network. One working copy serves
# two network planes, so each plane gets its own remote instead of
# fighting over origin's URL.
CURATOR_REMOTE = "curator"


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


def _has_generated_marker(frontmatter: dict | None) -> bool:
    """True when frontmatter declares a page as generated."""
    if not frontmatter:
        return False
    value = frontmatter.get("generated")
    if value is True:
        return True
    return isinstance(value, str) and value.strip().lower() == "true"


def _is_generated_name(path: str) -> bool:
    parts = [p for p in path.split("/") if p]
    return bool(parts) and parts[-1] in _GENERATED_NAMES


def member_selection(
    paths: list[str],
    fm_reader,
    *,
    shared_bucket: str,
) -> list[str]:
    """Map changed vault paths to an incremental `wiki` selection argv.

    Member and topic pages are where freshness is felt. Cross-refs
    heal on the nightly sweep.
    Collects the bucket owner for personal captures plus every name in
    `persons:` frontmatter (`fm_reader(path) -> dict`, fed by
    `git show` so the mapping matches the commit), and adds topic
    folders touched by captures. Returns e.g. `["--home", "--member",
    "Homer Simpson", "--topic", "camping"]`, or `[]` when nothing
    relevant changed (only generated pages or skipped dirs). Home is
    included whenever anything relevant changed at all.
    """
    members: list[str] = []
    topics: list[str] = []
    relevant = False

    for path in paths:
        parts = [p for p in path.split("/") if p]
        if not parts or parts[0] in _SKIP_TOP:
            continue
        fm = fm_reader(path) if path.endswith(".md") else {}
        if _has_generated_marker(fm):
            continue
        relevant = True
        if parts[0] != shared_bucket and len(parts) > 1 and parts[0] not in members:
            members.append(parts[0])
        topic = _topic_slug_from_capture_path(parts, shared_bucket=shared_bucket)
        if topic and topic not in topics:
            topics.append(topic)
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
    for member in members:
        argv += ["--member", member]
    for topic in topics:
        argv += ["--topic", topic]
    return argv


def _topic_slug_from_capture_path(parts: list[str], *, shared_bucket: str) -> str:
    """Return the topic slug for a capture path, or "" for non-topic paths."""
    capture_dirs = {"notes", "bookmarks", "documents"}
    if len(parts) >= 3 and parts[0] == shared_bucket and parts[2] in capture_dirs:
        return parts[1]
    if len(parts) >= 4 and parts[0] != shared_bucket and parts[2] in capture_dirs:
        return parts[1]
    return ""


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
# `generated: true` frontmatter, and `.git` internals are always skipped.

# Git diff status letters the mirror acts on. `A`dded and `M`odified
# copy the file in; `D`eleted removes it; `R`enamed moves it (old path
# removed, new path copied). `C`opied is treated like an add of the new
# path. `T` (type change) is treated as a modify.
_COPY_STATUSES = {"A", "M", "C", "T"}


def is_source_path(path: str, frontmatter: dict | None = None) -> bool:
    """True when a vault path is source the mirror should replay to brain.

    Excludes git internals and pages that declare `generated: true`.
    Filename is not source of truth for live files: a hand-written
    `about.md` with no generated marker is source and must be mirrored
    into brain. The generation step sees that source page and skips
    composing over it.
    """
    parts = [p for p in path.split("/") if p]
    if not parts or parts[0] == ".git":
        return False
    if _has_generated_marker(frontmatter):
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
    `generated: true` marker. Delete and old-rename paths use the legacy
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

    Generated pages in brain carry `generated: true`, so they never
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


def consume_trigger(state_dir: Path) -> bool:
    """Consume a pending mirror-now trigger. True when one was there.

    Deletion is the consumption: a concurrent second consumer loses the
    unlink race and correctly reports no trigger.
    """
    try:
        (state_dir / TRIGGER_NAME).unlink()
        return True
    except OSError:
        return False


async def sleep_until_tick(secs: float, state_dir: Path,
                           slice_secs: float = 1.0) -> bool:
    """Sleep up to `secs`, waking early when a mirror-now trigger lands.

    Returns True when the wake-up was triggered rather than timed. The
    sleep is sliced so `stack memory sync` sees a tick within about a
    second instead of a full poll interval.
    """
    deadline = time.monotonic() + secs
    while time.monotonic() < deadline:
        if consume_trigger(state_dir):
            return True
        await asyncio.sleep(min(slice_secs, max(deadline - time.monotonic(), 0.0)))
    return consume_trigger(state_dir)


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
    "pushed":              ("info", "{label}: pushed local commits to Forgejo"),
    "rebased":             ("warning", "{label}: local commits replayed onto the remote and pushed"),
    "rebased_unpushed":    ("warning", "{label}: local commits replayed onto the remote but the push failed ({detail})"),
    "push_failed":         ("warning", "{label}: local commits could not be pushed ({detail})"),
    "reset_to_remote":     ("warning", "{label}: realigned to the remote (projection, rebuilt from source)"),
    "preserved_and_reset": ("warning", "{label}: unrelated history, local history kept on branch {detail} and the working copy reset to the remote"),
    "unreachable":         ("warning", "{label}: Forgejo unreachable, sync is not making progress ({detail})"),
    "auth_failed":         ("error", "{label}: Forgejo rejected the credentials, sync is stuck until they are refreshed ({detail})"),
    "failed":              ("error", "{label}: git refused the recovery ({detail})"),
}


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
    """The curator's git view of the vault working copy.

    Reads only, with one exception: `sync` may replay local commits
    onto the remote to get out of a divergence. It never authors vault
    content — memory's writers stay the archivist, the CLI, and humans
    (ADR-011). The bind-mounted repo belongs to the host user, so git's
    dubious-ownership check is satisfied via a private
    GIT_CONFIG_GLOBAL rather than touching any shared config.
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

    async def sync(self) -> SyncResult:
        """Reconcile the working copy with Forgejo, recovering if wedged.

        The vault is the database (ADR-011), so its policy is
        `PRESERVE_LOCAL`: a local-only commit may be a todo tick, which
        exists nowhere else, and is replayed onto the remote rather
        than dropped. Only a history with no merge base at all — the
        remote repo re-created underneath us — makes the working copy
        step aside, and then onto a branch that keeps every commit.

        One fetch per tick replaces the old `ls-remote` probe. It costs
        the same ref exchange when there is nothing new, and having the
        remote's objects already in hand is what lets a recovery decide
        and act without a second round trip.

        Never fatal. Forgejo briefly unreachable means the tick is
        skipped and everything keeps serving what is on disk. It is no
        longer *silent*, though: anything short of progress is logged
        where an operator sees it.
        """
        return await asyncio.to_thread(self._sync)

    def _sync(self) -> SyncResult:
        result = self._reconcile()
        if result.status == "auth_failed" and self._refresh_remote():
            # The rejected credential may simply be the one we cached at
            # boot. Re-derive from the current environment and try once
            # more, then stop: a second failure is a real one.
            result = self._reconcile()
        return report_sync("vault", result)

    def _reconcile(self) -> SyncResult:
        return reconcile_with_remote(
            self.path, CURATOR_REMOTE, recovery=PRESERVE_LOCAL, env=self._env,
        )

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
        return _parse_frontmatter(text)

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
        code, _, _ = self._run("diff", "--cached", "--quiet")
        if code != 0:
            rc, _, _ = self._run(
                "-c", f"user.name={_BRAIN_AUTHOR_NAME}",
                "-c", f"user.email={_BRAIN_AUTHOR_EMAIL}",
                "commit", "-m", message,
            )
            if rc != 0:
                return False
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

        Brain is machine-owned and regenerable (ADR-011), so its policy
        is `RESET_LOCAL`: if the remote holds commits this copy does not,
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
        return reconcile_with_remote(
            self.path, CURATOR_REMOTE, recovery=RESET_LOCAL, env=self._env,
        )


# ── Rebuild ──────────────────────────────────────────────────────────────

async def rebuild(selection: list[str]) -> bool:
    """One wiki generation pass via the CLI entrypoint — the same code
    path `stack memory wiki` execs, in a subprocess so a wedged LLM
    call dies with the child instead of inside this loop."""
    label = " ".join(selection) if selection else "(full sweep)"
    logger.info("[curator] rebuilding wiki: {}", label)
    # Starting the child and waiting on it are separate failure modes, and
    # only the second one has a child to kill. Keeping them in one block
    # left `proc.kill()` reachable on a path where `proc` was never bound.
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, ENTRYPOINT, "wiki", *selection,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except Exception as e:
        logger.warning("[curator] rebuild failed to start: {}", e)
        return False

    try:
        out_bytes, _ = await asyncio.wait_for(
            proc.communicate(), timeout=REBUILD_TIMEOUT_SECS,
        )
    except TimeoutError:
        proc.kill()
        logger.warning("[curator] rebuild timed out after {}s", REBUILD_TIMEOUT_SECS)
        return False
    except Exception as e:
        logger.warning("[curator] rebuild failed: {}", e)
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
    mirror_file = state_dir / MIRROR_SHA_NAME
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
        ops = diff_to_fileops(
            await vault.name_status(since, until),
            lambda path: vault.frontmatter_at(until, path),
        )
        await brain.apply(ops)
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

    while True:
        # A mirror-now trigger (from `stack memory sync`) forces this
        # cycle to rebuild the wiki immediately, bypassing the debounce.
        # The debounce exists to batch a burst of filings into one
        # rebuild; an explicit trigger means "I want it current now",
        # which is what the test rig needs for a fast feedback loop.
        forced = await sleep_until_tick(POLL_SECS, state_dir)
        if forced:
            logger.info("[curator] mirror-now trigger received")
        # The curator owns the pull — the wiki container only watches
        # files. Runs even with rebuilds disabled: the wiki must not
        # rot just because the automation is off.
        await vault.sync()
        head = await vault.head()
        if not head:
            continue

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

        if not forced and not debounce.observe(head, time.monotonic()):
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
            # Say the shape of the failure, not just that there was one. A
            # per-cycle line at the same interval reads as a heartbeat; a
            # widening gap with a count on it reads as an outage.
            logger.warning(
                "[curator] rebuild failed {}x in a row; next attempt in {}m "
                "(source is still mirrored, only generated pages are stale)",
                debounce.failures, round(debounce.window() / 60),
            )


if __name__ == "__main__":
    asyncio.run(main())
