"""Docker runtime detection: prefer OrbStack, warn on Docker Desktop."""

import json as _json
import sys

import pytest

from stack import docker

CONTEXTS_ASK = "context ls"


@pytest.fixture(autouse=True)
def no_pinned_context(monkeypatch):
    # `docker._context` is a module global: init_runtime sets it for the
    # rest of the process, so each test starts from an unpinned module.
    monkeypatch.setattr(docker, "_context", None)
    monkeypatch.setattr(docker, "_preferred", None)


@pytest.fixture
def on_macos(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Darwin")


def _contexts(docker_scripted, *names):
    """Make `docker context ls` list `names`, one JSON object per line."""
    docker_scripted.answers(
        CONTEXTS_ASK, stdout="\n".join(_json.dumps({"Name": n}) for n in names))


@pytest.mark.usefixtures("on_macos")
class TestInitRuntime:

    def test_orbstack_available_no_warning(self, docker_scripted):
        _contexts(docker_scripted, "orbstack", "desktop-linux")
        status, warning = docker.init_runtime("orbstack")
        assert "orbstack" in status.lower()
        assert warning is None
        assert docker._context == "orbstack"

    def test_docker_desktop_only_warns(self, docker_scripted):
        _contexts(docker_scripted, "desktop-linux", "default")
        status, warning = docker.init_runtime("orbstack")
        assert warning is not None
        assert "not recommended" in warning.lower()
        assert docker._context == "desktop-linux"

    def test_pins_to_preferred(self, docker_scripted):
        _contexts(docker_scripted, "desktop-linux", "orbstack")
        docker.init_runtime("orbstack")
        assert docker._context == "orbstack"

    def test_custom_preferred_runtime(self, docker_scripted):
        _contexts(docker_scripted, "desktop-linux", "orbstack")
        status, warning = docker.init_runtime("desktop-linux")
        assert warning is None
        assert docker._context == "desktop-linux"

    def test_skips_on_linux(self, monkeypatch, docker_scripted):
        monkeypatch.setattr("platform.system", lambda: "Linux")
        status, warning = docker.init_runtime()
        assert warning is None
        assert docker._context is None
        assert docker_scripted.commands() == []

    def test_handles_docker_not_installed(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PATH", str(tmp_path))
        status, warning = docker.init_runtime()
        assert status is None
        assert "not installed" in warning


class TestDockerCommand:
    """Verify _docker() injects --context when set."""

    def test_context_injected(self, monkeypatch, docker_scripted):
        monkeypatch.setattr(docker, "_context", "orbstack")
        docker_scripted.answers("info", stdout="Docker is running")
        result = docker._docker("info", capture_output=True, text=True)
        assert result.stdout == "Docker is running"
        assert docker_scripted.contexts() == ["orbstack"]

    def test_no_context_when_none(self, docker_scripted):
        docker_scripted.answers("info")
        docker._docker("info", capture_output=True)
        assert docker_scripted.contexts() == [None]


class TestComposeUpForceRecreate:
    """compose_up always passes --force-recreate. Compose's service-config
    hash does not cover env_file *contents*, so a token written into a
    stacklet's .env between runs is invisible to a plain `up -d` and the
    running container stays on stale env. `stack up` is a deliberate
    user action, so unconditional bounce is the right trade for a
    reliable config-propagation contract."""

    def test_force_recreate_is_unconditional(self, docker_scripted):
        docker_scripted.answers("compose up")
        docker.compose_up("/tmp/compose.yml")
        (cmd,) = docker_scripted.commands()
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

    @pytest.fixture(autouse=True)
    def _unpinned(self, monkeypatch):
        monkeypatch.setattr(docker, "_context", None)

    @pytest.fixture
    def _run(self, docker_scripted):
        def run(returncode, stderr):
            docker_scripted.answers("compose up", code=returncode, stderr=stderr)
            return docker.compose_up("/tmp/compose.yml")
        return run

    def test_empty_selection_is_success(self, _run):
        assert _run(1, "no service selected") == (0, "")

    def test_message_is_matched_regardless_of_padding(self, _run):
        """Compose has moved this text between streams and added
        whitespace across versions; match on content, not layout."""
        assert _run(1, "  no service selected\n") == (0, "")

    def test_real_failures_still_propagate(self, _run):
        """The narrow allowance must not swallow a genuine error."""
        code, err = _run(1, "network stack declared as external, but could not be found")
        assert code == 1
        assert "could not be found" in err

    def test_success_is_untouched(self, _run):
        assert _run(0, "") == (0, "")


class TestComposeBuildRefreshesBaseImages:
    """A locally built image starts from a base image (caddy:2, a Python
    slim image). Without `--pull` the build cache reuses the base it
    first pulled for good: Watchtower does not touch built images, so
    that base, and whatever it carries, would never be patched. With it,
    a rebuild picks up a newer base when there is one and costs a
    registry lookup when there is not.

    Offline the pull fails, and a stacklet must still start from what is
    cached, so the build runs again without it."""

    def _build(self, docker_scripted, *returncodes):
        for code in returncodes:
            docker_scripted.answers("compose build", code=code, times=1)
        docker.compose_build("/tmp/compose.yml")
        return docker_scripted.commands()

    def test_the_build_pulls_newer_base_images(self, docker_scripted):
        (cmd,) = self._build(docker_scripted, 0)
        assert cmd[cmd.index("build"):] == ["build", "--pull"]

    def test_offline_it_builds_from_the_cached_base(self, docker_scripted):
        pulled, cached = self._build(docker_scripted, 1, 0)
        assert "--pull" in pulled
        assert cached[cached.index("build"):] == ["build"]


class TestComposePullFetchesOnlyWhatIsMissing:
    """The pull runs on every `stack up`, so a release that moves a service
    to a new image tag downloads it with its progress on screen. It must
    cost nothing when every image is here, and leave built images to the
    build."""

    def test_only_missing_images_are_pulled(self, docker_scripted):
        docker_scripted.answers("compose pull")
        docker.compose_pull("/tmp/compose.yml")
        (cmd,) = docker_scripted.commands()
        assert cmd[cmd.index("pull"):] == ["pull", "--policy", "missing", "--ignore-buildable"]


class TestACommandAsksDockerOnlyWhenItUsesIt:
    """Picking the runtime asks Docker for its contexts, ~55 ms. Every
    `stack` command paid it at start, also the many that never run a
    container command (an agent's list reads, the memory commands). It is
    asked on the first Docker command now, and that command is pinned to
    the runtime as before."""

    def _docker_on_path(self, tmp_path):
        """A `docker` that records its arguments and knows two contexts."""
        log = tmp_path / "docker.log"
        stub = tmp_path / "bin" / "docker"
        stub.parent.mkdir()
        stub.write_text(
            "#!/bin/sh\n"
            f'echo "$*" >> "{log}"\n'
            'if [ "$1" = "context" ]; then\n'
            '  echo \'{"Name":"default"}\'; echo \'{"Name":"orbstack"}\'\n'
            "fi\n")
        stub.chmod(0o755)
        return stub.parent, log

    def _stack(self, tmp_path, bin_dir, *args):
        import os
        import subprocess
        import sys
        from pathlib import Path
        repo = Path(__file__).resolve().parents[3]
        return subprocess.run([sys.executable, "-m", "stack", *args], capture_output=True,
                              text=True, cwd=str(repo), timeout=60,
                              env={**os.environ, "PYTHONPATH": str(repo / "lib"),
                                   "PATH": f"{bin_dir}:{os.environ['PATH']}"})

    def test_a_command_without_containers_never_asks_docker(self, tmp_path):
        bin_dir, log = self._docker_on_path(tmp_path)

        self._stack(tmp_path, bin_dir, "memory", "find", "zelt")

        assert not log.exists() or "context" not in log.read_text()

    @pytest.mark.skipif(sys.platform != "darwin",
                        reason="contexts are pinned on macOS only (OrbStack or Docker Desktop)")
    def test_the_first_docker_command_is_pinned_to_the_runtime(self, docker_scripted):
        _contexts(docker_scripted, "default", "orbstack")
        docker_scripted.answers("compose ps")
        docker.prefer_runtime("orbstack")
        docker.compose("compose.yml", "ps")

        calls = docker_scripted.commands()
        assert calls[0][:2] == ["context", "ls"]
        assert calls[1][0] == "compose"
        assert docker_scripted.contexts() == [None, "orbstack"]
