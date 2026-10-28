"""Write propagation — a committed page reaching the surfaces that show it.

Forgejo is the source of truth and nobody reads it. The agent reads the
brain projection through a read-only mount; the family reads Quartz's
render of that same tree. So a write that stops at Forgejo is invisible
to both until the curator's next poll, which is exactly how "I ticked it
off and nothing changed" happens.

The curator stays brain's only writer (ADR-011), so propagation is a
request and a wait, never a second writer. What is pinned here: the
ancestry check that defines "mirrored", the immediate success when the
mirror is already current, the capped wait, and — the property the write
path leans on — that propagation which never lands still leaves the
write committed. The end-to-end path (a live curator picking the trigger
up) rides the demo rig.

A write only wakes the curator; the pages it touched wait for the quiet
window like a filing's. `stack memory sync` wakes it and asks for those
pages at once. Which of the two a request is, is read here off the
curator's own tick, the way the curator reads it.

`stack memory sync` is otherwise the same two steps with an operator's
patience instead of an agent's, so it is tested here as the thin wrapper
it is.
"""

from __future__ import annotations

import asyncio
import importlib.util
import subprocess
import sys
import threading
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_MEMORY_DIR = _REPO_ROOT / "stacklets" / "memory"
sys.path.insert(0, str(_MEMORY_DIR))
sys.path.insert(0, str(_MEMORY_DIR / "bot"))

import curator  # noqa: E402

from lib import (  # noqa: E402
    MIRROR_SHA_NAME,
    MIRROR_TRIGGER_NAME,
    curator_state_dir_for,
    mirrored_contains,
    propagate_write,
    request_mirror,
    vault_path_for,
    wait_for_mirror,
)

