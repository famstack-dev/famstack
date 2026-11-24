"""CapturePipeline.reprocess: a correction of a filed capture.

A reply or `stack memory reprocess` corrects a capture that is already
filed. These pin what a correction keeps (the entry's file, its header,
the note's own words, a person's edits), where it finds the capture, and
that a corrected note is asked as the card conversation from its sources.
They share the stand-ins of `test_capture_pipeline.py`: the classifier
records the conversation it is shown, the mirror keeps what is written.
"""

from __future__ import annotations

import json

import pytest

from stack.cards.events import TextEdit
from stack.cards.history import CardHistory
from stack.cards.sources import NOTE, Source
from stacklets.docs.bot.vault_context import VaultContext
from stacklets.docs.bot.vault_entry import capture_frontmatter, render_capture
from tests.capture_mirror import FakeMirror
from tests.unit.stacklets.test_capture_pipeline import FakeClassifier, _old_note, _pipeline


class TestCorrectingAnEntryKeepsItsFile:
    """A correction re-renders the whole entry from the prior one and
    never sees the original bytes again. The archived file has to
    survive that, or replying "this is about school, not work" would
    quietly take the photograph off the page.

    The entry fed in here is rendered by `vault_entry`, not hand-written,
    so the read and the write cannot drift apart without this failing.
    """

    PATH = "marge/bookmarks/test-capture.md"

    def _entry(self, **kept) -> str:
        fm = capture_frontmatter(
            title="Schulkalender", captured_at="2026-03-14", kind="bookmark",
            source_uri="mxc://home.local/abc", persons=["Marge"],
            tags=["schule"], model=None, capture_id="$shot:home.local",
        )
        return render_capture(
            frontmatter=fm, kind="bookmark",
            captured_at="2026-03-14", source_uri="mxc://home.local/abc",
            persons=["Marge"], from_path=self.PATH, shared_bucket="family",
            summary="The school calendar for March.", facts=[],
            kept_media=kept or None,
        )

    async def _reprocess(self, raw: str) -> dict:
        mirror = FakeMirror()
        mirror._stored = {self.PATH: raw}
        pipe = _pipeline(mirror=mirror)
        await pipe.reprocess(
            vault_path=self.PATH, user_hint="das gehört zur Schule",
            sender_mxid="@marge:s",
        )
        return mirror.captures[-1]

    @pytest.mark.asyncio
    async def test_the_corrected_entry_still_names_the_file(self):
        published = await self._reprocess(self._entry(
            name="Bildschirmfoto.png",
            original="/media/2026/03/shot.png",
            embed="/media/2026/03/shot.webp",
        ))

        assert published["kept_media"] == {
            "name": "Bildschirmfoto.png",
            "original": "/media/2026/03/shot.png",
            "embed": "/media/2026/03/shot.webp",
        }

    @pytest.mark.asyncio
    async def test_a_document_stays_a_document_through_a_correction(self):
        """It had no embed before the correction and must not gain one:
        a PDF in an iframe is what the entry deliberately does not do."""
        published = await self._reprocess(self._entry(
            name="Anmeldung.pdf",
            original="/media/2026/03/form.pdf",
            embed="",
        ))

        assert published["kept_media"]["embed"] == ""
        assert published["kept_media"]["original"] == "/media/2026/03/form.pdf"

    @pytest.mark.asyncio
    async def test_an_entry_that_never_had_a_file_does_not_grow_one(self):
        published = await self._reprocess(self._entry())

        assert published["kept_media"] is None


class TestACorrectionReadsTheHeaderOrLeavesTheEntryAlone:
    """A correction rewrites an entry from its header: type, date, id.

    A header hand-edited into something the parser refuses would have
    to be guessed at, and the rewrite would file the capture under the
    guess. The entry is left as it is instead (and the log names it).
    """

    PATH = "marge/bookmarks/test-capture.md"

    def _entry(self) -> str:
        fm = capture_frontmatter(
            title="Schulkalender", captured_at="2026-03-14", kind="bookmark",
            source_uri="https://school.example/kalender", persons=["Marge"],
            tags=["schule"], model=None, capture_id="$cal:home.local",
        )
        return render_capture(
            frontmatter=fm, kind="bookmark",
            captured_at="2026-03-14", source_uri="https://school.example/kalender",
            persons=["Marge"], from_path=self.PATH, shared_bucket="family",
            summary="The school calendar for March.", facts=[],
        )

    async def _reprocess(self, raw: str):
        mirror = FakeMirror()
        mirror._stored = {self.PATH: raw}
        outcome = await _pipeline(mirror=mirror).reprocess(
            vault_path=self.PATH, user_hint="das gehört zur Schule",
            sender_mxid="@marge:s",
        )
        return outcome, mirror.captures

    @pytest.mark.asyncio
    async def test_a_header_with_a_one_line_list_is_corrected(self):
        raw = self._entry().replace("---\n", "---\nreminders: [Elternabend, Ferien]\n", 1)
        outcome, published = await self._reprocess(raw)
        assert outcome.status == "reclassified"
        assert published[-1]["capture_id"] == "$cal:home.local"

    @pytest.mark.asyncio
    async def test_a_header_that_does_not_parse_leaves_the_entry_alone(self):
        raw = self._entry().replace("---\n", "---\nnote: Marge: bitte pruefen\n", 1)
        outcome, published = await self._reprocess(raw)
        assert outcome.status == "capture_missing"
        assert published == []


