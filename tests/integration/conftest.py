"""The integration lane's pytest hooks."""

# A failed test keeps the logs of the containers it ran against.
from tests.failure_logs import pytest_runtest_makereport, pytest_runtest_setup  # noqa: F401

# Each test gets its own list of paired devices (`DEVICES_FILE`), as in the
# unit lane: the surface DSL's browser pairs itself by adding to it.
from tests.unit.surface.conftest import a_list_of_devices  # noqa: F401
