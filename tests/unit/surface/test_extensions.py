"""`stack extensions list` and `stack extensions add`: installing a stacklet
the release does not ship, from the catalog or from any git repo (ADR-022).

An extension is copied out of its repo, not cloned: `add` fetches the repo
into a temporary directory and writes one of its stacklets as a plain
folder into the first extension dir, the one discovery reads and the bot
runner mounts, with a `.source.toml` saying where it came from. Code from
anywhere but the catalog is code nobody reviewed for this stack, so its
`add` always shows a warning first, and `stack up` repeats where it came
from.

These tests run `./stack` in this process, through `stack.cli.main`,
against a fabricated instance whose `[core] extension_catalog` points at a
git repo built in the test, reached as `file://` like any remote: no
network. The extension dir starts out missing, as on an instance that
never had an extension.
"""

from __future__ import annotations

import os
import subprocess
import tomllib
from pathlib import Path

import pytest

from .conftest import StackCLI, make_stack, write_instance

# ── Repos built in the test ──────────────────────────────────────────────

# Who commits in a test repo. CI has no git identity, and the hooks of the
# checkout running the suite have no business in a throwaway repo.
_GIT_ENV = {"GIT_AUTHOR_NAME": "Lisa Simpson", "GIT_AUTHOR_EMAIL": "lisa@springfield.example",
            "GIT_COMMITTER_NAME": "Lisa Simpson", "GIT_COMMITTER_EMAIL": "lisa@springfield.example"}


def git(repo: Path, *args: str) -> str:
    """One git command in `repo`, its output."""
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", *args],
        check=True, capture_output=True, text=True, env={**os.environ, **_GIT_ENV}).stdout.strip()


def manifest(sid: str, name: str, **fields: str) -> str:
    """A stacklet.toml for a stacklet that runs no container."""
    lines = [f'id = "{sid}"', f'name = "{name}"',
             f'description = "{name}, for the tests"', 'type = "host"']
    lines += [f'{key} = "{value}"' for key, value in fields.items()]
    return "\n".join(lines) + "\n"


def commit(repo: Path, files: dict[str, str], subject: str = "feat: add the stacklets") -> Path:
    """Write these files into `repo` (made if missing) and commit them."""
    if not repo.exists():
        repo.mkdir(parents=True)
        git(repo, "init", "-q", "-b", "main")
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", subject)
    return repo


def url(repo: Path) -> str:
    return f"file://{repo}"


CATALOG_FILES = {
    "README.md": "The catalog.\n",
    "hue/stacklet.toml": manifest("hue", "Hue lights", stage="incubating"),
    "hue/cli/status.py": 'HELP = "The lights"\n\ndef run(args, stacklet, config):\n    return {}\n',
    "lamp/stacklet.toml": manifest("lamp", "Desk lamp"),
    "lamp/README.md": "Plug the lamp in, then: ./stack lamp on\n",
}

# A repo that is one extension, named in its manifest rather than by the
# repo, and bringing every kind of code the warning has to name.
KETTLE_FILES = {
    "stacklet.toml": manifest("kettle", "Kettle"),
    "docker-compose.yml": "services:\n  kettle-api:\n    image: kettle:1\n",
    "hooks/on_install.py": "def run(ctx):\n    pass\n",
    "hooks/on_start.py": "def run(ctx):\n    pass\n",
    "cli/brew.py": 'HELP = "Brew now"\n\ndef run(args, stacklet, config):\n    return {}\n',
    "bot/bot.toml": 'id = "kettle-bot"\nname = "Kettle"\n',
    # One level down, which a repo with a root manifest never searches.
    "spout/stacklet.toml": manifest("spout", "Spout"),
}

MULTI_FILES = {
    "README.md": "Two stacklets.\n",
    "a/stacklet.toml": manifest("a", "Alpha"),
    "b/stacklet.toml": manifest("b", "Bravo"),
}


@pytest.fixture
def catalog(tmp_path) -> Path:
    return commit(tmp_path / "repos" / "famstack-extensions.git", CATALOG_FILES)


