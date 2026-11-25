"""The rollback drill's releases: two local tags that must never reach a remote.

The drill tags its build and a broken build in its own clone. A pushed
drill tag would be a release that `stack update` offers to every
tester, so `make` refuses a clone with a remote, and `remove` deletes
both tags again. These tests run real git in a fabricated repository.
"""

from __future__ import annotations

import subprocess

import pytest

from tests.unit.driver import driver_code

releases = driver_code.load("drill_release")

IDENTITY = ["-c", "user.name=test", "-c", "user.email=test@localhost"]


def git(folder, *args) -> str:
    return subprocess.run(["git", "-C", str(folder), *IDENTITY, *args], capture_output=True, text=True,
                          check=True).stdout


@pytest.fixture
def clone(tmp_path):
    """A repository at the old release: the docs compose file with its `PAPERLESS_URL` line and one migration, tagged."""
    git(tmp_path, "init", "-q")
    compose = tmp_path / releases.COMPOSE_PATH
    compose.parent.mkdir(parents=True)
    compose.write_text("services:\n  paperless:\n    environment:\n" + releases.PAPERLESS_URL_LINE)
    migration = tmp_path / releases.MIGRATION_PATH
    migration.parent.mkdir(parents=True)
    (migration.parent / "0001_move_tags.py").write_text("def run(ctx):\n    pass\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "old release")
    git(tmp_path, "tag", releases.OLD)
    return tmp_path


def test_a_clone_with_a_remote_is_refused_and_gets_no_tag(clone):
    """Given a clone with a remote. Then `make` refuses, and no drill tag exists."""
    git(clone, "remote", "add", "origin", "https://example.invalid/famstack.git")

    with pytest.raises(RuntimeError, match="remote"):
        releases.make(clone)

    assert git(clone, "tag").split() == [releases.OLD]


def test_the_build_lets_paperless_answer_and_the_broken_build_adds_the_migration(clone):
    """Given a clone without a remote. Then the build allows the archivist's host, the broken build adds
    the migration on top, and the checkout stays on the build, as `stack update` leaves an instance."""
    made = releases.make(clone)

    assert made == releases.Releases(releases.OLD, releases.BUILD, releases.BROKEN)
    assert releases.ALLOWED_HOSTS_LINE.strip() in git(clone, "show", f"{releases.BUILD}:{releases.COMPOSE_PATH}")
    assert git(clone, "show", f"{releases.BROKEN}:{releases.MIGRATION_PATH}").startswith('"""Empty')
    assert git(clone, "describe", "--tags").strip() == releases.BUILD


def test_remove_deletes_both_drill_tags_and_keeps_the_old_release(clone):
    """Given the drill's tags. Then `remove` deletes both and names them; a second call deletes nothing."""
    releases.make(clone)

    assert releases.remove(clone) == [releases.BUILD, releases.BROKEN]
    assert git(clone, "tag").split() == [releases.OLD]
    assert releases.remove(clone) == []
