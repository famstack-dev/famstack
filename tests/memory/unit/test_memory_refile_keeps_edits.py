"""A refile keeps what people changed: `stack memory reprocess` after a correction or an edit.

ADR-018 §7. A note's history is its event log. A refile interprets the
note again from its sources, and must not write over what people did to
it since it was filed: a correction (`--msg`, a reply), an edit of its
clean version, an edit of its fields. A note with corrections or a
person's edits is rebuilt as a card conversation, the clean version
before the first of them and each event in time order; a person's field
edits are applied again after the model.

Marge and Homer run the commands as `./stack` does, on memory's test
instance (`running_memory`), and Homer replies in the chat, which the
archivist refiles. What is real: the commands and their loader, the card
write endpoint behind core's door, the curator's clone the host reads the
history from, the model half of the refile in the bot runner, started in a
process of its own as `docker exec` starts it, and the archivist's mirror
and its refile of a reply, used here as the docs stacklet's modules, since
the archivist bot that runs them on an instance reads Matrix. What stands
in: the Matrix room the reply arrives in, Paperless, and the model, an HTTP
server answering the chat completions API with the answers a test gives.

A test takes one to three seconds: each refile starts the bot runner's
command in an interpreter of its own, about a second each.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from stack.frontmatter import parse
from tests.memory.unit.instance import TOKEN, running_memory
from tests.model import Model
from tests.memory.unit.simpsons_cards import ONTOLOGY, VAULT

from tests.memory.unit.dsl import commit

WORDS = ("camping trip next weekend:\nflanders lends us his tent\nwe leave fri at 4\n"
         "bring 3 sleeping bags\nbart packs the flashlights")
CLEAN = ("Camping trip next weekend:\n\n- Flanders lends us his tent.\n- We leave on Friday at 4.\n"
         "- Bring 3 sleeping bags.\n- Bart packs the flashlights.")
AT_5 = CLEAN.replace("at 4.", "at 5.")
TITLE = "Camping trip"


# ── The instance ─────────────────────────────────────────────────────────

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


@pytest.fixture
def marge(memory):
    return memory.person("marge")


@pytest.fixture
def homer(memory):
    return memory.person("homer")


# ── The archivist ────────────────────────────────────────────────────────

class _Paperless:
    """Paperless, which the archivist asks for names when the memory has none."""

    async def get_tags(self):
        return []


@pytest.fixture
def archivist(memory, model, tmp_path, monkeypatch):
    """The archivist's mirror, filing through the curator's API, as the
    archivist bot builds it; its model is the test's."""
    from stacklets.docs.bot.git_mirror import GitMirror
    from stacklets.docs.bot.memory_client import MemoryClient

    monkeypatch.setattr("stack.ai.models._DEFAULT_MODEL", "stand-in")
    return GitMirror(data_dir=tmp_path / "archivist", memory=MemoryClient(memory.curator, token=TOKEN))


def _replies_in_the_chat(mirror, model, note, *, by: str, words: str):
    """`by` replies to the note's filing in the chat, and the archivist
    refiles it (`CapturePipeline.reprocess`, what its reply handler calls)."""
    from openai import AsyncOpenAI
    from stacklets.docs.bot.capture_pipeline import CapturePipeline
    from stacklets.docs.bot.pipeline import LLM, Classifier

    llm = LLM(AsyncOpenAI(base_url=model.url, api_key="k"), namespace="archivist-bot")
    pipeline = CapturePipeline(
        url_extractor=None, text_extractor=None, classifier=Classifier(llm, language="en"),
        mirror=mirror, capture_tags=None, paperless=_Paperless(),
        bot_name="archivist-bot", classify_max_chars=100_000, capture_keep_body=False,
        capture_tag_prompt_size=50)
    return asyncio.run(pipeline.reprocess(
        vault_path=note.path, user_hint=words, sender_mxid=f"@{by}:simpson",
        reply_text=words, reply_id="$reply:simpson", reply_at=_now()))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── The note, and what people do to it ───────────────────────────────────

def _filed(mirror, *, tags: list[str]):
    """Homer's camping note, filed by the archivist as he sent it, with a
    clean version of his words and these tags."""
    from stack.cards.sources import Source

    source = Source("original", "homer", "2026-10-20T08:00:00+00:00", "chat", "text",
                    "$camping:simpson", quote=WORDS)
    return asyncio.run(mirror.publish_capture(
        entity="homer", kind="note", source_uri=None, title_hint=None, body_text=WORDS,
        classification={"title": TITLE, "summary": ""}, captured_at="2026-10-20",
        model="stand-in", tags=tags, capture_id=source.id, submitter="@homer:simpson",
        clean=CLEAN, sources=[source], made={"at": "2026-10-20T08:00:05+00:00"}))


def _edits_the_text(memory, path: str, old: str, new: str, *, by: str) -> None:
    """`by` changes the note's clean version by hand in Forgejo, and the
    curator follows."""
    page = memory.page(path)
    assert page.count(old) == 1
    memory.edited_in_forgejo({path: page.replace(old, new)}, by=by, subject=f"Update '{path}'")


def _tags(repo, path: str) -> list[str]:
    return list(parse(repo.page(path)).get("tags") or [])


def _shown(page: str) -> str:
    """What the card says above its sources."""
    return page.split("## Sources")[0]