@pytest.fixture
def kettle(tmp_path) -> str:
    return url(commit(tmp_path / "repos" / "kettle-stacklet", KETTLE_FILES, "feat: brew on a schedule"))


@pytest.fixture
def multi(tmp_path) -> str:
    return url(commit(tmp_path / "repos" / "multi", MULTI_FILES))


@pytest.fixture
def extensions(tmp_path) -> Path:
    """The instance's one extension dir, which does not exist yet."""
    return tmp_path / "extensions"


@pytest.fixture
def stack(tmp_path, monkeypatch, as_stack_runs_it, catalog, extensions) -> StackCLI:
    """`./stack` against an instance whose catalog is `catalog`."""
    instance = write_instance(tmp_path, core=f'extension_catalog = "{url(catalog)}"\n')
    extensions.rmdir()
    return StackCLI(instance, monkeypatch)


def source_of(folder: Path) -> dict:
    return tomllib.loads((folder / ".source.toml").read_text())


def everything(ran) -> str:
    return ran.out + ran.err


# ── add, from the catalog ────────────────────────────────────────────────

def test_add_from_the_catalog_installs_a_flat_folder_that_stack_list_shows(stack, extensions, catalog):
    ran = stack("extensions", "add", "hue")

    assert ran.code == 0, everything(ran)
    assert (extensions / "hue" / "stacklet.toml").is_file()
    assert (extensions / "hue" / "cli" / "status.py").is_file()
    assert not (extensions / "lamp").exists()
    assert "Start it with: stack up hue" in ran.out
    assert "not from the" not in everything(ran)
    listed = stack("list")
    assert "Extensions" in listed.out and "Hue lights" in listed.out


def test_add_records_where_the_extension_came_from(stack, extensions, catalog):
    stack("extensions", "add", "hue")

    source = source_of(extensions / "hue")
    assert source["url"] == url(catalog)
    assert source["path"] == "hue"
    assert source["branch"] == "main"
    assert source["commit"] == git(catalog, "rev-parse", "HEAD")
    assert source["tree"] == git(catalog, "rev-parse", "HEAD:hue")


def test_add_starts_nothing(stack, extensions):
    """Installing is putting files in place; `stack up` stays the admin's call."""
    ran = stack("extensions", "add", "hue")

    assert ran.code == 0 and (extensions / "hue").is_dir()
    assert not (stack.instance / ".stack" / "hue.up").exists()
    assert not (stack.instance / ".stack" / "hue.setup-done").exists()


def test_add_points_at_the_readme_and_names_stack_up_only_when_up_runs_something(stack):
    """lamp has a README and no container or hook, like hue in the real
    catalog: the README holds the next step, and `stack up lamp` would
    start nothing, so `add` does not tell the admin to run it."""
    ran = stack("extensions", "add", "lamp")

    assert ran.code == 0, everything(ran)
    assert "Next: read" in ran.out and "lamp/README.md" in ran.out
    assert "stack up lamp" not in ran.out


def test_add_names_stack_up_for_an_extension_with_containers_or_hooks(stack, kettle):
    ran = stack("extensions", "add", kettle, "--yes")

    assert ran.code == 0, everything(ran)
    assert "Start it with: stack up kettle" in ran.out


def test_a_copy_in_progress_is_invisible_to_discovery(stack, extensions, monkeypatch):
    """Until the copy is complete, no `stack` command finds the extension.
    Otherwise a copy cut off by a crash would claim the extension's id, and
    a hidden folder sorts ahead of the real one."""
    from stack import extensions as ext

    seen: list[str] = []
    archive = ext.Fetched.archive

    def archive_and_look(self, path, into):
        archive(self, path, into)
        seen.extend(s["id"] for s in make_stack(stack.instance).discover())

    monkeypatch.setattr(ext.Fetched, "archive", archive_and_look)
    ran = stack("extensions", "add", "lamp")

    assert ran.code == 0, everything(ran)
    assert "lamp" not in seen
    assert [p.name for p in extensions.iterdir()] == ["lamp"]


