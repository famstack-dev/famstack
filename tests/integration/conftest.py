"""The integration lane's pytest hooks."""

# A failed test keeps the logs of the containers it ran against.
from tests.failure_logs import pytest_runtest_makereport, pytest_runtest_setup  # noqa: F401
