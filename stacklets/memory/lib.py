"""In-process API for the memory stacklet.

Memory is a host stacklet — no container, no port. It keeps the
family's curated knowledge in two places:

  - **Forgejo** (`family/memory`) — the canonical source of truth and
    the commit log. Web edits via Forgejo and Obsidian clones land
    here.
  - **A local working copy** under `<data_dir>/memory/vault/` —
    a `git clone` of the Forgejo repo. Every reader (the archivist
    classifier, `stack memory ...` CLI, future deriver bot, Phase 5
    wiki-rebuild) walks this directory. obsidiantools and Dataview
    expect a filesystem vault, so this is what gets us into the
    Obsidian ecosystem.

Sync model — pull-heavy, write-deliberate:

  - The install hook clones the vault after pushing seeds.
  - `on_start_ready` pulls on every `stack up memory`.
  - `stack memory pull` is the explicit refresh.
  - Writes (Phase 2 facts CLI, Phase 5 wiki-rebuild) edit the working
    copy then `git add && commit && push`.

The seed under `stacklets/memory/seeds/` stays in the repo as the
canonical starting state. It is read directly only when no vault
exists yet (a fresh checkout before the install hook runs, or when
Forgejo was unreachable on install).
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Callable, Iterable, List, Optional

# Vault-layout conventions live in the framework so both stacklets share
# one source. Re-exported here for memory's own callers (and back-compat
# with `from lib import correspondents_dir`).
from stack.vault import DEFAULT_SHARED_BUCKET, correspondents_dir

# Frontmatter parsing uses the shared stdlib-only module from stack.frontmatter
# so both host CLI and containers can parse identically.
from stack.frontmatter import FrontmatterError, parse as _parse_frontmatter_new

# `python-frontmatter` is intentionally not imported at module load.
# The CLI runs on a stdlib-only `python3` (see `./stack`), so the
# install hook would crash at import time if we pulled in a third-
# party package up here. The one function that needs it imports it
# lazily — the archivist and the `stack memory correspondents` CLI
# command both run with the bot's runtime deps available.

from stack.cards.commits import (MEMORY_BOT, MEMORY_BOT_EMAIL, NoServerName,  # noqa: F401
                                commit_author, commit_subject)
from stack.cards import HAND_WRITTEN, CardIdError, card_id_from, is_view, new_card_id, with_card_id
from stack import media
from stack.forgejo import ForgejoClient
from stack.ontology import Ontology
from stack.tags import with_declared_topics
from stack.vault import TOPIC_CARD

if TYPE_CHECKING:
    # Type-only: `stack.ai.client` pulls in the OpenAI SDK, which the
    # host interpreter does not have. See the query-rewrite section.
    from stack.ai.client import LLM
    from .memory_api import MemoryAPI


STACKLET_DIR = Path(__file__).resolve().parent
SEEDS_DIR = STACKLET_DIR / "seeds"
SEED_ONTOLOGY_PATH = SEEDS_DIR / "ontology.toml"

# The org and repo names the `memory` repo lives under in Forgejo. The
# `family` org is shared with the documents repo (created by archivist-
# bot's GitMirror), so memory just slots in alongside.
REPO_OWNER = "family"
REPO_NAME = "memory"
REPO_DESCRIPTION = (
    "The family's curated knowledge — ontology, facts, and wiki. "
    "Hand-edit any file here; the commit log is the learning history."
)
ORG_DESCRIPTION = (
    "Your family's Forgejo — documents, knowledge, and shared repos."
)

# The projection repo. `family/brain` is the derived mirror of memory's
# source plus every generated wiki page; Quartz renders it. It is fully
# disposable — the curator rebuilds it from memory at any time — so it
# carries only a minimal seed (.gitignore + a README); the rest of its
# content arrives through the mirror. Memory stays source-only and is
# never written by generation.
BRAIN_REPO_NAME = "brain"
BRAIN_REPO_DESCRIPTION = (
    "The family wiki, projected from memory. Generated and disposable — "
    "Quartz renders this; edit the source in the memory repo instead."
)

# Brain's seed. Kept inline rather than in a seeds dir because it is two
# files and the projection fills in everything else. The .gitignore
# matches memory's so an Obsidian clone of either repo behaves the same.
BRAIN_SEED_GITIGNORE = (
    "# Projection repo — rendered by Quartz, rebuilt from memory.\n"
    "# Obsidian per-user state must not hit git (see memory's .gitignore).\n"
    ".obsidian/\n"
    ".trash/\n"
    ".DS_Store\n"
)
BRAIN_SEED_README = (
    "# Family brain (projection)\n\n"
    "This repo is **generated**. It mirrors the source vault in "
    "`family/memory` and adds the wiki pages the curator composes. "
    "Quartz renders it.\n\n"
    "Do not hand-edit here — edits are overwritten on the next rebuild. "
    "Edit the source in `family/memory`; changes flow back through the "
    "mirror.\n"
)
BRAIN_SEED_COMMIT_MESSAGE = "seed: initial brain projection scaffold"
BRAIN_MIGRATION_TOKEN_NAME = "memory-brain-migration"
ONTOLOGY_PATH_IN_REPO = "ontology.toml"

BOT_USERNAME, BOT_EMAIL = MEMORY_BOT, MEMORY_BOT_EMAIL


def memory_forgejo(url: str, **credentials) -> ForgejoClient:
    """A Forgejo client for what the memory stacklet writes to Forgejo
    itself, the brain's scaffold, whatever account its credentials sign in
    as: every commit names the memory bot as its committer (ADR-015). The
    family memory is written through the curator, never through Forgejo.

    The memory bot has no Forgejo account. Its token is the admin's
    (`install_memory_to_forgejo_admin`), so the committer is named rather
    than asked for:

        seed_brain(memory_forgejo(code_url, token=token))
    """
    return ForgejoClient(url=url, committer=(BOT_USERNAME, BOT_EMAIL), **credentials)
# Name of the admin-owned token the install writes the brain's scaffold
# and clones it with. Issuing under the same name again replaces it.
WRITE_TOKEN_NAME = "memory-install"
TOKEN_SCOPES = [
    "write:repository", "read:repository",
    "read:user", "write:organization",
]
# The title of a new memory's first commit, the seed the curator gives it.
SEED_TITLE = "initial memory from famstack"


# ─── Seed loader ─────────────────────────────────────────────────────────
#
# The seed is the canonical *starting* state of an instance's memory.
# It mirrors `stacklets/docs/taxonomy.toml` with ids + synonyms +
# keywords + type cross-refs. Read directly only when no vault is
# available yet.

def load_seed_ontology() -> Ontology:
    """Read the shipped seed ontology and return a parsed `Ontology`."""
    return Ontology.load(SEED_ONTOLOGY_PATH)


# ─── Vault path + auth ───────────────────────────────────────────────────

def vault_path_for(data_dir: Path) -> Path:
    """Resolve the vault (memory source) path under a stack's data dir."""
    return Path(data_dir) / "memory" / "vault"


def brain_path_for(data_dir: Path) -> Path:
    """Resolve the brain projection working-copy path under a data dir.

    The curator pushes generated pages here and Quartz renders it; it
    sits beside the memory vault clone under the same data dir.
    """
    return Path(data_dir) / "memory" / "brain"


def authenticated_remote(remote_url: str, username: str, token: str) -> str:
    """Inject HTTPS basic auth credentials into a Forgejo remote URL.

    `http://stack-code:3000/family/memory.git` becomes
    `http://memory-bot:<token>@stack-code:3000/family/memory.git`.
    Tokens are URL-safe characters by Forgejo's spec; no escaping needed.
    """
    if "://" not in remote_url:
        return remote_url
    scheme, rest = remote_url.split("://", 1)
    return f"{scheme}://{username}:{token}@{rest}"


def vault_remote_url(code_url: str) -> str:
    """The git remote URL for the memory repo on a given code stacklet."""
    return f"{code_url.rstrip('/')}/{REPO_OWNER}/{REPO_NAME}.git"


def brain_remote_url(code_url: str) -> str:
    """The git remote URL for the brain projection repo."""
    return f"{code_url.rstrip('/')}/{REPO_OWNER}/{BRAIN_REPO_NAME}.git"


# The host's own address for its own published ports. `127.0.0.1` and
# not `localhost`, so the URL cannot be re-pointed by /etc/hosts or
# answered with an IPv6 address the port is not bound to.
LOOPBACK_HOST = "127.0.0.1"

_IPV4_LITERAL = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def host_code_url(code_url: str) -> str:
    """Rewrite a Forgejo URL so the *host* reaches it over loopback.

    In port mode `{code_url}` renders the LAN address
    (`http://192.168.188.42:42040`), because it is also the URL a phone
    on the couch clicks. Baked into a git remote or an API base on the
    machine itself, that address is a time bomb: the next DHCP lease
    moves it, and every host-side git call then hangs until its timeout
    and fails. The host is local to itself, so it talks to the
    published port on loopback, which no lease can move.

    Only a literal IPv4 address is rewritten. A hostname is left as it
    is: in domain mode it is stable DNS, an operator-configured
    `[core].host` name follows the machine, and on the container plane
    `stack-code` is the service name that must survive this call
    untouched.
    """
    scheme, sep, rest = code_url.partition("://")
    if not sep:
        return code_url
    authority, slash, path = rest.partition("/")
    host, colon, port = authority.rpartition(":")
    if not colon:                       # authority is a bare host
        host, port = authority, ""
    if not _IPV4_LITERAL.match(host):
        return code_url
    rebuilt = f"{LOOPBACK_HOST}:{port}" if port else LOOPBACK_HOST
    return f"{scheme}://{rebuilt}{slash}{path}"


# ─── Vault sync ──────────────────────────────────────────────────────────
#
# Clone-if-missing and best-effort pulls. Both shell out to `git` —
# every Mac and Linux has it, and the framework already uses subprocess
# liberally for Docker. No GitPython dep, no libgit2.

def _git(argv: List[str], *, timeout: int, env: Optional[dict] = None) -> tuple[int, str, str]:
    """Run one git command and never raise. Returns (rc, stdout, stderr).

    A remote that stopped answering is the failure mode this whole
    module exists to survive, and it arrives as a hang, not as an error
    code: git sits on the socket until the timeout expires. Letting
    `TimeoutExpired` escape is how a single unreachable remote took a
    lifecycle hook down mid-run and left the resources after it
    uncreated. So a timeout (and a missing git binary) comes back as a
    failing command like any other.
    """
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"git timed out after {timeout}s: {' '.join(argv[1:3])}"
    except (FileNotFoundError, OSError) as e:
        return 127, "", f"git unavailable: {e}"
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def run_git(repo_path: Path, *args: str, timeout: int = 60,
            env: Optional[dict] = None) -> tuple[int, str, str]:
    """Run a git command inside `repo_path`. Never raises — see `_git`."""
    return _git(["git", "-C", str(repo_path), *args], timeout=timeout, env=env)


def ensure_vault_cloned(
    vault_path: Path,
    remote_url: str,
    *,
    timeout: int = 60,
) -> bool:
    """Clone the memory repo into `vault_path` if not already present.

    `remote_url` is expected to carry credentials in the URL when the
    repo is private — see `authenticated_remote`. Returns True when a
    clone happened or a working copy was already there.
    """
    vault_path = Path(vault_path)
    if (vault_path / ".git").exists():
        return True

    vault_path.parent.mkdir(parents=True, exist_ok=True)
    rc, _, _ = _git(
        ["git", "clone", remote_url, str(vault_path)], timeout=timeout,
    )
    return rc == 0


def ensure_projection_cloned(
    brain_path: Path,
    remote_url: str,
    *,
    timeout: int = 60,
) -> bool:
    """Clone the brain projection into `brain_path`, adopting what is there.

    On a first install `brain_path` is never empty by the time the brain
    repo exists: `on_start` creates the wiki's media mountpoint inside it
    before the containers start, and the repo is created after they are
    up. `git clone` refuses a non-empty directory, so the directory is
    turned into a working copy in place instead. Files the repo tracks
    are replaced by the repo's version; untracked ones, the mountpoint
    among them, stay. That is only acceptable because brain is a
    projection that can be rebuilt at any time. The vault is the family's
    source and keeps the strict `ensure_vault_cloned`.

    Returns True when a working copy is there afterwards. On failure the
    directory is left as it was found.
    """
    brain_path = Path(brain_path)
    if (brain_path / ".git").exists():
        return True
    if not brain_path.is_dir() or not any(brain_path.iterdir()):
        return ensure_vault_cloned(brain_path, remote_url, timeout=timeout)

    staging = brain_path.with_name(brain_path.name + ".clone")
    shutil.rmtree(staging, ignore_errors=True)
    rc, _, _ = _git(["git", "clone", "--no-checkout", remote_url, str(staging)], timeout=timeout)
    if rc == 0:
        (staging / ".git").rename(brain_path / ".git")
        rc, _, _ = run_git(brain_path, "reset", "--hard", "--quiet", timeout=timeout)
        if rc != 0:
            shutil.rmtree(brain_path / ".git", ignore_errors=True)
    shutil.rmtree(staging, ignore_errors=True)
    return rc == 0