def test_a_url_that_starts_with_a_dash_is_a_url_never_an_option_to_git(stack, extensions):
    """`add` hands the URL to git. Read as an option, it would change what
    git does; read as a URL, git can only fail to find it."""
    ran = stack("extensions", "add", "--yes", "--", "--bare/x")

    assert ran.code == 1
    assert "repository '--bare/x' does not exist" in ran.err
    assert not extensions.exists()


def test_add_an_id_the_catalog_does_not_have_names_the_ones_it_has(stack, extensions):
    ran = stack("extensions", "add", "toaster")

    assert ran.code == 1
    assert "toaster" in ran.err and "hue" in ran.err and "lamp" in ran.err
    assert not extensions.exists()


# ── add, from any repo ───────────────────────────────────────────────────

def test_a_repo_with_a_root_manifest_is_one_extension_named_by_its_manifest(stack, extensions, kettle):
    ran = stack("extensions", "add", kettle, "--yes")

    assert ran.code == 0, everything(ran)
    assert (extensions / "kettle" / "stacklet.toml").is_file()
    assert not (extensions / "kettle-stacklet").exists()
    assert not (extensions / "spout").exists()
    assert source_of(extensions / "kettle")["path"] == "."


def test_from_a_repo_of_several_only_the_one_named_is_installed(stack, extensions, multi):
    ran = stack("extensions", "add", multi, "b", "--yes")

    assert ran.code == 0, everything(ran)
    assert (extensions / "b" / "stacklet.toml").is_file()
    assert not (extensions / "a").exists()
    assert "Bravo" in stack("list").out
    assert "Alpha" not in stack("list").out


def test_a_repo_of_several_without_an_id_asks_which_one_at_a_terminal(stack, extensions, multi):
    ran = stack("extensions", "add", multi, stdin="2\ny\n", terminal=True)

    assert ran.code == 0, everything(ran)
    assert "Alpha" in ran.out and "Bravo" in ran.out
    assert (extensions / "b").is_dir() and not (extensions / "a").exists()


def test_a_repo_of_several_without_an_id_or_a_terminal_names_them_and_installs_none(stack, extensions, multi):
    ran = stack("extensions", "add", multi, "--yes")

    assert ran.code == 1
    assert " a" in ran.err and " b" in ran.err
    assert not extensions.exists()


def test_a_repo_without_a_stacklet_is_refused(stack, extensions, tmp_path):
    empty = url(commit(tmp_path / "repos" / "notes", {"README.md": "Just notes.\n"}))

    ran = stack("extensions", "add", empty, "--yes")

    assert ran.code == 1
    assert f"No stacklet in {empty}" in ran.err
    assert not extensions.exists()


# ── The warning for code from elsewhere ─────────────────────────────────

def test_a_foreign_repo_warns_with_what_it_brings_even_with_yes(stack, kettle, tmp_path):
    ran = stack("extensions", "add", kettle, "--yes")

    shown = everything(ran)
    head = git(tmp_path / "repos" / "kettle-stacklet", "rev-parse", "--short", "HEAD")
    assert "kettle is not from the stack catalog" in shown
    assert kettle in shown and head in shown and "feat: brew on a schedule" in shown
    assert "kettle-api" in shown
    assert "on_install" in shown and "on_start" in shown
    assert "stack kettle brew" in shown
    assert "kettle-bot" in shown
    assert "secrets" in shown and "Install only code you trust" in shown
    assert "Install kettle?" not in shown


def test_a_foreign_repo_answered_no_writes_nothing(stack, extensions, kettle):
    ran = stack("extensions", "add", kettle, stdin="n\n", terminal=True)

    assert "not from the stack catalog" in ran.out
    assert "Install kettle?" in ran.out
    assert not extensions.exists()


def test_a_foreign_repo_answered_with_enter_writes_nothing(stack, extensions, kettle):
    """The question defaults to no: Enter is not consent to run strangers' code."""
    ran = stack("extensions", "add", kettle, stdin="\n", terminal=True)

    assert "Install kettle? [y/N]" in ran.out
    assert not extensions.exists()


