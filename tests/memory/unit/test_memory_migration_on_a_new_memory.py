"""A new install comes through every memory migration.

A new instance runs every migration on `stack up memory`, on a memory that
holds the shipped seed and the family's person files and no other card.
`test_memory_migration_chain.py` takes a full 0.4 memory through the same
chain, and each migration's own test has cards in the shape it expects. Here
the chain finds almost nothing to do, and a migration that raises on that
holds the bots of every new install.

A migration added to the folder is part of this chain without editing this
file. The seed is what `seed_files` gives for the shipped seeds, and the
person files are what `missing_people` gives for the Simpsons' accounts.

Each test takes about 2 seconds, nearly all of it to start the instance, because
the migrations commit through a running curator.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from stacklets.memory.format_check import format_report
from stacklets.memory.graph import load_graph
from stacklets.memory.lib import missing_people, seed_files
from tests.memory.unit.instance import RunningMemory, git, running_memory
from tests.memory.unit.test_memory_migration_chain import _failure, _migrate

# The accounts of the fabricated family, as `users.toml` lists them.
ACCOUNTS = [{"id": name.lower(), "name": f"{name} Simpson"}
            for name in ("Homer", "Marge", "Bart", "Lisa")]

# The files a family edits or reads first. No migration has a reason to change them.
UNTOUCHED = ["ontology.toml", "facts.toml", "index.md"]


@pytest.fixture
def new_memory(tmp_path) -> Iterator[RunningMemory]:
    """A new install's memory: the shipped seed and the Simpsons' person files."""
    files = {**seed_files(), **missing_people(ACCOUNTS, [])}
    with running_memory(tmp_path / "data", files, seeded_by="memory-bot",
                        seeded_as="seed") as running:
        yield running


@pytest.fixture
def migrated(new_memory, tmp_path):
    """The new memory after every migration ran once, as the first `stack up` runs them."""
    outcome, steps, migrations = _migrate(new_memory, tmp_path / "data")
    assert "failed" not in outcome, _failure(outcome, steps)
    return outcome, migrations


def test_the_new_memory_holds_what_the_install_gives_it(new_memory):
    """Given the fixture, then the memory holds the seed and a person file for
    each account, and no other card: the state this file is about."""
    held = git(new_memory.forgejo.repo, "ls-tree", "-r", "--name-only", "main").splitlines()
    people = [f"family/people/{account['id']}.md" for account in ACCOUNTS]
    assert sorted(held) == sorted([*seed_files(), *people])


def test_every_migration_finishes_on_a_new_memory(migrated):
    """Given a new memory, when every migration runs in order, then each one
    finishes, because one that fails holds the bots until it is fixed."""
    outcome, migrations = migrated
    assert outcome["applied"] == [m.key for m in migrations.shipped()]


def test_the_migrated_new_memory_meets_the_format(migrated, new_memory):
    """Given a new memory after the migrations, then the format check finds
    nothing, because the card browser's gauge reads the same report."""
    report = format_report(load_graph(new_memory.vault))
    assert {key: value for key, value in report.items()
            if key not in ("cards", "findings") and value} == {}


def test_the_migrations_leave_the_seeds_own_files_as_they_were(migrated, new_memory):
    """Given a new memory after the migrations, then `ontology.toml`,
    `facts.toml` and `index.md` are the shipped files, byte for byte."""
    seed = seed_files()
    assert {path: new_memory.forgejo.file(path) for path in UNTOUCHED} == \
        {path: seed[path] for path in UNTOUCHED}


def test_run_again_on_a_new_memory_changes_nothing_and_makes_no_commit(migrated, new_memory,
                                                                       tmp_path):
    """Given a migrated new memory, when every migration runs again, as after
    restoring an older backup of the record, then each finishes, and Forgejo
    and the clone stay as they were."""
    head, commits = new_memory.forgejo.head(), len(new_memory.written)

    outcome, steps, migrations = _migrate(new_memory, tmp_path / "restored")

    assert "failed" not in outcome, _failure(outcome, steps)
    assert outcome["applied"] == [m.key for m in migrations.shipped()]
    assert len(new_memory.written) == commits
    assert new_memory.forgejo.head() == head
    assert git(new_memory.vault, "status", "--porcelain") == ""