_SPEC = importlib.util.spec_from_file_location(
    "memory_cli_sync", _MEMORY_DIR / "cli" / "sync.py",
)
assert _SPEC and _SPEC.loader
memory_sync = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(memory_sync)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _commit(repo: Path, name: str, body: str) -> str:
    (repo / name).write_text(body, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", f"write {name}")
    return _git(repo, "rev-parse", "HEAD")


def _init(repo: Path) -> None:
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git(repo, "config", "user.email", "t@local")
    _git(repo, "config", "user.name", "t")


def _repo_with_two_commits(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "memory"
    _init(repo)
    return repo, _commit(repo, "a.md", "a"), _commit(repo, "b.md", "b")


def _data_dir_with_vault(tmp_path: Path) -> tuple[Path, Path, str]:
    """A stack data dir whose memory clone holds one committed page."""
    data_dir = tmp_path / "data"
    vault = vault_path_for(data_dir)
    _init(vault)
    return data_dir, vault, _commit(vault, "a.md", "a")


def _curator_tick(data_dir: Path) -> str:
    """How the curator's next tick starts, given the requests waiting for it.

    "forced": it regenerates the touched pages at once, past the quiet
    window. "woken": it mirrors at once and leaves the pages to the quiet
    window. "timed": nothing asked, it woke when its poll ran out.
    """
    state = curator_state_dir_for(data_dir)
    started = time.monotonic()
    forced = asyncio.run(curator.sleep_until_tick(0.5, state, slice_secs=0.01))
    if forced:
        return "forced"
    return "woken" if time.monotonic() - started < 0.25 else "timed"


class TestRequestMirror:
    def test_writes_trigger_and_creates_state_dir(self, tmp_path):
        state = tmp_path / "curator"
        trigger = request_mirror(state)
        assert trigger == state / MIRROR_TRIGGER_NAME
        assert trigger.exists()


class TestMirroredContains:
    def test_equal_sha_is_mirrored(self, tmp_path):
        repo, first, _ = _repo_with_two_commits(tmp_path)
        assert mirrored_contains(repo, first, first) is True

    def test_descendant_mirror_contains_older_target(self, tmp_path):
        repo, first, second = _repo_with_two_commits(tmp_path)
        assert mirrored_contains(repo, first, second) is True

    def test_older_mirror_does_not_contain_newer_target(self, tmp_path):
        repo, first, second = _repo_with_two_commits(tmp_path)
        assert mirrored_contains(repo, second, first) is False

    def test_unknown_or_empty_sha_is_not_yet(self, tmp_path):
        repo, first, _ = _repo_with_two_commits(tmp_path)
        assert mirrored_contains(repo, first, "") is False
        assert mirrored_contains(repo, first, "f" * 40) is False


class TestWaitForMirror:
    def test_already_current_succeeds_with_zero_timeout(self, tmp_path):
        repo, _, second = _repo_with_two_commits(tmp_path)
        state = tmp_path / "curator"
        state.mkdir()
        (state / MIRROR_SHA_NAME).write_text(second, encoding="utf-8")
        assert wait_for_mirror(state, repo, second, timeout=0) == second

    def test_waits_for_curator_to_record_the_mirror(self, tmp_path):
        repo, _, second = _repo_with_two_commits(tmp_path)
        state = tmp_path / "curator"
        state.mkdir()
        (state / MIRROR_SHA_NAME).write_text("", encoding="utf-8")

        def record():
            (state / MIRROR_SHA_NAME).write_text(second, encoding="utf-8")

        timer = threading.Timer(0.05, record)
        timer.start()
        try:
            got = wait_for_mirror(state, repo, second, timeout=2, interval=0.02)
        finally:
            timer.join()
        assert got == second

    def test_times_out_when_mirror_never_lands(self, tmp_path):
        repo, first, second = _repo_with_two_commits(tmp_path)
        state = tmp_path / "curator"
        state.mkdir()
        (state / MIRROR_SHA_NAME).write_text(first, encoding="utf-8")
        assert wait_for_mirror(state, repo, second, timeout=0.05, interval=0.02) is None


class TestPropagateWrite:
    """What a just-committed write does to make itself visible."""

    def test_a_mirror_already_carrying_the_write_reports_propagated(self, tmp_path):
        data_dir, vault, head = _data_dir_with_vault(tmp_path)
        state = curator_state_dir_for(data_dir)
        state.mkdir(parents=True)
        (state / MIRROR_SHA_NAME).write_text(head, encoding="utf-8")
        assert propagate_write(data_dir, timeout=0) is True

    def test_it_asks_the_curator_rather_than_writing_brain_itself(self, tmp_path):
        # ADR-011: the curator is brain's only writer. Propagation drops a
        # request and waits; it must never touch the projection directly.
        data_dir, _, _ = _data_dir_with_vault(tmp_path)
        propagate_write(data_dir, timeout=0.05, interval=0.02)
        assert _curator_tick(data_dir) != "timed"
        assert not (data_dir / "memory" / "brain").exists()

    def test_a_write_leaves_its_pages_to_the_quiet_window(self, tmp_path):
        # The agent writes several times in one turn and waits on the same
        # AI server the pages are regenerated with. A write that forced
        # the regeneration put a minutes-long model call between the agent
        # and its next answer, and kept the curator from mirroring the
        # writes that followed.
        data_dir, _, _ = _data_dir_with_vault(tmp_path)
        propagate_write(data_dir, timeout=0.05, interval=0.02)
        assert _curator_tick(data_dir) == "woken"

    def test_a_write_leaves_no_request_behind_once_the_curator_woke(self, tmp_path):
        data_dir, _, _ = _data_dir_with_vault(tmp_path)
        propagate_write(data_dir, timeout=0.05, interval=0.02)
        _curator_tick(data_dir)
        assert _curator_tick(data_dir) == "timed"

    def test_a_stalled_curator_costs_the_write_nothing(self, tmp_path):
        # The write is already committed to Forgejo by the time we get
        # here. A curator that is down, or busy in a nightly sweep, means
        # the wiki catches up later — never that the write failed.
        data_dir, _, _ = _data_dir_with_vault(tmp_path)
        assert propagate_write(data_dir, timeout=0.05, interval=0.02) is False

    def test_an_unclonable_vault_is_simply_not_propagated(self, tmp_path):
        # Nothing to name as the target, so there is nothing to wait for.
        # Still no exception: propagation never speaks for the write.
        assert propagate_write(tmp_path / "empty", timeout=0.05) is False


class TestRunGuards:
    def test_missing_data_dir_errors(self):
        assert "error" in memory_sync.run(None, {}, {})

    def test_uncloned_vault_errors(self, tmp_path):
        result = memory_sync.run(None, {}, {"data_dir": str(tmp_path)})
        assert "error" in result and "not cloned" in result["error"]


class TestSyncAsksForThePages:
    def test_sync_still_has_the_curator_regenerate_the_pages_at_once(self, tmp_path):
        # The operator's way to a current wiki now, and the test rig's.
        data_dir, _, head = _data_dir_with_vault(tmp_path)
        state = curator_state_dir_for(data_dir)
        state.mkdir(parents=True)
        (state / MIRROR_SHA_NAME).write_text(head, encoding="utf-8")

        assert memory_sync.run(None, {}, {"data_dir": str(data_dir)})["mirrored"] == head
        assert _curator_tick(data_dir) == "forced"
