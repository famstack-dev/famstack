"""Bringing the wiki up to date now, from the CLI.

The curator works on its own clock: after a filing it waits for a quiet
window before regenerating the pages the change touched, and once a night it
sweeps everything (diary, source reconcile, every page). A person checking a
filing, or an agent driving a scenario, cannot wait for either. So each has a
command that asks for it now and waits until it is done:

- `stack memory wiki update`: the cycle after a filing, pages included.
- `stack memory wiki update --all`: tonight's rebuild, the whole wiki.

`stack memory sync` stops earlier, at the mirror.

The curator stays the brain's only writer (ADR-011): a command asks the
curator's API for the commit its clone stands at, drops a trigger and waits
for the record the curator leaves. The API is the curator's own, over a
clone of a repository that plays Forgejo (`memory_git.running_memory`); a
thread plays its mirror and pages, answering that protocol: triggers in,
record files out.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

_MEMORY_DIR = Path(__file__).resolve().parents[3] / "stacklets" / "memory"
sys.path.insert(0, str(_MEMORY_DIR.parent))
sys.path.insert(0, str(Path(__file__).parent))

from stacklets.memory.lib import (  # noqa: E402
    MIRROR_SHA_NAME,
    MIRROR_TRIGGER_NAME,
    NIGHTLY_RUN_NAME,
    NIGHTLY_TRIGGER_NAME,
    REBUILT_SHA_NAME,
    curator_state_dir_for,
    progress_since,
    record_failed_rebuild,
    report_progress,
    request_nightly,
    wait_for_nightly,
)
from memory_git import RunningMemory, git, running_memory  # noqa: E402


def _command(name: str):
    from stack.stacklet_code import load
    module = load(_MEMORY_DIR, _MEMORY_DIR / "cli" / f"{name}.py")
    return module


@pytest.fixture
def memory(tmp_path) -> Iterator[RunningMemory]:
    """The memory as the commands reach it: Forgejo, the curator's clone in
    the data dir, and the curator's API behind core's door. The curator's
    state dir is there, as the curator makes it when it starts."""
    with running_memory(tmp_path / "data", {"README.md": "# The Simpsons\n"}) as running:
        curator_state_dir_for(Path(running.config["data_dir"])).mkdir(parents=True)
        yield running


def _at(memory: RunningMemory) -> tuple[Path, str]:
    """The data dir, and the commit the curator's clone stands at."""
    return Path(memory.config["data_dir"]), git(memory.vault, "rev-parse", "HEAD")


def _curator(state: Path, answer) -> threading.Thread:
    """The curator's tick: once a command has asked (its trigger is there),
    it does the work, which `answer` plays."""
    def tick():
        deadline = time.monotonic() + 10
        while not (state / MIRROR_TRIGGER_NAME).exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        answer()
    thread = threading.Thread(target=tick)
    thread.start()
    return thread


class TestTheCycleAfterAFiling:

    def test_update_waits_until_the_touched_pages_are_regenerated(self, memory):
        data_dir, head = _at(memory)
        state = curator_state_dir_for(data_dir)

        def curator():
            (state / MIRROR_SHA_NAME).write_text(head)
            time.sleep(0.1)
            (state / REBUILT_SHA_NAME).write_text(head)

        tick = _curator(state, curator)
        result = _command("wiki").run(["update"], {}, memory.config)
        tick.join()

        assert result == {"mirrored": head, "rebuilt": head, "target": head}

    def test_pages_that_never_come_are_named_as_such(self, memory, monkeypatch):
        data_dir, head = _at(memory)
        state = curator_state_dir_for(data_dir)
        (state / MIRROR_SHA_NAME).write_text(head)
        wiki = _command("wiki")
        monkeypatch.setattr(sys.modules["stacklets.memory.cli._curator"], "PAGES_WAIT_SECS", 0.1)

        result = wiki.run(["update"], {}, memory.config)

        assert "wiki pages" in result["error"]

    def test_a_failed_rebuild_ends_the_wait_and_says_when_it_retries(self, memory, monkeypatch):
        # The usual cause is the AI refusing or being down. Waiting out the
        # full timeout then says nothing a person can act on.
        data_dir, head = _at(memory)
        state = curator_state_dir_for(data_dir)
        wiki = _command("wiki")
        monkeypatch.setattr(sys.modules["stacklets.memory.cli._curator"], "PAGES_WAIT_SECS", 30)

        def curator():
            (state / MIRROR_SHA_NAME).write_text(head)
            record_failed_rebuild(state, retry_in=360)

        tick = _curator(state, curator)
        started = time.monotonic()
        result = wiki.run(["update"], {}, memory.config)
        tick.join()

        assert time.monotonic() - started < 5
        assert "failed" in result["error"] and "6 minutes" in result["error"]

    def test_a_failure_from_before_the_request_does_not_end_the_wait(self, memory):
        data_dir, head = _at(memory)
        state = curator_state_dir_for(data_dir)
        record_failed_rebuild(state, retry_in=360)
        time.sleep(0.01)

        def curator():
            (state / MIRROR_SHA_NAME).write_text(head)
            time.sleep(0.1)
            (state / REBUILT_SHA_NAME).write_text(head)

        tick = _curator(state, curator)
        result = _command("wiki").run(["update"], {}, memory.config)
        tick.join()

        assert result == {"mirrored": head, "rebuilt": head, "target": head}

    def test_sync_stops_at_the_mirror(self, memory):
        data_dir, head = _at(memory)
        state = curator_state_dir_for(data_dir)
        (state / MIRROR_SHA_NAME).write_text(head)

        result = _command("sync").run([], {}, memory.config)

        assert result == {"mirrored": head, "target": head}


