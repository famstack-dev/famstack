"""Installing: from a family file without a terminal, or typed in one.

`stack install --family <file> --yes`, an install that asks nothing:

A script, a provisioning tool or the e2e lane installs from a family file:
the dependency checks fail with a plain message instead of asking, the
admin password is made, the family is written through `stack family set`,
core starts, and the command says where the dashboard is. Nothing waits
for input, so it runs without a terminal.

Driven through the real entry point, `stack.cli.main`, from a checkout in
`tmp_path` with no stack.toml and a stand-in core: a stacklet with that id
and a compose file, so the real `stack up core` runs. Homebrew and Docker
are stood in for, as in test_lifecycle_sequencing.py, and stdin is empty
and no terminal. HOME is the test's, so nothing lands in the real one.

`./stack` typed in a terminal (the last section) asks nothing either, but
the dependency checks: it starts core and opens the dashboard, whose first
run sets the family up. There stdin is a terminal that fails the test on
any read, the browser is a stand-in that notes what it was asked to open,
and Homebrew's bin directory, where the `famstack` command goes, is a
folder of the test's.
"""

from __future__ import annotations

import io
import sys
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from stack import cli

SIMPSONS = """\
name = "Simpson"

[[people]]
name = "Homer Simpson"
words = "he"

[[people]]
name = "Marge Simpson"
words = "she"

[[people]]
name = "Bart Simpson"
"""


class Docker:
    """Docker as the install sees it, answering as `docker.check_docker` does:
    `state` is running, stopped or missing. Remembers what was started."""

    ANSWERS = {
        "running": ("Docker is running", None),
        "stopped": (None, "Docker is not running. Start it and try again."),
        "missing": (None, "Docker is not installed"),
    }

    def __init__(self):
        self.state = "running"
        self.started: list[str] = []

    def check(self):
        return self.ANSWERS[self.state]

    def compose_up(self, compose_file, *args, **kwargs):
        self.started.append(Path(compose_file).parent.name)
        return 0, ""


@pytest.fixture
def docker():
    stand_in = Docker()
    with patch("stack.docker.check_docker", side_effect=stand_in.check), \
         patch("stack.docker.init_runtime", return_value=("orbstack", None)), \
         patch("stack.docker.ensure_network", return_value=("mocked", None)), \
         patch("stack.docker.compose_pull", return_value=None), \
         patch("stack.docker.compose_up", side_effect=stand_in.compose_up):
        yield stand_in


@pytest.fixture
def brew(monkeypatch):
    """Homebrew is installed unless a test says otherwise."""
    state = {"installed": True}
    monkeypatch.setattr("stack.installer._has_brew", lambda: state["installed"])
    return state


@pytest.fixture
def checkout(tmp_path, monkeypatch, docker, brew):
    """A checkout nobody has installed yet, with a stand-in core."""
    core = tmp_path / "stacklets" / "core"
    core.mkdir(parents=True)
    (core / "stacklet.toml").write_text('id = "core"\nname = "Core"\n')
    (core / "docker-compose.yml").write_text("services:\n  tools:\n    image: busybox\n")
    (tmp_path / "simpsons.toml").write_text(SIMPSONS)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STACK_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    # Core's start would tell the server room, which this checkout has not got.
    monkeypatch.setattr(cli, "_notify", lambda _stck, _msg: None)
    return tmp_path


def install(capfd, monkeypatch, *argv: str) -> tuple[int, str]:
    """Run `stack install <argv>` without a terminal: (exit code, everything printed)."""
    monkeypatch.setattr(sys, "argv", ["stack", "install", *argv])
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    try:
        cli.main()
        code = 0
    except SystemExit as exit_:
        code = exit_.code or 0
    out, err = capfd.readouterr()
    return code, out + err


def read_toml(path):
    with open(path, "rb") as f:
        return tomllib.load(f)


def written(root) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*")
                  if p.is_file() and not p.is_relative_to(root / "stacklets") and p.name != "simpsons.toml")


# ── The install ──────────────────────────────────────────────────────────

def test_an_install_from_a_family_file_needs_no_terminal(checkout, docker, capfd, monkeypatch):
    code, printed = install(capfd, monkeypatch, "--family", "simpsons.toml", "--yes")

    assert code == 0, printed
    cfg = read_toml(checkout / "stack.toml")
    assert cfg["core"]["stack_owner"] == "Simpson"
    assert cfg["core"]["timezone"]
    assert cfg["core"]["language"] in ("en", "de")
    assert cfg["messages"]["server_name"] == "simpson"
    people = read_toml(checkout / "users.toml")["users"]
    assert [p["id"] for p in people] == ["homer", "marge", "bart"]
    secrets = read_toml(checkout / ".stack" / "secrets.toml")
    assert secrets["global__ADMIN_PASSWORD"].startswith("s")
    assert secrets["global__USER_HOMER_PASSWORD"] == "homer"
    assert docker.started == ["core"]
    assert (checkout / ".stack" / "core.setup-done").exists()
    assert ":42000/dashboard" in printed


