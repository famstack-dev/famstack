"""famstack's LaunchAgents for the AI: `host_services` asks launchd, not the files.

A stop that trusts the agent file deletes the file when launchd refused to
stop the job: the job runs on, and a second `stack down ai` finds nothing to
stop. So `stop` asks launchd whether it has the job, stops it by its label,
and removes the file only after launchd let go of it. A refusal is an error.

`run` stands in for the shell: a launchd that remembers its jobs and can be
told to refuse a stop.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stacklets.ai import host_services as hs


class Launchd:
    """The commands `host_services` sends, against jobs it remembers."""

    def __init__(self, jobs=(), refuses=False, load_fails=False):
        self.jobs, self.refuses, self.calls = set(jobs), refuses, []
        self.load_fails = load_fails

    def __call__(self, command: str) -> str:
        self.calls.append(command)
        verb, target = command.split()[1], command.split()[-1]
        label = Path(target).stem if target.endswith(".plist") else target.rsplit("/", 1)[-1]
        if verb == "list":
            if label not in self.jobs:
                raise RuntimeError(command)
            return '{ "PID" = 4242; };'
        if verb in ("remove", "unload"):
            if self.refuses or label not in self.jobs:
                raise RuntimeError(f"{command}: Input/output error")
            self.jobs.discard(label)
        if verb == "load":
            if self.load_fails:
                raise RuntimeError(f"{command}: Load failed: 5: Input/output error")
            self.jobs.add(label)
        if verb == "kickstart" and label not in self.jobs:
            raise RuntimeError(f"{command}: Could not find service")
        return ""


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    hs.agent_file(hs.WHISPER).parent.mkdir(parents=True)
    return tmp_path


def test_a_stop_launchd_refuses_raises_and_keeps_the_file(home):
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

    def broken_load(command, _launchd=launchd):
        if " load " in command and hs.agent_file(hs.OMLX).read_text() == "<plist>second</plist>":
            raise RuntimeError(f"{command}: Load failed")
        return _launchd(command)

    with pytest.raises(RuntimeError):
        hs.start(broken_load, hs.Agent(hs.OMLX, "<plist>second</plist>"))

    assert launchd.jobs == {hs.OMLX}
    assert hs.agent_file(hs.OMLX).read_text() == "<plist>first</plist>"


def test_waiting_for_a_stop_ends_when_the_job_is_gone(home):
    assert hs.wait_until_stopped(Launchd(), hs.OMLX, timeout=1) is True


def test_waiting_for_a_stop_says_when_the_process_still_runs(home):
    assert hs.wait_until_stopped(Launchd(jobs={hs.OMLX}), hs.OMLX, timeout=0.6) is False
