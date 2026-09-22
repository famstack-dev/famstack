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


class TestComposeUpForceRecreate:
    """compose_up always passes --force-recreate. Compose's service-config
    hash does not cover env_file *contents*, so a token written into a
    stacklet's .env between runs is invisible to a plain `up -d` and the
    running container stays on stale env. `stack up` is a deliberate
    user action, so unconditional bounce is the right trade for a
    reliable config-propagation contract."""

    def test_force_recreate_is_unconditional(self):
        from stack import docker
        docker._context = None

        mock = MagicMock()
        mock.returncode = 0
        mock.stderr = ""
        with patch("subprocess.run", return_value=mock) as run:
            docker.compose_up("/tmp/compose.yml")
            cmd = run.call_args[0][0]
        assert "--force-recreate" in cmd
        assert cmd.index("up") < cmd.index("--force-recreate")


class TestComposeUpWithNoActiveServices:
    """A stacklet whose every service is profile-gated off starts cleanly.

    When COMPOSE_PROFILES excludes all of a compose file's services,
    `docker compose up` exits 1 with "no service selected" on stderr.
    That is compose reporting an empty selection, not a failure to
    start anything, but the CLI reads any non-zero as "Failed to start
    services" and refuses to write the setup marker.

    The ai stacklet is the live example: STACK_AI_NO_VOICE=1 clears the
    profile, and its only service (Piper TTS) sits behind `voice`. The
    documented local-dev opt-out could therefore never finish setup,
    which in turn blocked every stacklet that `requires = ["ai"]`.

    The exit code and message below were taken from a real
    `docker compose up -d --force-recreate` run, not from reading the
    source, so this pins compose's actual contract.
    """

    def _run(self, returncode, stderr):
        from stack import docker
        docker._context = None

        mock = MagicMock()
        mock.returncode = returncode
        mock.stderr = stderr
        with patch("subprocess.run", return_value=mock):
            return docker.compose_up("/tmp/compose.yml")

    def test_empty_selection_is_success(self):
        assert self._run(1, "no service selected") == (0, "")

    def test_message_is_matched_regardless_of_padding(self):
        """Compose has moved this text between streams and added
        whitespace across versions; match on content, not layout."""
        assert self._run(1, "  no service selected\n") == (0, "")

    def test_real_failures_still_propagate(self):
        """The narrow allowance must not swallow a genuine error."""
        code, err = self._run(1, "network stack declared as external, but could not be found")
        assert code == 1
        assert "could not be found" in err

    def test_success_is_untouched(self):
        assert self._run(0, "") == (0, "")
