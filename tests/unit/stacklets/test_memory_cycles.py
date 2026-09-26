"""Bringing the wiki up to date now, from the CLI.

The curator works on its own clock: after a filing it waits for a quiet
window before regenerating the pages the change touched, and once a night it
sweeps everything (diary, source reconcile, every page). A person checking a
filing, or an agent driving a scenario, cannot wait for either. So each has a
command that asks for it now and waits until it is done:

- `stack memory wiki update`: the cycle after a filing, pages included.
- `stack memory wiki update --all`: tonight's rebuild, the whole wiki.

`stack memory sync` stops earlier, at the mirror.

The curator stays the brain's only writer (ADR-011): a command drops a
trigger and waits for the record the curator leaves. Here a thread plays the
curator, answering exactly that protocol: triggers in, record files out.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

_MEMORY_DIR = Path(__file__).resolve().parents[3] / "stacklets" / "memory"
sys.path.insert(0, str(_MEMORY_DIR))

from lib import (  # noqa: E402
    MIRROR_SHA_NAME,
    MIRROR_TRIGGER_NAME,
    NIGHTLY_RUN_NAME,
    NIGHTLY_TRIGGER_NAME,
    REBUILT_SHA_NAME,
    curator_state_dir_for,
    progress_since,
    report_progress,
    request_nightly,
    vault_path_for,
    wait_for_nightly,
)


def _command(name: str):
    spec = importlib.util.spec_from_file_location(f"memory_cli_{name}", _MEMORY_DIR / "cli" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _vault(tmp_path: Path) -> tuple[Path, str]:
    """A data dir whose memory clone holds one commit; its HEAD."""
    data_dir = tmp_path / "data"
    vault = vault_path_for(data_dir)
    vault.mkdir(parents=True)
    run = lambda *a: subprocess.run(["git", "-C", str(vault), *a], check=True, capture_output=True, text=True)  # noqa: E731
    run("init", "-q")
    run("-c", "user.email=t@local", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "filed")
    return data_dir, run("rev-parse", "HEAD").stdout.strip()


def _curator(state: Path, answer, delay: float = 0.05) -> threading.Timer:
    timer = threading.Timer(delay, answer)
    timer.start()
    return timer


class TestTheCycleAfterAFiling:

    def test_update_waits_until_the_touched_pages_are_regenerated(self, tmp_path):
        data_dir, head = _vault(tmp_path)
        state = curator_state_dir_for(data_dir)

        def curator():
            (state / MIRROR_SHA_NAME).write_text(head)
            time.sleep(0.1)
            (state / REBUILT_SHA_NAME).write_text(head)

        timer = _curator(state, curator)
        result = _command("wiki").run(["update"], {}, {"data_dir": str(data_dir)})
        timer.join()

        assert result == {"mirrored": head, "rebuilt": head, "target": head}

    def test_pages_that_never_come_are_named_as_such(self, tmp_path, monkeypatch):
        data_dir, head = _vault(tmp_path)
        state = curator_state_dir_for(data_dir)
        state.mkdir(parents=True)
        (state / MIRROR_SHA_NAME).write_text(head)
        wiki = _command("wiki")
        monkeypatch.setattr(sys.modules["_curator"], "PAGES_WAIT_SECS", 0.1)

        result = wiki.run(["update"], {}, {"data_dir": str(data_dir)})

        assert "wiki pages" in result["error"]

    def test_sync_stops_at_the_mirror(self, tmp_path):
        data_dir, head = _vault(tmp_path)
        state = curator_state_dir_for(data_dir)
        state.mkdir(parents=True)
        (state / MIRROR_SHA_NAME).write_text(head)

        result = _command("sync").run([], {}, {"data_dir": str(data_dir)})

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

    def test_update_all_waits_for_the_rebuild_and_says_how_long_it_took(self, tmp_path, monkeypatch):
        data_dir, _ = _vault(tmp_path)
        state = curator_state_dir_for(data_dir)

        def curator():
            (state / NIGHTLY_RUN_NAME).write_text(json.dumps({"at": time.time(), "ok": True, "asked": True}))

        wiki = _command("wiki")
        monkeypatch.setattr(sys.modules["_curator"], "wait_for_nightly",
                            lambda s, asked, timeout, on_poll: wait_for_nightly(
                                s, asked, timeout=2, interval=0.02, on_poll=on_poll))
        timer = _curator(state, curator)
        result = wiki.run(["update", "--all"], {}, {"data_dir": str(data_dir)})
        timer.join()

        assert result["ok"] is True and result["seconds"] >= 0

    def test_a_sweep_whose_pages_failed_is_an_error(self, tmp_path, monkeypatch):
        data_dir, _ = _vault(tmp_path)
        state = curator_state_dir_for(data_dir)

        def curator():
            (state / NIGHTLY_RUN_NAME).write_text(json.dumps({"at": time.time(), "ok": False}))

        wiki = _command("wiki")
        monkeypatch.setattr(sys.modules["_curator"], "wait_for_nightly",
                            lambda s, asked, timeout, on_poll: wait_for_nightly(
                                s, asked, timeout=2, interval=0.02, on_poll=on_poll))
        timer = _curator(state, curator)
        result = wiki.run(["update", "--all"], {}, {"data_dir": str(data_dir)})
        timer.join()

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

    def test_update_prints_each_page_the_curator_reports(self, tmp_path, capsys):
        data_dir, head = _vault(tmp_path)
        state = curator_state_dir_for(data_dir)

        def curator():
            (state / MIRROR_SHA_NAME).write_text(head)
            report_progress(state, "regenerating the pages new filings touched", fresh=True)
            report_progress(state, "published homer/about.md")
            time.sleep(0.8)  # one poll of the waiting command sees the lines first
            (state / REBUILT_SHA_NAME).write_text(head)

        timer = _curator(state, curator)
        _command("wiki").run(["update"], {}, {"data_dir": str(data_dir)})
        timer.join()

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
