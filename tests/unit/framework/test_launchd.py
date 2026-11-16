"""`stack.launchd`: stop and load a LaunchAgent by launchd's answer, not its exit code.

launchd lists a stopped job until its process has ended, and a `launchctl
load` of the same label in that moment loads nothing and exits 0. So a
stop returns only once launchd let the job go, and a load fails when
launchd does not have the job afterwards.

The commands go through a shell runner, as a hook's `ctx.shell` runs them,
to `launchctl` stood in first on PATH: a launchd that remembers its jobs
and lets a stopped one go as a real one does (`launchd_stand_in.py`).
"""

from __future__ import annotations

import subprocess
import time

import pytest

from stack import launchd

from tests.unit.stacklets.launchd_stand_in import launchd as launchd_stand_in

LABEL = "dev.famstack.api"


def shell(command: str, timeout: float | None = None) -> str:
    """A runner as `ctx.shell` is: stdout, or RuntimeError on a failure."""
    done = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout)
    if done.returncode:
        raise RuntimeError(f"{command}: {done.stderr.strip()}")
    return done.stdout


@pytest.fixture
def mac(tmp_path, monkeypatch):
    """launchd's jobs folder and the agent file of `LABEL`."""
    _bin, jobs = launchd_stand_in(tmp_path, monkeypatch)
    agent = tmp_path / "LaunchAgents" / f"{LABEL}.plist"
    agent.parent.mkdir()
    agent.write_text("<plist/>")
    return jobs, agent


def test_a_stop_returns_once_launchd_let_the_job_go(mac):
    """Given launchd runs the job, when it is stopped, then launchd no
    longer lists it, and a load right after it loads the job again."""
    _jobs, agent = mac
    launchd.load(shell, agent)

    assert launchd.stop(shell, LABEL) is True

    assert not launchd.loaded(shell, LABEL)
    launchd.load(shell, agent)
    assert launchd.loaded(shell, LABEL)


def test_a_stop_of_a_job_launchd_does_not_have_changes_nothing(mac):
    assert launchd.stop(shell, LABEL) is False


def test_a_load_into_a_job_that_is_still_leaving_raises(mac):
    """Given a job that is still leaving launchd, when its agent is loaded,
    then the load fails and loads nothing: launchd would refuse it with
    exit code 0 and go on listing the leaving job."""
    jobs, agent = mac
    (jobs / f"{LABEL}.leaving").write_text("5")

    with pytest.raises(RuntimeError, match=LABEL):
        launchd.load(shell, agent)

    assert not (jobs / LABEL).exists()


def test_a_load_launchd_refuses_raises_with_launchds_words(mac, monkeypatch):
    """Given launchd refuses the agent (its words, exit code 0), then the
    load fails with those words."""
    _jobs, agent = mac
    refusing = agent.parent / "refusing-bin"
    refusing.mkdir()
    (refusing / "launchctl").write_text(
        '#!/bin/sh\n[ "$1" = "list" ] && exit 113\n'
        'echo "Load failed: 5: Input/output error" >&2\nexit 0\n')
    (refusing / "launchctl").chmod(0o755)
    monkeypatch.setenv("PATH", f"{refusing}:/usr/bin:/bin")

    with pytest.raises(RuntimeError, match="Load failed: 5"):
        launchd.load(shell, agent)


def test_waiting_for_a_stop_ends_when_the_job_is_gone(mac):
    assert launchd.wait_until_stopped(shell, LABEL, timeout=1) is True


def test_waiting_for_a_stop_says_when_the_process_still_runs(mac):
    """launchd names a PID while the process runs; after the timeout the
    wait gives up and says so."""
    jobs, _agent = mac
    (jobs / LABEL).touch()

    assert launchd.wait_until_stopped(shell, LABEL, timeout=0.6) is False


def test_a_launchctl_that_hangs_fails_within_seconds(mac, monkeypatch):
    """Given `launchctl` does not answer, when a hook loads a job through
    its context's shell, then it fails with `RuntimeError`
    after `launchd.LAUNCHCTL_SECONDS`, not after the shell's own limit of
    twenty minutes, and a stop that waits on it does not hang the run."""
    from stack.hooks import build_hook_ctx
    jobs, agent = mac
    hangs = jobs.parent / "hanging-bin"
    hangs.mkdir()
    (hangs / "launchctl").write_text("#!/bin/sh\nexec sleep 30\n")
    (hangs / "launchctl").chmod(0o755)
    monkeypatch.setenv("PATH", f"{hangs}:/usr/bin:/bin")
    monkeypatch.setattr(launchd, "LAUNCHCTL_SECONDS", 0.5)
    ctx = build_hook_ctx("core", env={}, step_fn=lambda _m: None)

    started = time.monotonic()
    with pytest.raises(RuntimeError):
        launchd.load(ctx.shell, agent)

    assert time.monotonic() - started < 5


def test_a_stop_of_a_job_that_is_already_leaving_waits_until_it_is_gone(mac):
    """Given a job that is leaving launchd after an earlier stop, when it
    is stopped again, then the stop waits until launchd let it go: launchd
    refuses the second `remove`, and that is no failure while the job
    leaves."""
    jobs, _agent = mac
    (jobs / f"{LABEL}.leaving").write_text("3")

    assert launchd.stop(shell, LABEL) is True

    assert not launchd.loaded(shell, LABEL)


def test_a_stop_launchd_refuses_names_launchds_words_when_the_job_stays(mac, monkeypatch):
    """Given launchd refuses the `remove` and keeps the job, when it is
    stopped, then the stop fails after `STOP_SECONDS` with launchd's
    reason for the refusal."""
    jobs, _agent = mac
    (jobs / LABEL).touch()
    refusing = jobs.parent / "refusing-bin"
    refusing.mkdir()
    (refusing / "launchctl").write_text(
        '#!/bin/sh\n[ "$1" = "list" ] && echo \'{ "PID" = 1; };\' && exit 0\n'
        'echo "Not privileged to remove service." >&2\nexit 1\n')
    (refusing / "launchctl").chmod(0o755)
    monkeypatch.setenv("PATH", f"{refusing}:/usr/bin:/bin")
    monkeypatch.setattr(launchd, "STOP_SECONDS", 0.3)

    with pytest.raises(RuntimeError, match="Not privileged"):
        launchd.stop(shell, LABEL)


def test_a_list_that_fails_otherwise_is_an_error_not_a_missing_job(mac, monkeypatch):
    """Given `launchctl list` fails for another reason than "no such job"
    (exit 113), when launchd is asked about the job or the job is loaded,
    then that fails with launchd's words: taking the failure for a
    missing job would let a caller delete the agent file of a job that
    runs."""
    jobs, agent = mac
    failing = jobs.parent / "failing-bin"
    failing.mkdir()
    (failing / "launchctl").write_text(
        '#!/bin/sh\necho "Operation not permitted while System Integrity Protection is engaged" >&2\n'
        'exit 1\n')
    (failing / "launchctl").chmod(0o755)
    monkeypatch.setenv("PATH", f"{failing}:/usr/bin:/bin")

    with pytest.raises(RuntimeError, match="Operation not permitted"):
        launchd.loaded(shell, LABEL)
    with pytest.raises(RuntimeError, match="Operation not permitted"):
        launchd.load(shell, agent)