class TestACorrectionFindsTheCaptureWhereItIsNow:
    """A correction names the capture it is about by the id it was filed
    with, the message's own event id, which never changes. Its path does:
    a refile used to rename it, and photos filed as bookmarks have moved
    to notes. So the capture is looked up by its id first, and the path
    the filing message recorded is only the fallback while the local
    copy of the family memory has not caught up."""

    OLD = "family/painting/bookmarks/2026/10/raku-454811.md"
    NEW = "family/painting/notes/2026/10/raku-454811.md"
    ID = "$img:home.test"

    def _entry(self, path: str) -> str:
        return _old_note("Raku", capture_id=self.ID, path=path, summary="Eine Schale.",
                         embed="/media/2026/10/img.webp", caption="Raku im Garten")

    async def _correct(self, tmp_path, monkeypatch, *, local: str | None):
        if local:
            (tmp_path / local).parent.mkdir(parents=True)
            (tmp_path / local).write_text(self._entry(local), encoding="utf-8")
        monkeypatch.setenv("MEMORY_VAULT_DIR", str(tmp_path))
        mirror = FakeMirror()
        mirror._stored = {self.NEW: self._entry(self.NEW)}
        out = await _pipeline(
            mirror=mirror, vault=VaultContext(language="de", shared_bucket="family"),
        ).reprocess(
            vault_path=self.OLD, capture_id=self.ID,
            user_hint="das ist Keramik", sender_mxid="@marge:s",
        )
        return out, mirror

    @pytest.mark.asyncio
    async def test_a_moved_capture_is_found_by_its_id(self, tmp_path, monkeypatch):
        out, mirror = await self._correct(tmp_path, monkeypatch, local=self.NEW)
        assert out.status == "reclassified"
        assert mirror.captures[-1]["existing_path"] == self.NEW

    @pytest.mark.asyncio
    async def test_a_note_is_found_by_the_id_its_sidecar_records(self, tmp_path, monkeypatch):
        """A note filed today keeps its source ids in its sidecar, not in
        its frontmatter (ADR-018)."""
        card = (tmp_path / self.NEW)
        card.parent.mkdir(parents=True)
        card.write_text("---\ntype: note\ncard_id: note-0b6e2d91aa\ntitle: Raku\n---\n\n# Raku\n")
        card.with_suffix(".json").write_text(json.dumps(
            {"card_id": "note-0b6e2d91aa", "sources": [{"role": "original", "id": self.ID}]}))
        monkeypatch.setenv("MEMORY_VAULT_DIR", str(tmp_path))
        vault = VaultContext(language="de", shared_bucket="family")
        assert vault.card_path("capture", self.ID) == self.NEW

    @pytest.mark.asyncio
    async def test_without_the_id_on_disk_the_recorded_path_is_tried(
        self, tmp_path, monkeypatch,
    ):
        """Nothing local carries the id, and the recorded path is gone:
        the correction says the capture is missing rather than guessing."""
        out, _ = await self._correct(tmp_path, monkeypatch, local=None)
        assert out.status == "capture_missing"


