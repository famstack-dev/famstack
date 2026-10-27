"""Which release `stack update` moves to and `stack doctor` announces.

A family on a final release is never offered a pre-release: an instance on
v0.4.1 with v0.5.0-beta.1 tagged is up to date, and only a later final
(v0.4.2) is offered. An instance already on a pre-release is offered the
newest release of all, pre-releases included, so a beta tester moves from
beta to beta and on to the final. Anyone can still move to a pre-release by
naming it: `stack update v0.5.0-beta.1`. "On a pre-release" follows what
`stack version` reports, so a checkout past a tag counts as on that tag.

These tests run `./stack` in this process through `stack.cli.main`, the
function `./stack` calls, against a git checkout built for each case: one
commit per tag, checked out where the case says. The checkout holds no
stacklets, so neither command has a container to look at.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from .conftest import StackCLI, write_instance


# ── A checkout with releases ─────────────────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def _checkout(root: Path, tags: list[str], at: str, commits_past: int = 0) -> Path:
    """A checkout with one release per tag, in that order, sitting on `at`.

    `commits_past` adds that many commits after `at` on a branch, the shape
    of a developer's or a fork's checkout past a release. `stacklets/` is
    what `./stack` looks for to find its repo root.
    """
    (root / "stacklets").mkdir(parents=True)
    _git(root.parent, "init", "-q", str(root))
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    for tag in tags:
        (root / "VERSION").write_text(tag + "\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", f"release {tag}")
        _git(root, "tag", tag)
    _git(root, "checkout", "-q", at)
    if commits_past:
        _git(root, "switch", "-q", "-c", "dev")
        for n in range(commits_past):
            (root / "WORK").write_text(f"{n}\n")
            _git(root, "add", "-A")
            _git(root, "commit", "-qm", f"work {n}")
    return root


@pytest.fixture
def stack_at(tmp_path, monkeypatch, as_stack_runs_it):
    """`./stack` run from a checkout with these tags, on this one."""
    def make(tags: list[str], at: str, commits_past: int = 0) -> StackCLI:
        cli = StackCLI(write_instance(tmp_path), monkeypatch)
        monkeypatch.chdir(_checkout(tmp_path / "checkout", tags, at, commits_past))
        return cli
    return make


def _proposal(ran) -> str:
    """What `update` printed below its header line.

    The header shows the source's version constant next to the checkout
    (`stack 0.5.0-beta.1 (checkout at v0.4.1)`), which here names this
    repo's own version, not anything the command offers.
    """
    return ran.out.split("\n", 2)[2]


def _release_notice(ran) -> str | None:
    """The title of doctor's "a newer release" finding, or None."""
    titles = [f["title"] for f in ran.answer["findings"]]
    return next((t for t in titles if "newer release" in t), None)


# ── On a final release ───────────────────────────────────────────────────

class TestOnAFinalRelease:
    """A family on a final release moves between finals only."""

    def test_a_pre_release_is_not_offered(self, stack_at):
        stack = stack_at(["v0.4.1", "v0.5.0-beta.1"], at="v0.4.1")

        ran = stack("update", "--dry-run")

        assert ran.code == 0, ran.err
        assert "Already on v0.4.1" in ran.out
        assert "beta" not in _proposal(ran)

    def test_doctor_does_not_announce_a_pre_release(self, stack_at):
        stack = stack_at(["v0.4.1", "v0.5.0-beta.1"], at="v0.4.1")

        ran = stack("doctor", "--json")

        assert _release_notice(ran) is None, ran.out

    def test_a_later_final_is_offered(self, stack_at):
        stack = stack_at(["v0.4.1", "v0.5.0-beta.1", "v0.4.2"], at="v0.4.1")

        ran = stack("update", "--dry-run")

        assert ran.code == 0, ran.err
        assert "→ v0.4.2\n" in ran.out

    def test_doctor_announces_a_later_final(self, stack_at):
        stack = stack_at(["v0.4.1", "v0.5.0-beta.1", "v0.4.2"], at="v0.4.1")

        ran = stack("doctor", "--json")

        assert _release_notice(ran) == "a newer release is available: v0.4.2"

    def test_the_final_after_a_beta_cycle_offers_the_next_cycle_no_beta(self, stack_at):
        """v0.5.0 shipped; v0.6.0-beta.1 is for testers only."""
        stack = stack_at(["v0.5.0-beta.1", "v0.5.0", "v0.6.0-beta.1"], at="v0.5.0")

        ran = stack("update", "--dry-run")

        assert "Already on v0.5.0" in ran.out
        assert "v0.6.0" not in ran.out

    def test_a_checkout_past_a_final_counts_as_on_it(self, stack_at):
        """A branch two commits past v0.4.1 runs a final, so no beta."""
        stack = stack_at(["v0.4.1", "v0.5.0-beta.1"], at="v0.4.1", commits_past=2)

        update = stack("update", "--dry-run")
        doctor = stack("doctor", "--json")

        assert "2 commits ahead of v0.4.1, the newest release" in update.out
        assert "beta" not in _proposal(update)
        assert _release_notice(doctor) is None, doctor.out

    def test_naming_a_pre_release_moves_there(self, stack_at):
        """The explicit tag is the opt-in, as today."""
        stack = stack_at(["v0.4.1", "v0.5.0-beta.1"], at="v0.4.1")

        ran = stack("update", "v0.5.0-beta.1", "--dry-run")

        assert ran.code == 0, ran.err
        assert "→ v0.5.0-beta.1" in ran.out


# ── On a pre-release ─────────────────────────────────────────────────────

class TestOnAPreRelease:
    """A beta tester is offered the newest release of all."""

    def test_the_next_beta_is_offered(self, stack_at):
        stack = stack_at(["v0.5.0-beta.1", "v0.5.0-beta.2"], at="v0.5.0-beta.1")

        update = stack("update", "--dry-run")
        doctor = stack("doctor", "--json")

        assert "→ v0.5.0-beta.2" in update.out
        assert _release_notice(doctor) == "a newer release is available: v0.5.0-beta.2"

    def test_the_final_wins_over_the_betas_before_it(self, stack_at):
        stack = stack_at(["v0.5.0-beta.1", "v0.5.0-beta.2", "v0.5.0"], at="v0.5.0-beta.1")

        ran = stack("update", "--dry-run")

        assert "→ v0.5.0\n" in ran.out

    def test_on_the_newest_beta_it_is_up_to_date(self, stack_at):
        """The resting state: nothing newer in reach, both commands say so."""
        stack = stack_at(["v0.4.1", "v0.5.0-beta.1"], at="v0.5.0-beta.1")

        update = stack("update", "--dry-run")
        doctor = stack("doctor", "--json")

        assert "Already on v0.5.0-beta.1" in update.out
        assert _release_notice(doctor) is None, doctor.out
