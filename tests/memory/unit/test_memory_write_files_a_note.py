"""`stack memory write` of a new note files it as a note sent in the chat is.

A note has a clean version of its words, the words quoted under `## Sources`,
and a sidecar that says what it was made from (ADR-018). The capture pipeline
writes all three; a page written as given has none of them, and a refile has
nothing to read it again from. So a new note written with `stack memory write`
goes through the pipeline `stack memory capture` runs, in the bot runner.

Homer runs the command as `./stack` does, on memory's test instance
(`running_memory`). What is real: the command and its loader, the memory,
and the archivist's capture command in the bot runner, started in a process
of its own as `docker exec` starts it, with its pipeline and its mirror's
commit. What stands in: Paperless, which knows no names, and the model, an
HTTP server answering the chat completions API.
"""

from __future__ import annotations

import json

import pytest

from stack.frontmatter import parse
from tests.memory.unit.instance import Model, running_memory
from tests.memory.unit.simpsons_cards import ONTOLOGY, VAULT

from tests.memory.unit.dsl import commit

GRILL = "the grill needs new charcoal before saturday"


@pytest.fixture
def model(httpserver) -> Model:
    return Model(httpserver)


@pytest.fixture
def memory(tmp_path, httpserver, model):
    """The Simpsons' memory, with Paperless and the model where the bot
    runner reaches them."""
    httpserver.expect_request("/paperless/api/tags/").respond_with_json({"results": []})
    with running_memory(tmp_path, {**VAULT, "ontology.toml": ONTOLOGY}, seeded_by="memory-bot",
                        seeded_as="cards: the Simpsons", model=model.url,
                        paperless=httpserver.url_for("/paperless")) as running:
        yield running


def test_a_new_note_is_filed_with_its_sources_as_homers_note(memory, model):
    """Given no page at `homer/notes/charcoal.md`, when Homer writes his words
    there, then the memory has one new commit, the pipeline's `capture:`, by
    Homer; the note it filed quotes his words under `## Sources` and has a
    sidecar that records them as sent on the command line; and Homer is
    told the card id and the path the pipeline chose.

    Why: a note without its sources cannot be refiled from its words, and
    the path asked for is not the path filed at, so the answer must say it.
    About a second: the archivist's command starts an interpreter of its own.
    """
    homer = memory.person("homer")
    model.answers({"title": "Grill needs charcoal", "tags": [], "persons": ["Homer"],
                   "clean": "The grill needs new charcoal before Saturday."})

    answer = homer.writes("homer/notes/charcoal.md", GRILL + "\n")

    assert answer.get("ok"), answer
    path, card_id = answer["path"], answer["card_id"]
    assert path.startswith("homer/notes/") and path != "homer/notes/charcoal.md"
    assert memory.new_commits() == [commit(by="homer", saying="capture: Grill needs charcoal")]
    card = memory.page(path)
    assert parse(card)["card_id"] == card_id
    assert GRILL in card.split("## Sources", 1)[1]
    sidecar = json.loads(memory.page(path.removesuffix(".md") + ".json"))
    assert [(s["role"], s["channel"]) for s in sidecar["sources"]] == [("original", "cli")]
    assert homer.was_told(card_id, path)