class TestACorrectionKeepsTheNotesOwnWords:
    """A note keeps the words its writer sent; the summary is the model's
    reading of them. A reply that corrects the note is read against those
    words, and the refiled note still holds them as they were sent. Read
    back from the summary instead, the note's words would be replaced by a
    paraphrase, and a short note, which carries no summary, by nothing."""

    PATH = "homer/notes/test-capture.md"
    WORDS = "Replace the tires on the Plymouth before winter.\n\nAsk Lenny for a dealer."

    def _note(self, summary: str = "") -> str:
        return _old_note("Winter tires", self.WORDS, capture_id="$note:s", path=self.PATH,
                         persons=["homer"], tags=["car"], summary=summary, filed_by="homer")

    async def _correct(self, raw: str):
        mirror, classifier = FakeMirror(), FakeClassifier()
        mirror._stored = {self.PATH: raw}
        await _pipeline(mirror=mirror, classifier=classifier).reprocess(
            vault_path=self.PATH, user_hint="it is the station wagon",
            sender_mxid="@homer:s",
        )
        return mirror.captures[-1], classifier

    @pytest.mark.asyncio
    async def test_the_corrected_note_holds_the_words_as_sent(self):
        published, _ = await self._correct(self._note(summary="Homer plans new tires."))
        assert published["sources"][0].words == self.WORDS

    @pytest.mark.asyncio
    async def test_the_model_reads_the_words_not_its_old_summary(self):
        _, classifier = await self._correct(self._note(summary="Homer plans new tires."))
        assert self.WORDS in classifier.saw_text
        assert "Homer plans new tires." not in classifier.saw_text

    @pytest.mark.asyncio
    async def test_a_note_without_a_summary_is_corrected_from_its_words(self):
        published, classifier = await self._correct(self._note())
        assert self.WORDS in classifier.saw_text
        assert published["sources"][0].words == self.WORDS


