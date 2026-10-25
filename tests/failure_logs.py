"""A failed test keeps the logs of the containers it ran against.

The container lanes recreate containers between and during their steps:
`stack up` before the tests, the AI mode switch after the stubbed ones.
A recreated container's log starts empty, so by the time someone looks
at a failure its log is gone. These hooks take it as the test fails:
the log of every running `stack-*` container since the test started,
one file each, in `tests/results/<test>/`, which the failure report
names.

A lane takes the hooks by importing them into its conftest:

    from tests.failure_logs import pytest_runtest_makereport, pytest_runtest_setup  # noqa: F401
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

import pytest

RESULTS = Path(__file__).resolve().parent / "results"

_STARTED = pytest.StashKey[float]()


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    item.stash[_STARTED] = time.time()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item):
    report = yield
    if report.failed:
        where = keep_logs(item.nodeid, item.stash.get(_STARTED, time.time()))
        if where is not None:
            report.sections.append(("container logs", f"kept in {where}"))
    return report


def keep_logs(nodeid: str, since: float) -> Path | None:
    """The log since `since` of every running `stack-*` container, in a
    directory named after the test; None when Docker does not answer."""
    try:
        names = subprocess.run(
            ["docker", "ps", "--filter", "name=^stack-", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=10, check=True).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return None
    target = RESULTS / re.sub(r"[^A-Za-z0-9_.-]+", "_", nodeid)
    target.mkdir(parents=True, exist_ok=True)
    for name in names:
        try:
            log = subprocess.run(["docker", "logs", "--since", str(int(since)), name],
                                 capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            continue
        (target / f"{name}.log").write_text(log.stdout + log.stderr)
    return target
