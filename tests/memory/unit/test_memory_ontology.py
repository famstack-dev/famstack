"""`stack memory ontology`, described by what the admin sees it do.

The topics the classifier files under are the memory's `ontology.toml`.
When a release ships new topics, keywords or synonyms, this command writes
the shipped seed over the household's file, after showing the difference;
`--dry-run` shows it and writes nothing.

The real entry point, through core's card door to the curator's API and its
card store, on memory's test instance (`running_memory`); Forgejo is a bare
git repository the store pushes to.
"""

from __future__ import annotations

import pytest

from tests.memory.unit.instance import running_memory
from tests.springfield import REPO

from tests.memory.unit.dsl import commit

SEED = (REPO / "stacklets" / "memory" / "seeds" / "ontology.toml").read_text(encoding="utf-8")
OLDER = '[topic.camping]\nnames = { de = "Camping", en = "Camping" }\n'


@pytest.fixture
def memory(tmp_path):
    """The memory, with the ontology of an older release."""
    with running_memory(tmp_path, {"ontology.toml": OLDER}, seeded_by="memory-bot",
                        seeded_as="seed: initial memory from famstack") as running:
        yield running


@pytest.fixture
def homer(memory):
    return memory.person("homer")


def test_the_shipped_seed_replaces_the_ontology_in_one_commit_by_the_memory_bot(memory, homer):
    """Given the ontology of an older release, when the admin syncs it, then
    the memory holds the shipped seed, in one commit by the memory bot, and
    the admin was shown what changed.

    The household's topics are the classifier's whole vocabulary: a
    release's new topics reach it only this way.
    """
    answer = homer.runs("memory", "ontology", [])

    assert answer["applied"] is True
    assert memory.page("ontology.toml") == SEED
    assert memory.new_commits() == [commit(by="memory-bot", saying="chore(ontology): sync from shipped seed")]
    assert homer.was_told("-[topic.camping]")


def test_a_dry_run_shows_the_change_and_writes_nothing(memory, homer):
    answer = homer.runs("memory", "ontology", ["--dry-run"])

    assert answer["applied"] is False
    assert memory.new_commits() == []
    assert homer.was_told("Dry run", "-[topic.camping]")


def test_an_ontology_that_is_the_seed_already_commits_nothing(memory, homer):
    homer.runs("memory", "ontology", [])
    synced = len(memory.new_commits())

    answer = homer.runs("memory", "ontology", [])

    assert answer["in_sync"] is True
    assert len(memory.new_commits()) == synced