def test_a_foreign_repo_answered_yes_is_installed(stack, extensions, kettle):
    ran = stack("extensions", "add", kettle, stdin="y\n", terminal=True)

    assert ran.code == 0, everything(ran)
    assert (extensions / "kettle" / "stacklet.toml").is_file()


def test_a_foreign_repo_without_a_terminal_or_yes_warns_then_refuses(stack, extensions, kettle):
    ran = stack("extensions", "add", kettle)

    assert ran.code == 1
    assert "not from the stack catalog" in ran.out
    assert "Pass --yes to confirm (non-interactive)" in ran.err
    assert not extensions.exists()


def test_the_catalogs_own_url_is_the_catalog(stack, extensions, catalog):
    """Spelled without `.git` too: the same repo is not a stranger."""
    ran = stack("extensions", "add", url(catalog).removesuffix(".git"), "lamp")

    assert ran.code == 0, everything(ran)
    assert "not from the" not in everything(ran)
    assert (extensions / "lamp" / "stacklet.toml").is_file()


# ── What add refuses, leaving nothing behind ─────────────────────────────

def test_an_id_the_release_ships_is_refused(stack, extensions, tmp_path):
    """The release wins a clash, so this one would never load."""
    docs = url(commit(tmp_path / "repos" / "docs", {"stacklet.toml": manifest("docs", "My docs")}))

    ran = stack("extensions", "add", docs, "--yes")

    assert ran.code == 1
    assert "docs" in ran.err and "ships with" in ran.err
    assert not extensions.exists()


def test_an_id_already_installed_is_refused_and_says_where(stack, extensions, tmp_path):
    stack("extensions", "add", "hue")
    other = url(commit(tmp_path / "repos" / "hue-fork", {"stacklet.toml": manifest("hue", "Other hue")}))

    ran = stack("extensions", "add", other, "--yes")

    assert ran.code == 1
    assert str(extensions / "hue") in ran.err
    assert source_of(extensions / "hue")["path"] == "hue"


@pytest.mark.parametrize("text, says", [
    ("id = \n", "does not parse"),
    ('name = "Kettle"\ndescription = "Boils"\n', "no id"),
    ('id = "kettle"\ndescription = "Boils"\n', "no name"),
    ('id = "kettle"\nname = "Kettle"\n', "no description"),
    ('id = "../kettle"\nname = "Kettle"\ndescription = "Boils"\n', "not a plain folder name"),
])
def test_a_manifest_that_would_not_install_is_refused(stack, extensions, tmp_path, text, says):
    bad = url(commit(tmp_path / "repos" / "bad", {"stacklet.toml": text}))

    ran = stack("extensions", "add", bad, "--yes")

    assert ran.code == 1
    assert says in ran.err
    assert not extensions.exists()


def test_a_fetch_that_fails_shows_gits_reason(stack, extensions, tmp_path):
    missing = url(tmp_path / "repos" / "nowhere")

    ran = stack("extensions", "add", missing, "--yes")

    assert ran.code == 1
    assert "nowhere" in ran.err and "fatal" in ran.err.lower()
    assert not extensions.exists()


def test_a_copy_that_fails_halfway_leaves_no_folder(stack, extensions, tmp_path):
    """A file name the disk cannot hold: git keeps it, the copy cannot
    write it, and the files written before it go again."""
    repo = commit(tmp_path / "repos" / "long", {"stacklet.toml": manifest("long", "Long"),
                                                "a.txt": "written first\n"})
    blob = subprocess.run(["git", "-C", str(repo), "hash-object", "-w", "--stdin"], input="x\n",
                          check=True, capture_output=True, text=True).stdout.strip()
    git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{'x' * 300}.txt")
    git(repo, "commit", "-q", "-m", "feat: a name too long")

    ran = stack("extensions", "add", url(repo), "--yes")

    assert ran.code == 1
    assert not extensions.exists()


# ── list ─────────────────────────────────────────────────────────────────

