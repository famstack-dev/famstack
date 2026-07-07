"""`stack memory sync` — trigger + wait, curator stays brain's only writer.

The command owns no mirror logic: it drops the trigger file and polls
`last-mirrored-sha` against the memory clone's history. What is worth
pinning here is the wait machinery: the ancestry check that defines
"mirrored", the immediate success on an already-current mirror, and a
fast, capped timeout. The end-to-end path (trigger picked up by a live
curator) rides the demo rig.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import threading
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MEMORY_DIR = _REPO_ROOT / "stacklets" / "memory"
sys.path.insert(0, str(_MEMORY_DIR))

_SPEC = importlib.util.spec_from_file_location(
    "memory_cli_sync", _MEMORY_DIR / "cli" / "sync.py",
)
assert _SPEC and _SPEC.loader
memory_sync = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(memory_sync)

MIRROR_SHA_NAME = memory_sync.MIRROR_SHA_NAME
TRIGGER_NAME = memory_sync.TRIGGER_NAME
mirrored_contains = memory_sync.mirrored_contains
request_mirror = memory_sync.request_mirror
wait_for_mirror = memory_sync.wait_for_mirror


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _repo_with_two_commits(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "memory"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git(repo, "config", "user.email", "t@local")
    _git(repo, "config", "user.name", "t")
    (repo / "a.md").write_text("a", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "one")
    first = _git(repo, "rev-parse", "HEAD")
    (repo / "b.md").write_text("b", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "two")
    second = _git(repo, "rev-parse", "HEAD")
    return repo, first, second


class TestRequestMirror:
    def test_writes_trigger_and_creates_state_dir(self, tmp_path):
        state = tmp_path / "curator"
        trigger = request_mirror(state)
        assert trigger == state / TRIGGER_NAME
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


class TestRunGuards:
    def test_missing_data_dir_errors(self):
        assert "error" in memory_sync.run(None, {}, {})

    def test_uncloned_vault_errors(self, tmp_path):
        result = memory_sync.run(None, {}, {"data_dir": str(tmp_path)})
        assert "error" in result and "not cloned" in result["error"]
