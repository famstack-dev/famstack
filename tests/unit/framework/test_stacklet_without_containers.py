"""A stacklet that runs host services and no containers has a state.

Most stacklets are containers, and Docker answers what state they are in.
A stacklet without a compose file starts its services on the Mac from its
`on_start` hook (the ai stacklet's oMLX and Whisper) and stops them from
`on_stop`, so Docker knows nothing of it. These tests pin what the stack
says about such a stacklet anyway: running from `stack up` to `stack down`,
healthy while its health checks answer, waited on before the hooks that
need it, stopped by `stack down all`, and named when it runs code the
checkout has moved past.

The CLI runs for real (`handle_up`, `handle_list`, `handle_down`,
`handle_restart`, `handle_doctor`) over a fabricated stacklet, `kettle`,
in a git checkout. Its host service is a stand-in on localhost that its
hooks start and stop, the way `brew services` does for oMLX. Docker is a
stand-in too, so nothing here reads the containers of the Mac it runs on.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from werkzeug import Response

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

STATES = ("degraded", "online", "starting", "failing", "stopped", "available")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def _hook(log: Path, said: str, then: str = "") -> str:
    return ("from pathlib import Path\n"
            "def run(ctx):\n"
            f"    {then or 'pass'}\n"
            f"    with Path({str(log)!r}).open('a') as f:\n"
            f"        f.write({said!r} + '\\n')\n")


class Instance:
    """A checkout with `kettle`, a stacklet of host services only.

    `kettle` declares one health check against its service. Its hooks
    write to a log what ran, and `on_start` / `on_stop` start and stop the
    service by creating and removing the file the stand-in answers on.
    """

    def __init__(self, tmp_path: Path, httpserver):
        self.root = tmp_path / "checkout"
        self.log = tmp_path / "log"
        self.service = tmp_path / "kettle-runs"
        self.containers: dict[str, str] = {}
        self.listed = ""

        httpserver.expect_request("/health").respond_with_handler(self._answer)
        kettle = self.root / "stacklets" / "kettle"
        (kettle / "hooks").mkdir(parents=True)
        (kettle / "stacklet.toml").write_text(
            'id = "kettle"\nname = "Kettle"\nversion = "0.1.0"\n'
            'category = "test"\ntype = "host"\n\n'
            "[[health.checks]]\n"
            'name = "Kettle"\n'
            f'url  = "{httpserver.url_for("/health")}"\n'
            'hint = "The kettle does not answer"\n')
        service = str(self.service)
        (kettle / "hooks" / "on_start.py").write_text(
            _hook(self.log, "on_start", f"Path({service!r}).touch()"))
        (kettle / "hooks" / "on_stop.py").write_text(
            _hook(self.log, "on_stop", f"Path({service!r}).unlink(missing_ok=True)"))
        (kettle / "hooks" / "on_start_ready.py").write_text(
            _hook(self.log, "on_start_ready"))

        (self.root / "stack.toml").write_text('[core]\ntimezone = "Europe/Berlin"\n')
        (self.root / ".stack").mkdir()
        (self.root / ".stack" / "secrets.toml").write_text('global__ADMIN_PASSWORD = "test"\n')
        (self.root / ".gitignore").write_text(".stack/\nstack.toml\n.env\n__pycache__/\n")
        _git(self.root.parent, "init", "-q", str(self.root))
        _git(self.root, "config", "user.email", "test@example.com")
        _git(self.root, "config", "user.name", "Test")
        self.commit("add the kettle")

        from stack import Stack
        from stack.output import CollectorOutput
        self.stack = Stack(root=self.root, data=tmp_path / "data", output=CollectorOutput())

    def _answer(self, request) -> Response:
        """The host service: answers while it runs, and says it was asked."""
        with self.log.open("a") as f:
            f.write("health asked\n")
        return Response("ok" if self.service.exists() else "down",
                        status=200 if self.service.exists() else 503)

    def commit(self, subject: str) -> None:
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", subject)

    def add_container_stacklet(self, sid: str) -> None:
        """A stacklet with a compose file, whose state Docker reports."""
        sdir = self.root / "stacklets" / sid
        sdir.mkdir(parents=True)
        (sdir / "stacklet.toml").write_text(f'id = "{sid}"\nname = "{sid.title()}"\n')
        (sdir / "docker-compose.yml").write_text(f"name: stack-{sid}\nservices: {{}}\n")

    # ── Docker, as far as these tests need it ────────────────────────────

    def compose_up(self, compose_file, env=None):
        self.containers[Path(compose_file).parent.name] = "running"
        return 0, ""

    def compose_stop(self, compose_file):
        self.containers[Path(compose_file).parent.name] = "stopped"
        return 0, ""

    # ── The commands ─────────────────────────────────────────────────────

    def up(self, sid: str = "kettle") -> None:
        """`stack up <sid>`."""
        from stack.cli import handle_up
        handle_up(self.stack, argparse.Namespace(stacklet=[sid], no_voice=False))

    def down_all(self) -> None:
        """`stack down all`."""
        from stack.cli import handle_down
        handle_down(self.stack, argparse.Namespace(stacklet=["all"]))

    def restart(self) -> None:
        """`stack restart`, with no argument and no question."""
        from stack.cli import handle_restart
        handle_restart(self.stack, argparse.Namespace(stacklet=[], yes=True))

    def doctor(self, capsys) -> list[str]:
        """The titles of what `stack doctor --json` finds."""
        from stack.cli import handle_doctor
        capsys.readouterr()
        try:
            handle_doctor(self.stack, argparse.Namespace(json=True))
        except SystemExit:
            pass
        return [f["title"] for f in json.loads(capsys.readouterr().out)["findings"]]

    def list_says(self, capsys, sid: str = "kettle") -> str:
        """The state `stack list` shows for a stacklet, as a person reads it,
        with `(restart pending)` when it runs code the checkout moved past.
        The whole listing stays in `listed`."""
        from stack.cli import handle_list
        capsys.readouterr()
        handle_list(self.stack, argparse.Namespace())
        self.listed = capsys.readouterr().out
        line = next(ln for ln in self.listed.splitlines()
                    if f" {sid.title()} " in f" {ln} ")
        state = next(word for word in STATES if word in line)
        return f"{state} (restart pending)" if "restart pending" in line else state

    def happened(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []


@pytest.fixture
def instance(tmp_path, httpserver):
    made = Instance(tmp_path, httpserver)
    states = made.containers

    with patch("stack.docker.ensure_network", return_value=("mocked", None)), \
         patch("stack.docker.init_runtime", return_value=("mocked", None)), \
         patch("stack.docker.compose_pull", return_value=(0, "")), \
         patch("stack.docker.compose_up", side_effect=made.compose_up), \
         patch("stack.docker.compose_stop", side_effect=made.compose_stop), \
         patch("stack.docker.project_states", side_effect=lambda: dict(states)), \
         patch("stack.docker.running_project_ids",
               side_effect=lambda: {s for s, st in states.items() if st == "running"}), \
         patch("stack.docker.container_commits", return_value={}), \
         patch("stack.docker.containers_for", return_value=[]), \
         patch("stack.docker.compose_service_env", return_value={}), \
         patch("stack.cli._notify"):
        yield made


# ── State ────────────────────────────────────────────────────────────────

def test_it_is_online_once_up_while_its_service_answers(instance, capsys):
    """Given kettle is up and its service answers, then `stack list` shows
    it online. Docker has nothing to say about it, and it used to show as
    stopped while its service served."""
    instance.up()

    assert instance.list_says(capsys) == "online"


def test_it_is_degraded_when_its_service_stops_answering(instance, capsys):
    """Given kettle is up and its service then stops on its own (a crash,
    a `brew upgrade`), then `stack list` shows it degraded with the hint
    of the failing check, as it does for containers that fail theirs."""
    instance.up()
    instance.service.unlink()

    assert instance.list_says(capsys) == "degraded"
    assert "The kettle does not answer" in instance.listed


def test_it_is_available_until_it_is_set_up(instance, capsys):
    """Resting state: a stacklet never brought up is available, even with
    nothing to ask but its health check."""
    assert instance.list_says(capsys) == "available"


def test_a_stacklet_with_containers_still_follows_its_containers(instance, capsys):
    """Resting state: a stacklet with a compose file is what Docker says of
    its containers. Brought up, then its containers gone, it shows
    stopped: nothing the stack wrote at `stack up` keeps it online."""
    instance.add_container_stacklet("album")
    instance.up("album")
    assert instance.list_says(capsys, "album") == "online"

    del instance.containers["album"]

    assert instance.list_says(capsys, "album") == "stopped"


# ── stack up ─────────────────────────────────────────────────────────────

def test_up_waits_for_its_health_check_before_the_hooks_that_need_it(instance):
    """Given `stack up kettle`, then its health check is asked after
    `on_start` started the service and before `on_start_ready`, which may
    call it. Without containers the checks used to be skipped."""
    instance.up()

    assert instance.happened() == ["on_start", "health asked", "on_start_ready"]


# ── stack down all ───────────────────────────────────────────────────────

def test_down_all_stops_it(instance, capsys):
    """Given kettle is up, when `stack down all` runs, then its `on_stop`
    stops its service and it shows stopped. It used to be left running,
    because only stacklets with running containers were stopped."""
    instance.up()

    instance.down_all()

    assert instance.happened()[-1] == "on_stop"
    assert not instance.service.exists()
    assert instance.list_says(capsys) == "stopped"


def test_down_all_stops_it_after_what_depends_on_it(instance):
    """Given a stacklet with containers that requires kettle, then
    `stack down all` stops that one first and kettle after it, so its
    service outlives what calls it."""
    instance.add_container_stacklet("album")
    manifest = instance.root / "stacklets" / "album" / "stacklet.toml"
    manifest.write_text(manifest.read_text() + 'requires = ["kettle"]\n')
    instance.up()
    instance.up("album")

    from stack.cli import CLI
    result = CLI(instance.stack).down("all")

    assert result["stopped"] == ["album", "kettle"]


# ── Code the checkout has moved past ─────────────────────────────────────

def test_doctor_names_it_stale_and_a_bare_restart_restarts_it(instance, capsys):
    """Given kettle was started from a commit the checkout has moved past,
    with a change to kettle itself, then `stack doctor` reports it and
    `stack restart` restarts it, after which it runs the current code."""
    instance.up()
    hook = instance.root / "stacklets" / "kettle" / "hooks" / "on_start_ready.py"
    hook.write_text("# boils faster now\n" + hook.read_text())
    instance.commit("boil faster")

    assert instance.list_says(capsys) == "online (restart pending)"
    assert "kettle is running code from before the last update" in instance.doctor(capsys)

    instance.restart()

    assert instance.happened()[-4:] == ["on_stop", "on_start", "health asked", "on_start_ready"]
    assert instance.list_says(capsys) == "online"


def test_it_is_not_stale_when_the_checkout_moved_past_something_else(instance, capsys):
    """Given only another stacklet changed since kettle started, then
    kettle is not named: the same rule a release uses to decide what it
    staled."""
    instance.up()
    instance.add_container_stacklet("album")
    instance.commit("add an album")

    assert instance.list_says(capsys) == "online"