def test_list_with_nothing_installed_prints_the_catalog_and_how_to_install(stack, extensions):
    ran = stack("extensions", "list")

    assert ran.code == 0, everything(ran)
    for shown in ("hue", "Hue lights", "incubating", "lamp", "Desk lamp", "stack extensions add"):
        assert shown in ran.out
    assert "installed" not in ran.out
    assert not extensions.exists()


def test_list_shows_an_extension_from_the_catalog_as_installed(stack, catalog):
    stack("extensions", "add", "hue")

    hue = line_of(stack("extensions", "list").out, "hue")

    assert "installed" in hue and "update" not in hue


def test_list_says_when_the_catalog_has_a_newer_version(stack, catalog):
    stack("extensions", "add", "hue")
    commit(catalog, {"hue/stacklet.toml": manifest("hue", "Hue lights", stage="beta")}, "feat(hue): beta")

    hue = line_of(stack("extensions", "list").out, "hue")

    assert "update available" in hue


def test_a_change_elsewhere_in_the_catalog_is_no_update(stack, catalog):
    """An update is a change to the extension's own folder, not to the repo."""
    stack("extensions", "add", "hue")
    commit(catalog, {"lamp/stacklet.toml": manifest("lamp", "Desk lamp", stage="beta")}, "feat(lamp): beta")

    hue = line_of(stack("extensions", "list").out, "hue")

    assert "installed" in hue and "update" not in hue


def test_list_shows_one_from_another_repo_with_its_url_and_commit(stack, kettle, tmp_path):
    stack("extensions", "add", kettle, "--yes")

    ran = stack("extensions", "list")

    head = git(tmp_path / "repos" / "kettle-stacklet", "rev-parse", "--short", "HEAD")
    assert "Installed from elsewhere" in ran.out
    kettle_line = line_of(ran.out, "kettle")
    assert kettle.removeprefix("file://") in kettle_line and head in kettle_line


def test_a_folder_put_there_by_hand_is_listed_and_never_touched(stack, extensions):
    by_hand = extensions / "lamp"
    by_hand.mkdir(parents=True)
    (by_hand / "stacklet.toml").write_text(manifest("lamp", "My own lamp"))

    listed = stack("extensions", "list")
    added = stack("extensions", "add", "lamp")

    assert "by hand" in listed.out and "My own lamp" in listed.out
    assert added.code == 1 and str(by_hand) in added.err
    assert sorted(os.listdir(by_hand)) == ["stacklet.toml"]


def test_list_without_the_catalog_says_so_and_shows_the_last_one_fetched(stack, catalog, extensions):
    stack("extensions", "list")
    stack("extensions", "add", "hue")
    catalog.rename(catalog.with_name("moved"))

    ran = stack("extensions", "list")

    assert ran.code == 0, everything(ran)
    assert "Could not reach the catalog" in everything(ran)
    assert "lamp" in ran.out
    assert "installed" in line_of(ran.out, "hue")


def test_list_without_the_catalog_and_nothing_fetched_still_lists_what_is_installed(
        stack, catalog, kettle):
    stack("extensions", "add", kettle, "--yes")
    catalog.rename(catalog.with_name("moved"))

    ran = stack("extensions", "list")

    assert ran.code == 0, everything(ran)
    assert "Could not reach the catalog" in everything(ran)
    assert "kettle" in ran.out


def line_of(out: str, sid: str) -> str:
    """The line of `list` that starts with this id."""
    return next(line for line in out.splitlines() if line.strip().startswith(f"{sid} ")
                or line.strip() == sid)


# ── stack up ─────────────────────────────────────────────────────────────
# Through `Stack.up`, the part of `./stack up` before Docker. The command
# goes on to recreate core's containers on this Mac's Docker whenever a
# stack runs there, and a test must never touch a running instance.

def test_up_of_an_extension_from_elsewhere_says_where_it_comes_from(stack, multi):
    stack("extensions", "add", multi, "b", "--yes")

    up = make_stack(stack.instance).up("b")

    assert f"b comes from {multi}, not the stack catalog" in up["warnings"]


