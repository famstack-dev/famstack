"""Extension stacklets and the stage contract.

The repo ships the stacklets a release supports. Everything else — a
stacklet written for one household, a community one, one still being
designed — lives in the extensions directory and is discovered from
there. A stacklet author gets the same manifest, the same id, the same
lifecycle either way; only the location differs.

`stage` is the other half: a stacklet says how finished it is, and the
runtime repeats that to whoever runs it.
"""

import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CORE = REPO_ROOT / "stacklets" / "core"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _stacklet_at(root: Path, sid: str, name: str) -> Path:
    """Write a minimal stacklet into `root` and return `root`."""
    (root / sid).mkdir(parents=True, exist_ok=True)
    (root / sid / "stacklet.toml").write_text(f'id = "{sid}"\nname = "{name}"\n')
    return root


def _unconfigure(stck, key: str) -> None:
    """Drop a [core] key so the framework's own default applies."""
    cfg = stck.instance_dir / "stack.toml"
    cfg.write_text("\n".join(
        line for line in cfg.read_text().splitlines()
        if not line.startswith(key)))


def _configure(stck, line: str) -> None:
    """Set a [core] key in the instance's stack.toml, replacing any the
    test fixture already wrote (a duplicate key is a TOML error)."""
    _unconfigure(stck, line.split("=", 1)[0].strip())
    cfg = stck.instance_dir / "stack.toml"
    cfg.write_text(cfg.read_text().replace("[core]", f"[core]\n{line}", 1))


def _runner():
    """The bot runner module, imported the way the container sees it."""
    sys.path.insert(0, str(CORE / "bot-runner"))
    import main

    return main


