"""Memory's unit tests: the unit lane's rules, and memory's test instance.

The lane's rules (`tests.lanes.unit`) apply to every test here, as to the
rest of the unit lane. The fixtures:

- `memory`: memory's test instance (`instance.running_memory`), its data
  dir the test's `tmp_path`, holding the Simpsons' cards and ontology, or
  the files a test names with
  `@pytest.mark.parametrize("memory", [files], indirect=True)`. A test file
  that needs the instance with more (a model, another seed, the bot
  runner's services) defines its own `memory` from `running_memory`.
- `stack_cli`: `./stack` as a person runs it, as a process of its own.
- `forgejo_accounts`: `GET /api/v1/user` on the test's Forgejo stand-in.

The compile's prompt measurement (`compile_prompts.py`) starts in the
background once the lane has collected its tests, and the tests that read
it are collected last, so it is made while the workers run everything else.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator

import pytest

from tests.lanes.unit import (  # noqa: F401
    diary_back_in_english, httpserver_listen_address, pytest_runtest_call,
    pytest_runtest_setup, pytest_runtest_teardown, stacklet_packages_as_they_were)
from tests.springfield import REPO

from .instance import RunningMemory, running_memory
from .simpsons_cards import ONTOLOGY, VAULT


# The tests that read the background measurement of the compile's prompts.
_MEASURED = "test_memory_compile_prompts.py"


def pytest_collection_modifyitems(items):
    """The tests that read the prompt measurement last, in the order they were."""
    items.sort(key=lambda item: item.path.name == _MEASURED)


def pytest_collection_finish(session):
    """Start the prompt measurement when this run has a test that reads it,
    and not when it only lists the tests (`--collect-only`, an IDE's discovery)."""
    if session.config.option.collectonly:
        return
    if any(item.path.name == _MEASURED for item in session.items):
        from .compile_prompts import start

        start()


@pytest.fixture
def memory(tmp_path, request) -> Iterator[RunningMemory]:
    """The Simpsons' memory, running (see the module's docstring)."""
    files = getattr(request, "param", None) or {**VAULT, "ontology.toml": ONTOLOGY}
    with running_memory(tmp_path, files, seeded_by="memory-bot",
                        seeded_as="cards: the Simpsons") as running:
        yield running


@pytest.fixture
def stack_cli():
    """Run a `stack ...` command and return (returncode, stdout, stderr).

    Uses the real CLI entry point, real stacklets, real seeds: no stand-in
    intercepts a read. Matches how `stack` is invoked by the user and by
    other agents.
    """

    def _run(*args, timeout: int = 30) -> tuple[int, str, str]:
        cmd = [sys.executable, "-m", "stack", *args]
        env = {**os.environ, "PYTHONPATH": str(REPO / "lib")}
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, cwd=str(REPO), env=env,
        )
        return result.returncode, result.stdout, result.stderr

    return _run


@pytest.fixture
def forgejo_accounts(httpserver):
    """`GET /api/v1/user` on the test's Forgejo stand-in: the account each
    request signs in as (`forgejo_accounts.py`), which a client asks
    before it commits, to name itself as committer."""
    from .forgejo_accounts import signed_in

    httpserver.expect_request("/api/v1/user", method="GET").respond_with_handler(signed_in)
