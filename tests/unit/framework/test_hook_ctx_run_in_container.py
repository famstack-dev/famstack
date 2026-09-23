"""ctx.run_in_container(): run a command inside the stacklet's own container.

Hooks run on the host and often have to reach into a container: create
an account with the service's admin CLI, run a management script. With
`ctx.shell` that meant building a `docker exec` command string, which
breaks on quoting and, on failure, printed the whole command line
including any password it carried. `ctx.run_in_container` takes the arguments as a
list, never goes through a shell, finds the container by the naming
convention, and reports a failure without the arguments.

A stand-in `docker` script on PATH records what it receives, so these
tests pin the exact `docker exec` command line without Docker.
"""

import json
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "lib"))

from stack.hooks import build_hook_ctx  # noqa: E402


@pytest.fixture
def docker(tmp_path, monkeypatch):
    """A `docker` that records its arguments and answers like the real one.

    It prints `STDOUT` and `STDERR` from its environment and exits with
    `EXIT`, so each test decides what the command in the container did.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker-args.json"
    script = bin_dir / "docker"
    script.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import json, os, sys
        with open({str(log)!r}, "w") as f:
            json.dump(sys.argv[1:], f)
        sys.stdout.write(os.environ.get("STDOUT", ""))
        sys.stderr.write(os.environ.get("STDERR", ""))
        sys.exit(int(os.environ.get("EXIT", "0")))
    """))
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{__import__('os').environ['PATH']}")

    class Docker:
        def args(self):
            return json.loads(log.read_text())

        def answer(self, stdout="", stderr="", exit_code=0):
            monkeypatch.setenv("STDOUT", stdout)
            monkeypatch.setenv("STDERR", stderr)
            monkeypatch.setenv("EXIT", str(exit_code))

    return Docker()


def _ctx(stacklet_id="code"):
    return build_hook_ctx(stacklet_id, env={}, step_fn=lambda msg: None)


class TestWhichContainer:
    """The container follows the naming convention in dev.md."""

    def test_a_single_service_stacklet_runs_in_stack_id(self, docker):
        _ctx("code").run_in_container(["forgejo", "admin", "auth", "list"])
        assert docker.args() == ["exec", "stack-code", "forgejo", "admin", "auth", "list"]

    def test_a_named_service_runs_in_stack_id_service(self, docker):
        _ctx("docs").run_in_container(["python3", "manage.py", "check"], service="paperless")
        assert docker.args() == ["exec", "stack-docs-paperless", "python3", "manage.py", "check"]

    def test_runs_as_the_given_user(self, docker):
        """Forgejo's CLI refuses to run as root; its image expects `git`."""
        _ctx("code").run_in_container(["forgejo", "--version"], user="git")
        assert docker.args() == ["exec", "--user", "git", "stack-code", "forgejo", "--version"]


class TestArguments:

    def test_each_argument_arrives_as_written(self, docker):
        """No shell in between: spaces, quotes and $ are not interpreted,
        and a value cannot end one argument and start another."""
        tricky = ['family account', 'pa"ss $HOME; rm -rf /', "it's"]
        _ctx("code").run_in_container(["tool", *tricky])
        assert docker.args()[2:] == ["tool", *tricky]


class TestResult:

    def test_returns_what_the_command_printed(self, docker):
        docker.answer(stdout="ID\tName\n1\tfamily account\n")
        assert _ctx("code").run_in_container(["forgejo", "admin", "auth", "list"]) == (
            "ID\tName\n1\tfamily account\n")

    def test_a_failure_says_where_and_why(self, docker):
        """The last stderr line is what a service's CLI uses for its
        reason, and what a hook checks for ("already exists")."""
        docker.answer(stderr="some detail\nuser already exists\n", exit_code=1)
        with pytest.raises(RuntimeError) as failed:
            _ctx("code").run_in_container(["forgejo", "admin", "user", "create"])
        message = str(failed.value)
        assert "stack-code" in message
        assert "exit 1" in message
        assert "user already exists" in message

    def test_a_failure_never_repeats_the_arguments(self, docker):
        """Arguments carry passwords and client secrets, and the message
        is printed to the terminal."""
        docker.answer(stderr="bad input\n", exit_code=2)
        with pytest.raises(RuntimeError) as failed:
            _ctx("code").run_in_container(["forgejo", "admin", "user", "create",
                               "--password", "hunter2-secret"])
        assert "hunter2-secret" not in str(failed.value)
