"""Docker project ID queries: running vs all (includes stopped)."""

import json
from unittest.mock import patch, MagicMock


def _mock_compose_ls(projects):
    """Build a mock subprocess result for docker compose ls."""
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = json.dumps(projects)
    return mock


class TestRunningProjectIds:

    def test_finds_running_stacklets(self):
        from stack.docker import running_project_ids

        projects = [
            {"Name": "stack-ai", "Status": "running(3)"},
            {"Name": "stack-messages", "Status": "running(2)"},
        ]
        with patch("subprocess.run", return_value=_mock_compose_ls(projects)):
            assert running_project_ids() == {"ai", "messages"}

    def test_ignores_stopped(self):
        from stack.docker import running_project_ids

        projects = [
            {"Name": "stack-ai", "Status": "running(3)"},
            {"Name": "stack-bots", "Status": "exited(2)"},
        ]
        with patch("subprocess.run", return_value=_mock_compose_ls(projects)):
            assert running_project_ids() == {"ai"}

    def test_ignores_non_stack_projects(self):
        from stack.docker import running_project_ids

        projects = [
            {"Name": "stack-ai", "Status": "running(1)"},
            {"Name": "myapp", "Status": "running(1)"},
        ]
        with patch("subprocess.run", return_value=_mock_compose_ls(projects)):
            assert running_project_ids() == {"ai"}

    def test_returns_empty_on_failure(self):
        from stack.docker import running_project_ids

        mock = MagicMock()
        mock.returncode = 1
        mock.stdout = ""
        with patch("subprocess.run", return_value=mock):
            assert running_project_ids() == set()


class TestAllProjectIds:

    def test_includes_stopped(self):
        from stack.docker import all_project_ids

        projects = [
            {"Name": "stack-ai", "Status": "running(3)"},
            {"Name": "stack-bots", "Status": "exited(2)"},
        ]
        with patch("subprocess.run", return_value=_mock_compose_ls(projects)):
            assert all_project_ids() == {"ai", "bots"}

    def test_includes_all_states(self):
        from stack.docker import all_project_ids

        projects = [
            {"Name": "stack-messages", "Status": "running(2)"},
            {"Name": "stack-photos", "Status": "exited(1)"},
            {"Name": "stack-docs", "Status": "created(1)"},
        ]
        with patch("subprocess.run", return_value=_mock_compose_ls(projects)):
            assert all_project_ids() == {"messages", "photos", "docs"}

    def test_ignores_non_stack_projects(self):
        from stack.docker import all_project_ids

        projects = [
            {"Name": "stack-ai", "Status": "exited(1)"},
            {"Name": "other-project", "Status": "running(1)"},
        ]
        with patch("subprocess.run", return_value=_mock_compose_ls(projects)):
            assert all_project_ids() == {"ai"}

    def test_returns_empty_on_failure(self):
        from stack.docker import all_project_ids

        mock = MagicMock()
        mock.returncode = 1
        mock.stdout = ""
        with patch("subprocess.run", return_value=mock):
            assert all_project_ids() == set()

    def test_returns_empty_on_no_projects(self):
        from stack.docker import all_project_ids

        with patch("subprocess.run", return_value=_mock_compose_ls([])):
            assert all_project_ids() == set()
