"""The extension lane's own tests: the unit lane's rules, and the kettle.

`kettle` is the made-up extension in `tests/extensions/kettle/`, which
these tests install with `installed` (`tests/extensions/instance.py`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.lanes.unit import (  # noqa: F401
    diary_back_in_english, httpserver_listen_address, pytest_runtest_call,
    pytest_runtest_setup, pytest_runtest_teardown, stacklet_packages_as_they_were)

KETTLE = Path(__file__).resolve().parents[1] / "kettle"


@pytest.fixture
def kettle() -> Path:
    return KETTLE
