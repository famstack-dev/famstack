"""A renamed topic has one page in the wiki, at its new path.

`stack memory topic rename` moves a topic's folder in the memory. The
curator's mirror then moves the cards in the brain, and asks the wiki for
the pages of both ids, because cards left the old folder and arrived in
the new one. The old page is a view only the wiki writes, so the wiki must
take it away, or the topic shows twice.

The compile runs as the bot runner's `wiki` command runs it (`wiki.run`)
with `--fast`, which asks no model, over a brain and a vault that hold the
Simpsons' cards as the mirror leaves them.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from stacklets.memory.bot.cli import wiki
from tests.memory.unit.instance import views
from tests.memory.unit.simpsons_cards import ONTOLOGY, VAULT

OLD, NEW = "family/itchy-scratchy-land", "family/itchy-and-scratchy-land"
TICKETS = "notes/2026/10/tickets.md"
CARDS = {
    **VAULT, "ontology.toml": ONTOLOGY,
    f"{OLD}/topic.md": ("---\ntype: topic\ncard_id: topic-itchy-scratchy-land\n"
                        "title: Itchy Scratchy Land\n---\n\n# Itchy Scratchy Land\n"),
    f"{OLD}/{TICKETS}": ("---\ntype: note\ncard_id: note-6f6f6f6f6f\ntitle: Tickets for the park\n"
                         "date: \"2026-10-15\"\n---\n\n# Tickets for the park\n\n"
                         "> [!summary]\n> Four tickets, one day.\n"),
}


class Trees:
    """The vault and the brain, as the curator's clone and its mirror hold them."""

    def __init__(self, root: Path, monkeypatch):
        self.brain, self.vault = root / "brain", root / "vault"
        for base in (self.brain, self.vault):
            for path, text in CARDS.items():
                (base / path).parent.mkdir(parents=True, exist_ok=True)
                (base / path).write_text(text, encoding="utf-8")
        for name, value in (("BRAIN_REPO_DIR", self.brain), ("MEMORY_VAULT_DIR", self.vault),
                            ("SHARED_BUCKET", "family"), ("LANGUAGE", "en"),
                            ("WIKI_CALLS_LOG", root / "wiki-calls.jsonl")):
            monkeypatch.setenv(name, str(value))

    def compile(self, *flags: str) -> int:
        return asyncio.run(wiki.run(None, ["--fast", *flags]))

    def rename(self) -> None:
        """The topic's folder moved in the vault, and its cards in the brain."""
        shutil.move(self.vault / OLD, self.vault / NEW)
        for card in ("topic.md", TICKETS):
            (self.brain / NEW / card).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(self.brain / OLD / card, self.brain / NEW / card)


@pytest.fixture
def trees(tmp_path, monkeypatch) -> Trees:
    trees = Trees(tmp_path, monkeypatch)
    assert trees.compile() == 0
    assert f"{OLD}/about.md" in views(trees.brain)
    return trees


def test_the_pages_the_curator_asks_for_after_a_rename_leave_one_topic_page(trees):
    """Given the topic's page compiled, when the topic is renamed and the
    wiki compiles the pages of the old and the new id, then the topic has
    one page, at the new path, and the old folder is gone from the brain."""
    trees.rename()

    assert trees.compile("--topic", "itchy-scratchy-land", "--topic", "itchy-and-scratchy-land") == 0

    pages = views(trees.brain)
    assert f"{NEW}/about.md" in pages
    assert [p for p in pages if p.startswith(f"{OLD}/")] == []
    assert not (trees.brain / OLD).exists(), "no empty folder is left for the old id"


def test_the_nightly_sweep_takes_away_a_page_whose_topic_is_gone(trees):
    trees.rename()

    assert trees.compile() == 0

    assert [p for p in views(trees.brain) if p.startswith(f"{OLD}/")] == []


def test_a_topic_still_in_the_vault_keeps_its_page(trees):
    """Resting state: nothing renamed, the page stays."""
    assert trees.compile("--topic", "itchy-scratchy-land") == 0

    assert f"{OLD}/about.md" in views(trees.brain)
