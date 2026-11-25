"""The release offer before a first install from `main`.

A person who clones famstack is on `main`, which is the work in progress.
Before the installer asks anything else, it offers the newest release, so
that a new install starts on a tested release and `stack update` then moves
it from release to release. These tests drive `./stack install` as a
process, in a temporary clone with release tags:

    v0.4.4 ── v0.5.0-beta.1 ── (main: this checkout's stack and lib/)

The tagged commits carry a stand-in `stack` that writes the release it ran
from into `ran-from`. A run that ends there proves that the switch started
the installer of the tag, and that the installer of `main` did not go on.
Without a TTY, a bare `./stack` shows the status, so the tests run
`./stack install`, which is the same installer. Every run ends at the
installer's first question after the offer, because the input ends there,
and the installer then stops without changes.
"""

from __future__ import annotations

import doctest
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from stack import install_release
from stack.updater import Checkout
from tests.springfield import write_instance

REPO = Path(__file__).resolve().parents[3]

# The tag's installer as the test sees it: it records the release it runs from.
TAGGED_STACK = """#!/bin/sh
cd "$(dirname "$0")" && git describe --tags > ran-from
"""

# A run's output reaches the welcome's question when the installer of the
# checkout went on.
WELCOME_QUESTION = "[Y/n]"


# ── The clone ────────────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args],
                            capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _commit_release(root: Path, tag: str, runnable: bool = True) -> None:
    """A commit with the stand-in `stack`, tagged as `tag`. With `runnable`
    False, the stand-in is not executable, so it cannot start."""
    stack = root / "stack"
    stack.write_text(TAGGED_STACK)
    stack.chmod(0o755 if runnable else 0o644)
    (root / "stacklets").mkdir(exist_ok=True)
    (root / "stacklets" / ".keep").write_text(tag)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", f"release {tag}")
    _git(root, "tag", "-a", tag, "-m", tag)


def _commit_main(root: Path) -> None:
    """The work in progress: this checkout's `stack` and `lib/`."""
    shutil.copy2(REPO / "stack", root / "stack")
    shutil.rmtree(root / "lib", ignore_errors=True)
    shutil.copytree(REPO / "lib", root / "lib",
                    ignore=shutil.ignore_patterns("__pycache__"))
    (root / "stacklets").mkdir(exist_ok=True)
    (root / "stacklets" / ".keep").write_text("main")
    (root / ".gitignore").write_text("ran-from\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "work in progress")


def a_clone(tmp_path: Path, tags=("v0.4.4", "v0.5.0-beta.1"), runnable: bool = True) -> Path:
    """A clone on `main`, one commit past the releases `tags`, in that order.
    With `runnable` False, the `stack` of each tag cannot start."""
    root = tmp_path / "famstack"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "core.hooksPath", "/dev/null")
    for tag in tags:
        _commit_release(root, tag, runnable)
    _commit_main(root)
    return root