def _classified(**fields) -> dict:
    return {"title": TITLE, "tags": ["camping"], "persons": [], **fields}


# ── A person's field edits ───────────────────────────────────────────────

def test_a_tag_marge_added_stays_through_a_refile_and_the_models_other_tags_come_in(
        memory, archivist, marge, model):
    """Given Homer's note tagged `camping`, when Marge adds `tent` with
    `stack memory card edit` and the note is refiled, then it has her tag
    and the tags the model gave. Why: a refile interprets the note again,
    and a tag a person chose is not the model's to take back; the edit is
    applied again as an addition, so the model's own suggestions still
    come in."""
    note = _filed(archivist, tags=["camping"])
    marge.edits(note.card_id, "--add", "tags=tent")
    model.answers(_classified(tags=["camping", "outdoors"], clean=CLEAN))

    refiled = marge.refiles(note.card_id)

    assert refiled.get("ok"), refiled
    assert sorted(_tags(memory, note.path)) == ["camping", "outdoors", "tent"]
    assert len(model.requests) == 1, "a field edit is no edit of the text: one call, as today"


def test_a_tag_marge_removed_stays_removed_when_the_model_suggests_it_again(
        memory, archivist, marge, model):
    """Given Homer's note tagged `camping` and `tent`, when Marge removes
    `camping` and the model suggests it again on a refile, then the note
    is not tagged `camping`, and the model's new `outdoors` comes in."""
    note = _filed(archivist, tags=["camping", "tent"])
    marge.edits(note.card_id, "--remove", "tags=camping")
    model.answers(_classified(tags=["camping", "tent", "outdoors"], clean=CLEAN))

    marge.refiles(note.card_id)

    assert sorted(_tags(memory, note.path)) == ["outdoors", "tent"]


def test_a_tag_marge_added_stays_when_lisa_corrects_the_note_in_the_chat(
        memory, archivist, marge, model):
    """Given Homer's note tagged `camping`, when Marge adds `tent` with
    `stack memory card edit` and Lisa then replies in the chat with a
    correction, then the archivist's refile keeps Marge's tag, and the
    correction is in the card, committed as Lisa's reply. Why: a refile
    from the chat reads the
    title, tags and persons again, and the bot runner has no clone to read
    a person's edits from; it reads the card's history from the curator,
    so a reply replays the same edits a refile at the terminal does."""
    note = _filed(archivist, tags=["camping"])
    marge.edits(note.card_id, "--add", "tags=tent")
    model.answers({"clean": AT_5}, _classified(tags=["camping", "outdoors"]))

    outcome = _replies_in_the_chat(archivist, model, note, by="lisa",
                                   words="we leave at 5, not 4")

    assert outcome.status == "reclassified", outcome
    assert sorted(_tags(memory, note.path)) == ["camping", "outdoors", "tent"]
    assert AT_5 in _shown(memory.page(note.path))
    assert memory.new_commits()[-1] == commit(by="lisa", saying="reply: ")


# ── Corrections and a person's text edits ────────────────────────────────

def test_a_correction_then_marges_edit_of_another_point_are_given_to_the_model_in_order(
        memory, archivist, homer, marge, model):
    """Given Homer's note, Homer's correction `we leave at 5, not 4`, and
    then Marge's edit of the clean version by hand (4 sleeping bags, not
    3), when the note is refiled, then the model is asked with the card
    conversation: the task, Homer's words, the clean version as it was
    before the correction as the model's own answer, the correction, then
    Marge's edit, then the request; and the card shows what it answered.

    Why: a refile reads the note from its sources, and a person's edit is
    no source. Given as one more event after the correction, it is kept;
    rebuilt from the sources alone, it was lost. The note is refiled, not
    refused because somebody changed it by hand."""
    note = _filed(archivist, tags=["camping"])
    model.answers({"clean": AT_5}, _classified())
    homer.refiles(note.card_id, correction="we leave at 5, not 4")
    _edits_the_text(memory, note.path, "- Bring 3 sleeping bags.",
                    "- Bring 4 sleeping bags.", by="marge")
    rebuilt = AT_5.replace("3 sleeping", "4 sleeping")
    model.answers({"clean": rebuilt}, _classified())

    refiled = marge.refiles(note.card_id)

    assert refiled.get("ok"), refiled
    asked = model.requests[2]
    assert [m["role"] for m in asked] == ["system", "user", "assistant", "user", "user", "user"]
    assert WORDS in asked[1]["content"] and "Homer" in asked[1]["content"]
    assert json.loads(asked[2]["content"]) == {"clean": CLEAN}
    assert [m["content"] for m in asked[3:5]] == [
        "Homer: we leave at 5, not 4",
        "Marge edited the card: changed 'Bring 3 sleeping bags.' to 'Bring 4 sleeping bags.'"]
    assert rebuilt in _shown(memory.page(note.path))


def test_a_note_nobody_corrected_or_edited_is_asked_once_as_today(memory, archivist, marge, model):
    """Given Homer's note as it was filed, when it is refiled, then the
    model is asked once, one message with his words: the capture prompt,
    which writes the clean version and the title and tags together."""
    note = _filed(archivist, tags=["camping"])
    model.answers(_classified(clean=CLEAN))

    marge.refiles(note.card_id)

    assert len(model.requests) == 1
    [message] = model.requests[0]
    assert message["role"] == "user" and WORDS in message["content"]
