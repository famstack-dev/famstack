"""The product's command on PATH, next to `./stack`.

After a fresh install an admin types `famstack status` in any terminal,
in any directory, and gets what `./stack status` gives in the checkout.
The installer puts a small script in Homebrew's bin directory for that.

That directory is shared with everything else Homebrew users install, so
the rules are about ownership: a free name is taken, our own script is
left as it is, and anyone else's command of that name is never written
over or removed. Which instance a command runs against is not the
script's business: `STACK_DIR` reaches the CLI unchanged.

Every test here uses a bin directory under tmp_path and a search path
made of tmp directories, so the machine's real PATH never decides a result.
"""

import os
import subprocess
import sys
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import pytest

REPO = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(REPO / "lib"))

from stack import global_command
from stack.global_command import FOREIGN, FREE, NAME, OURS


@pytest.fixture
def bin_dir(tmp_path):
    d = tmp_path / "bin"
    d.mkdir()
    return d


def _foreign(directory: Path) -> Path:
    """Somebody else's command of the same name."""
    other = directory / NAME
    other.write_text("#!/bin/sh\necho somebody else's famstack\n")
    other.chmod(0o755)
    return other


# ── Installing ──────────────────────────────────────────────────────────

class TestInstall:

    def test_a_free_name_is_taken(self, tmp_path, bin_dir):
        assert global_command.state(REPO, bin_dir, path=str(bin_dir)) == FREE
        assert global_command.install(REPO, bin_dir, path=str(bin_dir))
        assert global_command.state(REPO, bin_dir, path=str(bin_dir)) == OURS
        assert os.access(bin_dir / NAME, os.X_OK)

    def test_installing_again_changes_nothing(self, bin_dir):
        global_command.install(REPO, bin_dir, path=str(bin_dir))
        before = (bin_dir / NAME).stat()
        assert global_command.install(REPO, bin_dir, path=str(bin_dir))
        after = (bin_dir / NAME).stat()
        assert (after.st_mtime_ns, after.st_ino) == (before.st_mtime_ns, before.st_ino)

    def test_a_foreign_command_in_the_bin_dir_is_left_alone(self, bin_dir):
        other = _foreign(bin_dir)
        content = other.read_text()
        assert global_command.state(REPO, bin_dir, path=str(bin_dir)) == FOREIGN
        assert not global_command.install(REPO, bin_dir, path=str(bin_dir))
        assert other.read_text() == content

    def test_a_foreign_command_earlier_on_path_is_not_shadowed(self, tmp_path, bin_dir):
        # Ours would never run, and would change what the name means for
        # the user once their PATH changes. Leave the name to them.
        earlier = tmp_path / "earlier"
        earlier.mkdir()
        _foreign(earlier)
        path = os.pathsep.join([str(earlier), str(bin_dir)])
        assert global_command.state(REPO, bin_dir, path=path) == FOREIGN
        assert not global_command.install(REPO, bin_dir, path=path)
        assert not (bin_dir / NAME).exists()

    def test_the_command_of_another_checkout_is_foreign(self, tmp_path, bin_dir):
        other_checkout = tmp_path / "other-checkout"
        global_command.install(other_checkout, bin_dir, path=str(bin_dir))
        assert global_command.state(REPO, bin_dir, path=str(bin_dir)) == FOREIGN
        assert not global_command.install(REPO, bin_dir, path=str(bin_dir))


# ── Running it ──────────────────────────────────────────────────────────

class TestRunning:
    """The installed command is the checkout's `./stack`, from anywhere."""

    def _run(self, bin_dir, cwd, *args, env=None):
        global_command.install(REPO, bin_dir, path=str(bin_dir))
        return subprocess.run(
            [str(bin_dir / NAME), *args], cwd=cwd, env={**os.environ, **(env or {})},
            capture_output=True, text=True, timeout=60,
        )

    def test_it_runs_the_checkout_from_outside_any_checkout(self, tmp_path, bin_dir):
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        result = self._run(bin_dir, elsewhere, "version", env={"STACK_DIR": ""})
        assert result.returncode == 0, result.stderr
        assert "Can't find stack directory" not in result.stderr

    def test_stack_dir_still_selects_the_instance(self, tmp_path, bin_dir):
        # A path that does not exist makes the CLI refuse by name, which
        # proves the variable reached it through the script.
        missing = tmp_path / "no-such-instance"
        result = self._run(bin_dir, tmp_path, "list", env={"STACK_DIR": str(missing)})
        assert result.returncode != 0
        assert str(missing) in result.stderr


# ── Uninstalling ────────────────────────────────────────────────────────

def _instance(root: Path, instance_dir: Path | None = None):
    from stack import Stack
    from stack.output import CollectorOutput

    inst = instance_dir or root
    inst.mkdir(parents=True, exist_ok=True)
    # A product name other than the command's: the command is fixed.
    (inst / "stack.toml").write_text('[core]\nname = "stack"\n')
    (root / "stacklets").mkdir(parents=True, exist_ok=True)
    return Stack(root=root, data=root / "data", instance_dir=inst, output=CollectorOutput())


def _uninstall(stck, bin_dir, monkeypatch):
    from stack.cli import handle_uninstall

    monkeypatch.setattr(sys, "argv", ["stack", "uninstall"])
    monkeypatch.setattr(global_command, "bin_dir", lambda: bin_dir)
    with patch("stack.docker.all_project_ids", return_value=set()), \
         patch("builtins.input", return_value=""):
        handle_uninstall(stck, Namespace(yes=True))


class TestUninstall:

    def test_uninstall_removes_our_command(self, tmp_path, bin_dir, monkeypatch):
        checkout = tmp_path / "checkout"
        stck = _instance(checkout)
        global_command.install(checkout, bin_dir, path=str(bin_dir))
        _uninstall(stck, bin_dir, monkeypatch)
        assert not (bin_dir / NAME).exists()

    def test_uninstall_leaves_a_foreign_command(self, tmp_path, bin_dir, monkeypatch):
        stck = _instance(tmp_path / "checkout")
        other = _foreign(bin_dir)
        _uninstall(stck, bin_dir, monkeypatch)
        assert other.exists()

    def test_uninstalling_a_separate_instance_keeps_the_command(self, tmp_path, bin_dir, monkeypatch):
        # A STACK_DIR instance (a test run, a sandbox) shares the checkout.
        # The command still serves the checkout's own instance.
        checkout = tmp_path / "checkout"
        stck = _instance(checkout, instance_dir=tmp_path / "sandbox")
        global_command.install(checkout, bin_dir, path=str(bin_dir))
        _uninstall(stck, bin_dir, monkeypatch)
        assert global_command.is_ours(bin_dir / NAME, checkout)