def run_stack(root: Path, *args: str, typed: str = "") -> subprocess.CompletedProcess:
    """`./stack <args>` in the clone, with `typed` as the person's input."""
    env = {key: value for key, value in os.environ.items() if key != "STACK_DIR"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run([str(root / "stack"), *args], cwd=root, input=typed,
                          capture_output=True, text=True, env=env, timeout=60)


def ran_from(root: Path) -> str | None:
    """The release whose installer ran, or None when no tag's installer ran."""
    marker = root / "ran-from"
    return marker.read_text().strip() if marker.exists() else None


def position(root: Path) -> str:
    return Checkout(root).describe()


# ── The offer ────────────────────────────────────────────────────────────────

def test_a_clone_of_main_is_offered_the_newest_releases_first(tmp_path):
    """Given a clone on `main` past the newest tag, when the person runs the
    installer, then it offers the newest pre-release and the newest final
    release before it asks anything else. `main` stays until a choice."""
    root = a_clone(tmp_path)
    before = position(root)

    result = run_stack(root, "install")

    offered = result.stdout
    assert "v0.5.0-beta.1" in offered and "v0.4.4" in offered
    assert "main" in offered
    assert offered.index("v0.5.0-beta.1") < offered.index("v0.4.4")
    assert "work in progress" in offered[:offered.index("v0.5.0-beta.1")], \
        "the offer says that main is the work in progress before it lists the releases"
    assert WELCOME_QUESTION not in offered, "the input ended at the offer: nothing else is asked"
    assert position(root) == before
    assert ran_from(root) is None


@pytest.mark.parametrize("typed, tag", [("1\n", "v0.5.0-beta.1"), ("\n", "v0.5.0-beta.1"),
                                        ("2\n", "v0.4.4")])
def test_a_picked_release_is_checked_out_and_its_installer_runs(tmp_path, typed, tag):
    """Given the offer, when the person picks a release (Enter picks the
    recommended pre-release), then the checkout is on that tag, and the
    installer of that tag runs in place of the installer of `main`."""
    root = a_clone(tmp_path)

    result = run_stack(root, "install", typed=typed)

    assert result.returncode == 0, result.stderr
    assert position(root) == tag
    assert ran_from(root) == tag
    assert WELCOME_QUESTION not in result.stdout


def test_a_final_release_newer_than_every_prerelease_is_the_only_release_offered(tmp_path):
    """Given tags beta.1, 0.4.4 and 0.5.0, when the installer offers the
    releases, then it recommends 0.5.0 and offers no older pre-release: a
    person must not pick an old beta. Enter takes 0.5.0."""
    root = a_clone(tmp_path, tags=("v0.5.0-beta.1", "v0.4.4", "v0.5.0"))

    result = run_stack(root, "install", typed="\n")

    assert "v0.5.0-beta.1" not in result.stdout
    assert ran_from(root) == "v0.5.0"


def test_staying_on_main_installs_main_and_says_what_update_offers(tmp_path):
    """Given the offer, when the person picks `main`, then the installer of
    `main` goes on (to its welcome) and says that `stack update` offers
    releases only."""
    root = a_clone(tmp_path)
    before = position(root)

    result = run_stack(root, "install", typed="3\n")

    assert position(root) == before
    assert ran_from(root) is None
    after_the_choice = result.stdout.split("Number", 1)[1]
    assert "stack update" in after_the_choice
    assert WELCOME_QUESTION in after_the_choice


def test_the_examples_of_the_offer_hold():
    """The examples in `stack.install_release`'s docs are the order of the
    offer: a pre-release only when it is newer than every final release."""
    result = doctest.testmod(install_release)
    assert result.attempted > 0 and result.failed == 0


# ── When nothing is offered ──────────────────────────────────────────────────

def _asks_nothing_about_releases(root: Path, result, before: str) -> None:
    assert WELCOME_QUESTION in result.stdout, result.stdout + result.stderr
    assert "v0.4.4" not in result.stdout.split(WELCOME_QUESTION)[0]
    assert position(root) == before
    assert ran_from(root) is None


def test_a_clone_on_a_tag_is_asked_nothing_about_releases(tmp_path):
    """Given a clone on a tag (the stand-in replaced by this checkout's
    installer), when the person runs the installer, then it asks nothing
    about releases."""
    root = a_clone(tmp_path)
    _git(root, "tag", "v0.6.0-beta.1")
    _git(root, "checkout", "-q", "v0.6.0-beta.1")
    before = position(root)

    _asks_nothing_about_releases(root, run_stack(root, "install"), before)


def test_a_clone_on_main_exactly_at_a_tag_is_asked_nothing(tmp_path):
    """Given `main` on the newest tag, with no commit after it, then the
    checkout is that release and nothing is asked."""
    root = a_clone(tmp_path)
    _git(root, "tag", "v0.6.0-beta.1")
    before = position(root)

    _asks_nothing_about_releases(root, run_stack(root, "install"), before)


def test_a_clone_on_another_branch_is_asked_nothing_about_releases(tmp_path):
    """Given a clone on a branch other than `main` (a branch under test),
    then the installer installs it as it is."""
    root = a_clone(tmp_path)
    _git(root, "checkout", "-q", "-b", "feature/snapshot")
    before = position(root)

    _asks_nothing_about_releases(root, run_stack(root, "install"), before)


def test_a_clone_without_tags_says_it_found_no_release_and_goes_on(tmp_path):
    """Given a shallow clone without tags, then the installer says that it
    found no release, and installs the checkout as it is."""
    root = a_clone(tmp_path, tags=())
    before = position(root)

    result = run_stack(root, "install")

    welcome = result.stdout.index(WELCOME_QUESTION)
    assert "main" in result.stdout[:welcome], "the notice names the checkout it installs"
    assert position(root) == before
    assert ran_from(root) is None


def test_a_checkout_with_local_edits_is_not_switched(tmp_path):
    """Given a clone on `main` with a changed file, then the installer does
    not switch, names the changed file, and installs the checkout as it is,
    because a switch would carry the edits into another release or fail
    halfway."""
    root = a_clone(tmp_path)
    (root / "lib" / "stack" / "prompt.py").write_text(
        (root / "lib" / "stack" / "prompt.py").read_text() + "\n# an edit\n")
    head = _git(root, "rev-parse", "HEAD")

    result = run_stack(root, "install", typed="1\n")

    welcome = result.stdout.index(WELCOME_QUESTION)
    assert "lib/stack/prompt.py" in result.stdout[:welcome]
    assert _git(root, "rev-parse", "HEAD") == head
    assert ran_from(root) is None


@pytest.mark.usefixtures("docker_with_no_containers")
def test_an_install_that_is_set_up_is_asked_nothing_and_stays(tmp_path):
    """Given stack.toml and a family, when the admin runs `./stack`, then
    nothing changes: no question, no switch. The stack's code never jumps
    to another release without the update's checks."""
    root = a_clone(tmp_path)
    instance = write_instance(tmp_path / "springfield")
    for name in ("stack.toml", "users.toml"):
        shutil.copy2(instance / name, root / name)
    (root / ".gitignore").write_text("ran-from\nstack.toml\nusers.toml\n")
    _git(root, "commit", "-qam", "ignore the instance's files")
    before = position(root)

    result = run_stack(root, "install", typed="1\n")

    assert "v0.5.0-beta.1" not in result.stdout
    assert position(root) == before
    assert ran_from(root) is None


@pytest.mark.usefixtures("docker_with_no_containers")
def test_an_install_that_stopped_halfway_is_asked_nothing_and_stays(tmp_path):
    """Given a stack.toml alone (no family, no core: the install stopped at
    a question), when the admin runs `./stack` again, then the installer
    goes on with the checkout as it is: no offer, no switch. Its code
    changes only through `stack update`."""
    root = a_clone(tmp_path)
    instance = write_instance(tmp_path / "springfield")
    shutil.copy2(instance / "stack.toml", root / "stack.toml")
    (root / ".gitignore").write_text("ran-from\nstack.toml\n")
    _git(root, "commit", "-qam", "ignore the instance's files")
    before = position(root)

    result = run_stack(root, "install", typed="1\n")

    assert "v0.5.0-beta.1" not in result.stdout
    assert position(root) == before
    assert ran_from(root) is None


def test_a_release_whose_installer_cannot_start_says_how_to_go_back(tmp_path):
    """Given a picked release whose `./stack` cannot start, when the switch
    runs, then the installer ends with an error and no traceback. It names
    the tag the checkout is now on and the command that goes back to main,
    because the checkout has already moved."""
    root = a_clone(tmp_path, runnable=False)

    result = run_stack(root, "install", typed="1\n")

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert position(root) == "v0.5.0-beta.1"
    assert "v0.5.0-beta.1" in result.stdout.split("Number", 1)[1]
    assert "git checkout main" in result.stdout


def test_a_script_install_neither_asks_nor_switches(tmp_path):
    """Given `./stack install --family <file> --yes` on `main`, then it
    installs the checkout as it is, because the e2e runs install a branch
    under test this way."""
    root = a_clone(tmp_path)
    before = position(root)
    family = tmp_path / "family.toml"
    family.write_text("this is not a family file\n")

    result = run_stack(root, "install", "--family", str(family), "--yes")

    assert "v0.5.0-beta.1" not in result.stdout + result.stderr
    assert position(root) == before
    assert ran_from(root) is None
