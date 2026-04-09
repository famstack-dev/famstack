"""Docker runtime detection: prefer OrbStack, warn on Docker Desktop."""

import json as _json
from unittest.mock import patch, MagicMock


def _mock_context_ls(contexts):
    """contexts: list of dicts with Name key."""
    m = MagicMock()
    m.returncode = 0
    m.stdout = "\n".join(_json.dumps(c) for c in contexts)
    return m


class TestInitRuntime:

    def test_orbstack_available_no_warning(self):
        from stack import docker
        docker._context = None

        with patch("subprocess.run", return_value=_mock_context_ls([
            {"Name": "orbstack"}, {"Name": "desktop-linux"},
        ])):
            with patch("platform.system", return_value="Darwin"):
                status, warning = docker.init_runtime("orbstack")
        assert "orbstack" in status.lower()
        assert warning is None
        assert docker._context == "orbstack"

    def test_docker_desktop_only_warns(self):
        from stack import docker
        docker._context = None

        with patch("subprocess.run", return_value=_mock_context_ls([
            {"Name": "desktop-linux"}, {"Name": "default"},
        ])):
            with patch("platform.system", return_value="Darwin"):
                status, warning = docker.init_runtime("orbstack")
        assert warning is not None
        assert "not recommended" in warning.lower()
        assert docker._context == "desktop-linux"

    def test_pins_to_preferred(self):
        from stack import docker
        docker._context = None

        with patch("subprocess.run", return_value=_mock_context_ls([
            {"Name": "desktop-linux"}, {"Name": "orbstack"},
        ])):
            with patch("platform.system", return_value="Darwin"):
                docker.init_runtime("orbstack")
        assert docker._context == "orbstack"

    def test_custom_preferred_runtime(self):
        from stack import docker
        docker._context = None

        with patch("subprocess.run", return_value=_mock_context_ls([
            {"Name": "desktop-linux"}, {"Name": "orbstack"},
        ])):
            with patch("platform.system", return_value="Darwin"):
                status, warning = docker.init_runtime("desktop-linux")
        assert warning is None
        assert docker._context == "desktop-linux"

    def test_skips_on_linux(self):
        from stack import docker
        docker._context = None

        with patch("platform.system", return_value="Linux"):
            status, warning = docker.init_runtime()
        assert warning is None
        assert docker._context is None

    def test_handles_docker_not_installed(self):
        from stack import docker
        docker._context = None

        with patch("subprocess.run", side_effect=FileNotFoundError):
            with patch("platform.system", return_value="Darwin"):
                status, warning = docker.init_runtime()
        assert status is None
        assert "not installed" in warning


class TestDockerCommand:
    """Verify _docker() injects --context when set."""

    def test_context_injected(self):
        from stack import docker
        docker._context = "orbstack"

        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "Docker is running"
        with patch("subprocess.run", return_value=mock) as run:
            docker._docker("info", capture_output=True)
            cmd = run.call_args[0][0]
        assert cmd == ["docker", "--context", "orbstack", "info"]

    def test_no_context_when_none(self):
        from stack import docker
        docker._context = None

        mock = MagicMock()
        mock.returncode = 0
        with patch("subprocess.run", return_value=mock) as run:
            docker._docker("info", capture_output=True)
            cmd = run.call_args[0][0]
        assert cmd == ["docker", "info"]
