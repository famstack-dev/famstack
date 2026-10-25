"""What the curator's sync does when the remote has stopped cooperating.

A vault sync that cannot make progress used to have exactly one
behaviour: fail, log nothing anyone would read, and try again in
thirty seconds. Forever. These tests pin the way out of each wedge,
against real git repositories in the states that produce it (see
`tests/conftest.py`), because the failure being covered here is a real
remote misbehaving and a stubbed one cannot misbehave convincingly.

The policies are the point, and they differ by who owns the truth:

  - **memory** is the database. Its clone is written and synced by the
    card store, which never resets it (`test_memory_card_store.py` pins
    how); here only the curator's wiring of it.
  - **brain** is a projection. It is regenerable from memory, so it may
    take the remote as given and rebuild on top.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory" / "bot"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory" / "bot" / "cli"))

from lib import (  # noqa: E402
    is_auth_failure,
    reconcile_with_remote,
)


# ── Reading the repositories back ────────────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _head(repo: Path, rev: str = "HEAD") -> str:
    return _git(repo, "rev-parse", rev)


def _log_subjects(repo: Path, rev: str = "HEAD") -> list[str]:
    return _git(repo, "log", "--format=%s", rev).splitlines()


def _ff_only_pull_fails(repo: Path) -> bool:
    """True when `git pull --ff-only` cannot resolve this state.

    The old sync was exactly this command, so a fixture that this
    succeeds on is not reproducing the outage. Asserted alongside the
    recovery so the test is pinned to the failure, not to our idea of it.
    """
    return subprocess.run(
        ["git", "-C", str(repo), "pull", "--ff-only"],
        capture_output=True, text=True,
    ).returncode != 0


# ── The everyday outcomes ────────────────────────────────────────────────

class TestReconcileWhenNothingIsWrong:
    """The healthy paths still have to be cheap and quiet, or the
    recovery machinery has made the common case worse."""

    def test_agreeing_copies_report_up_to_date(self, git_healthy_clone):
        result = reconcile_with_remote(
            git_healthy_clone.local, "origin",
        )
        assert result.status == "up_to_date"

    def test_a_remote_ahead_is_a_plain_fast_forward(
        self, git_healthy_clone, git_commit, tmp_path,
    ):
        other = tmp_path / "other-writer"
        subprocess.run(
            ["git", "clone", str(git_healthy_clone.remote), str(other)],
            capture_output=True, check=True,
        )
        _git(other, "config", "user.email", "test@famstack.local")
        _git(other, "config", "user.name", "Test")
        git_commit(other, "family/notes/a.md", "note\n", "learn: a note")
        _git(other, "push", "origin", "main")

        result = reconcile_with_remote(
            git_healthy_clone.local, "origin",
        )

        assert result.status == "fast_forwarded"
        assert (git_healthy_clone.local / "family/notes/a.md").exists()


# ── brain: the remote is taken as given ─────────────────────────────────

class TestProjectionRecovery:
    """The policy for `family/brain`."""

    def test_unrelated_history_resets_without_preserving_anything(
        self, git_unrelated_history_clone,
    ):
        local = git_unrelated_history_clone.local

        result = reconcile_with_remote(local, "origin")

        assert result.status == "reset_to_remote"
        assert _head(local) == _head(local, "origin/main")

    def test_diverged_history_takes_the_remote_as_the_new_base(
        self, git_diverged_clone,
    ):
        local = git_diverged_clone.local

        result = reconcile_with_remote(local, "origin")

        assert result.status == "reset_to_remote"
        assert _head(local) == _head(local, "origin/main")

    def test_unpushed_local_commits_are_left_for_the_caller_to_push(
        self, git_healthy_clone, git_commit,
    ):
        """Last cycle's projection waiting on a push is not a divergence.
        Resetting it away would drop work the caller is about to deliver."""
        local = git_healthy_clone.local
        sha = git_commit(local, "index.md", "generated\n", "brain: project")

        result = reconcile_with_remote(local, "origin")

        assert result.status == "ahead"
        assert _head(local) == sha


class TestThePoliciesDiffer:

    async def test_the_memory_keeps_the_history_a_projection_may_discard(
        self, git_unrelated_history_clone, tmp_path,
    ):
        """Same wedge, two owners, two answers. If these ever converge,
        either memory started losing commits or brain started holding on
        to what it can regenerate."""
        from curator import CURATOR_REMOTE, Vault

        source = git_unrelated_history_clone.local
        projection = tmp_path / "projection"
        shutil.copytree(source, projection)
        _git(source, "remote", "add", CURATOR_REMOTE, str(git_unrelated_history_clone.remote))
        before = _head(source)

        kept = await Vault(source).sync()
        reset = reconcile_with_remote(projection, "origin")

        assert kept.state == "diverged" and _head(source) == before
        assert reset.status == "reset_to_remote"
        assert _head(projection) == _head(projection, "origin/main")


# ── A remote that stopped answering ──────────────────────────────────────

class TestRemoteFailures:

    def test_unreachable_remote_leaves_the_working_copy_alone(
        self, git_unreachable_remote_clone,
    ):
        local = git_unreachable_remote_clone.local
        before = _head(local)

        result = reconcile_with_remote(local, "origin")

        assert result.status == "unreachable"
        assert _head(local) == before

    def test_rejected_credentials_read_differently_from_a_dead_host(self):
        """An expired token and an unplugged network both fail the fetch,
        and only one of them is fixed by re-deriving the URL."""
        assert is_auth_failure(
            "remote: Forgejo: Credentials are incorrect or have expired"
        )
        assert is_auth_failure("fatal: Authentication failed for 'http://host/x.git'")
        assert is_auth_failure(
            "fatal: unable to access 'http://host/x.git': "
            "The requested URL returned error: 403"
        )
        assert not is_auth_failure("fatal: unable to access: Could not resolve host")
        assert not is_auth_failure("")


# ── The curator's own wiring ─────────────────────────────────────────────

@pytest.fixture
def auth_failing_transport(tmp_path, monkeypatch) -> str:
    """A git transport that always answers "your credentials are wrong".

    A real `git-remote-<scheme>` helper on PATH, so git produces the
    failure itself: the code under test sees the same stderr Forgejo
    would produce with an expired token, through the same code path.
    Returns the URL to point a remote at.
    """
    bindir = tmp_path / "fake-git-transports"
    bindir.mkdir()
    helper = bindir / "git-remote-authfail"
    helper.write_text(
        "#!/bin/sh\n"
        "echo \"fatal: Authentication failed for 'authfail://memory.git'\" >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return "authfail://memory.git"


@pytest.fixture
def warnings_logged():
    """Collect anything the curator says at WARNING or above.

    Loguru sinks are the one collaborator these tests stub, because the
    assertion *is* about the logging: an outage that only shows up at
    DEBUG is how this went unnoticed for weeks.
    """
    from loguru import logger

    lines: list[str] = []
    sink_id = logger.add(lines.append, level="WARNING")
    yield lines
    logger.remove(sink_id)


@pytest.fixture
def debug_logged():
    """Everything the curator says, DEBUG included.

    `warnings_logged` deliberately starts at WARNING. The line pinned
    below only ever appears at DEBUG, so it needs its own sink.
    """
    from loguru import logger

    lines: list[str] = []
    sink_id = logger.add(lines.append, level="DEBUG")
    yield lines
    logger.remove(sink_id)


class TestVaultSync:
    """The curator's vault sync: through the store, one auth retry, loud."""

    async def test_a_vault_that_cannot_be_joined_to_forgejo_says_so(
        self, git_unrelated_history_clone, warnings_logged,
    ):
        """The memory repository created again in Forgejo leaves the clone
        as the family's only full copy: the cycle keeps it, pushes nothing
        and says so where an operator looks, every cycle until a person
        decides."""
        from curator import CURATOR_REMOTE, Vault

        local = git_unrelated_history_clone.local
        _git(local, "remote", "add", CURATOR_REMOTE,
             str(git_unrelated_history_clone.remote))
        before = _head(local)

        result = await Vault(local).sync()

        assert result.state == "diverged"
        assert _head(local) == before
        assert any("diverged" in line for line in warnings_logged)

    async def test_a_stale_credential_is_re_derived_and_retried_once(
        self, git_healthy_clone, auth_failing_transport, monkeypatch, tmp_path,
    ):
        """The token baked into the remote URL expires on its own
        schedule. Re-reading the environment is the whole fix, and it
        only counts if the sync then actually completes."""
        from curator import CURATOR_REMOTE, Vault

        # Where `vault_remote_url` will look: <CODE_URL>/family/memory.git
        forgejo = tmp_path / "forgejo"
        (forgejo / "family").mkdir(parents=True)
        (forgejo / "family" / "memory.git").symlink_to(git_healthy_clone.remote)
        monkeypatch.setenv("CODE_URL", str(forgejo))
        monkeypatch.setenv("MATRIX_ADMIN_USER", "stackadmin")
        monkeypatch.setenv("MATRIX_ADMIN_PASSWORD", "hunter2")

        local = git_healthy_clone.local
        _git(local, "remote", "add", CURATOR_REMOTE, auth_failing_transport)

        result = await Vault(local).sync()

        assert result.state == "ok"

    async def test_a_credential_that_cannot_be_re_derived_is_an_error(
        self, git_healthy_clone, auth_failing_transport, monkeypatch,
        warnings_logged,
    ):
        from curator import CURATOR_REMOTE, Vault

        for key in ("CODE_URL", "MATRIX_ADMIN_USER", "MATRIX_ADMIN_PASSWORD"):
            monkeypatch.delenv(key, raising=False)
        local = git_healthy_clone.local
        _git(local, "remote", "add", CURATOR_REMOTE, auth_failing_transport)

        result = await Vault(local).sync()

        assert result.state == "unreachable"
        assert any("rejected the credentials" in line for line in warnings_logged)


