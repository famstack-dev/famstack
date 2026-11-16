"""famstack's LaunchAgents for the AI: `host_services` asks launchd, not the files.

A stop that trusts the agent file deletes the file when launchd refused to
stop the job: the job runs on, and a second `stack down ai` finds nothing to
stop. So `stop` asks launchd whether it has the job, stops it by its label,
and removes the file only after launchd let go of it. A refusal is an error.

A start reads launchd's answer too: `launchctl load` exits 0 when launchd
refuses the agent, and says why only in its output. So `start` asks
launchd afterwards whether it has the job, and raises with that output.

`run` stands in for the shell: a launchd that remembers its jobs and can be
told to refuse a stop, or a load, as a real one refuses it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stack import launchd
from stacklets.ai import host_services as hs


class Launchd:
    """The commands `host_services` sends, against jobs it remembers."""

    def __init__(self, jobs=(), refuses=False, load_fails=False):
        self.jobs, self.refuses, self.calls = set(jobs), refuses, []
        self.load_fails = load_fails

    def __call__(self, command: str, timeout: float | None = None) -> str:
        self.calls.append(command)
        if command.startswith("launchctl list "):
            # The list command turns launchd's "no such job" (exit 113)
            # into a line of its output (`launchd.listed`).
            label = command.split()[2]
            return '{ "PID" = 4242; };' if label in self.jobs else launchd.NOT_LISTED
        # The shell takes the redirection of a load's output, not launchctl.
        words = [w for w in command.split() if w != "2>&1"]
        verb, target = words[1], words[-1]
        label = Path(target).stem if target.endswith(".plist") else target.rsplit("/", 1)[-1]
        if verb in ("remove", "unload"):
            if self.refuses or label not in self.jobs:
                raise RuntimeError(f"{command}: Input/output error")
            self.jobs.discard(label)
        if verb == "load":
            # launchd refuses a load with exit code 0, in words only.
            if self.load_fails:
                return "Load failed: 5: Input/output error"
            self.jobs.add(label)
        if verb == "kickstart" and label not in self.jobs:
            raise RuntimeError(f"{command}: Could not find service")
        return ""


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    hs.agent_file(hs.WHISPER).parent.mkdir(parents=True)
    return tmp_path


def test_a_stop_launchd_refuses_raises_and_keeps_the_file(home, monkeypatch):
    """A refused stop waits `STOP_SECONDS` for the job to leave, as one
    that is already leaving does; the job stays, so the stop fails."""
    from stack import launchd as launchd_module
    monkeypatch.setattr(launchd_module, "STOP_SECONDS", 0.2)
    hs.agent_file(hs.WHISPER).write_text("<plist/>")
    launchd = Launchd(jobs={hs.WHISPER}, refuses=True)

    with pytest.raises(RuntimeError):
        hs.stop(launchd, hs.WHISPER)

    assert hs.agent_file(hs.WHISPER).exists()
    assert hs.up(launchd) == {hs.WHISPER}


def test_a_job_without_its_file_is_stopped_by_its_label(home):
    launchd = Launchd(jobs={hs.WHISPER})

    assert hs.stop(launchd, hs.WHISPER) is True

    assert launchd.jobs == set()


def test_a_stop_removes_the_file_after_launchd_let_go(home):
    hs.agent_file(hs.OMLX).write_text("<plist/>")
    launchd = Launchd(jobs={hs.OMLX})

    assert hs.stop(launchd, hs.OMLX) is True

    assert launchd.jobs == set() and not hs.agent_file(hs.OMLX).exists()


def test_nothing_to_stop_changes_nothing(home):
    launchd = Launchd()

    assert hs.stop(launchd, hs.OMLX) is False
    assert [c for c in launchd.calls if " list " not in c] == []


def test_up_names_what_launchd_has_and_what_login_would_load(home):
    hs.agent_file(hs.OMLX).write_text("<plist/>")

    assert hs.up(Launchd(jobs={hs.WHISPER})) == {hs.OMLX, hs.WHISPER}


# ── A restart keeps the service ──────────────────────────────────────────

def test_a_restart_for_new_settings_keeps_the_job_loaded(home):
    """A restart that oMLX needs to read its settings again never takes the
    job out of launchd, so a failure cannot leave no AI behind."""
    agent = hs.Agent(hs.OMLX, "<plist>first</plist>")
    launchd = Launchd()
    hs.start(launchd, agent)
    launchd.load_fails = True

    assert hs.start(launchd, agent, changed=True) == "restarted"

    assert launchd.jobs == {hs.OMLX}
    assert not [c for c in launchd.calls if " unload " in c]


def test_a_new_agent_that_does_not_load_puts_the_old_one_back(home):
    """A changed agent file needs unload and load. When the load fails,
    the agent that ran before is written and loaded again."""
    launchd = Launchd()
    hs.start(launchd, hs.Agent(hs.OMLX, "<plist>first</plist>"))

    def broken_load(command, timeout=None, _launchd=launchd):
        if " load " in command and hs.agent_file(hs.OMLX).read_text() == "<plist>second</plist>":
            _launchd.calls.append(command)
            return "Load failed: 5: Input/output error"
        return _launchd(command)

    with pytest.raises(RuntimeError):
        hs.start(broken_load, hs.Agent(hs.OMLX, "<plist>second</plist>"))

    assert launchd.jobs == {hs.OMLX}
    assert hs.agent_file(hs.OMLX).read_text() == "<plist>first</plist>"


def test_a_load_launchd_refuses_fails_the_start_with_launchds_words(home):
    """Given launchd cannot load the agent, then the start fails with the
    reason launchd gave and never reports the service as loaded."""
    launchd = Launchd(load_fails=True)

    with pytest.raises(RuntimeError, match="Load failed: 5"):
        hs.start(launchd, hs.Agent(hs.WHISPER, "<plist/>"))

    assert launchd.jobs == set()