def test_running_it_again_changes_nothing(checkout, capfd, monkeypatch):
    """A script that failed halfway is run again, as the installer always allowed."""
    install(capfd, monkeypatch, "--family", "simpsons.toml", "--yes")
    stack_toml = (checkout / "stack.toml").read_bytes()
    secrets = (checkout / ".stack" / "secrets.toml").read_bytes()

    code, printed = install(capfd, monkeypatch, "--family", "simpsons.toml", "--yes")

    assert code == 0, printed
    assert (checkout / "stack.toml").read_bytes() == stack_toml
    assert (checkout / ".stack" / "secrets.toml").read_bytes() == secrets


# ── Stops with a plain message, before writing anything ──────────────────

def test_without_homebrew_it_says_so_and_stops(checkout, brew, docker, capfd, monkeypatch):
    brew["installed"] = False

    code, printed = install(capfd, monkeypatch, "--family", "simpsons.toml", "--yes")

    assert code == 1
    assert "Homebrew" in printed
    assert written(checkout) == []
    assert docker.started == []


def test_without_docker_it_names_orbstack_and_stops(checkout, docker, capfd, monkeypatch):
    docker.state = "missing"

    code, printed = install(capfd, monkeypatch, "--family", "simpsons.toml", "--yes")

    assert code == 1
    assert "brew install --cask orbstack" in printed
    assert written(checkout) == []


def test_with_docker_stopped_it_says_to_start_it(checkout, docker, capfd, monkeypatch):
    docker.state = "stopped"

    code, printed = install(capfd, monkeypatch, "--family", "simpsons.toml", "--yes")

    assert code == 1
    assert "Start OrbStack" in printed
    assert written(checkout) == []


def test_a_family_file_with_problems_stops_it_before_anything_is_written(checkout, docker, capfd, monkeypatch):
    (checkout / "simpsons.toml").write_text(SIMPSONS + 'relation = "nephew"\n')

    code, printed = install(capfd, monkeypatch, "--family", "simpsons.toml", "--yes")

    assert code == 1
    assert "Bart Simpson: The relation is" in printed
    assert written(checkout) == []
    assert docker.started == []


def test_without_a_terminal_it_needs_yes(checkout, capfd, monkeypatch):
    code, printed = install(capfd, monkeypatch, "--family", "simpsons.toml")

    assert code == 1
    assert "--yes" in printed
    assert written(checkout) == []


def test_yes_alone_is_not_an_install(checkout, capfd, monkeypatch):
    """The wizard asks its questions; there is nothing to say yes to without a file."""
    code, printed = install(capfd, monkeypatch, "--yes")

    assert code == 1
    assert "--family" in printed
    assert written(checkout) == []


# ── Typed in a terminal ──────────────────────────────────────────────────

class Terminal(io.StringIO):
    """A terminal nobody types in: a question would read it, and fail."""

    def isatty(self) -> bool:
        return True

    def readline(self, *args):
        raise AssertionError("the install asked a question")

    read = readline


@pytest.fixture
def terminal(checkout, monkeypatch):
    """`./stack` in a terminal: what the browser was asked to open, and the
    folder the `famstack` command goes to."""
    opened: list[str] = []
    bin_dir = checkout / "brew-bin"
    bin_dir.mkdir()
    monkeypatch.setattr("stack.installer._open", opened.append)
    monkeypatch.setattr("stack.global_command.bin_dir", lambda: bin_dir)
    monkeypatch.setattr("stack.installer.REPO_ROOT", checkout)
    monkeypatch.setattr(sys, "stdin", Terminal())
    return opened


def typed(capfd, monkeypatch, *argv: str) -> tuple[int, str]:
    monkeypatch.setattr(sys, "argv", ["stack", *argv])
    try:
        cli.main()
        code = 0
    except SystemExit as exit_:
        code = exit_.code or 0
    out, err = capfd.readouterr()
    return code, out + err


def test_in_a_terminal_it_starts_core_and_opens_the_dashboard(checkout, terminal, docker, capfd, monkeypatch):
    code, printed = typed(capfd, monkeypatch)

    assert code == 0, printed
    assert docker.started == ["core"]
    assert terminal == ["http://localhost:42000/dashboard"]
    assert "http://localhost:42000/dashboard" in printed
    cfg = read_toml(checkout / "stack.toml")
    assert cfg["core"]["timezone"]
    assert cfg["core"]["language"] in ("en", "de")
    assert read_toml(checkout / ".stack" / "secrets.toml")["global__ADMIN_PASSWORD"]


def test_the_family_is_left_to_the_browser(checkout, terminal, capfd, monkeypatch):
    """Nobody is named in the terminal, and the chat's permanent name waits
    for the family: the first run writes both."""
    typed(capfd, monkeypatch, "install")

    cfg = read_toml(checkout / "stack.toml")
    assert cfg["core"]["stack_owner"] == ""
    assert cfg["messages"]["server_name"] == ""
    assert not (checkout / "users.toml").exists()


def test_run_again_before_the_first_run_it_opens_the_dashboard_again(checkout, terminal, capfd, monkeypatch):
    typed(capfd, monkeypatch, "install")
    secrets = (checkout / ".stack" / "secrets.toml").read_bytes()

    code, _ = typed(capfd, monkeypatch, "install")

    assert code == 0
    assert terminal == ["http://localhost:42000/dashboard"] * 2
    assert (checkout / ".stack" / "secrets.toml").read_bytes() == secrets

