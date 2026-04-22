"""Uninstall: only destroys stacklets that have state (data, markers, containers)."""

import sys
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "lib"))


def _make_env(tmp_path, stacklet_ids):
    """Set up an isolated Stack with multiple stacklets."""
    from stack import Stack
    from stack.output import CollectorOutput

    (tmp_path / "stack.toml").write_text('[core]\ntimezone = "UTC"\n')
    (tmp_path / "users.toml").write_text(
        '[[users]]\nname = "Test"\nemail = "t@t"\npassword = "p"\nrole = "admin"\n')
    stack_dir = tmp_path / ".stack"
    stack_dir.mkdir(exist_ok=True)
    (stack_dir / "secrets.toml").write_text('global__ADMIN_PASSWORD = "test"\n')

    for sid in stacklet_ids:
        sdir = tmp_path / "stacklets" / sid
        sdir.mkdir(parents=True, exist_ok=True)
        (sdir / "stacklet.toml").write_text(
            f'id = "{sid}"\nname = "{sid.title()}"\ncategory = "test"\n')

    output = CollectorOutput()
    stck = Stack(root=tmp_path, data=tmp_path / "data", output=output)
    return stck


class TestUninstallSelectivity:

    def test_skips_clean_stacklets(self, tmp_path):
        """Stacklets with no data, no marker, no container are not destroyed."""
        from stack.cli import handle_uninstall

        stck = _make_env(tmp_path, ["alpha", "beta", "gamma"])

        # Only beta has data on disk
        (tmp_path / "data" / "beta").mkdir(parents=True)

        destroyed = []
        original_destroy = stck.destroy

        def tracking_destroy(sid):
            destroyed.append(sid)
            return original_destroy(sid)

        stck.destroy = tracking_destroy
        args = Namespace(yes=True)

        with patch("stack.docker.all_project_ids", return_value=set()), \
             patch("stack.docker.compose_down", return_value=(0, "")), \
             patch("builtins.input", return_value=""):
            handle_uninstall(stck, args)

        assert destroyed == ["beta"]

    def test_destroys_stacklet_with_marker(self, tmp_path):
        """Stacklets with a setup-done marker are destroyed."""
        from stack.cli import handle_uninstall

        stck = _make_env(tmp_path, ["alpha", "beta"])

        # alpha has setup-done marker (was set up)
        marker = tmp_path / ".stack" / "alpha.setup-done"
        marker.touch()

        destroyed = []
        original_destroy = stck.destroy

        def tracking_destroy(sid):
            destroyed.append(sid)
            return original_destroy(sid)

        stck.destroy = tracking_destroy
        args = Namespace(yes=True)

        with patch("stack.docker.all_project_ids", return_value=set()), \
             patch("stack.docker.compose_down", return_value=(0, "")):
            handle_uninstall(stck, args)

        assert "alpha" in destroyed
        assert "beta" not in destroyed

    def test_destroys_stacklet_with_container(self, tmp_path):
        """Stacklets with lingering Docker containers are destroyed."""
        from stack.cli import handle_uninstall

        stck = _make_env(tmp_path, ["alpha", "beta", "gamma"])

        destroyed = []
        original_destroy = stck.destroy

        def tracking_destroy(sid):
            destroyed.append(sid)
            return original_destroy(sid)

        stck.destroy = tracking_destroy
        args = Namespace(yes=True)

        # gamma has a stopped container in Docker
        with patch("stack.docker.all_project_ids", return_value={"gamma"}), \
             patch("stack.docker.compose_down", return_value=(0, "")):
            handle_uninstall(stck, args)

        assert destroyed == ["gamma"]

    def test_not_setup_prints_message(self, tmp_path, capsys):
        """When nothing is set up, prints info message instead of uninstalling."""
        from stack.cli import handle_uninstall
        from stack import Stack
        from stack.output import CollectorOutput

        # No config, no state, no data
        (tmp_path / "stacklets").mkdir()
        stck = Stack(root=tmp_path, data=tmp_path / "data", output=CollectorOutput())
        args = Namespace(yes=True)

        with patch("stack.docker.all_project_ids", return_value=set()):
            handle_uninstall(stck, args)

        captured = capsys.readouterr().out
        assert "not set up" in captured

    def test_removes_config_files(self, tmp_path):
        """Config files are removed after stacklet destruction."""
        from stack.cli import handle_uninstall

        stck = _make_env(tmp_path, [])
        args = Namespace(yes=True)

        assert (tmp_path / "stack.toml").exists()
        assert (tmp_path / "users.toml").exists()

        with patch("stack.docker.all_project_ids", return_value=set()), \
             patch("stack.docker.compose_down", return_value=(0, "")):
            handle_uninstall(stck, args)

        assert not (tmp_path / "stack.toml").exists()
        assert not (tmp_path / "users.toml").exists()


