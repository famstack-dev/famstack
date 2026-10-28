"""The compile's record of its model calls, as a file on disk, and the log
level of memory's entry point.

The record's content is tested where the compile makes it
(`test_memory_wiki_call_usage.py`, `test_memory_diary_compile.py`). Here:
that the file stays bounded, and that a `LOG_LEVEL` an admin sets in any
case, or mistypes, never stops a memory command. The entry point runs as a
process of its own, as `docker exec` starts it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from stack.ai.client import CallCost
from stacklets.memory.bot import call_costs
from tests.springfield import REPO

ENTRYPOINT = REPO / "stacklets" / "memory" / "bot" / "cli_entrypoint.py"


class TestTheRecordStaysBounded:
    def test_a_full_record_moves_aside_once_and_a_new_one_starts(self, tmp_path, monkeypatch):
        """Given a record file past its size, when the next call is recorded,
        then the old record is kept as `<file>.1` and the new file holds
        only the new call.

        The record must never fill the data dir, and the newest calls are
        the ones a comparison needs.
        """
        log = tmp_path / "wiki-calls.jsonl"
        log.write_text("x" * (call_costs.ROTATE_BYTES + 1), encoding="utf-8")
        monkeypatch.setenv(call_costs.CALLS_LOG, str(log))

        call_costs.recorder("view", "index.md")(CallCost(estimated_tokens=10, seconds=0.5))

        assert (tmp_path / "wiki-calls.jsonl.1").stat().st_size == call_costs.ROTATE_BYTES + 1
        [call] = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        assert (call["kind"], call["page"]) == ("view", "index.md")


def _entrypoint(log_level: str) -> subprocess.CompletedProcess:
    """Memory's entry point with no command: it prints its usage and exits 2."""
    env = {**os.environ, "PYTHONPATH": str(REPO / "lib"), "LOG_LEVEL": log_level}
    return subprocess.run([sys.executable, str(ENTRYPOINT)], capture_output=True, text=True,
                          env=env, timeout=30)


class TestTheLogLevel:
    @pytest.mark.parametrize("level", ["debug", "Info", "WARNING"])
    def test_a_level_in_any_case_is_taken(self, level):
        """Given `LOG_LEVEL` in any case, when a memory command starts,
        then it runs as without one.

        Admins write `debug` as often as `DEBUG`.
        """
        done = _entrypoint(level)

        assert done.returncode == 2, done.stderr
        assert "LOG_LEVEL" not in done.stderr

    def test_an_unknown_level_is_named_and_the_command_runs(self):
        """Given a mistyped `LOG_LEVEL`, when a memory command starts, then it
        names the variable once and runs at INFO.

        A typo in one setting must not stop every memory command, the
        nightly sweep among them.
        """
        done = _entrypoint("verbose")

        assert done.returncode == 2, done.stderr
        assert done.stderr.count("LOG_LEVEL") == 1
