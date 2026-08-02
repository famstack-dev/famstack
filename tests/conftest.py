"""Git repositories in the states that break a sync loop.

Every git test in this repo used to build the same thing: a healthy
temp repo, cloned once, asserted on the happy path. That is why a
remote which had stopped working — moved host, expired token, history
re-created underneath the clone — could fail every thirty seconds for
an unknown length of time without a single test noticing.

These fixtures build the unhealthy states instead, and live here rather
than beside one stacklet's tests because nothing about them is specific
to memory: any stacklet that keeps a working copy in sync with Forgejo
(the docs mirror, anything that follows) can borrow them as they are.

    git_healthy_clone            local and remote agree
    git_diverged_clone           both sides committed, shared merge base
    git_unrelated_history_clone  remote repo re-created, no merge base
    git_unreachable_remote_clone remote URL points nowhere

Each yields a `GitPair(remote, local)` of paths, and each is a
throwaway copy the test may wreck. `git_commit` is the callable they
are built from, exposed so a test can add its own commits.

Every state is built once per session and copied per test. Spawning
git costs about 150ms a call here, so building four repositories from
scratch for each of a dozen tests is most of a minute of nothing; the
copies are milliseconds. `_build_*` below is still the readable
definition of each state, it just runs once.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest


@dataclass
class GitPair:
    """A bare "server" repo and a working copy cloned from it."""

    remote: Path
    local: Path


def _git(*args: str, cwd: Path | None = None) -> str:
    """Run git, failing the test loudly with git's own words."""
    result = subprocess.run(
        ["git", *args], cwd=str(cwd) if cwd else None,
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _init_working_copy(path: Path) -> None:
    """Give a clone an identity and no signing.

    The developer running the suite may well have commit signing on
    globally; the container these paths actually run in has no global
    config at all. Pinning both here keeps the fixture the same repo on
    either machine.
    """
    _git("config", "user.email", "test@famstack.local", cwd=path)
    _git("config", "user.name", "Test", cwd=path)
    _git("config", "commit.gpgsign", "false", cwd=path)


def _commit(repo: Path, name: str, text: str, message: str) -> str:
    target = Path(repo) / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    _git("add", ".", cwd=repo)
    _git("commit", "-m", message, cwd=repo)
    return _git("rev-parse", "HEAD", cwd=repo)


def _seed_bare(bare: Path, workdir: Path, text: str) -> None:
    """Create a bare repo carrying one root commit on `main`."""
    _git("init", "--bare", "--initial-branch=main", str(bare))
    _git("clone", str(bare), str(workdir))
    _init_working_copy(workdir)
    _commit(workdir, "README.md", text, "seed")
    _git("push", "origin", "main", cwd=workdir)


def _build_healthy(root: Path) -> None:
    """A working copy that agrees with its remote. The baseline."""
    _seed_bare(root / "remote.git", root / "seed", "# vault\n")
    _git("clone", str(root / "remote.git"), str(root / "working-copy"))
    _init_working_copy(root / "working-copy")
    shutil.rmtree(root / "seed")


def _build_diverged(root: Path) -> None:
    """Both sides committed since they last agreed.

    The shape a todo tick makes: someone edited the working copy while
    someone else pushed to Forgejo. There is a merge base, so nothing
    here is unrecoverable — but `pull --ff-only` fails on it forever.
    """
    _build_healthy(root)
    other = root / "other-writer"
    _git("clone", str(root / "remote.git"), str(other))
    _init_working_copy(other)
    _commit(other, "family/documents/filed.md", "filed upstream\n", "learn: filing")
    _git("push", "origin", "main", cwd=other)
    shutil.rmtree(other)

    _commit(
        root / "working-copy", "family/todos.md",
        "- [x] buy duff\n", "todo: tick buy duff",
    )


def _build_recreated_remote(root: Path) -> None:
    """A remote repo re-created from nothing: no commit in common."""
    _seed_bare(root / "remote.git", root / "seed", "# new life\n")
    shutil.rmtree(root / "seed")


@pytest.fixture(scope="session")
def _git_states(tmp_path_factory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("git-states")
    _build_healthy(root / "healthy")
    _build_diverged(root / "diverged")
    _build_recreated_remote(root / "recreated")
    return {
        "healthy": root / "healthy",
        "diverged": root / "diverged",
        "recreated": root / "recreated",
    }


def _checkout(state: Path, into: Path) -> GitPair:
    """Copy a prepared state into a test's own tmp dir."""
    remote, local = into / "remote.git", into / "working-copy"
    shutil.copytree(state / "remote.git", remote)
    shutil.copytree(state / "working-copy", local)
    _git("-C", str(local), "remote", "set-url", "origin", str(remote))
    return GitPair(remote=remote, local=local)


@pytest.fixture
def git_commit():
    """`git_commit(repo, name, text, message=...)` -> the new commit SHA."""
    def _call(repo: Path, name: str, text: str, message: str = "edit") -> str:
        return _commit(repo, name, text, message)

    return _call


@pytest.fixture
def git_healthy_clone(_git_states, tmp_path) -> GitPair:
    return _checkout(_git_states["healthy"], tmp_path)


@pytest.fixture
def git_diverged_clone(_git_states, tmp_path) -> GitPair:
    return _checkout(_git_states["diverged"], tmp_path)


@pytest.fixture
def git_unrelated_history_clone(_git_states, tmp_path) -> GitPair:
    """The clone's remote was wiped and re-created: two histories, no
    merge base, and no amount of rebasing can bridge them."""
    pair = _checkout(_git_states["healthy"], tmp_path)
    recreated = tmp_path / "recreated.git"
    shutil.copytree(_git_states["recreated"] / "remote.git", recreated)
    _git("-C", str(pair.local), "remote", "set-url", "origin", str(recreated))
    return GitPair(remote=recreated, local=pair.local)


@pytest.fixture
def git_unreachable_remote_clone(_git_states, tmp_path) -> GitPair:
    """A remote that no longer answers.

    A LAN address the machine gave up in a DHCP lease behaves like
    this: the clone is intact, its remote is a dead end.
    """
    pair = _checkout(_git_states["healthy"], tmp_path)
    gone = tmp_path / "moved-away.git"
    _git("-C", str(pair.local), "remote", "set-url", "origin", str(gone))
    return GitPair(remote=gone, local=pair.local)