class TestACorrectedNoteIsRefiledFromItsWords:
    """A correction refiles a note as the card conversation (ADR-018 §7):
    the model is shown the note's words, the card as filed with its clean
    version, and each correction or edit in time order. The capture prompt
    is not asked again, so the card's title, tags and people stay where no
    correction names them, and a topic's note keeps its topic."""

    PATH = "family/itchy-scratchy-land/notes/2026/10/trip-3a338e.md"
    WORDS = "homer calls the hotel thu, budget max 200"
    CORRECTED = "Homer calls the hotel on Friday. Budget: at most 200."
    # What the capture prompt would answer: none of it may reach the card.
    ANSWER = {"title": "Hotel call", "tags": ["phone"], "persons": ["Bart"]}
    REPLY = "Marge: it is Friday, not Thursday"

    async def _correct(self, corrected=CORRECTED):
        mirror = FakeMirror()
        classifier = FakeClassifier(payload=dict(self.ANSWER), corrected=corrected)
        mirror._stored = {self.PATH: _old_note(
            "Trip", self.WORDS, capture_id="$n:s", path=self.PATH, persons=["homer"],
            tags=["itchy-scratchy-land", "hotel"])}
        await _pipeline(mirror=mirror, classifier=classifier).reprocess(
            vault_path=self.PATH, user_hint=self.REPLY,
            sender_mxid="@marge:s", reply_text="it is Friday, not Thursday")
        return mirror.captures[-1], classifier

    @staticmethod
    def _events(conversation) -> list[str]:
        return [m["content"] for m in conversation[3:-1]]

    @pytest.mark.asyncio
    async def test_the_model_is_shown_the_words_the_card_and_the_correction(self):
        _, classifier = await self._correct()
        conversation = classifier.saw_conversation
        assert self.WORDS in conversation[1]["content"]
        card = json.loads(conversation[2]["content"])
        assert (card["title"], card["persons"]) == ("Trip", ["homer"])
        assert self._events(conversation) == [self.REPLY]

    @pytest.mark.asyncio
    async def test_an_edited_note_that_shows_no_clean_version_starts_from_the_one_written_today(self):
        """The model keeps the shape of the clean version it is given: a
        card that shows the words alone is given a tidy one first, by the
        capture prompt, and the conversation starts from that."""
        tidy = "Homer calls the hotel on Thursday. Budget: at most 200."
        edit = TextEdit("lisa", "2026-10-21T09:00:00+00:00", (("max 200", "max 220"),))
        mirror = FakeMirror(history=CardHistory(edits=(edit,)))
        classifier = FakeClassifier(payload={**self.ANSWER, "clean": tidy}, corrected=self.CORRECTED)
        mirror._stored = {self.PATH: _old_note(
            "Trip", self.WORDS, capture_id="$n:s", path=self.PATH, persons=["homer"], tags=["hotel"])}
        await _pipeline(mirror=mirror, classifier=classifier).reprocess(
            vault_path=self.PATH, user_hint="", sender_mxid="@marge:s",
            reply_text="it is Friday, not Thursday")
        conversation = classifier.saw_conversation
        assert json.loads(conversation[2]["content"])["clean"] == tidy
        assert self._events(conversation) == [edit.message(), self.REPLY]

    @pytest.mark.asyncio
    async def test_a_reply_puts_a_hand_edited_quote_back_and_reads_the_edit_as_a_note(self):
        """A reply in the chat refiles a note by the rule `stack memory
        reprocess` follows (ADR-018 §4): the quote Lisa edited by hand, as
        the card's history shows it, goes back to the words as sent, and her
        words are her note, a correction before the reply."""
        edited = "Homer calls the hotel on Thursday, budget max 200"
        note = Source(NOTE, "lisa", "2026-10-21T09:00:00+00:00", "cards", "text",
                      "cards:0a1b2c3d4e5f6a7b", quote=edited)
        mirror = FakeMirror(history=CardHistory(quotes_as_sent=(("$n:s", self.WORDS),), quote_edits=(note,)))
        classifier = FakeClassifier(payload=dict(self.ANSWER), corrected=self.CORRECTED)
        mirror._stored = {self.PATH: _old_note(
            "Trip", edited, capture_id="$n:s", path=self.PATH, persons=["homer"], tags=["hotel"])}
        await _pipeline(mirror=mirror, classifier=classifier).reprocess(
            vault_path=self.PATH, user_hint="", sender_mxid="@marge:s",
            reply_text="it is Friday, not Thursday")
        assert [(s.role, s.person, s.words) for s in mirror.captures[-1]["sources"]] == [
            ("original", "marge", self.WORDS), ("note", "lisa", edited),
            ("reply", "marge", "it is Friday, not Thursday")]
        conversation = classifier.saw_conversation
        assert self.WORDS in conversation[1]["content"]
        assert self._events(conversation) == [f"Lisa: {edited}", self.REPLY]

    @pytest.mark.asyncio
    async def test_the_fields_no_correction_names_stay_as_the_card_holds_them(self):
        """The title and tags; the people are proven where person files exist
        (`test_a_correction_keeps_what_it_does_not_name`)."""
        published, _ = await self._correct()
        assert published["classification"]["title"] == "Trip"
        assert "phone" not in published["tags"]

    @pytest.mark.asyncio
    async def test_the_note_keeps_its_topic_and_files_the_corrected_text(self):
        published, _ = await self._correct()
        assert published["tags"][0] == "itchy-scratchy-land"
        assert published["clean"] == self.CORRECTED
        assert published["made"]["conversation_template"] == "c0e1c2d3"
        assert [(s.role, s.person, s.words) for s in published["sources"]] == [
            ("original", "marge", self.WORDS), ("reply", "marge", "it is Friday, not Thursday")]

    @pytest.mark.asyncio
    async def test_a_refused_corrected_text_keeps_the_text_the_card_shows(self):
        """Given a card whose text Lisa edited (the budget is 220 now), when
        a correction's rewrite loses the budget, then the card keeps the
        text it shows, her edit included, says why the rewrite was refused,
        and its title stays as filed. Its words alone would drop her edit."""
        shown = "Homer calls the hotel on Thursday. Budget: at most 220."
        card = ("---\ntype: note\ncard_id: note-0b6e2d91aa\ntitle: Trip\ntags:\n  - hotel\n---\n\n"
                f"# Trip\n\n{shown}\n\n## Sources\n\n> {self.WORDS}\n")
        sidecar = json.dumps({"card_id": "note-0b6e2d91aa", "filed_by": "marge", "sources": [
            {"role": "original", "person": "marge", "time": "2026-10-20T08:00:00+00:00",
             "channel": "chat", "kind": "text", "id": "$n:s"}]})
        mirror = FakeMirror()
        mirror._stored = {self.PATH: card, self.PATH.removesuffix(".md") + ".json": sidecar}
        classifier = FakeClassifier(payload=dict(self.ANSWER), corrected="Homer calls the hotel on Friday.")
        outcome = await _pipeline(mirror=mirror, classifier=classifier).reprocess(
            vault_path=self.PATH, user_hint="", sender_mxid="@homer:s",
            reply_text="it is Friday, not Thursday")
        published = mirror.captures[-1]
        assert published["clean"] == shown
        assert outcome.clean_rejected
        assert published["classification"]["title"] == "Trip"