class TestTheWholeWikiNow:

    def test_asking_wakes_the_curator_and_names_the_sweep(self, tmp_path):
        state = tmp_path / "curator"

        request_nightly(state)

        assert (state / NIGHTLY_TRIGGER_NAME).exists()
        assert (state / MIRROR_TRIGGER_NAME).exists()  # the tick wakes within a second

    def test_a_sweep_that_finished_before_asking_does_not_count(self, tmp_path):
        state = tmp_path / "curator"
        state.mkdir()
        (state / NIGHTLY_RUN_NAME).write_text(json.dumps({"at": time.time() - 3600, "ok": True}))

        assert wait_for_nightly(state, time.time(), timeout=0.1, interval=0.02) is None

    def test_update_all_waits_for_the_rebuild_and_says_how_long_it_took(self, memory, monkeypatch):
        data_dir, _ = _at(memory)
        state = curator_state_dir_for(data_dir)

        def curator():
            (state / NIGHTLY_RUN_NAME).write_text(json.dumps({"at": time.time(), "ok": True, "asked": True}))

        wiki = _command("wiki")
        monkeypatch.setattr(sys.modules["stacklets.memory.cli._curator"], "wait_for_nightly",
                            lambda s, asked, timeout, on_poll: wait_for_nightly(
                                s, asked, timeout=2, interval=0.02, on_poll=on_poll))
        tick = _curator(state, curator)
        result = wiki.run(["update", "--all"], {}, memory.config)
        tick.join()

        assert result["ok"] is True and result["seconds"] >= 0

    def test_a_sweep_whose_pages_failed_is_an_error(self, memory, monkeypatch):
        data_dir, _ = _at(memory)
        state = curator_state_dir_for(data_dir)

        def curator():
            (state / NIGHTLY_RUN_NAME).write_text(json.dumps({"at": time.time(), "ok": False}))

        wiki = _command("wiki")
        monkeypatch.setattr(sys.modules["stacklets.memory.cli._curator"], "wait_for_nightly",
                            lambda s, asked, timeout, on_poll: wait_for_nightly(
                                s, asked, timeout=2, interval=0.02, on_poll=on_poll))
        tick = _curator(state, curator)
        result = wiki.run(["update", "--all"], {}, memory.config)
        tick.join()

        assert "failed" in result["error"]


class TestTheWikiCommand:
    """`wiki` is a noun: its verbs say what happens to the wiki."""

    def test_bare_wiki_lists_its_verbs(self):
        assert set(_command("wiki").run([], {}, {})["commands"]) == {"update", "clean"}

    def test_an_unknown_verb_names_the_known_ones(self):
        error = _command("wiki").run(["rebuild"], {}, {})["error"]
        assert "update" in error and "clean" in error

    def test_all_and_a_page_selection_contradict_each_other(self):
        error = _command("wiki").run(["update", "--all", "--member", "homer"], {}, {})["error"]
        assert "--all" in error and "--member" in error


class TestProgressWhileWaiting:
    """A command waiting on the curator prints what it does, as it does it."""

    def test_update_prints_each_page_the_curator_reports(self, memory, capsys):
        data_dir, head = _at(memory)
        state = curator_state_dir_for(data_dir)

        def curator():
            (state / MIRROR_SHA_NAME).write_text(head)
            report_progress(state, "regenerating the pages new filings touched", fresh=True)
            report_progress(state, "published homer/about.md")
            time.sleep(0.8)  # one poll of the waiting command sees the lines first
            (state / REBUILT_SHA_NAME).write_text(head)

        tick = _curator(state, curator)
        _command("wiki").run(["update"], {}, memory.config)
        tick.join()

        out = capsys.readouterr().out
        assert out.count("published homer/about.md") == 1
        assert out.index("regenerating") < out.index("published homer/about.md")

    def test_steps_from_before_the_request_are_not_shown(self, tmp_path):
        state = tmp_path / "curator"
        state.mkdir()
        report_progress(state, "yesterday's rebuild")
        asked = time.time()
        report_progress(state, "today's rebuild")

        assert progress_since(state, asked) == ["today's rebuild"]
