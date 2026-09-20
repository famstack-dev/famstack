"""Moving a checkout from one release to the next.

A release is a git tag, so an update is: find the tags, work out what the
jump changes, move the working tree, and restart the stacklets whose files
moved under them. The reasoning lives in `stack.updater`; this pins it.

The git tests drive a real repository in a temp directory. Git's behaviour
under a dirty tree is the whole reason this module exists, and a fake would
only prove that the fake agrees with what we assumed.
"""

import subprocess
from pathlib import Path

import pytest

from stack.updater import (
    Checkout, latest_tag, restart_targets, running_version, stale_stacklets,
    version_key,
)


# ── Fixtures ─────────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A checkout with two releases, shaped like the real one.

    v1 ships a `docs` stacklet; v2 changes it, adds `photos`, and touches
    the framework, so one repository covers every restart case.
    """
    root = tmp_path / "checkout"
    (root / "stacklets" / "docs").mkdir(parents=True)
    (root / "lib" / "stack").mkdir(parents=True)
    _git(root.parent, "init", "-q", str(root))
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")

    (root / "stacklets" / "docs" / "docker-compose.yml").write_text("image: paperless:1\n")
    (root / "lib" / "stack" / "cli.py").write_text("VERSION = 'v1'\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "first release")
    _git(root, "tag", "v0.1.0")

    (root / "stacklets" / "docs" / "docker-compose.yml").write_text("image: paperless:2\n")
    (root / "stacklets" / "photos").mkdir()
    (root / "stacklets" / "photos" / "stacklet.toml").write_text('id = "photos"\n')
    (root / "lib" / "stack" / "cli.py").write_text("VERSION = 'v2'\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "second release")
    _git(root, "tag", "v0.2.0")

    _git(root, "checkout", "-q", "v0.1.0")
    return root


# ── Which tag is the newest ──────────────────────────────────────────────

class TestLatestRelease:
    """Pre-1.0 tags are mostly prereleases, and they have to sort right."""

    def test_picks_the_highest_version(self):
        assert latest_tag(["v0.2.1", "v0.3.0", "v0.2.2"]) == "v0.3.0"

    def test_a_release_outranks_its_own_prereleases(self):
        assert latest_tag(["v0.3.0-beta.3", "v0.3.0"]) == "v0.3.0"

    def test_prereleases_sort_numerically_not_alphabetically(self):
        """beta.10 is newer than beta.9, which a string sort gets wrong."""
        tags = ["v0.3.0-beta.9", "v0.3.0-beta.10"]
        assert latest_tag(tags) == "v0.3.0-beta.10"

    def test_the_real_tag_history_resolves(self):
        tags = ["v0.2.1", "v0.2.2", "v0.3.0-beta.1", "v0.3.0-beta.2", "v0.3.0-beta.3"]
        assert latest_tag(tags) == "v0.3.0-beta.3"

    def test_ignores_tags_that_are_not_versions(self):
        assert latest_tag(["nightly", "v0.1.0", "some-branch-point"]) == "v0.1.0"

    def test_no_versions_at_all(self):
        assert latest_tag(["nightly"]) is None
        assert latest_tag([]) is None

    def test_version_order_is_total(self):
        """Sorting by the key must agree with the intended release order."""
        ordered = ["v0.2.1", "v0.2.2", "v0.3.0-beta.1", "v0.3.0-beta.2", "v0.3.0"]
        assert sorted(reversed(ordered), key=version_key) == ordered


# ── What this checkout actually is ───────────────────────────────────────

class TestRunningVersion:
    """`VERSION` in the source is the last number someone typed.

    Between tags it names a hundred different trees, which is how a
    checkout 102 commits past beta.3 reports itself as beta.3. git
    already answers this exactly: the nearest tag, the distance from it,
    and the commit.
    """

    def test_on_a_release_it_is_the_release(self):
        assert running_version("v0.3.0-beta.3", "0.3.0-beta.3") == "0.3.0-beta.3"

    def test_past_a_release_it_says_how_far(self):
        assert running_version("v0.3.0-beta.3-102-ge861bd5",
                               "0.3.0-beta.3") == "0.3.0-beta.3-102-ge861bd5"

    def test_uncommitted_work_is_part_of_the_answer(self):
        """A dirty tree is not the tag, however close it sits."""
        assert "dirty" in running_version("v0.3.0-beta.3-1-gabc123-dirty",
                                          "0.3.0-beta.3")

    def test_a_bumped_but_untagged_version_shows_both(self):
        """The release gate bumps the constant before the tag exists, and
        for that window the two disagree. Hiding either one is a lie."""
        answer = running_version("v0.3.0-beta.3-4-gabc123", "0.3.0-beta.4")

        assert "0.3.0-beta.4" in answer
        assert "v0.3.0-beta.3-4-gabc123" in answer

    def test_without_git_the_constant_is_all_there_is(self):
        assert running_version("", "0.3.0-beta.3") == "0.3.0-beta.3"


# ── What has to be restarted ─────────────────────────────────────────────

class TestRestartTargets:
    """A release only makes a stacklet stale if it changed that stacklet,
    or changed the framework every stacklet runs on."""

    def test_only_the_stacklets_the_release_touched(self):
        changed = ["stacklets/docs/docker-compose.yml", "docs/admin-guide.md"]
        assert restart_targets(changed, running={"docs", "photos"}) == ["docs"]

    def test_a_stacklet_that_is_not_running_is_left_alone(self):
        changed = ["stacklets/photos/stacklet.toml"]
        assert restart_targets(changed, running={"docs"}) == []

    def test_a_framework_change_stales_everything_running(self):
        changed = ["lib/stack/stack.py"]
        assert restart_targets(changed, running={"docs", "core"}) == ["core", "docs"]

    def test_the_cli_wrapper_counts_as_the_framework(self):
        assert restart_targets(["stack"], running={"docs"}) == ["docs"]

    def test_a_docs_only_release_restarts_nothing(self):
        changed = ["README.md", "docs/adr/adr-013.md"]
        assert restart_targets(changed, running={"docs", "core"}) == []


# ── Running code versus code on disk ─────────────────────────────────────

class TestStaleStacklets:
    """After an update the sources have moved and the containers have not.

    Same rule as a release: a stacklet is stale when commits since the one
    its containers were built from changed files inside it, or the
    framework every stacklet shares. Anything else would nag about a
    release that had nothing to do with it.
    """

    def _changes(self, mapping):
        return lambda commit: mapping.get(commit, [])

    def test_a_stacklet_the_commits_since_did_not_touch_is_fine(self):
        changes = self._changes({"old": ["stacklets/photos/stacklet.toml"]})
        assert stale_stacklets({"docs": "old"}, {"docs"}, changes) == []

    def test_a_stacklet_whose_files_moved_is_stale(self):
        changes = self._changes({"old": ["stacklets/docs/docker-compose.yml"]})
        assert stale_stacklets({"docs": "old"}, {"docs"}, changes) == ["docs"]

    def test_a_framework_change_stales_every_running_stacklet(self):
        changes = self._changes({"old": ["lib/stack/stack.py"]})
        stamps = {"docs": "old", "core": "old"}
        assert stale_stacklets(stamps, {"docs", "core"}, changes) == ["core", "docs"]

    def test_a_stacklet_with_no_stamp_is_not_guessed_about(self):
        """Containers from before stamping existed, or a lost marker. An
        unknown answer is reported as unknown, not as a problem."""
        changes = self._changes({"old": ["lib/stack/stack.py"]})
        assert stale_stacklets({}, {"docs"}, changes) == []

    def test_only_running_stacklets_are_considered(self):
        changes = self._changes({"old": ["lib/stack/stack.py"]})
        assert stale_stacklets({"docs": "old"}, set(), changes) == []

    def test_containers_started_at_different_commits(self):
        """Restarting one stacklet and not another is the normal state
        halfway through applying an update."""
        changes = self._changes({
            "old": ["stacklets/docs/docker-compose.yml"],
            "new": [],
        })
        stamps = {"docs": "old", "memory": "new"}
        assert stale_stacklets(stamps, {"docs", "memory"}, changes) == ["docs"]


# ── The checkout itself ──────────────────────────────────────────────────

class TestCheckout:
    """Everything that talks to git, against a real repository."""

    def test_reads_its_tags_and_position(self, repo):
        co = Checkout(repo)
        assert co.is_git()
        assert set(co.tags()) == {"v0.1.0", "v0.2.0"}
        assert co.current_tag() == "v0.1.0"

    def test_knows_when_it_is_not_on_a_release(self, repo):
        _git(repo, "checkout", "-q", "-b", "work")
        (repo / "new.txt").write_text("x")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "past the tag")

        assert Checkout(repo).current_tag() is None

    def test_knows_when_a_release_is_already_behind_it(self, repo):
        """A developer on a branch, or anyone on main, is past the newest
        tag. Offering to move them backwards onto it would stash their
        work to deliver an older stack, so the caller has to be able to
        tell the difference."""
        co = Checkout(repo)
        assert co.contains("v0.1.0") is True, "HEAD is v0.1.0"
        assert co.contains("v0.2.0") is False, "v0.2.0 is ahead"

        co.checkout("v0.2.0")
        assert co.contains("v0.1.0") is True
        assert co.contains("v0.2.0") is True

    def test_counts_how_far_ahead_of_a_release_it_is(self, repo):
        """"Already past v0.3.0-beta.3" tells you the direction and not
        the distance, which is the part that decides whether you care."""
        co = Checkout(repo)
        assert co.commits_ahead_of("v0.1.0") == 0

        co.checkout("v0.2.0")
        assert co.commits_ahead_of("v0.1.0") == 1

    def test_reports_what_a_jump_changes(self, repo):
        co = Checkout(repo)
        changed = co.changed_paths("v0.1.0", "v0.2.0")

        assert "stacklets/docs/docker-compose.yml" in changed
        assert "stacklets/photos/stacklet.toml" in changed
        assert restart_targets(changed, running={"docs"}) == ["docs"]

    def test_reports_the_commits_in_between(self, repo):
        assert Checkout(repo).log_subjects("v0.1.0", "v0.2.0") == ["second release"]

    def test_moves_the_working_tree(self, repo):
        co = Checkout(repo)
        ok, err = co.checkout("v0.2.0")

        assert ok, err
        assert co.current_tag() == "v0.2.0"
        assert "paperless:2" in (repo / "stacklets/docs/docker-compose.yml").read_text()

    def test_a_clean_tree_needs_no_stash(self, repo):
        assert Checkout(repo).is_dirty() is False

    def test_an_edited_file_makes_it_dirty(self, repo):
        (repo / "stacklets/docs/docker-compose.yml").write_text("image: mine\n")
        assert Checkout(repo).is_dirty() is True

    def test_names_the_files_it_will_stash(self, repo):
        """The plan lists them, so a truncated path is a lie the admin
        cannot check. `git status --porcelain` pads with the status
        columns, and the first line's padding is easy to lose."""
        (repo / "stacklets/docs/docker-compose.yml").write_text("image: mine\n")
        (repo / "lib/stack/cli.py").write_text("VERSION = 'mine'\n")

        assert Checkout(repo).dirty_paths() == [
            "lib/stack/cli.py",
            "stacklets/docs/docker-compose.yml",
        ]

    def test_stash_and_pop_round_trip(self, repo):
        co = Checkout(repo)
        (repo / "stacklets/docs/docker-compose.yml").write_text("image: mine\n")

        assert co.stash() is True
        assert co.is_dirty() is False
        ok, err = co.stash_pop()

        assert ok, err
        assert (repo / "stacklets/docs/docker-compose.yml").read_text() == "image: mine\n"

    def test_a_conflicting_pop_keeps_the_stash(self, repo):
        """The case that decides whether this command can be trusted: the
        admin edited the same file the release changed. git keeps the
        stash, so the edit is recoverable, and the caller must say so."""
        co = Checkout(repo)
        (repo / "stacklets/docs/docker-compose.yml").write_text("image: mine\n")
        co.stash()
        assert co.checkout("v0.2.0")[0] is True

        ok, err = co.stash_pop()

        assert ok is False
        assert "conflict" in err.lower()
        assert co.has_stash() is True

    def test_knows_the_ref_to_come_back_to(self, repo):
        """On a branch that is the branch, so a revert restores the tree
        without moving the branch pointer. Detached, it is the commit."""
        co = Checkout(repo)
        assert co.position() == co._run("rev-parse", "HEAD")[1]

        _git(repo, "checkout", "-q", "-b", "work")
        assert co.position() == "work"

    def test_a_conflicting_pop_can_be_wound_all_the_way_back(self, repo):
        """The transaction. When the admin's edit and the release collide,
        the update puts everything back rather than leaving a tree with
        conflict markers in files that have to parse."""
        co = Checkout(repo)
        was = co.position()
        (repo / "stacklets/docs/docker-compose.yml").write_text("image: mine\n")
        co.stash()
        co.checkout("v0.2.0")
        ok, _ = co.stash_pop()
        assert ok is False, "this edit is meant to conflict"

        co.force_checkout(was)
        restored, err = co.stash_pop()

        assert restored, err
        assert co.current_tag() == "v0.1.0", "back on the release we started on"
        assert (repo / "stacklets/docs/docker-compose.yml").read_text() == "image: mine\n"
        assert co.has_stash() is False, "nothing left behind to clean up"
        assert "<<<<<<<" not in (repo / "stacklets/docs/docker-compose.yml").read_text()

    def test_knows_whether_it_is_on_a_branch(self, repo):
        """A tag checkout is detached; a clone someone just made is not.
        The update says different things to each."""
        co = Checkout(repo)
        assert co.branch() is None, "the fixture sits on a tag"

        _git(repo, "checkout", "-q", "-b", "work")
        assert co.branch() == "work"

    def test_tags_come_from_every_remote_not_just_origin(self, repo, tmp_path):
        """The fork case. Someone who forked has an origin without the
        project's releases in it, and the releases they want live on the
        upstream remote they added. Fetching only origin would offer
        them whatever their fork was tagged with, which is usually
        nothing, and call that the newest release.
        """
        fork = tmp_path / "fork.git"
        upstream = tmp_path / "upstream.git"
        _git(repo, "clone", "--bare", "-q", str(repo), str(fork))
        _git(repo, "clone", "--bare", "-q", str(repo), str(upstream))
        _git(Path(upstream), "tag", "v9.9.9")

        # Exactly the shape a contributor has: origin is their fork, and
        # the releases are on the remote they added. With one remote git
        # would pick it regardless, which is why both exist here.
        _git(repo, "remote", "add", "origin", str(fork))
        _git(repo, "remote", "add", "upstream", str(upstream))

        co = Checkout(repo)
        assert "v9.9.9" not in co.tags()
        ok, err = co.fetch_tags()

        assert ok, err
        assert "v9.9.9" in co.tags(), "a release on the upstream remote is invisible"
        assert latest_tag(co.tags()) == "v9.9.9"

    def test_a_directory_that_is_not_a_checkout(self, tmp_path):
        assert Checkout(tmp_path).is_git() is False