def point_remote_at(repo_path: Path, remote_url: str, *,
                    name: str = "origin", timeout: int = 10) -> bool:
    """Re-derive a working copy's remote URL, adding the remote if absent.

    A remote URL rots in two independent ways: the host part (a LAN IP
    baked in at clone time, moved by the next DHCP lease) and the
    embedded credential (a Forgejo token that expired). Both are held
    in git's config, where nothing ever refreshes them. So every start
    re-points the remote at the URL the current config says it should
    be, and a moved host no longer survives a `stack up`.

    The credential is whatever the caller passes: the brain's start
    issues a fresh token for it (`ensure_brain_projection_admin`).
    """
    repo_path = Path(repo_path)
    if not (repo_path / ".git").exists():
        return False
    rc, _, _ = run_git(repo_path, "remote", "set-url", name, remote_url, timeout=timeout)
    if rc != 0:
        rc, _, _ = run_git(repo_path, "remote", "add", name, remote_url, timeout=timeout)
    return rc == 0


# ─── Divergence recovery for the projection ──────────────────────────────
#
# `git pull --ff-only` has exactly one outcome when a working copy and
# its remote disagree: it fails, and it keeps failing, every cycle,
# forever. That is not a sync loop, it is a wedge with a retry timer on
# it. This section is the way out for `family/brain`, a projection:
# machine-owned, regenerable from memory at any time, so it may take the
# remote as given and re-project on top. `family/memory` is the database
# and never takes this way: its clone is written and synced by the card
# store (`memory.cards`), which never resets it.

# Text git and Forgejo use when the credentials, not the network, are
# the problem. An expired token reads differently from an unreachable
# host, and the two need different responses: refresh and retry versus
# wait and retry.
_AUTH_FAILURE_MARKERS = (
    "authentication failed",
    "invalid username or password",
    "credentials are incorrect",     # Forgejo's wording for an expired token
    "could not read username",
    "returned error: 401",
    "returned error: 403",
)


def is_auth_failure(stderr: str) -> bool:
    """True when git's error text says the credentials were rejected."""
    text = (stderr or "").lower()
    return any(marker in text for marker in _AUTH_FAILURE_MARKERS)


@dataclass
class SyncResult:
    """Outcome of one reconcile, terse enough to log verbatim.

    `status` is one of:

        "up_to_date"      — nothing to do.
        "fast_forwarded"  — the everyday pull.
        "ahead"           — local has commits the remote lacks and the
                            caller pushes them itself.
        "reset_to_remote" — realigned to the remote, to be rebuilt.
        "unreachable"     — the remote did not answer.
        "auth_failed"     — the remote rejected the credentials.
        "failed"          — git refused an operation; `detail` carries
                            its complaint.
    """

    status: str
    detail: str = ""


def reconcile_with_remote(
    repo_path: Path,
    remote: str,
    *,
    branch: str = "main",
    env: Optional[dict] = None,
    timeout: int = 60,
) -> SyncResult:
    """Bring a projection's working copy back into agreement with `remote`.

    Heads equal, or local behind: a fast-forward. Local ahead: commits
    that have not travelled yet, which the caller pushes. Diverged, or no
    merge base at all because the remote was re-created: the local side is
    dropped and the working copy reset to the remote, since it can be
    regenerated.

    Never raises.
    """
    def git(*args: str) -> tuple[int, str, str]:
        return run_git(repo_path, *args, timeout=timeout, env=env)

    rc, _, err = git("fetch", "--quiet", remote, branch)
    if rc != 0:
        return SyncResult("auth_failed" if is_auth_failure(err) else "unreachable", err)

    rc, remote_head, err = git("rev-parse", "FETCH_HEAD")
    if rc != 0 or not remote_head:
        return SyncResult("unreachable", err)
    rc, local_head, err = git("rev-parse", "HEAD")
    if rc != 0 or not local_head:
        return SyncResult("failed", err)

    if local_head == remote_head:
        return SyncResult("up_to_date")
    if git("merge-base", "--is-ancestor", local_head, remote_head)[0] == 0:
        rc, _, err = git("merge", "--ff-only", remote_head)
        return SyncResult("fast_forwarded" if rc == 0 else "failed", err)
    if git("merge-base", "--is-ancestor", remote_head, local_head)[0] == 0:
        return SyncResult("ahead")     # the caller's own push delivers it
    rc, _, err = git("reset", "--hard", remote_head)
    return SyncResult("reset_to_remote" if rc == 0 else "failed", err)


# ─── Write propagation (Forgejo -> the trees people read) ────────────────
#
# A commit in Forgejo is the truth, and it is also invisible. The agent
# reads the brain projection through a read-only mount; the family reads
# Quartz's render of that same tree. Both are fed by the curator, which
# pulls memory and mirrors it into brain on its poll tick. Left to the
# tick alone a write is unseeable by its own author for most of a poll
# interval -- which is how an agent comes to tick an item off, read the
# page back to check itself, and find its own work missing.
#
# The curator stays brain's only writer (ADR-011), so a writer here asks
# instead of mirroring: drop a trigger file the curator's tick loop
# watches, then watch the sha it records until the commit shows up in it.
# Both ends are plain files in the shared state dir, which is the one
# thing a host process and a container process can both reach with no
# transport between them.

# The curator consumes the trigger by deleting it, so its content is
# only ever a breadcrumb for whoever is debugging the loop.
MIRROR_TRIGGER_NAME = "mirror-now"
# The file the curator writes after each successful projection.
MIRROR_SHA_NAME = "last-mirrored-sha"
# The memory commit the wiki pages were last regenerated up to.
REBUILT_SHA_NAME = "last-rebuilt-sha"
# When regenerating the touched pages last failed, and when the curator
# tries again. A waiting command reads it to stop instead of timing out.
REBUILD_FAILED_NAME = "last-rebuild-failed"
# Asks the curator for its nightly sweep now, whatever the time.
NIGHTLY_TRIGGER_NAME = "nightly-now"
# When the last nightly sweep finished, and whether its rebuild succeeded.
NIGHTLY_RUN_NAME = "last-nightly-run"
# What the curator is doing: one timestamped line per step and per page.
PROGRESS_NAME = "progress"

# How long a write waits for its own change to become visible. Short on
# purpose: the curator slices its sleep by the second and a mirror is
# file copies plus one commit, so this covers the ordinary case -- and
# the extraordinary one must never hold up a commit that already landed.
MIRROR_WAIT_SECS = 5.0
MIRROR_POLL_INTERVAL = 0.25


def curator_state_dir_for(data_dir: Path) -> Path:
    """Where the curator keeps its progress, reachable from both planes."""
    return Path(data_dir) / "memory" / "curator"


def request_mirror(state_dir: Path) -> Path:
    """Drop the trigger file the curator's tick loop watches."""
    state_dir.mkdir(parents=True, exist_ok=True)
    trigger = state_dir / MIRROR_TRIGGER_NAME
    trigger.write_text(str(time.time()), encoding="utf-8")
    return trigger


def mirrored_contains(memory: Path, target: str, mirrored: str) -> bool:
    """True when the curator's recorded sha already includes `target`.

    Checked against the memory clone's history: the curator pulls that
    same working copy before mirroring, so once `target` is projected
    both commits exist there. A sha git does not know yet simply reads
    as "not yet" and the caller keeps waiting.
    """
    if not mirrored:
        return False
    if mirrored == target:
        return True
    rc, _, _ = run_git(
        Path(memory), "merge-base", "--is-ancestor", target, mirrored, timeout=10,
    )
    return rc == 0


def wait_for_mirror(state_dir: Path, memory: Path, target: str, *,
                    timeout: float = MIRROR_WAIT_SECS,
                    interval: float = MIRROR_POLL_INTERVAL,
                    on_poll: Callable[[], None] | None = None) -> Optional[str]:
    """Poll `last-mirrored-sha` until it contains `target`.

    Returns the mirrored sha, or None on timeout. The condition is
    checked once before the clock is consulted, so an already-current
    mirror succeeds even with a zero timeout.
    """
    return _wait_for_sha(Path(state_dir) / MIRROR_SHA_NAME, memory, target, timeout, interval, on_poll)


def wait_for_rebuilt(state_dir: Path, memory: Path, target: str, *,
                     timeout: float, interval: float = MIRROR_POLL_INTERVAL,
                     on_poll: Callable[[], None] | None = None,
                     asked: float | None = None) -> Optional[str]:
    """Poll `last-rebuilt-sha` until the wiki pages are regenerated up to `target`.

    The curator records it after regenerating the pages a change touched,
    and also when a change touched none, so it always moves on. With
    `asked`, a failure the curator records after that moment ends the
    wait early: None, and `failed_rebuild_since` says when it retries.
    """
    failed = (lambda: failed_rebuild_since(state_dir, asked) is not None) if asked else None
    return _wait_for_sha(Path(state_dir) / REBUILT_SHA_NAME, memory, target, timeout,
                         interval, on_poll, stop=failed)


def _wait_for_sha(sha_file: Path, memory: Path, target: str, timeout: float,
                  interval: float, on_poll: Callable[[], None] | None,
                  stop: Callable[[], bool] | None = None) -> Optional[str]:
    deadline = time.monotonic() + timeout
    while True:
        if on_poll:
            on_poll()
        recorded = sha_file.read_text(encoding="utf-8").strip() if sha_file.exists() else ""
        if mirrored_contains(memory, target, recorded):
            return recorded
        if (stop and stop()) or time.monotonic() >= deadline:
            return None
        time.sleep(interval)


def record_failed_rebuild(state_dir: Path, *, retry_in: float) -> None:
    """Note that regenerating the touched pages failed, and the next attempt."""
    state_dir.mkdir(parents=True, exist_ok=True)
    now = time.time()
    (Path(state_dir) / REBUILD_FAILED_NAME).write_text(
        json.dumps({"at": now, "retry_at": now + retry_in}), encoding="utf-8")