class TestUninstallNotFlag:
    """`--not X` preserves X's state while uninstalling everything else.

    Use case: uninstalling a stack where one stacklet (typically `ai`)
    carries very expensive state such as downloaded model weights. The
    flag lets the user avoid a multi-gigabyte re-download on the next
    install by keeping that stacklet's container, data dir, and setup
    marker untouched.
    """

    def test_excluded_stacklet_is_not_destroyed(self, tmp_path):
        from stack.cli import handle_uninstall

        stck = _make_env(tmp_path, ["alpha", "beta", "ai"])
        # Both alpha and ai have markers; only alpha should get destroyed.
        (tmp_path / ".stack" / "alpha.setup-done").touch()
        (tmp_path / ".stack" / "ai.setup-done").touch()

        destroyed: list[str] = []
        original_destroy = stck.destroy
        stck.destroy = lambda sid: (destroyed.append(sid), original_destroy(sid))[1]

        args = Namespace(yes=True, exclude=["ai"])

        with patch("stack.docker.all_project_ids", return_value=set()), \
             patch("stack.docker.compose_down", return_value=(0, "")), \
             patch("builtins.input", return_value=""):
            handle_uninstall(stck, args)

        assert destroyed == ["alpha"]

    def test_excluded_stacklet_keeps_setup_marker(self, tmp_path):
        from stack.cli import handle_uninstall

        stck = _make_env(tmp_path, ["alpha", "ai"])
        (tmp_path / ".stack" / "alpha.setup-done").touch()
        (tmp_path / ".stack" / "ai.setup-done").touch()

        args = Namespace(yes=True, exclude=["ai"])

        with patch("stack.docker.all_project_ids", return_value=set()), \
             patch("stack.docker.compose_down", return_value=(0, "")), \
             patch("builtins.input", return_value=""):
            handle_uninstall(stck, args)

        # ai's marker and the .stack/ dir both survive; alpha's marker is gone.
        assert (tmp_path / ".stack" / "ai.setup-done").exists()
        assert not (tmp_path / ".stack" / "alpha.setup-done").exists()

    def test_without_not_flag_marker_dir_is_fully_removed(self, tmp_path):
        """Sanity check: plain uninstall still wipes .stack/ as before."""
        from stack.cli import handle_uninstall

        stck = _make_env(tmp_path, ["alpha"])
        (tmp_path / ".stack" / "alpha.setup-done").touch()

        args = Namespace(yes=True, exclude=[])

        with patch("stack.docker.all_project_ids", return_value=set()), \
             patch("stack.docker.compose_down", return_value=(0, "")), \
             patch("builtins.input", return_value=""):
            handle_uninstall(stck, args)

        assert not (tmp_path / ".stack").exists()

    def test_excluded_data_subdir_survives_delete_prompt(self, tmp_path):
        """Even when the user confirms 'delete' on the data prompt, the
        excluded stacklet's data subdirectory must stay intact — that's
        the whole reason this flag exists (avoid redownloading models)."""
        from stack.cli import handle_uninstall

        stck = _make_env(tmp_path, ["alpha", "ai"])
        (stck.data / "alpha").mkdir(parents=True)
        (stck.data / "ai").mkdir(parents=True)
        (stck.data / "ai" / "model.bin").write_text("expensive weights")

        args = Namespace(yes=True, exclude=["ai"])

        with patch("stack.docker.all_project_ids", return_value=set()), \
             patch("stack.docker.compose_down", return_value=(0, "")), \
             patch("builtins.input", return_value="delete"):
            handle_uninstall(stck, args)

        assert not (stck.data / "alpha").exists()
        assert (stck.data / "ai" / "model.bin").read_text() == "expensive weights"
