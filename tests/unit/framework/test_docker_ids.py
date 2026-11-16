"""Docker project ID queries: running vs all (includes stopped)."""

import json

import pytest

from stack.docker import all_project_ids, running_project_ids


@pytest.fixture(autouse=True)
def no_pinned_context(monkeypatch):
    # A module global that an earlier test may have set; the stand-in drops
    # the pin either way, this keeps the recorded command independent of it.
    monkeypatch.setattr("stack.docker._context", None)
    monkeypatch.setattr("stack.docker._preferred", None)


def _compose_ls(docker, projects):
    docker.answers("compose ls", stdout=json.dumps(projects))


class TestRunningProjectIds:

    def test_finds_running_stacklets(self, docker_scripted):
        _compose_ls(docker_scripted, [
            {"Name": "stack-ai", "Status": "running(3)"},
            {"Name": "stack-messages", "Status": "running(2)"},
        ])
        assert running_project_ids() == {"ai", "messages"}

    def test_ignores_stopped(self, docker_scripted):
        _compose_ls(docker_scripted, [
            {"Name": "stack-ai", "Status": "running(3)"},
            {"Name": "stack-bots", "Status": "exited(2)"},
        ])
        assert running_project_ids() == {"ai"}

    def test_ignores_non_stack_projects(self, docker_scripted):
        _compose_ls(docker_scripted, [
            {"Name": "stack-ai", "Status": "running(1)"},
            {"Name": "myapp", "Status": "running(1)"},
        ])
        assert running_project_ids() == {"ai"}

    def test_returns_empty_on_failure(self, docker_scripted):
        # A failed command can still have printed a list; it is not trusted.
        docker_scripted.answers("compose ls", code=1,
                                stdout='[{"Name": "stack-ai", "Status": "running(3)"}]')
        assert running_project_ids() == set()


class TestAllProjectIds:

    def test_includes_stopped(self, docker_scripted):
        _compose_ls(docker_scripted, [
            {"Name": "stack-ai", "Status": "running(3)"},
            {"Name": "stack-bots", "Status": "exited(2)"},
        ])
        assert all_project_ids() == {"ai", "bots"}

    def test_includes_all_states(self, docker_scripted):
        _compose_ls(docker_scripted, [
            {"Name": "stack-messages", "Status": "running(2)"},
            {"Name": "stack-photos", "Status": "exited(1)"},
            {"Name": "stack-docs", "Status": "created(1)"},
        ])
        assert all_project_ids() == {"messages", "photos", "docs"}

    def test_ignores_non_stack_projects(self, docker_scripted):
        _compose_ls(docker_scripted, [
            {"Name": "stack-ai", "Status": "exited(1)"},
            {"Name": "other-project", "Status": "running(1)"},
        ])
        assert all_project_ids() == {"ai"}

    def test_returns_empty_on_failure(self, docker_scripted):
        # A failed command can still have printed a list; it is not trusted.
        docker_scripted.answers("compose ls", code=1,
                                stdout='[{"Name": "stack-ai", "Status": "running(3)"}]')
        assert all_project_ids() == set()

    def test_returns_empty_on_no_projects(self, docker_scripted):
        _compose_ls(docker_scripted, [])
        assert all_project_ids() == set()