def failed_rebuild_since(state_dir: Path, since: float) -> Optional[dict]:
    """The failure the curator recorded at or after `since`, or None."""
    try:
        record = json.loads((Path(state_dir) / REBUILD_FAILED_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if record.get("at", 0) >= since else None


# ── The nightly sweep, on request ────────────────────────────────────────
# Tonight's sweep (diary, full source reconcile, every wiki page) can be
# asked for now. The curator stays the one writer: this drops a trigger,
# wakes its tick, and waits for the run record the sweep leaves behind.

def request_nightly(state_dir: Path) -> float:
    """Ask for the nightly sweep now; the time asked, to wait against."""
    state_dir.mkdir(parents=True, exist_ok=True)
    asked = time.time()
    (state_dir / NIGHTLY_TRIGGER_NAME).write_text(str(asked), encoding="utf-8")
    request_mirror(state_dir)  # wakes the tick within a second
    return asked


def last_nightly_run(state_dir: Path) -> Optional[dict]:
    """The last finished sweep: {"at": epoch seconds, "ok": bool}, or None."""
    record = Path(state_dir) / NIGHTLY_RUN_NAME
    try:
        return json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def wait_for_nightly(state_dir: Path, asked: float, *, timeout: float,
                     interval: float = 2.0,
                     on_poll: Callable[[], None] | None = None) -> Optional[dict]:
    """Wait for a sweep that finished after `asked`; its record, or None."""
    deadline = time.monotonic() + timeout
    while True:
        if on_poll:
            on_poll()
        run = last_nightly_run(state_dir)
        if run and run.get("at", 0) >= asked:
            return run
        if time.monotonic() >= deadline:
            return None
        time.sleep(interval)


# ── What the curator is doing ────────────────────────────────────────────
# A command waiting on the curator would otherwise sit silent for minutes.
# The curator appends one line per step and per page it writes, stamped
# with the time; a waiting command prints the lines stamped after it asked.
# The file starts over with each piece of work, so it stays small.

def report_progress(state_dir: Path, what: str, *, fresh: bool = False) -> None:
    """Record one step of the curator's work; `fresh` starts a new piece of work."""
    with open(Path(state_dir) / PROGRESS_NAME, "w" if fresh else "a", encoding="utf-8") as log:
        log.write(f"{time.time()!r} {what}\n")


def progress_since(state_dir: Path, since: float) -> list[str]:
    """The steps the curator recorded at or after `since`, oldest first."""
    try:
        lines = (Path(state_dir) / PROGRESS_NAME).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    steps = []
    for line in lines:
        stamp, _, what = line.partition(" ")
        try:
            if float(stamp) >= since:
                steps.append(what)
        except ValueError:
            continue
    return steps


# ─── Vault readers ───────────────────────────────────────────────────────

def load_ontology_from_vault(vault_path: Path) -> Optional[Ontology]:
    """Read `ontology.toml` from the vault working copy.

    Returns None when the vault has no ontology file yet (a freshly-
    cloned repo where the seed push hasn't landed, or a hand-curated
    instance that deleted the file).
    """
    ontology_file = Path(vault_path) / ONTOLOGY_PATH_IN_REPO
    if not ontology_file.exists():
        return None
    return Ontology.load(ontology_file)


# ─── Correspondents (shared-bucket entity layer) ─────────────────────────
#
# Correspondents are organizations the family corresponds with (banks,
# insurers, schools, councils, online services). They belong to the
# institutional layer — senders of mail, classifier hints, Paperless
# canonicalisers — and so live under the shared bucket alongside the
# documents they index.
#
# Layout: `<vault>/<shared_bucket>/correspondents/<name>.md`. The
# shared bucket slug is configured in `stack.toml [core] shared_bucket`
# and defaults to "family". Personal correspondents (a future feature
# for entity-specific senders, e.g. Bart's school) would live under
# the matching entity bucket — same shape, different parent.
#
# Living outside `wiki/` keeps hand-curated correspondents safe from
# the wiki-engine's regenerate pass.
#
# Example shape (family/correspondents/duff-insurance.md), a card of type
# correspondent (`schemas/cards/correspondent.json`):
#
#     ---
#     type: correspondent
#     card_id: correspondent-duff-insurance
#     title: Duff Insurance
#     aliases:
#       - "Duff Insurance Ortsverband Springfield"
#     topics: [insurance, vehicle]
#     address: "Hansastraße 19, 80686 München"
#     website: "https://www.duff-insurance.example"
#     ---
#
#     # Duff Insurance
#     [free-form notes]
#
# A file of the shape before cards (`kind: correspondent`, its name in
# `canonical`) is brought to this one by migrations 0004, 0006 and 0009.
#
# `aliases` rolls up everything the classifier has seen as the
# `correspondent_aliases` field on documents from this sender. The
# classifier prompt embeds the (canonical, aliases) pairs so the LLM
# can canonicalize new variants before they hit Paperless.

@dataclass
class Correspondent:
    """A single correspondent wiki entry."""

    canonical: str
    aliases: List[str] = field(default_factory=list)
    topics: List[str] = field(default_factory=list)
    address: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None
    # Path on disk, useful for `stack memory correspondents show`.
    source_path: Optional[Path] = None

    def all_known_names(self) -> List[str]:
        """Canonical + every alias, no duplicates, canonical first."""
        seen = {self.canonical}
        out = [self.canonical]
        for a in self.aliases:
            if a and a not in seen:
                seen.add(a)
                out.append(a)
        return out


def load_correspondents_from_vault(
    vault_path: Path,
    shared_bucket: str = DEFAULT_SHARED_BUCKET,
) -> List[Correspondent]:
    """Walk `<vault>/<shared_bucket>/correspondents/*.md` and return the parsed entries.

    `shared_bucket` defaults to "family" — the conventional name. Pass
    the configured slug when the deployment overrides it via stack.toml.

    A page that says it is something else (`type` or `kind` other than
    `correspondent`) is skipped. The name is the card's `title` (ADR-017),
    else `canonical:` from before cards, else the file stem.
    """
    folder = Path(vault_path) / correspondents_dir(shared_bucket)
    if not folder.exists():
        return []

    result: List[Correspondent] = []
    for md_path in sorted(folder.glob("*.md")):
        try:
            text = md_path.read_text(encoding="utf-8")
        except OSError:
            continue
        meta = _parse_frontmatter(text, md_path)
        if not meta:
            # No frontmatter at all — likely a README or stray note, skip.
            continue
        if any(meta.get(k) and meta.get(k) != "correspondent" for k in ("type", "kind")):
            continue
        canonical = meta.get("title") or meta.get("canonical") or md_path.stem
        if not canonical:
            continue
        result.append(Correspondent(
            canonical=str(canonical),
            # _fm_list, not a comprehension: a bare string iterates into
            # one entry per character, so a hand edit that writes a
            # single value where a list belongs turns "insurance" into
            # eleven topics instead of one.
            aliases=_fm_list(meta, "aliases"),
            topics=_fm_list(meta, "topics"),
            address=meta.get("address"),
            phone=meta.get("phone"),
            email=meta.get("email"),
            website=meta.get("website"),
            source_path=md_path,
        ))
    return result


def correspondents_prompt_section(correspondents: List[Correspondent]) -> str:
    """Render the correspondents block embedded in the classifier prompt.

    Output shape:

        Existing correspondents (canonical; aliases in parens):
          - Duff Insurance (Duff Insurance Ortsverband Springfield, Duff Insurance Versicherung AG)
          - Springfield Mutual
          - Anthropic

    Returns an empty string when no correspondents are known yet —
    the prompt builder falls back to the flat Paperless list in that
    case.
    """
    if not correspondents:
        return ""
    lines = ["Existing correspondents (canonical; aliases in parens):"]
    for c in correspondents:
        if c.aliases:
            lines.append(f"  - {c.canonical} ({', '.join(c.aliases)})")
        else:
            lines.append(f"  - {c.canonical}")
    return "\n".join(lines)


# ─── Persons (entity-bucket entity layer) ────────────────────────────────
#
# Persons are the household members the wiki carries a page for. They
# parallel correspondents in shape: a canonical name plus the synonyms
# the family uses in real documents (formal, nicknames, maiden names).
# Unlike correspondents they live one-per-entity under each member's
# bucket -- the same about.md the home page links to -- so the wiki is
# the single curation surface and there is no parallel registry file
# to keep in sync.
#
# Layout: `<vault>/<slug>/about.md`. The page body is free-form prose
# the family edits; the frontmatter is the structured signal the
# classifier reads.
#
# Example shape (vault/maggie/about.md):
#
#     ---
#     title: Margaret
#     slug: maggie
#     canonical: Margaret
#     synonyms:
#       - Maggie
#       - Margaret Bouvier
#     ---
#
#     # Margaret
#     [free-form notes, links to documents]
#
# `synonyms` is the curated set the classifier needs to recognise so
# "Marge Bouvier" on a marriage certificate resolves to the same
# person as "Marge" on a utility bill.

# Skip set for `load_persons_from_vault`: top-level directories that
# carry an `about.md` but are not a household member. Kept in sync
# with the `stack memory wiki` command's own skip set; both walk the
# same vault structure.
_NON_MEMBER_DIRS = {".git", ".obsidian", "wiki", "private", "templates", "_shared"}


@dataclass
class Person:
    """A single household-member wiki entry."""

    canonical: str
    slug: str
    synonyms: List[str] = field(default_factory=list)
    # Path on disk, useful for `stack memory persons show` and tests.
    source_path: Optional[Path] = None

    def all_known_names(self) -> List[str]:
        """Canonical + every synonym, no duplicates, canonical first."""
        seen = {self.canonical}
        out = [self.canonical]
        for s in self.synonyms:
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        return out


def load_persons_from_vault(
    vault_path: Path,
    shared_bucket: str = DEFAULT_SHARED_BUCKET,
) -> List[Person]:
    """Walk `<vault>/<slug>/about.md` and return the parsed entries.

    Each entity bucket directly under the vault root is treated as a
    potential household member. Buckets that are reserved for shared
    content (the `shared_bucket`, `wiki/`, `.git/`, ...) are skipped
    by name. A bucket without an `about.md` is also skipped -- a
    member can exist on the file system (with captured notes) before
    the wiki overview pass has generated their page.

    Pages tagged with a `kind:` other than `person` are skipped --
    leaves room for non-member entries to coexist under the same
    directory shape without leaking into the classifier prompt.
    """
    vault_path = Path(vault_path)
    if not vault_path.exists():
        return []

    skip = _NON_MEMBER_DIRS | {shared_bucket}
    result: List[Person] = []
    for about in sorted(vault_path.glob("*/about.md")):
        slug = about.parent.name
        if slug in skip or slug.startswith("."):
            continue
        try:
            text = about.read_text(encoding="utf-8")
        except OSError:
            continue
        meta = _parse_frontmatter(text, about)
        if meta.get("kind") and meta.get("kind") != "person":
            continue
        canonical = meta.get("canonical") or meta.get("title") or slug
        if not canonical:
            continue
        result.append(Person(
            canonical=str(canonical),
            slug=str(meta.get("slug") or slug),
            synonyms=_fm_list(meta, "synonyms"),
            source_path=about,
        ))
    return result


def persons_prompt_section(persons: List[Person]) -> str:
    """Render the persons block embedded in the classifier prompt.

    Output shape, mirroring `correspondents_prompt_section`:

        Family members (canonical first name; synonyms in parens):
          - Marge (Marjorie, Margaret, Marge Bouvier, Marge Simpson)
          - Homer
          - Bart (Bartholomew, Bart Simpson)

    Returns an empty string when no curated person pages exist yet --
    the prompt builder falls back to the flat list of first names
    pulled from Paperless Person tags in that case.
    """
    if not persons:
        return ""
    lines = ["Family members (canonical first name; synonyms in parens):"]
    for p in persons:
        if p.synonyms:
            lines.append(f"  - {p.canonical} ({', '.join(p.synonyms)})")
        else:
            lines.append(f"  - {p.canonical}")
    return "\n".join(lines)


def people_prompt_section(people) -> str:
    """The persons block from the person files, in the shape above.

    The canonical name is the person's first name, the name their
    `Person:` tag carries; the full name and every alias the family
    listed follow in parens, so "Margaret" on a birth certificate is
    recognised as Maggie. A person without a chat account is listed like
    any other.
    """
    if not people:
        return ""
    lines = ["Family members (canonical first name; synonyms in parens):"]
    for p in people:
        others = [n for n in p.all_names() if n != p.first_name]
        lines.append(f"  - {p.first_name} ({', '.join(others)})" if others
                     else f"  - {p.first_name}")
    return "\n".join(lines)


def get_ontology(vault_path: Optional[Path] = None) -> Ontology:
    """Return the live ontology from the vault, else the shipped seed, with
    the topics the vault's topic cards declare on top of it.

    Callers pass an explicit vault path so this stays a pure function
    with no env or framework reach. The CLI commands read the path
    from `config["data_dir"]`, the install hook from `ctx.stack.data`,
    and the archivist from `MEMORY_VAULT_DIR` in its container.

    A topic card is the household's own topic (`topic_card_titles`). Every
    writer and reader of tags takes its topics from here, so the topic
    cards are topics for the tag rule, the search, the format check and
    the card schemas alike (`stack.tags.with_declared_topics`), while a
    classifier is still offered the ontology's alone.

    Calling with `None` is the pre-install / test-friendly path:
    seed-only, no filesystem touch beyond the shipped file.
    """
    if vault_path is None:
        return load_seed_ontology()
    ontology = load_ontology_from_vault(vault_path) or load_seed_ontology()
    return with_declared_topics(ontology, topic_card_titles(Path(vault_path)))


# A topic's card, in the topic's folder (ADR-017 §10). A memory from before
# topic cards had a file of their own keeps it as `about.md` there.
TOPIC_CARDS = (TOPIC_CARD, "about.md")


def topic_declared_at(path: str) -> str:
    """The topic id a topic card at vault path `path` declares, its folder; "" elsewhere.

    >>> topic_declared_at("family/itchy-scratchy-land/topic.md")
    'itchy-scratchy-land'
    >>> topic_declared_at("family/itchy-scratchy-land/notes/2026/10/tent-3a338e.md")
    ''
    """
    parts = path.split("/")
    return parts[1] if len(parts) == 3 and parts[2] in TOPIC_CARDS else ""


def topic_card_titles(vault_path: Path) -> dict[str, str]:
    """Each topic the vault's topic cards declare: its id, the card's folder, to its title.

    A topic card (`<bucket>/<folder>/topic.md`, `type: topic`) is the
    household's own topic on top of the ontology. Buckets with a folder of
    one name declare one topic id, titled by the first card in path order.
    A file that does not read as a topic card declares nothing.
    """
    titles: dict[str, str] = {}
    for name in TOPIC_CARDS:
        for file in sorted(Path(vault_path).glob(f"*/*/{name}")):
            try:
                fields = _parse_frontmatter_new(file.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, FrontmatterError):
                continue
            if str(fields.get("type") or "").strip() != "topic":
                continue
            topic = topic_declared_at(file.relative_to(vault_path).as_posix())
            titles.setdefault(topic, str(fields.get("title") or "").strip())
    return titles


# ─── Install (HTTP API) ──────────────────────────────────────────────────
#
# Day-zero seeding still goes through Forgejo's HTTP contents API: the
# vault doesn't exist yet, so we have nowhere local to commit against.
# Subsequent writes (facts CLI, wiki-rebuild) work through the vault
# and `git push`.

def ensure_memory_repo(client: ForgejoClient) -> dict:
    """Create the `family` org and `memory` repo if they don't exist."""
    org_existed = client.get_org(REPO_OWNER) is not None
    if not org_existed:
        client.create_org(REPO_OWNER, description=ORG_DESCRIPTION)

    repo_existed = client.get_repo(REPO_OWNER, REPO_NAME) is not None
    if not repo_existed:
        client.create_repo(
            REPO_OWNER, REPO_NAME,
            description=REPO_DESCRIPTION,
            private=True, owner_is_org=True,
        )

    return {
        "created_org": not org_existed,
        "created_repo": not repo_existed,
    }


def ensure_brain_repo(client: ForgejoClient) -> dict:
    """Create the `family/brain` projection repo if it doesn't exist.

    The `family` org is created by `ensure_memory_repo` (memory installs
    first), so this only ever has to make the repo. `create_repo` is
    idempotent on a 409/already-exists, so re-running the hook is safe.
    """
    repo_existed = client.get_repo(REPO_OWNER, BRAIN_REPO_NAME) is not None
    if not repo_existed:
        client.create_repo(
            REPO_OWNER, BRAIN_REPO_NAME,
            description=BRAIN_REPO_DESCRIPTION,
            private=True, owner_is_org=True,
        )
    return {"created_repo": not repo_existed}


def seed_brain(
    client: ForgejoClient,
    *,
    author_name: Optional[str] = None,
    author_email: Optional[str] = None,
) -> dict:
    """Push brain's minimal seed (.gitignore + README) if missing.

    Only the two scaffold files — everything else in brain arrives
    through the curator's mirror. Files already present are left alone,
    so this is idempotent and never clobbers a projection in progress.
    """
    created: list[str] = []
    skipped: list[str] = []
    seed = {
        ".gitignore": BRAIN_SEED_GITIGNORE,
        "README.md": BRAIN_SEED_README,
    }
    for repo_path, content in seed.items():
        existing = client.get_file(REPO_OWNER, BRAIN_REPO_NAME, repo_path)
        if existing is not None:
            skipped.append(repo_path)
            continue
        client.put_file(
            REPO_OWNER, BRAIN_REPO_NAME, repo_path,
            content=content,
            message=BRAIN_SEED_COMMIT_MESSAGE,
            author_name=author_name,
            author_email=author_email,
        )
        created.append(repo_path)
    return {"created": created, "skipped": skipped}


def declares_generated(content: str) -> bool:
    """True when a page's frontmatter declares the whole file a view.

    The splice marker only brackets a *region*, and a hand-written page may
    reserve one for the wiki to fill in later — the seeded root `index.md`
    does exactly that. Treating the marker as proof of a generated page
    deleted that welcome page on every start, which left a fresh install
    serving 404 at the wiki root until the first nightly sweep.

    The view's frontmatter is the whole-page declaration
    (`stack.cards.is_view`, which still knows the older `generated: true`),
    and the compiler writes it on every page it publishes.
    """
    return is_view(_parse_frontmatter(content))


# Where Forgejo tells the curator of a push to the memory repo: the curator's
# cards API (`memory/cards_api.py`, `PORT`), on the stack network.
CURATOR_PUSH_URL = "http://stack-memory-curator:8090/push"


def add_admins_to_the_family(client: ForgejoClient, usernames: Iterable[str]) -> None:
    """Make the household's admins members of the family org's Owners team,
    so the memory and its brain show among their own repositories in
    Forgejo. Idempotent: a member stays a member."""
    names = list(usernames)
    if not names:
        return
    team = client.get_owners_team_id(REPO_OWNER)
    for name in names:
        client.add_team_member(team, name)


def ensure_push_webhook(client: ForgejoClient) -> bool:
    """Have the memory repo notify the curator of every push; True when added now.

    Every writer pushes to Forgejo (the archivist, the CLI, a person's
    clone), so a webhook there is the one place that sees them all. The
    curator still polls: a notice that does not arrive is picked up there.
    """
    for hook in client.list_hooks(REPO_OWNER, REPO_NAME):
        if (hook.get("config") or {}).get("url") == CURATOR_PUSH_URL:
            return False
    client.create_hook(REPO_OWNER, REPO_NAME, CURATOR_PUSH_URL)
    return True


def ensure_brain_projection_admin(
    *,
    code_url: str,
    admin_user: str,
    admin_password: str,
    brain_path: Optional[Path] = None,
) -> dict:
    """Ensure upgraded instances have the brain repo and local checkout.

    `on_install_success` only runs once. Instances installed before the
    brain projection existed already have `memory.setup-done`, so normal
    `stack up memory` skips the install hook forever. This helper is the
    idempotent migration path used from `on_start_ready`: create
    `family/brain` if missing, seed its scaffold if missing, and clone it
    locally when the working copy is absent.
    """
    # Host plane: talk to Forgejo on loopback so neither the API calls
    # nor the clone URL carry a LAN IP that the next lease invalidates.
    code_url = host_code_url(code_url)
    admin = ForgejoClient(
        url=code_url,
        admin_user=admin_user, admin_password=admin_password,
    )
    if not admin.ping():
        return {"skipped_reason": "forgejo unreachable"}

    brain_state = ensure_brain_repo(admin)
    admin_token = admin.issue_token(
        admin_user, admin_password, BRAIN_MIGRATION_TOKEN_NAME, TOKEN_SCOPES,
    )
    admin_token_client = memory_forgejo(code_url, token=admin_token)
    brain_seeds = seed_brain(admin_token_client)

    cloned_brain = False
    if brain_path is not None:
        had_brain = (brain_path / ".git").exists()
        brain_remote = authenticated_remote(
            brain_remote_url(code_url),
            admin_user, admin_token,
        )
        cloned_brain = ensure_projection_cloned(brain_path, brain_remote) and not had_brain
        if had_brain:
            # Freshly issued token, current host URL — the clone made on
            # an older lease with an older token gets both refreshed.
            point_remote_at(brain_path, brain_remote)

    return {
        "created_brain_repo": brain_state["created_repo"],
        "brain_seeds": brain_seeds,
        "cloned_brain": cloned_brain,
    }


def seed_files(seed_dir: Optional[Path] = None, *,
               shared_bucket: str = DEFAULT_SHARED_BUCKET) -> dict[str, str]:
    """What a new memory starts with: each shipped seed file's path in the
    memory, and its text.

    Every file under `seed_dir` (the shipped `seeds/` by default), at the
    path it has there. A leading `_shared/` segment is the household's
    bucket: `seeds/_shared/correspondents/README.md` is
    `family/correspondents/README.md`, or under whatever the bucket is named.
    """
    src = (seed_dir or SEEDS_DIR).resolve()
    return {_resolve_seed_repo_path(path.relative_to(src).as_posix(), shared_bucket):
            path.read_text(encoding="utf-8")
            for path in sorted(p for p in src.rglob("*") if p.is_file())}


def people_folder(shared_bucket: str = DEFAULT_SHARED_BUCKET) -> str:
    """Where the family's person files are: `<shared_bucket>/people`."""
    from stack.people import PEOPLE_DIR
    return f"{shared_bucket}/{PEOPLE_DIR}"


def missing_people(users: list[dict], existing: Iterable[str], *,
                   shared_bucket: str = DEFAULT_SHARED_BUCKET) -> dict[str, str]:
    """The person file each account in `users` still needs: path to text.

    The family's people are one file each under `<shared_bucket>/people/`
    (`stack.people`). Every account holder gets one, marked with their
    account, which makes them a member. `existing` is the paths there
    now: an account whose file is there gets none, so a family edit (an
    alias, a corrected name) survives every restart, and a new account
    gets its file on the next `stack up memory`.

    That makes this the whole upgrade path for an existing install: a
    restart of the memory stacklet writes the files, and every reader
    switches from its old guess to them as soon as they appear.
    """
    from stack.people import people_from_accounts, person_id, person_path, render_person

    folder = people_folder(shared_bucket)
    have = {person_id(PurePosixPath(path).stem) for path in existing
            if path.endswith(".md") and PurePosixPath(path).parent.as_posix() == folder}
    return {person_path(p.id, shared_bucket): render_person(p)
            for p in people_from_accounts(users) if p.id not in have}


# Seed paths beginning with `_shared/` get retargeted to the
# configured bucket slug. Everything else lands at the vault root
# verbatim — keeps `ontology.toml`, `facts.toml`, `README.md` at the
# top regardless of bucket configuration.
_SHARED_SEED_PREFIX = "_shared/"


def _resolve_seed_repo_path(rel_path: str, shared_bucket: str) -> str:
    if rel_path.startswith(_SHARED_SEED_PREFIX):
        return f"{shared_bucket}/{rel_path[len(_SHARED_SEED_PREFIX):]}"
    return rel_path




# ─── Memory vault search ────────────────────────────────────────────────
#
# Full-text query over the curated vault. Pure-Python regex walk over
# `*.md` files, post-filtered by YAML frontmatter (`persons`, `tags`).
# Two callers today: `stack memory search` (CLI argparse + formatter
# wrapper) and the archivist bot (formats results into Matrix). The
# CLI is the agent-facing surface; the lib function is the in-process
# call for code that already imports `memory.lib`.
#
# Keeping the clone current is the curator's: it pulls on Forgejo's push
# notice and every cycle, and a command asks it to pull now before it
# reads (`cli/_curator.pull_now`). `search_memory` is the search alone.


def _parse_frontmatter(text: str, path: Path | str | None = None) -> dict:
    """Parse vault entry frontmatter using the shared stdlib-only module.

    Delegates to stack.frontmatter.parse, which handles the strict
    §2 subset per the vault format spec. A page whose frontmatter does
    not parse is read without it, so search still finds it by its text,
    and none of its fields are guessed. Pass the page's `path` to have
    that reported on stderr.
    """
    try:
        return _parse_frontmatter_new(text)
    except FrontmatterError as e:
        if path is not None:
            print(f"[memory] {path}: frontmatter does not parse ({e}), "
                  "read without it", file=sys.stderr)
        return {}


def _fm_list(fm: dict, key: str) -> List[str]:
    """Read a frontmatter field as a list of strings, regardless of shape."""
    v = fm.get(key)
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str):
        return [v]
    return []


def _norm_tag(value: str) -> str:
    """Collapse whitespace and lower-case for tag comparison.

    Writers and queriers disagree on spacing: `'Person: Homer'`,
    `Person:Homer`, and `PERSON :HOMER` all normalize to the same
    token so the filter doesn't care which spelling was used.
    """
    return re.sub(r"\s+", "", value).lower()


def body_only(text: str) -> str:
    """Return the text after the closing `---` of the frontmatter block.

    Public because the natural-language query layer also strips
    frontmatter when feeding full file contents into the synthesis
    prompt -- the YAML header is metadata the model already sees as
    structured `kind`/`date`/`persons` fields, so duplicating it in
    the body context only crowds the context window.

    Why this exists: the search regex must not match against YAML
    field names. A query for `date` would otherwise hit every memory
    file via its `date:` frontmatter line; same for `tags`,
    `correspondent`, `persons`, etc. Structural-vs-content matters
    here -- the family asks "when did Bart…", not "tell me every
    file with a `date` field". Returns the original text unchanged
    when no frontmatter is present, so files that pre-date the
    classifier still surface.
    """
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    if end < 0:
        return text
    return text[end + len("\n---\n"):]


def extract_summary_callout(text: str) -> str:
    """Pull the `> [!summary]` callout out of a memory-vault file.

    The archivist writes every classified document with an Obsidian-style
    callout that contains the LLM's prose summary, the bulleted facts,
    and any action items. That block is the right size for feeding
    into a downstream LLM when answering a question -- big enough to
    carry the actual content, small enough that fitting N hits into
    one context is cheap.

    Returns the callout body with the leading `> ` blockquote prefix
    stripped from each line, or "" when no callout is present (files
    older than the classifier, or non-archivist-written notes).
    """
    body = body_only(text)
    lines = body.splitlines()
    captured: List[str] = []
    in_callout = False
    for line in lines:
        if not in_callout:
            if line.strip().startswith("> [!summary]"):
                in_callout = True
            continue
        # Inside the callout: every line starts with `>`. A line that
        # doesn't ends the block. Blank `>` lines stay as paragraph
        # breaks inside the summary -- they're meaningful (separate
        # the prose from the Facts heading from the Action items).
        if not line.startswith(">"):
            break
        captured.append(line[1:].lstrip(" "))
    return "\n".join(captured).strip()


def strip_diacritics(text: str) -> str:
    """Drop combining marks, so "Käse" and "Kase" are the same word.

    The family writes German; somebody searching from a phone keyboard
    or in a hurry types the bare vowel. Byte-literal matching answers
    that with nothing at all, which reads like the vault has no such
    page rather than like the query was spelled differently.

    Case is deliberately left alone. The query is a regex, and
    lower-casing one rewrites its operators: `\\W` becomes `\\w` and
    stops meaning the opposite of what it said. Callers match
    case-insensitively anyway.

    Decomposing first is what makes both spellings of an umlaut agree:
    a precomposed "ü" and a "u" followed by a combining diaeresis are
    the same letter to a reader and have to be the same here.

    What this is not is transliteration. "Kaese" stays a different word
    from "Käse"; folding those together needs a German-specific table,
    not a Unicode normalisation form.
    """
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


_HEADING = re.compile(r"^#{1,6}\s+(.*)$")


def _excerpt(text: str, patterns: List["re.Pattern[str]"], max_len: int = 200,
             fold_diacritics: bool = True) -> str:
    """First body line that matches a pattern, with its section heading.

    The result is `<nearest heading> › <line>`, or the line alone when
    no heading comes before it, or the heading alone when the heading
    itself matched.

    `patterns` are the query's alternatives, rarest first, compiled
    against folded text. A page found through "Bart|Seepferdchen" shows
    the line that says "Seepferdchen": the rare word is the one that tells the
    reader why this page came back. The query itself is a regex, so
    matching it as a substring (the old way) found nothing for any
    query with a `|` in it.

    Body starts after the *closing* `---` of the frontmatter block --
    otherwise hits on `title:` or `persons:` lines would surface as
    excerpts, which is noisy and misleading.

    Folds the same way the match did, so a page found through "Kase"
    still shows the line saying "Käse". A hit whose excerpt came back
    empty is a hit the reader cannot judge.
    """
    # Each line with the nearest Markdown heading above it. A diary
    # month is one page and the day is its heading, so a line without
    # its heading does not say when it happened.
    sections: List[tuple] = []
    heading = ""
    for raw in body_only(text).splitlines():
        line = raw.strip()
        if not line:
            continue
        if m := _HEADING.match(line):
            heading = m.group(1).strip()
            sections.append((heading, ""))
        else:
            sections.append((heading, line))

    for pattern in patterns:
        for heading, line in sections:
            candidate = line or heading
            if pattern.search(strip_diacritics(candidate) if fold_diacritics else candidate):
                if len(candidate) > max_len:
                    candidate = candidate[:max_len] + "…"
                return f"{heading} › {candidate}" if line and heading else candidate
    return ""


def split_alternatives(query: str) -> List[str]:
    """The top-level alternatives of a regex, e.g. `a|b(c|d)` -> `a`, `b(c|d)`.

    Splits on `|` outside groups and character classes, and never on an
    escaped one. A query without a top-level `|` is one alternative.
    """
    parts: List[str] = []
    current: List[str] = []
    depth, in_class, escaped = 0, False, False
    for ch in query:
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        elif in_class:
            in_class = ch != "]"
        elif ch == "[":
            in_class = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == "|" and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    parts.append("".join(current))
    return [part for part in parts if part.strip()]


def _idf(doc_count: int, doc_freq: int) -> float:
    """BM25's inverse document frequency: rare words weigh more."""
    return math.log(1 + (doc_count - doc_freq + 0.5) / (doc_freq + 0.5))


def search_memory(
    query: str,
    vault: Path,
    persons: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    scopes: Optional[List[str]] = None,
    limit: int = 20,
    fold_diacritics: bool = True,
    search_frontmatter: bool = True,
) -> List[dict]:
    """Walk the vault, return result dicts, most relevant first.

    `query` is a Python regex matched case-insensitively against the
    *body* of each file -- frontmatter is stripped before matching so
    generic field names (`date:`, `tags:`, `persons:`, ...) don't
    pull in every file in the vault. `persons` and `tags` still
    narrow against the structured frontmatter: a doc passes when,
    for every supplied axis, at least one of its frontmatter values
    matches at least one requested value. Persons compare
    case-insensitively; tags as the rule writes them
    (`stack.tags.tag_for`: a topic's label in any language is its id),
    else through `_norm_tag` (whitespace-and-case normalized).

    `scopes` is a list of allowed path prefixes (entity-rooted, e.g.
    `["family/", "marge/"]`). A doc passes when its vault-relative
    path starts with any prefix. `None` means no scope filter (all
    docs allowed); an empty list means no docs allowed -- the
    archivist passes a closed scope set for unknown senders so they
    can't read personal notes by accident.

    Returns dicts with keys `path`, `rel`, `title`, `date`,
    `persons`, `tags`, `excerpt`, `matched_terms`, and `fields` (the
    frontmatter). Sorted by relevance,
    then by frontmatter `date` descending; files without a date sort to
    the end.

    Relevance: each top-level alternative of the query (`Bart|Seepferdchen` has
    two) scores its inverse document frequency on every page it
    matches, summed. A word that is on every page about a child adds
    almost nothing; a word on one page decides the order. Before this,
    results were sorted by date alone, so the newest pages naming the
    child filled the limit and the one page with the rare word never
    came back. A query with one alternative scores every hit the same
    and keeps the date order.

    `fold_diacritics` strips combining marks from both the query and
    the text before matching, so "Kase" reaches "Käse" and the reverse.
    `search_frontmatter` matches the title, tag and person *values*
    alongside the body, so a page titled "Elternabend" is found by
    searching for Elternabend, and one tagged `medical` by searching for
    the topic's label in any language (`stack.tags.tag_words`). Both are on by default; passing False
    restores the older behaviour, which is what the retrieval lab
    measures against.

    On a missing vault directory or invalid regex, returns `[]`
    rather than raising -- callers decide how to surface the
    failure.
    """
    if not vault.exists():
        return []

    folded_query = strip_diacritics(query) if fold_diacritics else query
    try:
        pattern = re.compile(folded_query, re.IGNORECASE)
    except re.error:
        return []
    try:
        term_patterns = [re.compile(term, re.IGNORECASE)
                         for term in split_alternatives(folded_query)]
    except re.error:
        # An alternative that is not a regex on its own, e.g. a split
        # the splitter got wrong. Rank the whole query as one term.
        term_patterns = []
    term_patterns = term_patterns or [pattern]

    from stack.tags import tag_for, tag_words

    ontology = get_ontology(vault)

    def tag_key(tag: str) -> str:
        return tag_for(tag, ontology) or _norm_tag(tag)

    persons = persons or []
    tags = tags or []
    want_persons = {p.lower() for p in persons}
    want_tags = {tag_key(t) for t in tags}

    # Normalize scopes: ensure each prefix ends with "/" so "marge"
    # doesn't accidentally match "margery/...". `None` keeps the
    # historic open behavior; `[]` denies everything.
    if scopes is None:
        scope_prefixes: Optional[List[str]] = None
    else:
        scope_prefixes = [s if s.endswith("/") else f"{s}/" for s in scopes]

    results: List[dict] = []
    # Corpus statistics for the ranking: pages in scope, and how many
    # of them contain each alternative. Counted before the person and
    # tag filters, because rarity is a property of the vault.
    doc_count = 0
    doc_freq = [0] * len(term_patterns)
    for md_path in vault.rglob("*.md"):
        if not md_path.is_file():
            continue
        try:
            rel = str(md_path.relative_to(vault))
        except ValueError:
            rel = str(md_path)
        if scope_prefixes is not None and not any(
            rel.startswith(p) for p in scope_prefixes
        ):
            continue
        try:
            text = md_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        doc_count += 1
        # Match against body only -- frontmatter field names (`date:`,
        # `tags:`, `persons:`, ...) would otherwise trivially match
        # generic keywords and drown real hits.
        body = body_only(text)
        fm = _parse_frontmatter(text, rel)
        doc_persons = _fm_list(fm, "persons")
        doc_tags = _fm_list(fm, "tags")

        # The values, never the keys. Stripping frontmatter kept a
        # query for "date" from hitting every page through its `date:`
        # line, and threw away the title and tags in the process --
        # which are the most descriptive text a page has. Joining the
        # values back in restores them without letting the field names
        # back through.
        #
        # Persons stay out. A name says who a page concerns, not what
        # it says, so matching it as prose makes any question
        # mentioning Lisa match every page Lisa is on. Measured: it
        # took one query from four hits to twenty and pushed the
        # answer from rank two to rank nine. `--person` is the right
        # way to ask that question and it already exists.
        haystack = body
        if search_frontmatter:
            haystack = "\n".join((
                str(fm.get("title") or ""),
                " ".join(w for t in doc_tags for w in tag_words(t, ontology)),
                body,
            ))
        if fold_diacritics:
            haystack = strip_diacritics(haystack)
        if not pattern.search(haystack):
            continue
        matched = [i for i, term in enumerate(term_patterns)
                   if term.search(haystack)]
        for i in matched:
            doc_freq[i] += 1

        if persons and not any(p.lower() in want_persons for p in doc_persons):
            continue
        if tags:
            doc_norm = {tag_key(t) for t in doc_tags}
            if not (doc_norm & want_tags):
                continue

        results.append({
            "path": md_path,
            "rel": rel,
            "title": fm.get("title") or md_path.stem,
            "date": fm.get("date") or "",
            "persons": doc_persons,
            "tags": doc_tags,
            # Filled in after ranking, for the returned hits only.
            "excerpt": "",
            "_text": text,
            "_matched": matched,
            # The `> [!summary]` callout, stripped of blockquote
            # prefixes. Drives the synthesis step: feeding summaries
            # to the LLM is cheaper than feeding bodies and usually
            # enough to answer the question.
            "summary": extract_summary_callout(text),
            # Paperless source id for the document this memory file
            # mirrors. Lets a downstream deduper recognise that a
            # `Memory` hit and a `Paperless` hit are the same doc and
            # collapse them in the synthesis evidence list. Empty
            # when the file isn't a Paperless mirror (e.g. capture
            # notes, hand-written wiki entries).
            "paperless_id": fm.get("paperless_id") or "",
            # The Matrix event this file was captured from. Assigned
            # once and never rewritten, which is what makes it the only
            # safe key for a link that outlives the file: `rel` carries
            # the bucket, the topic slug and the title slug, and all
            # three change under ordinary use (a re-scope, a topic
            # rename, a corrected title). Empty for anything not
            # captured from chat.
            "capture_id": fm.get("capture_id") or "",
            # A diary card's id, the same kind of key for the same
            # reason: a correction can move the card to another day.
            "entry_id": fm.get("entry_id") or "",
            # The card's own id (ADR-016), which any card carries and its
            # link (`/go/<card_id>`) uses before the two above.
            "card_id": card_id_from(fm),
            # The frontmatter as written, for a reader that shows the hit
            # as its card (`stack.card_render`).
            "fields": fm,
        })

    idf = [_idf(doc_count, df) for df in doc_freq]
    for r in results:
        r["_score"] = round(sum(idf[i] for i in r["_matched"]), 6)
    results.sort(
        key=lambda r: (r["_score"], str(r.get("date") or ""), r["rel"]),
        reverse=True,
    )
    results = results[:limit]

    terms = split_alternatives(query) if len(term_patterns) > 1 else [query]
    for r in results:
        rarest_first = sorted(r.pop("_matched"), key=lambda i: -idf[i])
        r["matched_terms"] = [terms[i] for i in rarest_first
                              if i < len(terms)]
        r["excerpt"] = _excerpt(r.pop("_text"),
                                [term_patterns[i] for i in rarest_first],
                                fold_diacritics=fold_diacritics)
        del r["_score"]
    return results


# ─── Natural-language query rewrite ──────────────────────────────────────
#
# `search_memory` takes a Python regex. A family question is not one:
# "What do we still need to buy for the camping trip?" asks for those
# exact words, adjacent, which no file contains. Anyone holding a
# sentence has to turn it into keywords first, and that step lives here
# rather than in whichever bot happens to ask. Memory owns the vault,
# so memory owns what a query means against it. The archivist had this
# hop and the agent did not, which is why the same question answered in
# chat and returned nothing from the CLI.
#
# The rewrite needs a model, and memory neither owns one nor opens one:
# `rewrite_query` takes the caller's `stack.ai.client.LLM`. The
# archivist already built one, and the memory CLI commands that need a
# model already docker-exec into the same bot-runner container that
# holds it (see `cli/_common.py`). Nothing new gets wired up.
#
# That is also why the third-party imports sit inside the function.
# `stack memory search` runs on the host's stdlib-only python and
# imports this module, exactly like the frontmatter loader above.


def build_rewrite_prompt(
    question: str, ontology_section: str, language: str = "en",
) -> str:
    """Recall-mode prompt: question in, JSON list of search keywords out.

    The ontology block is what makes the keywords hit: it names the
    topics and forms this family actually files under, so the model
    expands and translates into the vault's vocabulary instead of
    guessing at a generic one.

    It also has to be held at arm's length, which is what the closing
    instruction does. Given the list and nothing else, a model answers
    with the list: "what do we still need to buy for the camping
    trip" came back as Travel, Shopping, Receipt, all real topic
    names, none of them words any camping note contains, and the
    search returned a Kwik-E-Mart receipt. `search_memory` matches
    document bodies with frontmatter stripped, so a category that
    only ever appears as a tag can never match. The subject of the
    question comes first; the topics are there for spelling and
    language.
    """
    return f"""You extract search keywords from a question, so a regex walker can look up family documents.

The family classifies their documents under these topics and forms:

{ontology_section}

Language hint: {language}.
- If the question is in German, prefer German keywords.
- If it is in English, prefer English keywords.
- For an ambiguous or generic question, include the most likely topic name plus one or two synonyms in the document language.

Question: {question}

Reply with a JSON object: {{"keywords": ["word1", "word2", "word3"]}}.
2 to 4 keywords. Start with the concrete subject of the question:
the thing, place, activity or person it is about, written the way a
document about it would write it. The topic list above is context for
vocabulary and language, not a menu to answer from; a category name is
only a keyword if a document would actually contain that word. No
phrases, no quotes, no prose around the JSON. Output ONLY the JSON
object."""


def parse_rewrite_response(raw: str) -> List[str]:
    """Pull a keyword list out of the rewrite model's response.

    Accepts the requested object form `{"keywords": [...]}` and the
    bare-array fallback that some smaller models produce when they
    forget the wrapper. Empty list on any parse failure -- the caller
    treats empty as "no rewrite, search the question verbatim." Caps
    at 6 entries so a chatty model can't blow up the alternation
    regex; trims whitespace and drops empties so a stray `""` doesn't
    poison the join.
    """
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []

    if isinstance(data, dict):
        v = data.get("keywords")
        candidates = v if isinstance(v, list) else []
    elif isinstance(data, list):
        candidates = data
    else:
        candidates = []

    cleaned: List[str] = []
    for x in candidates:
        if isinstance(x, (str, int, float)):
            s = str(x).strip()
            if s:
                cleaned.append(s)
        if len(cleaned) >= 6:
            break
    return cleaned


async def rewrite_query(
    question: str,
    *,
    llm: "LLM",
    ontology_section: str = "",
    language: str = "en",
) -> List[str]:
    """Extract search keywords from a natural-language question.

    The model reads the family's topic and doctype ontology and
    produces 2-4 keywords that would literally appear in a matching
    document. Translation and synonym expansion happen here, so the
    regex walker downstream stays dumb.

    Best-effort: any transport failure or parse error returns an empty
    list, which callers treat as "no rewrite, search the question
    verbatim." Recall is a quality-of-life feature, never a gate.
    Synonym selection is the model's job and we don't second-guess it,
    but we do strip empties and cap the list length.

    Callers get the keywords rather than a finished query because they
    render differently: `keywords_to_regex` for the vault walker, an
    ` OR ` join for Paperless, and a "Searched for: ..." line in chat
    so a family can see when a bad rewrite hid results.
    """
    from loguru import logger
    from stack.ai.client import (
        LLMModelNotFoundError,
        LLMTimeoutError,
        LLMUnavailableError,
    )

    prompt = build_rewrite_prompt(question, ontology_section, language)
    try:
        # Greedy, like the wiki generator and for the same reason:
        # sampling makes the same question search for different words
        # run to run. Picking the words a document already contains is
        # a lookup, not a creative act, and a family comparing two
        # searches should not be comparing two dice rolls.
        raw = await llm.complete(
            "recall", prompt, json_mode=True, temperature=0.0,
        )
    except (LLMUnavailableError, LLMModelNotFoundError, LLMTimeoutError) as e:
        logger.warning("[recall] LLM unavailable for rewrite: {}", e)
        return []
    keywords = parse_rewrite_response(raw)
    if not keywords:
        # Surface the raw payload so an empty keyword list is
        # debuggable: an off-shape JSON response is a prompt or
        # model issue, not a transport one, and we can't fix it
        # blind.
        logger.warning(
            "[recall] rewrite parse produced no keywords; raw={!r}",
            (raw or "")[:200],
        )
    return keywords


def keywords_to_regex(keywords: List[str]) -> str:
    """Render keywords as the alternation regex `search_memory` reads.

    `re.escape` per keyword is mandatory, not decoration: a model that
    answers "C++" or "Lisa's" or "(foo)" would otherwise ship a pattern
    that fails to compile, and `search_memory` returns `[]` on a bad
    regex, so the search would go quiet rather than loud.

    An empty keyword list renders as the empty pattern, which matches
    every line. Callers that got no keywords back fall back to the
    literal query instead of calling this.
    """
    return "|".join(re.escape(k) for k in keywords)


def install_memory_to_forgejo_admin(
    *,
    code_url: str,
    admin_user: str,
    admin_password: str,
    brain_path: Optional[Path] = None,
) -> dict:
    """Run the install pipeline using admin credentials directly.

    No bot user is created. The repositories are set up through the admin
    account: `family/memory` (the source vault), created empty, and
    `family/brain` (the projection Quartz renders), with a .gitignore +
    README scaffold that fills in from the mirror. The memory is the
    curator's from then on: it clones it and gives it its seed as its
    first commit, when the install asks it to sync.

    Idempotent across every step. When `brain_path` is supplied, the
    pipeline also clones the brain locally.

    Returns a dict describing what changed. On `forgejo unreachable`,
    returns `{\"skipped_reason\": \"...\"}` and makes no further calls.
    """
    admin = ForgejoClient(
        url=code_url,
        admin_user=admin_user, admin_password=admin_password,
    )
    if not admin.ping():
        return {"skipped_reason": "forgejo unreachable"}

    repo_state = ensure_memory_repo(admin)
    brain_state = ensure_brain_repo(admin)

    # Use admin token for file writes (admin has write:repository scope).
    admin_token = admin.issue_token(
        admin_user, admin_password, WRITE_TOKEN_NAME, TOKEN_SCOPES,
    )
    admin_token_client = memory_forgejo(code_url, token=admin_token)
    brain_seeds = seed_brain(admin_token_client)

    cloned_brain = False
    if brain_path is not None:
        brain_remote = authenticated_remote(
            brain_remote_url(code_url),
            admin_user, admin_token,
        )
        cloned_brain = ensure_projection_cloned(brain_path, brain_remote)

    return {
        "created_org": repo_state["created_org"],
        "created_repo": repo_state["created_repo"],
        "created_brain_repo": brain_state["created_repo"],
        "brain_seeds": brain_seeds,
        "cloned_brain": cloned_brain,
    }


# ─── Topic todo lists move to lists/ ─────────────────────────────────────
#
# Until lists became one kind of file, each topic kept its todos in
# `<bucket>/<topic>/todos.md`: a title heading and checkbox lines, with a
# due date written after a dash. They now live with every other list in
# `<bucket>/lists/<topic>.md` (`stack.lists`). The move runs on every
# `stack up memory` and finds nothing once it is done.

# The old due date followed the item after a long dash (U+2014).
_OLD_DUE = re.compile(r"^(.*?)\s+\u2014\s+(\d{4}-\d{2}-\d{2})$")
_TITLE_LINE = re.compile(r"^#\s+(.+?)\s*$")


def todos_as_list(text: str, topic: str, *, shared_bucket: str = DEFAULT_SHARED_BUCKET,
                  language: str = "") -> str:
    """A topic's old `todos.md` as a list file, every tick and line kept.

    The title heading becomes the list's title, the due dates become
    `📅` fields, and every other line stays as the family wrote it.
    """
    from dataclasses import replace

    from stack.lists import format_item, list_path, new_list, parse_item, topic_list_description

    path = list_path(topic, bucket=shared_bucket)
    body = text.split("\n---\n", 1)[1] if text.startswith("---\n") and "\n---\n" in text else text
    title, titled = topic.replace("-", " ").title(), False
    lines: list[str] = []
    for line in body.splitlines():
        if not (titled or lines) and (heading := _TITLE_LINE.match(line)):
            title, titled = heading[1], True
            continue
        item = parse_item(line, path=path)
        if item and not item.due and (old := _OLD_DUE.match(item.text)):
            line = format_item(replace(item, text=old[1], due=old[2]), path=path)
        if line.strip() or lines:
            lines.append(line)
    head = new_list(title, path=path, topics=[topic],
                    description=topic_list_description(title, language))
    rest = "\n".join(lines).strip("\n")
    return f"{head}\n{rest}\n" if rest else head


def move_todos_to_lists(memory: MemoryAPI, vault: Path, *,
                        shared_bucket: str = DEFAULT_SHARED_BUCKET,
                        language: str = "",
                        trailer: str = "") -> tuple[list[str], str | None]:
    """Move every topic's `todos.md` in `vault` to `lists/<topic>.md`.

    Returns the topics moved and the sha of the commit that moved them,
    None when there was nothing to move. One commit for all of them, by
    the memory bot, through the curator (`_commit`), so the history reads
    as one step; `trailer` ends its message. A topic that already has a
    list keeps it, and the old file's items that are not on it yet are
    added.
    """
    from stack.lists import LISTS_DIR, insert_item, list_path, read_list

    changes: dict[str, str] = {}
    created: set[str] = set()
    removed: set[str] = set()
    moved: list[str] = []
    bucket = Path(vault) / shared_bucket
    for folder in sorted(p for p in bucket.iterdir() if p.is_dir()) if bucket.is_dir() else []:
        topic = folder.name
        if topic in (LISTS_DIR, "people") or not (folder / "todos.md").is_file():
            continue
        old_path = f"{shared_bucket}/{topic}/todos.md"
        new_path = list_path(topic, bucket=shared_bucket)
        converted = todos_as_list((folder / "todos.md").read_text(encoding="utf-8"), topic,
                                  shared_bucket=shared_bucket, language=language)
        current = Path(vault) / new_path
        if current.is_file():
            content = current.read_text(encoding="utf-8")
            for item in read_list(converted, path=new_path).items:
                if not any(i.text.casefold() == item.text.casefold()
                           for i in read_list(content, path=new_path).items):
                    content = insert_item(content, item, path=new_path)
            changes[new_path] = content
        else:
            changes[new_path] = converted
            created.add(new_path)
        removed.add(old_path)
        moved.append(topic)
    commit = _commit(memory, vault, changes, title="move each topic's todo list to lists/",
                     trailer=trailer, created=frozenset(created), removed=frozenset(removed),
                     kind="lists")
    return moved, commit



# ─── Card ids ────────────────────────────────────────────────────────────
#
# Every card in the memory repo carries an id of its own (ADR-016 §2).
# Writers give a new card its id; this pass gives one to every card that
# has none, over a git working copy of the repo: migration 0002 over all
# of it once, the curator over the files each push changed. Both apply
# `stack.cards.with_card_id`, the rule a writer follows too. Two files
# with one id (a note copied by hand) are told apart by their first
# commit: the earlier keeps the id, the later gets a new one.

@dataclass
class CardIds:
    """What a pass over the memory repo changes to give every card its id.

    `changes` maps a path to the file's new text. `left` holds each file
    the rule cannot place without guessing, with the reason, for the
    admin: it is reported and left as it is.

    >>> CardIds().changes
    {}
    """

    changes: dict[str, str] = field(default_factory=dict)
    left: list[str] = field(default_factory=list)


def card_ids_for(vault: Path, *, since: str | None = None) -> CardIds:
    """What gives the cards in the working copy `vault` an id of their own.

    `since` is a commit: only the files changed after it get an id, as
    the curator sees a push. None gives every file one, as migration 0002
    does. Ids two files share are told apart wherever the files are.
    """
    texts = _markdown_files(vault)
    ids = {path: _stored_card_id(text) for path, text in texts.items()}
    taken = {card_id for card_id in ids.values() if card_id}
    found = CardIds()

    def give(path: str, card_id: str = "") -> None:
        text = found.changes.get(path, texts[path])
        try:
            new = with_card_id(text, path, card_id, taken=taken)
        except CardIdError as e:
            found.left.append(f"{path}: {e}")
            return
        if new != text:
            found.changes[path] = new
            ids[path] = _stored_card_id(new)
            taken.add(ids[path])

    changed = _changed_since(vault, since) if since else None
    for path in sorted(texts):
        if changed is None or path in changed:
            give(path)

    shared = _shared_ids(ids)
    born = _first_commits(vault) if shared else {}
    for paths in shared.values():
        oldest_first = sorted(paths, key=lambda p: (born.get(p, math.inf), p))
        for path in oldest_first[1:]:
            fields = _parse_frontmatter(found.changes.get(path, texts[path]))
            card_type = str(fields.get("type") or "").strip() or HAND_WRITTEN
            give(path, new_card_id(card_type, path, taken=taken))
    return found


def give_card_ids(memory: MemoryAPI, vault: Path, *,
                  trailer: str = "") -> tuple[CardIds, str | None]:
    """Give every card in the curator's clone `vault` an id; see `card_ids_for`.
    Migration 0002's pass; the curator gives the files of each push theirs
    (`curator.give_ids_since`).

    One commit by the memory bot (`_commit`). Returns what the pass found
    and the commit's sha, None when there was nothing to change.
    `trailer` ends the message.
    """
    found = card_ids_for(vault)
    title = f"give {len(found.changes)} card(s) an id of their own"
    return found, _commit(memory, vault, found.changes, title=title, trailer=trailer)


def _commit(memory: MemoryAPI, vault: Path, changes: dict[str, str], *, title: str,
            trailer: str = "", created: frozenset[str] = frozenset(),
            removed: frozenset[str] = frozenset(), kind: str = "cards") -> str | None:
    """Write `changes` (path to new text) and take out `removed` in one commit
    by the memory bot, through the curator; its sha, or None for no changes.

    Each file is written on the version it has in `vault`, the curator's
    clone the change was made from, and a file in `created` as new, so an
    edit made meanwhile, or a file that appeared, makes the curator refuse
    the commit (`memory_api.Refused`) and nothing is written. `trailer`
    (`Migration: memory/0002`) ends the message.
    """
    from .memory_api import MEMORY_BOT, put, remove

    if not (changes or removed):
        return None
    read = [p for p in sorted(changes) if p not in created] + sorted(removed)
    versions = _blob_shas(vault, read) if read else {}
    key, _, value = trailer.partition(": ")
    written = memory.commit(
        [put(p, changes[p], base="" if p in created else versions[p]) for p in sorted(changes)]
        + [remove(p, base=versions[p]) for p in sorted(removed)],
        by=MEMORY_BOT, kind=kind, title=title, trailers={key: value} if value else {})
    return written.commit or None


def memory_for_migration(ctx) -> tuple[MemoryAPI, Path]:
    """The curator's API, and its clone brought up to Forgejo, as one of
    memory's migrations reads and writes the memory: it reads the cards in
    the clone and writes its change through the curator, on the versions
    it read (`_commit`). Raises when the curator has no clone yet; the next
    `stack up memory` runs the migration again."""
    from .memory_api import MemoryAPI

    memory = MemoryAPI.from_hook(ctx)
    synced = memory.sync()
    if synced.state == "missing":
        raise RuntimeError(f"the memory's curator has no clone yet: {synced.detail}")
    return memory, vault_path_for(ctx.stack.data)


def _markdown_files(vault: Path) -> dict[str, str]:
    """Every tracked markdown file in `vault` and its text."""
    rc, out, err = run_git(vault, "ls-files", "-z")
    if rc != 0:
        raise OSError(f"cannot list the files in {vault}: {err}")
    texts: dict[str, str] = {}
    for path in out.split("\0"):
        if path.endswith(".md"):
            try:
                texts[path] = (vault / path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
    return texts


def _stored_card_id(text: str) -> str:
    return card_id_from(_parse_frontmatter(text))


def _shared_ids(ids: dict[str, str]) -> dict[str, list[str]]:
    """Each id more than one file carries, with those files."""
    holders: dict[str, list[str]] = {}
    for path, card_id in ids.items():
        if card_id:
            holders.setdefault(card_id, []).append(path)
    return {card_id: paths for card_id, paths in holders.items() if len(paths) > 1}


def _changed_since(vault: Path, since: str) -> set[str] | None:
    """The files changed after commit `since`; None when git cannot tell
    (a history rewritten under it), which makes the pass look at all."""
    rc, out, _ = run_git(vault, "diff", "-z", "--name-only", since, "HEAD")
    return set(filter(None, out.split("\0"))) if rc == 0 else None


def _first_commits(vault: Path) -> dict[str, float]:
    """When each file in `vault` was first committed, carried along its renames.

    One pass over the history, oldest first. A rename (`-M`) keeps the
    file's date, so a document the archivist moved to a new title is as
    old as its first filing. A copy does not: `git log --follow` would
    pair an exact copy with its original and give both the original's
    date, which is the one case this is asked to tell apart.
    """
    rc, out, _ = run_git(vault, "-c", "core.quotePath=false", "log", "--reverse", "-M",
                         "--name-status", "--format=@%ct")
    born: dict[str, float] = {}
    when = math.inf
    for line in out.splitlines() if rc == 0 else []:
        if line.startswith("@"):
            when = float(line[1:])
            continue
        status, *paths = line.split("\t")
        if status.startswith("R") and len(paths) == 2:
            born[paths[1]] = born.pop(paths[0], when)
        elif status == "A" and paths:
            born.setdefault(paths[0], when)
        elif status == "D" and paths:
            born.pop(paths[0], None)
    return born


def _blob_shas(vault: Path, paths: list[str]) -> dict[str, str]:
    """The git blob sha of each file as it is in the working copy."""
    rc, out, err = run_git(vault, "hash-object", "--", *paths)
    shas = out.split()
    if rc != 0 or len(shas) != len(paths):
        raise OSError(f"cannot hash the files in {vault}: {err}")
    return dict(zip(paths, shas))


# ─── The cards a record links ────────────────────────────────────────────
#
# A record links the cards of the people, the correspondent and the topic it
# names (ADR-017 §3, §4, §10). The archivist files new records that way;
# these passes bring the files that were there before to the same shape,
# over a working copy of the memory repo, with the archivist's functions
# (`stack.cards.links`): migration 0003 names people by id, migration 0004
# declares the correspondents and topics records name.

@dataclass
class CardLinks:
    """What a pass over the memory repo changes, and what it left for a person.

    `changes` maps a path to the file's new text, `created` holds the paths
    among them that are new files. `left` says what the rule could not
    decide without guessing, one line per finding, for the admin.

    >>> CardLinks().changes
    {}
    """

    changes: dict[str, str] = field(default_factory=dict)
    created: set[str] = field(default_factory=set)
    left: list[str] = field(default_factory=list)


def people_by_id_for(vault: Path, *, shared_bucket: str = DEFAULT_SHARED_BUCKET) -> CardLinks:
    """What names the people in the working copy `vault`'s records by id.

    In each document, note, bookmark and email: a `persons` entry a
    declared person goes by becomes their id, each link to a person's
    compiled page (`<id>/about.md`) in the meta block points at their
    card, and `Person: <name>` tags go (`stack.cards.links.people_by_id`).
    In every file, a link to a document that moved since points at where
    it is now, when its Paperless id names exactly one document
    (`stack.cards.links.moved_links`). A name no one goes by, and a link
    no one document answers, are left as they are and listed in `left`.
    """
    from stack.cards.links import moved_links, people_by_id
    from stack.people import load_people

    people = load_people(vault, shared_bucket)
    if not people:
        raise RuntimeError(f"no person files under {shared_bucket}/people/ to name people by")
    texts = _markdown_files(vault)
    documents: dict[str, list[str]] = {}
    for path, text in texts.items():
        fields = _parse_frontmatter(text)
        if fields.get("type") == "document" and str(fields.get("paperless_id") or "").strip():
            documents.setdefault(str(fields["paperless_id"]).strip(), []).append(path)

    found = CardLinks()
    for path in sorted(texts):
        text, unknown = people_by_id(texts[path], path, people, bucket=shared_bucket)
        text, unplaced = moved_links(text, path, exists=texts.__contains__, documents=documents)
        found.left += [f"{path}: no declared person goes by {name!r}" for name in unknown]
        found.left += unplaced
        if text != texts[path]:
            found.changes[path] = text
    return found


def name_people_by_id(memory: MemoryAPI, vault: Path, *,
                      shared_bucket: str = DEFAULT_SHARED_BUCKET,
                      trailer: str = "") -> tuple[CardLinks, str | None]:
    """Name the people in `vault`'s records by id (`people_by_id_for`), in one
    commit by the memory bot; what the pass found and the commit's sha."""
    found = people_by_id_for(vault, shared_bucket=shared_bucket)
    title = f"name people by id in {len(found.changes)} card(s)"
    return found, _commit(memory, vault, found.changes, title=title, trailer=trailer)


def declared_cards_for(vault: Path, *, shared_bucket: str = DEFAULT_SHARED_BUCKET) -> CardLinks:
    """The cards the working copy `vault`'s records name and it does not have.

    One correspondent's card for each correspondent a document names
    (`stack.cards.links.correspondent_card`), one topic's card for each
    topic a record is filed into (`stack.cards.links.topic_card`). A card
    that is there is never rewritten. A correspondent's file of the old
    shape (`kind: correspondent`, no `type`) gets `type: correspondent`
    and its id, and nothing else.
    """
    from stack.cards.links import correspondent_card, topic_card, with_correspondent_type
    from stack.vault import topic_of

    texts = _markdown_files(vault)
    found = CardLinks()

    def declare(path: str, text: str) -> None:
        if path not in texts and path not in found.changes:
            found.changes[path] = text
            found.created.add(path)

    for path in sorted(texts):
        fields = _parse_frontmatter(texts[path])
        name = str(fields.get("correspondent") or "").strip()
        if fields.get("type") == "document" and name:
            declare(*correspondent_card(name, bucket=shared_bucket))
        if topic := topic_of(path):
            declare(*topic_card(*topic))
        if path.startswith(f"{correspondents_dir(shared_bucket)}/"):
            if (typed := with_correspondent_type(texts[path], path)) != texts[path]:
                found.changes[path] = typed
    return found


def declare_cards(memory: MemoryAPI, vault: Path, *,
                  shared_bucket: str = DEFAULT_SHARED_BUCKET,
                  trailer: str = "") -> tuple[CardLinks, str | None]:
    """Write the cards `vault`'s records name (`declared_cards_for`), in one
    commit by the memory bot; what the pass found and the commit's sha."""
    found = declared_cards_for(vault, shared_bucket=shared_bucket)
    title = f"declare {len(found.changes)} correspondent and topic card(s)"
    return found, _commit(memory, vault, found.changes, title=title, trailer=trailer,
                          created=frozenset(found.created))


# ─── Topic tags by id ────────────────────────────────────────────────────
#
# A card tags a topic by the ontology's id and its keywords lowercase with
# hyphens (ADR-017 §4). The archivist writes new records' tags that way
# (`stack.tags.card_tags`), and so does the diary compile; migrations 0005
# and 0010 bring the files that were there before to the same shape with
# the same rule (`stack.tags.tag_for`). 0005 leaves diary cards alone:
# they are signed (`stack.digest`), and 0010 signs the ones the compile
# still owns again, so they stay the compile's.

def topic_tags_for(vault: Path) -> CardLinks:
    """What tags the topics of the working copy `vault`'s cards by id.

    Each tag that names a topic of the vault's ontology (`ontology.toml`,
    else the shipped seed), by its id or its label in any language,
    becomes the id; every other tag is lowercased with hyphens; two that
    become one are one, in the place of the first. A tag that is no
    keyword even so stays as it is and is listed in `left`
    (`stack.tags.with_topic_tags`). Diary cards are left alone: they are
    `diary_topic_tags_for`'s.
    """
    from stack.tags import with_topic_tags

    ontology = get_ontology(vault)
    texts = _markdown_files(vault)
    found = CardLinks()
    for path in sorted(texts):
        if _parse_frontmatter(texts[path]).get("type") == "diary":
            continue
        text, left = with_topic_tags(texts[path], ontology)
        found.left += [f"{path}: tag {tag!r} is no topic and no keyword" for tag in left]
        if text != texts[path]:
            found.changes[path] = text
    return found


def tag_topics_by_id(memory: MemoryAPI, vault: Path, *,
                     trailer: str = "") -> tuple[CardLinks, str | None]:
    """Tag the topics of `vault`'s cards by id (`topic_tags_for`), in one
    commit by the memory bot; what the pass found and the commit's sha."""
    found = topic_tags_for(vault)
    title = f"tag topics by id in {len(found.changes)} card(s)"
    return found, _commit(memory, vault, found.changes, title=title, trailer=trailer)


def diary_topic_tags_for(vault: Path) -> CardLinks:
    """What tags the topics of the working copy `vault`'s diary cards by id.

    The rule of every other card (`topic_tags_for`): a topic's label in
    any language becomes its id, every other tag is lowercased with
    hyphens, and a tag that is no keyword even so stays and is listed in
    `left`. A card the compile still owns is signed again
    (`stack.digest.signed`), so it is what the compile writes today; a
    card a person changed gets ids too, and stays unsigned and theirs.
    """
    from stack.digest import changed_by_hand, signed
    from stack.tags import with_topic_tags

    ontology = get_ontology(vault)
    found = CardLinks()
    for path, text in sorted(_markdown_files(vault).items()):
        if _parse_frontmatter(text).get("type") != "diary":
            continue
        new, left = with_topic_tags(text, ontology)
        found.left += [f"{path}: tag {tag!r} is no topic and no keyword" for tag in left]
        if new != text:
            found.changes[path] = new if changed_by_hand(text) else signed(new)
    return found


def tag_diary_topics_by_id(memory: MemoryAPI, vault: Path, *,
                           trailer: str = "") -> tuple[CardLinks, str | None]:
    """Tag the topics of `vault`'s diary cards by id (`diary_topic_tags_for`),
    in one commit by the memory bot; what the pass found and the commit's sha."""
    found = diary_topic_tags_for(vault)
    title = f"tag topics by id in {len(found.changes)} diary card(s)"
    return found, _commit(memory, vault, found.changes, title=title, trailer=trailer)


# ─── Kinds ───────────────────────────────────────────────────────────────
#
# A card's `kind` is one of its type's fixed set (ADR-017 §6,
# `stack.cards.CARD_KINDS`). The writers write it: the nightly move every
# archive it adds to (`stack.lists.as_archive`), the diary compile every
# card. Migration 0006 brings the files written before to the same shape.

# A diary card's line from before kinds: what it was recorded as.
_MEDIUM_LINE = re.compile(r"^medium:", re.MULTILINE)


def card_kinds_for(vault: Path) -> CardLinks:
    """What gives the cards in the working copy `vault` their kind.

    A list's archive gets `kind: archive` (`stack.lists.as_archive`). A
    diary card's `medium` line becomes its `kind` line where it is; a card
    the compile still owns is signed again (`stack.digest.signed`) and
    stays the compile's, one a person changed stays theirs. A `kind` that
    only repeats a type that has none (`type: correspondent` with
    `kind: correspondent`, from before types) goes. Any other `kind`
    outside its type's set is left as it is and listed in `left`.
    """
    from stack.cards import ARCHIVE, CARD_KINDS, card_kind, kind_problem
    from stack.digest import changed_by_hand, signed
    from stack.frontmatter import with_field
    from stack.lists import as_archive

    found = CardLinks()
    for path, text in sorted(_markdown_files(vault).items()):
        fields = _parse_frontmatter(text)
        card_type = str(fields.get("type") or "").strip()
        new = text
        if card_type == "list" and card_kind(path, fields) == ARCHIVE:
            new = as_archive(text)
        elif card_type == "diary" and fields.get("medium") and not fields.get("kind"):
            head, sep, rest = text.partition("\n---\n")
            new = _MEDIUM_LINE.sub("kind:", head, count=1) + sep + rest
            if not changed_by_hand(text):
                new = signed(new)
        elif card_type not in CARD_KINDS and str(fields.get("kind") or "").strip() == card_type:
            new = with_field(text, "kind", "")
        if problem := kind_problem(path, _parse_frontmatter(new)):
            found.left.append(f"{path}: {problem}")
        if new != text:
            found.changes[path] = new
    return found


def give_card_kinds(memory: MemoryAPI, vault: Path, *,
                    trailer: str = "") -> tuple[CardLinks, str | None]:
    """Give the cards in `vault` their kind (`card_kinds_for`), in one commit
    by the memory bot; what the pass found and the commit's sha."""
    found = card_kinds_for(vault)
    title = f"give {len(found.changes)} card(s) their kind"
    return found, _commit(memory, vault, found.changes, title=title, trailer=trailer)


# ─── A note's sources ────────────────────────────────────────────────────
#
# A note keeps what it is about on its card and where it came from in a
# sidecar beside it (ADR-018). The archivist writes new notes that way;
# migration 0008 brings the notes filed before to the same shape, without
# a model call (`note_card.legacy_note`).

def note_sources_for(vault: Path, *, media_root: Path | None = None) -> CardLinks:
    """What gives the notes in the working copy `vault` their sources and a sidecar.

    Each note filed in the format before (its words, caption and replies in
    sections of their own, where it came from in the frontmatter) becomes
    a card with `## Sources` and a new sidecar beside it. A photo's hash
    comes from the media store under `media_root`. What the old card did
    not record (a reply's time, a photo the store has no record of) and
    words that carry prompt text are kept, marked and listed in `left`. A
    note written in the memory, one already converted, and every other
    card stay as they are.
    """
    from .note_card import is_legacy_note, legacy_note
    from stack.cards.sources import sidecar_path

    def media_sha(link: str) -> str:
        return media.recorded_sha256(media_root, link) if media_root is not None else ""

    found = CardLinks()
    for path, text in sorted(_markdown_files(vault).items()):
        side = sidecar_path(path)
        if not is_legacy_note(text) or (vault / side).exists():
            continue
        converted = legacy_note(text, path=path, media_sha=media_sha)
        found.changes[path] = converted.card
        found.changes[side] = converted.sidecar
        found.created.add(side)
        found.left += converted.problems
    return found


def give_notes_their_sources(memory: MemoryAPI, vault: Path, *,
                             media_root: Path | None = None,
                             trailer: str = "") -> tuple[CardLinks, str | None]:
    """Give the notes in `vault` their sources and a sidecar (`note_sources_for`),
    in one commit by the memory bot; what the pass found and the commit's sha."""
    found = note_sources_for(vault, media_root=media_root)
    title = f"give {len(found.created)} note(s) their sources and a sidecar"
    return found, _commit(memory, vault, found.changes, title=title, trailer=trailer,
                          created=frozenset(found.created))


# ─── Correspondents written by hand ─────────────────────────────────────
#
# Before correspondents were cards, this module documented a shape for a
# correspondent's file written by hand that named it in `canonical`. A
# card's name is its `title` (ADR-017 §5); migration 0009 brings those
# files to it.

# The line that named a correspondent before cards had a title.
_CANONICAL_LINE = re.compile(r"^canonical:", re.MULTILINE)


def correspondent_titles_for(vault: Path) -> CardLinks:
    """What gives the correspondents in the working copy `vault` their title.

    A correspondent with a `canonical` and no title gets its `canonical`
    line as its `title` line, where it is, the name as written. One with a
    title loses `canonical`; a name there that is neither the title nor an
    alias is kept as an alias and listed in `left`. Every other line and
    every other card stay as they are.
    """
    from stack.frontmatter import with_field

    found = CardLinks()
    for path, text in sorted(_markdown_files(vault).items()):
        fields = _parse_frontmatter(text)
        if fields.get("type") != "correspondent" or "canonical" not in fields:
            continue
        canonical = str(fields.get("canonical") or "").strip()
        title = str(fields.get("title") or "").strip()
        if canonical and "title" not in fields:
            head, sep, rest = text.partition("\n---\n")
            found.changes[path] = _CANONICAL_LINE.sub("title:", head, count=1) + sep + rest
            continue
        new = with_field(text, "canonical", "")
        if not title:
            new = with_field(new, "title", canonical)
        elif canonical and canonical != title and canonical not in _fm_list(fields, "aliases"):
            new = with_field(new, "aliases", [*_fm_list(fields, "aliases"), canonical])
            found.left.append(f"{path}: its name {canonical!r} is kept as an alias of {title!r}")
        found.changes[path] = new
    return found


def title_correspondents(memory: MemoryAPI, vault: Path, *,
                         trailer: str = "") -> tuple[CardLinks, str | None]:
    """Give the correspondents in `vault` their title (`correspondent_titles_for`),
    in one commit by the memory bot; what the pass found and the commit's sha."""
    found = correspondent_titles_for(vault)
    title = f"give {len(found.changes)} correspondent(s) their title"
    return found, _commit(memory, vault, found.changes, title=title, trailer=trailer)


# ─── The people of diary cards ───────────────────────────────────────────
#
# A diary card names its people by id like every other record (ADR-017
# §4). The compile writes new cards that way; migration 0007 brings the
# cards written before to the same shape. A diary card is signed with a
# digest (`stack.digest`): the pass changes only cards the compile still
# owns and signs them again, so they stay the compile's.

def diary_people_by_id_for(vault: Path, *,
                           shared_bucket: str = DEFAULT_SHARED_BUCKET) -> CardLinks:
    """What names the people of the working copy `vault`'s diary cards by id.

    In each diary card the compile still owns, a `persons` entry a
    declared person goes by becomes their id and every `Person: <name>`
    tag goes (`stack.cards.links.persons_by_id`), and the card is signed
    again. A name no one goes by stays as it is and is listed in `left`.
    A card a person changed is left as it is, and listed when it still
    holds names or `Person:` tags: it is theirs.
    """
    from stack.cards.links import persons_by_id
    from stack.digest import changed_by_hand, signed
    from stack.people import load_people

    people = load_people(vault, shared_bucket)
    if not people:
        raise RuntimeError(f"no person files under {shared_bucket}/people/ to name people by")
    found = CardLinks()
    for path, text in sorted(_markdown_files(vault).items()):
        if _parse_frontmatter(text).get("type") != "diary":
            continue
        new, unknown = persons_by_id(text, people)
        if new != text and changed_by_hand(text):
            found.left.append(f"{path}: a person changed it, so it keeps its names and tags")
            continue
        found.left += [f"{path}: no declared person goes by {name!r}" for name in unknown]
        if new != text:
            found.changes[path] = signed(new)
    return found


def name_diary_people_by_id(memory: MemoryAPI, vault: Path, *,
                            shared_bucket: str = DEFAULT_SHARED_BUCKET,
                            trailer: str = "") -> tuple[CardLinks, str | None]:
    """Name the people of `vault`'s diary cards by id (`diary_people_by_id_for`),
    in one commit by the memory bot; what the pass found and the commit's sha."""
    found = diary_people_by_id_for(vault, shared_bucket=shared_bucket)
    title = f"name people by id in {len(found.changes)} diary card(s)"
    return found, _commit(memory, vault, found.changes, title=title, trailer=trailer)
