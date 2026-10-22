"""The model half of `stack memory reprocess`: what a note would become.

It runs in the bot runner, refiles the note through the archivist's own
pipeline and answers with the card and the sidecar that refile would
write, written nowhere. These drive it as the host command does, a card
and its sidecar on stdin and one JSON object back, with the model and the
chat as the only stand-ins.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs" / "bot"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs" / "bot" / "cli"))

import reprocess_note  # noqa: E402
from _chat import ChatThread  # noqa: E402
from memory.note_card import ORIGINAL, REPLY, Source, render_sources, sidecar_text  # noqa: E402
from stack.frontmatter import parse as parse_frontmatter  # noqa: E402

PATH = "family/itchy-scratchy-land/notes/2026/10/trip-3a338e.md"
WORDS = "homer calls the hotel thu abt the late checkin, budget max 200"
CLEAN = "Homer calls the hotel on Thursday about the late check-in. Budget: at most 200."


def _note(words: str = WORDS, *, replies=()) -> tuple[str, str]:
    """A note as it is filed today: its card and its sidecar."""
    sources = [Source(ORIGINAL, "marge", "2026-10-20T08:00:00+00:00", "chat", "text",
                      "$n:simpson", quote=words)]
    sources += [Source(REPLY, who, "2026-10-20T09:00:00+00:00", "chat", "text", f"$r{i}:simpson",
                       quote=text) for i, (who, text) in enumerate(replies)]
    card = ("---\ntype: note\ncard_id: note-9c0f7c0256\ntitle: Trip\ntags:\n"
            "  - itchy-scratchy-land\n  - hotel\n---\n\n# Trip\n\n"
            f"Homer will call the hotel.\n\n{render_sources(sources)}\n")
    return card, sidecar_text(card_id="note-9c0f7c0256", filed_by="marge", sources=sources,
                              made={"at": "2026-10-20T08:00:05+00:00", "model": "old"})


class _Paperless:
    """Paperless, which the pipeline asks for the family's names when the
    memory declares no people."""

    async def get_tags(self):
        return ["Person: Homer", "Person: Marge"]


class _Model:
    """The classifier, answering what a model would and recording what it read."""

    def __init__(self, answer: dict):
        self.answer, self.saw, self.corrected_from, self.rebuilt_from = answer, {}, {}, {}

    async def classify_capture(self, **kwargs):
        self.saw = kwargs
        return dict(self.answer)

    async def correct_note(self, **kwargs):
        self.corrected_from = kwargs
        return {"clean": self.answer.get("clean", ""), "prompt_template": "c0e1c2d3"}

    async def rebuild_note(self, **kwargs):
        self.rebuilt_from = kwargs
        return {"clean": self.answer.get("clean", ""), "prompt_template": "c0e1c2d3"}


@pytest.fixture(autouse=True)
def _mirror_env(monkeypatch, tmp_path):
    """The settings the bot runner has for the mirror; nothing is reached with them."""
    monkeypatch.setenv("CODE_URL", "http://stack-code:3000")
    monkeypatch.setenv("MATRIX_ADMIN_USER", "stackadmin")
    monkeypatch.setenv("MATRIX_ADMIN_PASSWORD", "secret")
    monkeypatch.setenv("STACK_COMMIT", "9a5bdc9")
    monkeypatch.setattr(reprocess_note, "_DATA_DIR", tmp_path)
    _chat_has(monkeypatch, None)


def _chat_has(monkeypatch, thread: ChatThread | None):
    """What Synapse answers for the note's message (`_chat.read_thread` is
    tested against an HTTP server of its own)."""
    async def read_note_thread(event_id):
        return thread
    monkeypatch.setattr(reprocess_note, "read_note_thread", read_note_thread)


def _event(event_id, sender, body):
    return {"type": "m.room.message", "event_id": event_id, "sender": sender,
            "origin_server_ts": 1_761_000_000_000, "content": {"msgtype": "m.text", "body": body}}


async def _propose(monkeypatch, capsys, note: tuple[str, str], answer: dict, *, msg="", by="",
                   history: dict | None = None):
    model = _Model(answer)
    card, sidecar = note
    request = {"path": PATH, "text": card, "sidecar": sidecar, "msg": msg, "by": by,
               **(history or {})}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
    code = await reprocess_note.run(_Paperless(), model, [])
    out = capsys.readouterr().out
    return code, (json.loads(out) if out.strip() else {}), model


ANSWER = {"title": "Trip: hotel and budget", "tags": ["hotel"], "persons": ["Homer"],
          "clean": CLEAN, "prompt_template": "3f9a1c2b7d4e"}


class TestANoteIsProposedAsItWouldBeFiledToday:

    @pytest.mark.asyncio
    async def test_the_proposal_is_the_clean_version_then_its_sources(self, monkeypatch, capsys):
        code, answer, _ = await _propose(monkeypatch, capsys, _note(), ANSWER)
        assert code == 0
        proposed = answer["proposed"]
        assert proposed.index(CLEAN) < proposed.index(f"## Sources\n\n> {WORDS}")
        assert "Homer will call the hotel." not in proposed, "the old interpretation goes"
        assert answer["title"] == "Trip: hotel and budget"

    @pytest.mark.asyncio
    async def test_the_note_keeps_its_id_its_filer_and_its_topic(self, monkeypatch, capsys):
        _, answer, _ = await _propose(monkeypatch, capsys, _note(), ANSWER)
        fields = parse_frontmatter(answer["proposed"])
        assert fields["card_id"] == "note-9c0f7c0256"
        assert fields["tags"][0] == "itchy-scratchy-land"
        assert json.loads(answer["proposed_sidecar"])["filed_by"] == "marge"

    @pytest.mark.asyncio
    async def test_the_model_reads_the_words_and_never_the_interpretation(self, monkeypatch, capsys):
        _, _, model = await _propose(monkeypatch, capsys, _note(), ANSWER)
        assert model.saw["text"] == WORDS
        assert "Homer will call the hotel" not in json.dumps(model.saw)

    @pytest.mark.asyncio
    async def test_the_sidecar_says_what_made_it(self, monkeypatch, capsys):
        _, answer, _ = await _propose(monkeypatch, capsys, _note(), ANSWER, msg="x", by="homer")
        made = json.loads(answer["proposed_sidecar"])["made"]
        assert (made["code_commit"], made["prompt_template"], made["requested_by"]) == (
            "9a5bdc9", "3f9a1c2b7d4e", "homer")

    @pytest.mark.asyncio
    async def test_a_note_in_the_older_format_is_proposed_in_todays(self, monkeypatch, capsys):
        old = ("---\ntype: note\ncard_id: note-9c0f7c0256\ntitle: Trip\nfiled_by: marge\n"
               "capture_id: \"$n:simpson\"\ntimestamp: \"2026-10-20T08:00:00Z\"\n---\n\n# Trip\n\n"
               f"> [!quote]- Original paste\n> {WORDS}\n")
        _, answer, model = await _propose(monkeypatch, capsys, (old, ""), ANSWER)
        assert model.saw["text"] == WORDS
        assert "Original paste" not in answer["proposed"]
        assert json.loads(answer["proposed_sidecar"])["sources"][0]["id"] == "$n:simpson"


class TestACorrectionIsReadAndKept:
    """`--msg` is appended as a `note` source by the person, read with the
    replies the note keeps, so the next refile reads it again."""

    @pytest.mark.asyncio
    async def test_the_model_reads_the_stored_replies_and_the_message(self, monkeypatch, capsys):
        _, _, model = await _propose(monkeypatch, capsys, _note(replies=[("bart", "and my swimsuit")]),
                                     ANSWER, msg="it is Friday, not Thursday", by="marge")
        assert model.corrected_from["corrections"] == \
            "Bart: and my swimsuit\nMarge: it is Friday, not Thursday"

    @pytest.mark.asyncio
    async def test_the_history_the_host_read_is_where_the_rebuild_starts(self, monkeypatch, capsys):
        """The host reads the card's history from its clone and sends the
        clean version before the first event and a person's edits: a note a
        person edited is asked as the card conversation, from that clean
        version, then every event in time order."""
        edit = {"person": "lisa", "at": "2026-10-20T12:00:00+02:00",
                "changes": [["Budget: at most 200.", "Budget: at most 220."]]}
        _, _, model = await _propose(monkeypatch, capsys, _note(replies=[("bart", "and my swimsuit")]),
                                     ANSWER, msg="it is Friday, not Thursday", by="marge",
                                     history={"prior": CLEAN, "edits": [edit]})
        assert model.rebuilt_from["prior"] == CLEAN
        assert model.rebuilt_from["events"] == [
            "Bart: and my swimsuit",
            "Lisa edited the card: changed 'Budget: at most 200.' to 'Budget: at most 220.'",
            "Marge: it is Friday, not Thursday"]

    @pytest.mark.asyncio
    async def test_the_proposal_keeps_it_as_the_last_source(self, monkeypatch, capsys):
        _, answer, _ = await _propose(monkeypatch, capsys, _note(), ANSWER,
                                      msg="it is Friday, not Thursday", by="marge")
        assert answer["proposed"].rstrip().endswith("> it is Friday, not Thursday")
        last = json.loads(answer["proposed_sidecar"])["sources"][-1]
        assert (last["role"], last["person"], last["channel"]) == ("note", "marge", "cli")


class TestTheModelReadsTheNotesSources:
    """The answer lists what the model read, each source with its quote,
    so the card browser shows exactly that."""

    @pytest.mark.asyncio
    async def test_the_cards_sources_are_listed(self, monkeypatch, capsys):
        _, answer, _ = await _propose(monkeypatch, capsys, _note(), ANSWER,
                                      msg="it is Friday", by="marge")
        assert [(s["role"], s["person"], s["quote"]) for s in answer["sources"]] == [
            ("original", "marge", WORDS), ("note", "marge", "it is Friday")]

    @pytest.mark.asyncio
    async def test_a_reply_in_the_thread_the_card_lacks_is_read_and_kept(self, monkeypatch, capsys):
        _chat_has(monkeypatch, ChatThread(
            _event("$n:simpson", "@marge:simpson", "an edited message"),
            [_event("$f", "@archivist-bot:simpson", "Filed: Trip"),
             _event("$r", "@bart:simpson", "and my swimsuit")]))
        _, answer, model = await _propose(monkeypatch, capsys, _note(), ANSWER)
        assert model.corrected_from == {"words": WORDS, "corrections": "Bart: and my swimsuit"}, \
            "sources are only appended; the chat replaces none"
        assert [s["id"] for s in json.loads(answer["proposed_sidecar"])["sources"]] == [
            "$n:simpson", "$r"]


class TestANoteThatCannotBeRefiledIsRefused:

    @pytest.mark.asyncio
    async def test_one_without_its_words(self, monkeypatch, capsys):
        card = "---\ntype: note\ncard_id: note-9c0f7c0256\ntitle: Own\n---\n\n# Own\n"
        code, answer, model = await _propose(monkeypatch, capsys, (card, ""), ANSWER)
        assert code == 0
        assert "no words" in answer["refused"]
        assert model.saw == {}, "no model call"

    @pytest.mark.asyncio
    async def test_one_whose_words_carry_an_older_reprocess_prompt(self, monkeypatch, capsys):
        note = _note(words=WORDS + "\n\nExisting tags on this entry: hotel, trip")
        _, answer, model = await _propose(monkeypatch, capsys, note, ANSWER)
        assert "Existing tags on this entry:" in answer["refused"]
        assert model.saw == {}

    @pytest.mark.asyncio
    async def test_one_whose_card_and_sidecar_do_not_pair(self, monkeypatch, capsys):
        card, sidecar = _note()
        _, answer, model = await _propose(monkeypatch, capsys, (card.replace(f"> {WORDS}", ""), sidecar),
                                          ANSWER)
        assert "do not pair" in answer["refused"]
        assert model.saw == {}

    @pytest.mark.asyncio
    async def test_no_proposal_without_a_model_answer(self, monkeypatch, capsys):
        code, answer, _ = await _propose(monkeypatch, capsys, _note(), {})
        assert code == 1
        assert answer == {}