class TestExtensionDiscovery:
    """Stacklets outside the repo are first-class."""

    def test_finds_stacklets_in_the_extensions_dir(self, make_stack):
        stck = make_stack(stacklets=["basic"], extensions=["with_hooks"])
        ids = {s["id"] for s in stck.discover()}
        assert ids == {"basic", "with_hooks"}

    def test_reports_where_a_stacklet_came_from(self, make_stack):
        """`source` is what tells an operator which tree to edit."""
        stck = make_stack(stacklets=["basic"], extensions=["with_hooks"])
        by_id = {s["id"]: s for s in stck.discover()}
        assert by_id["basic"]["source"] == "repo"
        assert by_id["with_hooks"]["source"] == "extension"

    def test_path_points_into_the_extensions_dir(self, make_stack):
        """Hooks, compose files and CLI plugins all resolve from `path`."""
        stck = make_stack(extensions=["with_hooks"])
        found = stck.discover()[0]
        assert Path(found["path"]) == stck.mounted_extension_dir / "with_hooks"

    def test_repo_wins_a_name_clash(self, make_stack):
        """An extension must never shadow a stacklet the release ships."""
        stck = make_stack(stacklets=["basic"])
        clash = stck.mounted_extension_dir / "basic"
        clash.mkdir(parents=True)
        (clash / "stacklet.toml").write_text(
            'id = "basic"\nname = "Impostor"\n')

        found = [s for s in stck.discover() if s["id"] == "basic"]
        assert len(found) == 1
        assert found[0]["name"] == "Basic"
        assert found[0]["source"] == "repo"

    def test_a_configured_dir_that_is_not_there_is_skipped(self, make_stack):
        """Most instances never grow one. Its absence is not a failure."""
        stck = make_stack(stacklets=["basic"])

        assert not stck.mounted_extension_dir.exists()
        assert [s["id"] for s in stck.discover()] == ["basic"]

    def test_the_default_is_a_product_named_dir_in_the_admins_home(self, make_stack):
        """Not in the repo, which an upgrade replaces, and not in the data
        dir, which is what running services write. The admin's own."""
        stck = make_stack()
        _unconfigure(stck, "extension_dirs")

        assert stck.extension_dirs == [Path.home() / "stack-extensions"]
        assert stck.root not in stck.mounted_extension_dir.parents
        assert stck.data not in stck.mounted_extension_dir.parents

    def test_nothing_creates_the_default(self, make_stack):
        """An instance with no extensions has no such directory."""
        stck = make_stack(stacklets=["basic"])
        _unconfigure(stck, "extension_dirs")
        before = stck.mounted_extension_dir.exists()

        stck.up("basic")

        assert stck.mounted_extension_dir.exists() == before

    def test_a_single_dir_can_be_written_as_a_string(self, make_stack, tmp_path):
        """TOML invites `extension_dirs = "~/dev"`. Read it as one entry
        rather than as a list of characters."""
        elsewhere = _stacklet_at(tmp_path / "dev", "basic", "From Dev")

        stck = make_stack()
        _configure(stck, f'extension_dirs = "{elsewhere}"')

        assert stck.extension_dirs == [elsewhere]
        assert [s["name"] for s in stck.discover()] == ["From Dev"]

    def test_several_dirs_are_searched_in_order(self, make_stack, tmp_path):
        """A dev checkout beside the default one, resolved like a PATH."""
        first = _stacklet_at(tmp_path / "dev", "basic", "From Dev")
        second = _stacklet_at(tmp_path / "vendor", "basic", "From Vendor")
        _stacklet_at(tmp_path / "vendor", "extra", "Only In Vendor")

        stck = make_stack()
        _configure(stck, f'extension_dirs = ["{first}", "{second}"]')

        assert stck.extension_dirs == [first, second]
        by_id = {s["id"]: s for s in stck.discover()}
        assert by_id["basic"]["name"] == "From Dev", "earlier dir wins the id"
        assert by_id["extra"]["name"] == "Only In Vendor"

    def test_the_first_dir_is_the_one_containers_see(self, make_stack, tmp_path):
        """Compose cannot iterate a list, so one dir is bind-mounted, and
        `{extensions_dir}` names that one wherever the stacklet itself
        was loaded from."""
        first = _stacklet_at(tmp_path / "dev", "quiet", "Quiet")
        second = tmp_path / "vendor"
        shutil.copytree(FIXTURES_DIR / "incubating", second / "incubating")

        stck = make_stack()
        _configure(stck, f'extension_dirs = ["{first}", "{second}"]')

        assert stck.mounted_extension_dir == first
        assert stck.env("incubating")["EXT_DIR"] == str(first)

    def test_a_bot_outside_the_mounted_dir_is_flagged(self, make_stack, tmp_path):
        """The one thing a second dir silently costs: the bot runner
        cannot see it. Nothing else in the system would ever say so."""
        mounted = _stacklet_at(tmp_path / "dev", "quiet", "Quiet")
        other = _stacklet_at(tmp_path / "vendor", "chatty", "Chatty")
        (other / "chatty" / "bot").mkdir()

        stck = make_stack()
        _configure(stck, f'extension_dirs = ["{mounted}", "{other}"]')

        assert stck.up("quiet")["warnings"] == []
        warning = " ".join(stck.up("chatty")["warnings"])
        assert "bot runner" in warning
        assert str(mounted) in warning

    def test_the_mount_source_is_a_template_var(self, make_stack):
        """core renders EXTENSIONS_DIR from it to mount the tree for bots."""
        stck = make_stack(extensions=["incubating"])
        env = stck.env("incubating")
        assert env["EXT_DIR"] == str(stck.mounted_extension_dir)

    def test_the_mount_falls_back_while_there_is_nothing_to_mount(self, make_stack):
        """A bind mount needs a source that exists. An instance with no
        extensions gets an empty stand-in in the runtime state dir rather
        than a directory in the admin's home it never asked for."""
        stck = make_stack(stacklets=["incubating"])

        mount = stck.extension_mount_source
        assert mount == stck.instance_dir / ".stack" / "no-extensions"
        assert stck.env("incubating")["EXT_DIR"] == str(mount)

    def test_the_real_dir_takes_over_as_soon_as_it_exists(self, make_stack):
        stck = make_stack(stacklets=["incubating"])
        stck.mounted_extension_dir.mkdir(parents=True)

        assert stck.extension_mount_source == stck.mounted_extension_dir


class TestStage:
    """A stacklet declares how finished it is; `stack up` repeats it."""

    def test_stage_defaults_to_stable(self, make_stack):
        stck = make_stack(stacklets=["basic"])
        assert stck.discover()[0]["stage"] == "stable"

    def test_stage_is_read_from_the_manifest(self, make_stack):
        stck = make_stack(stacklets=["incubating"])
        assert stck.discover()[0]["stage"] == "incubating"

    def test_up_warns_about_an_incubating_stacklet(self, make_stack):
        """The warning is the whole point: no promises, not for production."""
        stck = make_stack(stacklets=["incubating"])
        result = stck.up("incubating")

        assert result["stage"] == "incubating"
        warning = " ".join(result["warnings"])
        assert "incubating" in warning.lower()
        assert "production" in warning.lower()
        assert warning in " ".join(stck.output.warnings)

    def test_up_says_nothing_about_a_stable_stacklet(self, make_stack):
        stck = make_stack(stacklets=["basic"])
        result = stck.up("basic")
        assert result["warnings"] == []
        assert stck.output.warnings == []

    def test_an_unknown_stage_still_warns(self, make_stack):
        """Anything but 'stable' is a claim of unfinishedness. Repeat it."""
        stck = make_stack()
        ext = stck.mounted_extension_dir / "alpha_thing"
        ext.mkdir(parents=True)
        (ext / "stacklet.toml").write_text(
            'id = "alpha_thing"\nname = "Alpha Thing"\nstage = "alpha"\n')

        result = stck.up("alpha_thing")
        assert result["source"] == "extension"
        assert "alpha" in " ".join(result["warnings"]).lower()