class TestANewMemory:
    """The curator makes its clone of the memory, and gives a new memory its
    seed. The install creates the repository in Forgejo and asks the curator
    to sync; from then on the clone and every write to it are the curator's
    (ADR-019)."""

    @pytest.fixture
    def forgejo(self, tmp_path, monkeypatch) -> Path:
        """Where `CODE_URL` names Forgejo; `family/memory.git` is made by a test."""
        code = tmp_path / "forgejo"
        (code / "family").mkdir(parents=True)
        monkeypatch.setenv("CODE_URL", str(code))
        monkeypatch.setenv("MATRIX_ADMIN_USER", "stackadmin")
        monkeypatch.setenv("MATRIX_ADMIN_PASSWORD", "hunter2")
        return code / "family" / "memory.git"

    @pytest.fixture
    def seeds(self, tmp_path) -> Path:
        """What a release ships as a new memory's first files."""
        seeds = tmp_path / "seeds"
        (seeds / "_shared" / "correspondents").mkdir(parents=True)
        (seeds / "_shared" / "correspondents" / "README.md").write_text("# Correspondents\n")
        (seeds / "ontology.toml").write_text('[topic.medical]\nnames = { en = "Medical" }\n')
        return seeds

    async def test_a_new_memory_is_cloned_and_given_its_seed_in_one_commit(
            self, forgejo, seeds, tmp_path):
        """Given the memory repository Forgejo has just created, with no
        commit, when the curator syncs, then it has a clone of it, and the
        memory has the shipped seed in one commit by the memory bot, its
        shared files in the household's bucket."""
        from curator import Vault

        _git(forgejo.parent, "init", "-q", "--bare", "-b", "main", forgejo.name)
        vault = Vault(tmp_path / "vault", server_name="simpson", shared_bucket="simpsons",
                      seeds=seeds)

        result = await vault.sync()

        assert result.state == "ok"
        assert (tmp_path / "vault" / "simpsons" / "correspondents" / "README.md").is_file()
        assert _git(forgejo, "log", "--format=%an <%ae> | %cn | %s", "main") == \
            "memory-bot <memory-bot@local> | memory-bot | seed: initial memory from famstack"
        assert sorted(_git(forgejo, "ls-tree", "-r", "--name-only", "main").splitlines()) == \
            ["ontology.toml", "simpsons/correspondents/README.md"]

    def test_asked_through_its_api_the_curator_clones_and_seeds_at_once(
            self, forgejo, seeds, tmp_path):
        """What the install does once it has made the repository: it asks
        the curator to sync, and when the answer comes the memory is there,
        seeded, before the hooks and migrations that follow write to it."""
        sys.path.insert(0, str(_REPO_ROOT / "stacklets"))
        sys.path.insert(0, str(Path(__file__).parent))
        from curator import Vault
        from memory.memory_api import MemoryAPI
        from memory_git import TOKEN, curator_serving

        _git(forgejo.parent, "init", "-q", "--bare", "-b", "main", forgejo.name)
        vault = Vault(tmp_path / "vault", seeds=seeds)

        with curator_serving(vault.store, TOKEN, sync=vault.sync_now) as curator:
            synced = MemoryAPI(curator, token=TOKEN, where="the curator").sync()

        assert synced.state == "ok"
        assert _log_subjects(forgejo, "main") == ["seed: initial memory from famstack"]

    async def test_a_memory_that_has_commits_is_never_seeded_again(
            self, forgejo, seeds, tmp_path, git_healthy_clone):
        """The seed is a new memory's first commit, and nothing else's: a
        family that deleted a seed file keeps it deleted."""
        from curator import Vault

        forgejo.symlink_to(git_healthy_clone.remote)
        before = _head(git_healthy_clone.remote, "main")

        result = await Vault(tmp_path / "vault", seeds=seeds).sync()

        assert result.state == "ok"
        assert _head(git_healthy_clone.remote, "main") == before
        assert not (tmp_path / "vault" / "ontology.toml").exists()

    async def test_without_the_repository_in_forgejo_the_sync_says_what_makes_it(
            self, forgejo, seeds, tmp_path):
        from curator import Vault

        result = await Vault(tmp_path / "vault", seeds=seeds).sync()

        assert result.state == "missing"
        assert "stack up memory" in result.detail
        assert not (tmp_path / "vault").exists()