def test_up_of_an_extension_from_the_catalog_says_nothing_about_its_origin(stack):
    stack("extensions", "add", "lamp")

    up = make_stack(stack.instance).up("lamp")

    assert "error" not in up
    assert not [w for w in up["warnings"] if "comes from" in w]


# ── update ───────────────────────────────────────────────────────────────
# `stack extensions update` replaces an installed extension with the newest
# version of its path in its repo, on the branch it was installed from. A
# folder that differs from what was installed is never overwritten, and the
# `.env` that `stack up` writes into the folder survives the update.

def snapshot(folder: Path) -> dict[str, tuple[bytes, int, int]]:
    """Every file under `folder`: its bytes, modification time and mode."""
    found = {}
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            stat = path.stat()
            found[str(path.relative_to(folder))] = (path.read_bytes(), stat.st_mtime_ns, stat.st_mode)
    return found


LIGHTS = 'HELP = "Switch the lights"\n\ndef run(args, stacklet, config):\n    return {}\n'


def test_update_replaces_a_catalog_extension_and_keeps_the_env_stack_up_wrote(stack, extensions, catalog):
    stack("extensions", "add", "hue")
    hue = extensions / "hue"
    (hue / ".env").write_text("HUE_BRIDGE=10.0.0.9\n")
    (hue / "cli" / "__pycache__").mkdir()
    (hue / "cli" / "__pycache__" / "status.cpython-312.pyc").write_bytes(b"\0cache")
    commit(catalog, {"hue/cli/lights.py": LIGHTS}, "feat(hue): switch the lights")

    ran = stack("extensions", "update", "hue")

    assert ran.code == 0, everything(ran)
    assert "not from the" not in everything(ran)
    assert (hue / "cli" / "lights.py").read_text() == LIGHTS
    assert (hue / ".env").read_text() == "HUE_BRIDGE=10.0.0.9\n"
    assert source_of(hue)["tree"] == git(catalog, "rev-parse", "HEAD:hue")
    assert source_of(hue)["commit"] == git(catalog, "rev-parse", "HEAD")
    assert not (extensions / ".incoming").exists()
    listed = line_of(stack("extensions", "list").out, "hue")
    assert "installed" in listed and "update" not in listed


def test_list_says_how_to_apply_an_update(stack, catalog):
    stack("extensions", "add", "hue")
    commit(catalog, {"hue/cli/lights.py": LIGHTS}, "feat(hue): switch the lights")

    ran = stack("extensions", "list")

    assert "stack extensions update hue" in ran.out


def test_a_commit_elsewhere_in_the_repo_is_up_to_date_and_writes_nothing(stack, extensions, catalog):
    stack("extensions", "add", "hue")
    before = snapshot(extensions)
    commit(catalog, {"lamp/stacklet.toml": manifest("lamp", "Desk lamp", stage="beta")}, "feat(lamp): beta")

    ran = stack("extensions", "update", "hue")

    assert ran.code == 0, everything(ran)
    assert "up to date" in line_of(ran.out, "hue")
    assert snapshot(extensions) == before


def test_update_follows_the_branch_the_extension_was_installed_from(stack, extensions, catalog):
    """The repo's default branch moving to another branch is not an update."""
    stack("extensions", "add", "hue")
    git(catalog, "switch", "-q", "-c", "next")
    commit(catalog, {"hue/cli/lights.py": LIGHTS}, "feat(hue): switch the lights")

    ran = stack("extensions", "update", "hue")

    assert ran.code == 0, everything(ran)
    assert "up to date" in line_of(ran.out, "hue")
    assert not (extensions / "hue" / "cli" / "lights.py").exists()