class TestListRendering:
    """`stack list` keeps the two origins apart and repeats the stage."""

    def _listing(self):
        return {
            "stacklets": [
                {"id": "photos", "name": "Photos", "source": "repo",
                 "online": True, "enabled": True},
                {"id": "drive", "name": "Drive", "source": "extension",
                 "stage": "incubating"},
            ],
            "online": 1,
            "total": 2,
            "extension_dirs": ["/data/extensions"],
        }

    def test_extensions_are_listed_under_their_own_heading(self, capsys):
        """Where a stacklet came from decides who supports it, so the
        reader should not have to remember which name is not ours."""
        from stack.cli import print_list

        print_list(self._listing())
        out = capsys.readouterr().out

        assert out.index("Photos") < out.index("Extensions") < out.index("Drive")

    def test_the_heading_names_the_directory(self, capsys):
        """The answer to "where did this come from" is in the output, not
        in the docs."""
        from stack.cli import print_list

        print_list(self._listing())
        assert "/data/extensions" in capsys.readouterr().out

    def test_an_instance_without_extensions_reads_as_before(self, capsys):
        from stack.cli import print_list

        listing = self._listing()
        listing["stacklets"] = listing["stacklets"][:1]
        print_list(listing)

        assert "Extensions" not in capsys.readouterr().out

    def test_the_stage_is_marked_on_the_stacklet_line(self, capsys):
        from stack.prompt import status_list

        status_list([{"id": "drive", "name": "Drive", "stage": "incubating"}])
        assert "incubating" in capsys.readouterr().out

    def test_a_stable_stacklet_line_is_unchanged(self, capsys):
        from stack.prompt import status_list

        status_list([{"id": "photos", "name": "Photos", "stage": "stable",
                      "online": True, "enabled": True}])
        assert capsys.readouterr().out.strip().endswith("online")


class TestBotRunnerMount:
    """The bots' view of the extensions tree.

    Three files have to agree before a bot shipped by an extension
    stacklet can run: core's manifest renders the host path, core's
    compose mounts it, and the runner scans the container path. Nothing
    reports it when one of them drifts. The bot just never appears.
    """

    def test_core_mounts_the_path_the_framework_renders(self):
        import tomllib

        manifest = tomllib.loads((CORE / "stacklet.toml").read_text())
        rendered = manifest["env"]["defaults"]["EXTENSIONS_DIR"]
        assert rendered == "{extension_mount}"
        assert "${EXTENSIONS_DIR}:" in (CORE / "docker-compose.yml").read_text()

    def test_the_runner_scans_where_compose_mounts(self):
        import re

        compose = (CORE / "docker-compose.yml").read_text()
        mounted = re.search(r"\$\{EXTENSIONS_DIR\}:(/[^\s:]+)", compose).group(1)
        assert str(_runner().EXTENSIONS_DIR) == mounted

    def test_a_bot_is_found_in_either_tree(self, tmp_path):
        repo, extensions = tmp_path / "stacklets", tmp_path / "extensions"
        (repo / "docs" / "bot").mkdir(parents=True)
        (extensions / "drive" / "bot").mkdir(parents=True)
        roots = (repo, extensions)

        bot_dir_for = _runner().bot_dir_for
        assert bot_dir_for("docs", roots) == repo / "docs" / "bot"
        assert bot_dir_for("drive", roots) == extensions / "drive" / "bot"

    def test_the_repo_tree_wins(self, tmp_path):
        """Same rule as discovery: an extension never shadows a release."""
        repo, extensions = tmp_path / "stacklets", tmp_path / "extensions"
        (repo / "docs" / "bot").mkdir(parents=True)
        (extensions / "docs" / "bot").mkdir(parents=True)

        found = _runner().bot_dir_for("docs", (repo, extensions))
        assert found == repo / "docs" / "bot"