class TestBrainSync:
    """Brain realigns instead of overwriting, and a push that never
    lands is reported rather than forced."""

    async def test_a_recreated_remote_realigns_the_projection(
        self, git_unrelated_history_clone, tmp_path,
    ):
        from curator import CURATOR_REMOTE, Brain

        local = git_unrelated_history_clone.local
        _git(local, "remote", "add", CURATOR_REMOTE,
             str(git_unrelated_history_clone.remote))

        result = await Brain(local, tmp_path / "source").sync()

        assert result.status == "reset_to_remote"
        assert _head(local) == _head(local, f"{CURATOR_REMOTE}/main")

    async def test_a_push_that_cannot_land_is_reported_not_forced(
        self, git_unreachable_remote_clone, tmp_path, warnings_logged,
    ):
        """The incident's brain symptom: `family/brain` did not exist, so
        every push was refused. The old fallback answered by force-pushing
        and logging "remote diverged", which was both useless and untrue."""
        from curator import CURATOR_REMOTE, Brain

        local = git_unreachable_remote_clone.local
        _git(local, "remote", "add", CURATOR_REMOTE,
             str(git_unreachable_remote_clone.remote))
        (local / "index.md").write_text("generated\n", encoding="utf-8")

        pushed = await Brain(local, tmp_path / "source").commit_push("brain: project")

        assert pushed is False
        assert any("not reaching Forgejo" in line for line in warnings_logged)
        # The commit is still here for the next cycle to deliver.
        assert "brain: project" in _log_subjects(local)

    async def test_a_routine_commit_does_not_report_git_as_failing(
        self, git_healthy_clone, tmp_path, debug_logged,
    ):
        """`git diff --cached --quiet` answers with its exit code, and 1
        means staged changes exist — the reason to commit, not a fault.
        Reporting it as "brain git diff failed" told anyone reading the
        curator's log that the mirror was broken at exactly the moments
        it was working, which is worse than silence.
        """
        from curator import CURATOR_REMOTE, Brain

        local = git_healthy_clone.local
        _git(local, "remote", "add", CURATOR_REMOTE,
             str(git_healthy_clone.remote))
        (local / "index.md").write_text("generated\n", encoding="utf-8")

        pushed = await Brain(local, tmp_path / "source").commit_push("brain: project")

        assert pushed is True
        assert "brain: project" in _log_subjects(local)
        assert [ln for ln in debug_logged if "git diff failed" in ln] == []