@pytest.mark.parametrize("edit", ["changed", "added", "removed", "mode"])
def test_an_edited_extension_is_refused_and_keeps_the_edit(stack, extensions, catalog, edit):
    stack("extensions", "add", "hue")
    hue = extensions / "hue"
    status = hue / "cli" / "status.py"
    if edit == "changed":
        status.write_text("# my own version\n")
    elif edit == "added":
        (hue / "notes.txt").write_text("my notes\n")
    elif edit == "removed":
        status.unlink()
    else:
        status.chmod(0o755)
    before = snapshot(hue)
    commit(catalog, {"hue/cli/lights.py": LIGHTS}, "feat(hue): switch the lights")

    ran = stack("extensions", "update", "hue")

    assert ran.code == 1
    assert "changed since it was installed" in ran.err
    assert snapshot(hue) == before


def test_update_of_a_set_up_extension_says_to_restart_it_and_restarts_nothing(stack, extensions, catalog):
    stack("extensions", "add", "hue")
    (stack.instance / ".stack" / "hue.setup-done").write_text("")
    commit(catalog, {"hue/cli/lights.py": LIGHTS}, "feat(hue): switch the lights")

    ran = stack("extensions", "update", "hue")

    assert ran.code == 0, everything(ran)
    assert "Restart it to run the new code: stack restart hue" in ran.out
    assert not (stack.instance / ".stack" / "hue.up").exists()


def test_update_of_an_extension_never_set_up_names_no_restart(stack, catalog):
    stack("extensions", "add", "hue")
    commit(catalog, {"hue/cli/lights.py": LIGHTS}, "feat(hue): switch the lights")

    ran = stack("extensions", "update", "hue")

    assert ran.code == 0, everything(ran)
    assert "restart" not in ran.out


# ── update, from any repo ────────────────────────────────────────────────

def kettle_moves_on(tmp_path) -> Path:
    return commit(tmp_path / "repos" / "kettle-stacklet",
                  {"cli/descale.py": 'HELP = "Descale"\n\ndef run(args, stacklet, config):\n    return {}\n'},
                  "feat: descale")


def test_a_foreign_update_warns_with_the_new_version_even_with_yes(stack, extensions, kettle, tmp_path):
    stack("extensions", "add", kettle, "--yes")
    repo = kettle_moves_on(tmp_path)

    ran = stack("extensions", "update", "kettle", "--yes")

    shown = everything(ran)
    assert ran.code == 0, shown
    assert "kettle is not from the stack catalog" in shown
    assert git(repo, "rev-parse", "--short", "HEAD") in shown and "feat: descale" in shown
    assert "stack kettle brew, descale" in shown
    assert "Update kettle?" not in shown
    assert (extensions / "kettle" / "cli" / "descale.py").is_file()


def test_a_foreign_update_answered_no_changes_nothing(stack, extensions, kettle, tmp_path):
    stack("extensions", "add", kettle, "--yes")
    before = snapshot(extensions)
    kettle_moves_on(tmp_path)

    ran = stack("extensions", "update", "kettle", stdin="n\n", terminal=True)

    assert "not from the stack catalog" in ran.out
    assert "Update kettle? [y/N]" in ran.out
    assert snapshot(extensions) == before


def test_a_foreign_update_without_a_terminal_or_yes_warns_then_refuses(stack, extensions, kettle, tmp_path):
    stack("extensions", "add", kettle, "--yes")
    before = snapshot(extensions)
    kettle_moves_on(tmp_path)

    ran = stack("extensions", "update", "kettle")

    assert ran.code == 1
    assert "not from the stack catalog" in ran.out
    assert "Pass --yes to confirm (non-interactive)" in ran.err
    assert snapshot(extensions) == before


# ── update, of several ───────────────────────────────────────────────────

def test_update_without_an_id_updates_each_and_names_the_one_installed_by_hand(
        stack, extensions, catalog, kettle, tmp_path):
    stack("extensions", "add", "hue")
    stack("extensions", "add", kettle, "--yes")
    by_hand = extensions / "toaster"
    by_hand.mkdir()
    (by_hand / "stacklet.toml").write_text(manifest("toaster", "My toaster"))
    commit(catalog, {"hue/cli/lights.py": LIGHTS}, "feat(hue): switch the lights")
    kettle_moves_on(tmp_path)

    ran = stack("extensions", "update", "--yes")

    assert ran.code == 0, everything(ran)
    assert (extensions / "hue" / "cli" / "lights.py").is_file()
    assert (extensions / "kettle" / "cli" / "descale.py").is_file()
    assert "installed by hand, not updated" in line_of(ran.out, "toaster")
    assert sorted(os.listdir(by_hand)) == ["stacklet.toml"]


