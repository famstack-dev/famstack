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
