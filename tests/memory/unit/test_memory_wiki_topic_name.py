"""A topic's wiki page is titled with the name on the topic's card.

The family named the topic "Itchy & Scratchy Land"; its folder, the id,
is `itchy-scratchy-land`, which has lost the `&`. The page the wiki writes
for the topic must carry the card's title, because the menu and the page
show it. A title made from the id would drop the `&`, an apostrophe and
every umlaut.

The compile runs as the bot runner's `wiki` command runs it (`wiki.run`)
with `--fast`, which asks no model, over a brain and a vault that hold the
Simpsons' cards as the mirror leaves them.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from stack.frontmatter import parse
from stacklets.memory.bot.cli import wiki
from tests.memory.unit.simpsons_cards import ONTOLOGY, VAULT

TOPIC = "family/itchy-scratchy-land"
CARDS = {
    **VAULT, "ontology.toml": ONTOLOGY,
    f"{TOPIC}/topic.md": ("---\ntype: topic\ncard_id: topic-itchy-scratchy-land\n"
                          "title: Itchy & Scratchy Land\n---\n\n# Itchy & Scratchy Land\n"),
    f"{TOPIC}/notes/2026/10/tickets.md": (
        "---\ntype: note\ncard_id: note-6f6f6f6f6f\ntitle: Tickets for the park\n"
        "date: \"2026-10-15\"\n---\n\n# Tickets for the park\n\n> [!summary]\n> Four tickets, one day.\n"),
}


@pytest.fixture
def brain(tmp_path: Path, monkeypatch) -> Path:
    """The brain after one `--fast` compile over the vault's cards."""
    brain, vault = tmp_path / "brain", tmp_path / "vault"
    for base in (brain, vault):
        for path, text in CARDS.items():
            (base / path).parent.mkdir(parents=True, exist_ok=True)
            (base / path).write_text(text, encoding="utf-8")
    for name, value in (("BRAIN_REPO_DIR", brain), ("MEMORY_VAULT_DIR", vault),
                        ("SHARED_BUCKET", "family"), ("LANGUAGE", "en"),
                        ("WIKI_CALLS_LOG", tmp_path / "wiki-calls.jsonl")):
        monkeypatch.setenv(name, str(value))
    assert asyncio.run(wiki.run(None, ["--fast", "--topic", "itchy-scratchy-land"])) == 0
    return brain


def test_a_topic_page_is_titled_with_the_topic_cards_title(brain):
    """Given a topic card titled "Itchy & Scratchy Land", when the wiki
    compiles the topic's page, then the page's title is the card's title."""
    page = (brain / TOPIC / "about.md").read_text(encoding="utf-8")

    assert parse(page)["title"] == "Itchy & Scratchy Land"
