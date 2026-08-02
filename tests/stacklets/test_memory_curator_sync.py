"""What the curator's sync does when the remote has stopped cooperating.

A vault sync that cannot make progress used to have exactly one
behaviour: fail, log nothing anyone would read, and try again in
thirty seconds. Forever. These tests pin the way out of each wedge,
against real git repositories in the states that produce it (see
`tests/conftest.py`), because the failure being covered here is a real
remote misbehaving and a stubbed one cannot misbehave convincingly.

The policies are the point, and they differ by who owns the truth:

  - **memory** is the database. Its local commits may be a todo tick,
    which is information that exists nowhere else (ADR-011), so they
    are replayed onto the remote or set aside on a branch, never
    dropped.
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

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory" / "bot"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory" / "bot" / "cli"))

from lib import (  # noqa: E402
    PRESERVE_LOCAL,
    PRESERVED_BRANCH_PREFIX,
    RESET_LOCAL,
    is_auth_failure,
    preserved_branch_name,
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


def _wedged_branches(repo: Path) -> list[str]:
    out = _git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/")
    return [b for b in out.splitlines() if b.startswith(PRESERVED_BRANCH_PREFIX)]


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
            git_healthy_clone.local, "origin", recovery=PRESERVE_LOCAL,
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
            git_healthy_clone.local, "origin", recovery=PRESERVE_LOCAL,
        )

        assert result.status == "fast_forwarded"
        assert (git_healthy_clone.local / "family/notes/a.md").exists()


# ── memory: local commits survive, always ────────────────────────────────

class TestSourceRecovery:
    """`PRESERVE_LOCAL` — the policy for `family/memory`."""

    def test_diverged_history_keeps_the_local_commit_and_delivers_it(
        self, git_diverged_clone,
    ):
        """The todo-tick case, and the worst possible outcome is losing it.

        Local ticked a todo, Forgejo took a filing. `pull --ff-only`
        fails on this and would keep failing. Reconciling has to end
        with both commits present and the divergence actually gone, not
        merely re-ordered into the same wedge for the next cycle.
        """
        local = git_diverged_clone.local
        assert _ff_only_pull_fails(local)

        result = reconcile_with_remote(local, "origin", recovery=PRESERVE_LOCAL)

        assert result.status == "rebased"
        assert (local / "family/todos.md").read_text() == "- [x] buy duff\n"
        assert (local / "family/documents/filed.md").exists()
        # Delivered, not just replayed: the remote carries the tick now.
        assert _head(local) == _head(local, "origin/main")
        assert "todo: tick buy duff" in _log_subjects(local)

    def test_local_only_commits_are_pushed_rather_than_left_to_diverge(
        self, git_healthy_clone, git_commit,
    ):
        local = git_healthy_clone.local
        git_commit(local, "family/todos.md", "- [x] call dentist\n", "todo: tick")

        result = reconcile_with_remote(local, "origin", recovery=PRESERVE_LOCAL)

        assert result.status == "pushed"
        assert _head(local) == _head(local, "origin/main")

    def test_unrelated_history_is_preserved_on_a_branch_before_the_reset(
        self, git_unrelated_history_clone,
    ):
        """The night this broke, by hand: `git branch wedged-orphan-<date>
        main`, then `git reset --hard <remote>`. Nothing else bridges two
        histories that share no commit, and nothing else keeps the local
        one findable afterwards."""
        local = git_unrelated_history_clone.local
        stranded = _head(local)
        assert _ff_only_pull_fails(local)

        result = reconcile_with_remote(local, "origin", recovery=PRESERVE_LOCAL)

        assert result.status == "preserved_and_reset"
        preserved = _wedged_branches(local)
        assert preserved == [preserved_branch_name(stranded)]
        assert preserved[0].startswith(f"{PRESERVED_BRANCH_PREFIX}-")
        assert _head(local, preserved[0]) == stranded
        # And the working copy is now the remote, ready to move again.
        assert _head(local) == _head(local, "origin/main")
        assert (local / "README.md").read_text() == "# new life\n"

    def test_recovery_is_idempotent(self, git_unrelated_history_clone):
        """A second cycle must find a healthy repo, not manufacture a
        second orphan branch every thirty seconds."""
        local = git_unrelated_history_clone.local
        reconcile_with_remote(local, "origin", recovery=PRESERVE_LOCAL)
        after_first = _wedged_branches(local)

        result = reconcile_with_remote(local, "origin", recovery=PRESERVE_LOCAL)

        assert result.status == "up_to_date"
        assert _wedged_branches(local) == after_first
        assert len(after_first) == 1


# ── brain: regenerable, so realignment is free ───────────────────────────

class TestProjectionRecovery:
    """`RESET_LOCAL` — the policy for `family/brain`."""

    def test_unrelated_history_resets_without_preserving_anything(
        self, git_unrelated_history_clone,
    ):
        local = git_unrelated_history_clone.local

        result = reconcile_with_remote(local, "origin", recovery=RESET_LOCAL)

        assert result.status == "reset_to_remote"
        assert _wedged_branches(local) == []
        assert _head(local) == _head(local, "origin/main")

    def test_diverged_history_takes_the_remote_as_the_new_base(
        self, git_diverged_clone,
    ):
        local = git_diverged_clone.local

        result = reconcile_with_remote(local, "origin", recovery=RESET_LOCAL)

        assert result.status == "reset_to_remote"
        assert _head(local) == _head(local, "origin/main")

    def test_unpushed_local_commits_are_left_for_the_caller_to_push(
        self, git_healthy_clone, git_commit,
    ):
        """Last cycle's projection waiting on a push is not a divergence.
        Resetting it away would drop work the caller is about to deliver."""
        local = git_healthy_clone.local
        sha = git_commit(local, "index.md", "generated\n", "brain: project")

        result = reconcile_with_remote(local, "origin", recovery=RESET_LOCAL)

        assert result.status == "ahead"
        assert _head(local) == sha


class TestThePoliciesDiffer:

    def test_source_preserves_the_history_a_projection_may_discard(
        self, git_unrelated_history_clone, tmp_path,
    ):
        """Same wedge, two owners, two answers. If these ever converge,
        either memory started losing commits or brain started hoarding
        branches it can regenerate."""
        source = git_unrelated_history_clone.local
        projection = tmp_path / "projection"
        shutil.copytree(source, projection)

        preserved = reconcile_with_remote(source, "origin", recovery=PRESERVE_LOCAL)
        reset = reconcile_with_remote(projection, "origin", recovery=RESET_LOCAL)

        assert preserved.status == "preserved_and_reset"
        assert reset.status == "reset_to_remote"
        assert len(_wedged_branches(source)) == 1
        assert _wedged_branches(projection) == []
        # Both end up at the remote — only the cost of getting there differs.
        assert _head(source) == _head(projection)


# ── A remote that stopped answering ──────────────────────────────────────

class TestRemoteFailures:

    def test_unreachable_remote_leaves_the_working_copy_alone(
        self, git_unreachable_remote_clone,
    ):
        local = git_unreachable_remote_clone.local
        before = _head(local)

        result = reconcile_with_remote(local, "origin", recovery=PRESERVE_LOCAL)

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


class TestVaultSync:
    """The curator's vault sync: source policy, one auth retry, loud."""

    async def test_recovers_a_wedged_vault_and_says_so(
        self, git_unrelated_history_clone, warnings_logged,
    ):
        from curator import CURATOR_REMOTE, Vault

        local = git_unrelated_history_clone.local
        _git(local, "remote", "add", CURATOR_REMOTE,
             str(git_unrelated_history_clone.remote))

        result = await Vault(local).sync()

        assert result.status == "preserved_and_reset"
        assert len(_wedged_branches(local)) == 1
        assert any("unrelated history" in line for line in warnings_logged)

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

        assert result.status == "up_to_date"

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

        assert result.status == "auth_failed"
        assert any("rejected the credentials" in line for line in warnings_logged)


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
        assert _wedged_branches(local) == []

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