class TestANoteIsRefiledFromItsSources:
    """A refile reads the note's sources from its card and its sidecar,
    never its interpretation (ADR-018 §6). A reply is appended as a source
    with its event id and time; sources are only appended."""

    PATH = "family/trip/notes/2026/10/trip-3a338e.md"
    CARD = ("---\ntype: note\ncard_id: note-0b6e2d91aa\ntitle: Trip\ntags:\n  - hotel\n---\n\n"
            "# Trip\n\nHomer calls the hotel on Thursday.\n\n## Sources\n\n"
            "> homer calls the hotel thu\n\n> and my swimsuit\n")
    SIDECAR = json.dumps({"card_id": "note-0b6e2d91aa", "filed_by": "marge", "sources": [
        {"role": "original", "person": "marge", "time": "2026-10-20T08:00:00+00:00",
         "channel": "chat", "kind": "text", "id": "$n:s"},
        {"role": "reply", "person": "bart", "time": "2026-10-20T09:00:00+00:00",
         "channel": "chat", "kind": "text", "id": "$r1:s"}]})

    async def _correct(self, reply="it is Friday"):
        mirror, classifier = FakeMirror(), FakeClassifier(payload={"title": "Trip", "tags": []})
        mirror._stored = {self.PATH: self.CARD, self.PATH.removesuffix(".md") + ".json": self.SIDECAR}
        await _pipeline(mirror=mirror, classifier=classifier).reprocess(
            vault_path=self.PATH, user_hint="ignored", sender_mxid="@marge:s",
            reply_text=reply, reply_id="$r2:s", reply_at="2026-10-21T10:00:00+00:00")
        return mirror.captures[-1], classifier

    @pytest.mark.asyncio
    async def test_the_model_reads_the_words_and_the_replies_in_order(self):
        _, classifier = await self._correct()
        conversation = classifier.saw_conversation
        assert "homer calls the hotel thu" in conversation[1]["content"]
        assert [m["content"] for m in conversation[3:-1]] == [
            "Bart: and my swimsuit", "Marge: it is Friday"]

    @pytest.mark.asyncio
    async def test_a_note_in_no_topic_is_not_asked_for_todos(self):
        """Given a note in Marge's own notes, which keep no list, when she
        corrects it, then the conversation asks for no to-dos: only a
        topic's list takes them."""
        mirror, classifier = FakeMirror(), FakeClassifier(payload={"title": "Trip", "tags": []})
        path = "marge/notes/2026/10/trip-3a338e.md"
        mirror._stored = {path: self.CARD, path.removesuffix(".md") + ".json": self.SIDECAR}
        await _pipeline(mirror=mirror, classifier=classifier).reprocess(
            vault_path=path, user_hint="", sender_mxid="@marge:s", reply_text="it is Friday")
        assert "action_items" not in classifier.saw_conversation[0]["content"]

    @pytest.mark.asyncio
    async def test_the_reply_is_appended_with_its_event_and_its_time(self):
        published, _ = await self._correct()
        assert [s.id for s in published["sources"]] == ["$n:s", "$r1:s", "$r2:s"]
        assert published["sources"][-1].time == "2026-10-21T10:00:00+00:00"
        assert published["made"]["requested_by"] == "marge"

    @pytest.mark.asyncio
    async def test_a_card_whose_quotes_and_sidecar_do_not_pair_is_left_alone(self):
        mirror = FakeMirror()
        mirror._stored = {self.PATH: self.CARD.replace("\n\n> and my swimsuit", ""),
                          self.PATH.removesuffix(".md") + ".json": self.SIDECAR}
        out = await _pipeline(mirror=mirror).reprocess(
            vault_path=self.PATH, user_hint="", sender_mxid="@marge:s", reply_text="x")
        assert out.status == "capture_missing"
        assert mirror.captures == []

    @pytest.mark.asyncio
    async def test_a_photo_is_looked_at_again_from_the_media_store(self, tmp_path, monkeypatch):
        """A refile with no correction reads the note again, the photo included."""
        (tmp_path / "2026" / "10").mkdir(parents=True)
        (tmp_path / "2026" / "10" / "p1.jpg").write_bytes(b"\xff\xd8\xff photo")
        monkeypatch.setenv("MEDIA_ARCHIVE_DIR", str(tmp_path))
        card = self.CARD.replace("> homer calls the hotel thu",
                                 "> ![[/media/2026/10/p1.jpg]]\n>\n> Bart drew this"
                                 ).replace("\n\n> and my swimsuit", "")
        sidecar = json.dumps({**json.loads(self.SIDECAR), "sources": [
            {**json.loads(self.SIDECAR)["sources"][0], "kind": "image"}]})
        mirror, classifier = FakeMirror(), FakeClassifier(payload={"title": "Drawing", "tags": []})
        mirror._stored = {self.PATH: card, self.PATH.removesuffix(".md") + ".json": sidecar}
        await _pipeline(mirror=mirror, classifier=classifier).reprocess(
            vault_path=self.PATH, user_hint="", sender_mxid="@marge:s")
        assert classifier.saw_text == "Bart drew this"
        assert [i.data for i in classifier.saw_images] == [b"\xff\xd8\xff photo"]