def test_one_refusal_does_not_stop_the_others_and_one_repo_is_fetched_once(stack, extensions, catalog):
    stack("extensions", "add", "hue")
    stack("extensions", "add", "lamp")
    (extensions / "hue" / "cli" / "status.py").write_text("# my own version\n")
    commit(catalog, {"hue/cli/lights.py": LIGHTS, "lamp/README.md": "Plug it in.\n"}, "feat: both")

    ran = stack("extensions", "update")

    assert ran.code == 1
    assert "changed since it was installed" in ran.err
    assert (extensions / "lamp" / "README.md").read_text() == "Plug it in.\n"
    assert not (extensions / "hue" / "cli" / "lights.py").exists()
    assert ran.err.count("Fetching") == 1


# ── What update refuses, changing nothing ───────────────────────────────

def test_update_of_an_id_not_installed_is_refused(stack, extensions):
    ran = stack("extensions", "update", "toaster")

    assert ran.code == 1
    assert "toaster" in ran.err and "not installed" in ran.err


def test_update_of_a_folder_put_there_by_hand_is_refused(stack, extensions):
    by_hand = extensions / "lamp"
    by_hand.mkdir(parents=True)
    (by_hand / "stacklet.toml").write_text(manifest("lamp", "My own lamp"))

    ran = stack("extensions", "update", "lamp")

    assert ran.code == 1
    assert "installed by hand: stack extensions does not change it" in ran.err
    assert sorted(os.listdir(by_hand)) == ["stacklet.toml"]


@pytest.mark.parametrize("change, says", [
    ({"hue/stacklet.toml": "id = \n"}, "does not parse"),
    ({"hue/stacklet.toml": manifest("lights", "Hue lights")}, "lights"),
    (None, "no longer"),
])
def test_a_new_version_that_would_not_install_is_refused(stack, extensions, catalog, change, says):
    stack("extensions", "add", "hue")
    before = snapshot(extensions)
    if change:
        commit(catalog, change, "feat(hue): break it")
    else:
        git(catalog, "rm", "-q", "-r", "hue")
        git(catalog, "commit", "-q", "-m", "feat: drop hue")

    ran = stack("extensions", "update", "hue")

    assert ran.code == 1
    assert says in ran.err
    assert snapshot(extensions) == before


def test_an_update_that_cannot_fetch_shows_gits_reason(stack, extensions, catalog):
    stack("extensions", "add", "hue")
    catalog.rename(catalog.with_name("moved"))

    ran = stack("extensions", "update", "hue")

    assert ran.code == 1
    assert "fatal" in ran.err.lower()
    assert (extensions / "hue" / "stacklet.toml").is_file()


def test_an_update_whose_copy_fails_halfway_leaves_the_old_folder(stack, extensions, catalog):
    """A file name the disk cannot hold: the new copy cannot be written,
    and the old folder stays as it was."""
    stack("extensions", "add", "hue")
    (extensions / "hue" / ".env").write_text("HUE_BRIDGE=10.0.0.9\n")
    before = snapshot(extensions)
    commit(catalog, {"hue/cli/lights.py": LIGHTS}, "feat(hue): switch the lights")
    blob = subprocess.run(["git", "-C", str(catalog), "hash-object", "-w", "--stdin"], input="x\n",
                          check=True, capture_output=True, text=True).stdout.strip()
    git(catalog, "update-index", "--add", "--cacheinfo", f"100644,{blob},hue/{'x' * 300}.txt")
    git(catalog, "commit", "-q", "-m", "feat(hue): a name too long")

    ran = stack("extensions", "update", "hue")

    assert ran.code == 1
    assert snapshot(extensions) == before
    assert not (extensions / ".incoming").exists()
