"""Unit tests for the archivist enrichment pipeline.

The pipeline is the shared classify + apply-to-Paperless + reformat core
used by the archivist bot (live uploads) and the `stack docs reprocess`
CLI (reprocessing filed documents). Tests use in-memory stub versions of
PaperlessAPI and Classifier so the unit exercises matching + update
assembly without HTTP or LLM calls.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs" / "bot"))

from pipeline import (  # noqa: E402
    EnrichResult,
    LLMModelNotFoundError,
    LLMTimeoutError,
    LLMUnavailableError,
    enrich_document,
    reformat_document,
)


# ── Stub collaborators ────────────────────────────────────────────────────

class StubPaperless:
    """In-memory PaperlessAPI stand-in. Records calls for assertions."""

    def __init__(self, tags=None, doc_types=None, correspondents=None,
                 user_id: int | None = 7):
        self.tags = dict(tags or {})
        self.doc_types = dict(doc_types or {})
        self.correspondents = dict(correspondents or {})
        self._next_id = 1000
        self.updates: list[tuple[int, dict]] = []
        self.created_tags: list[tuple[str, str]] = []  # (name, color)
        self.created_doc_types: list[str] = []
        self.created_correspondents: list[str] = []
        # Notes ledger: {doc_id: [{"id": int, "note": str, "user": int}]}.
        # `user_id` is "whoami" for the bot's token — set to None to
        # simulate /users/me/ being unavailable.
        self.user_id = user_id
        self.notes: dict[int, list[dict]] = {}
        self._next_note_id = 5000

    async def get_tags(self):
        return dict(self.tags)

    async def get_doc_types(self):
        return dict(self.doc_types)

    async def get_correspondents(self):
        return dict(self.correspondents)

    async def update_doc(self, doc_id, updates):
        self.updates.append((doc_id, dict(updates)))
        return True

    async def create_tag(self, name, color="#4caf50"):
        tid = self._next_id
        self._next_id += 1
        self.tags[name] = tid
        self.created_tags.append((name, color))
        return tid

    async def create_doc_type(self, name):
        tid = self._next_id
        self._next_id += 1
        self.doc_types[name] = tid
        self.created_doc_types.append(name)
        return tid

    async def create_correspondent(self, name):
        tid = self._next_id
        self._next_id += 1
        self.correspondents[name] = tid
        self.created_correspondents.append(name)
        return tid

    # ── Notes (used by the summary sink) ─────────────────────────────

    async def get_current_user_id(self):
        return self.user_id

    async def list_notes(self, doc_id):
        return [dict(n) for n in self.notes.get(doc_id, [])]

    async def add_note(self, doc_id, text):
        entry = {"id": self._next_note_id, "note": text, "user": self.user_id}
        self._next_note_id += 1
        self.notes.setdefault(doc_id, []).append(entry)
        return True

    async def delete_note(self, doc_id, note_id):
        bucket = self.notes.get(doc_id, [])
        self.notes[doc_id] = [n for n in bucket if n["id"] != note_id]
        return True

    def seed_note(self, doc_id: int, text: str, user: int | None) -> int:
        """Insert a pre-existing note with an explicit owner. Returns its id."""
        nid = self._next_note_id
        self._next_note_id += 1
        self.notes.setdefault(doc_id, []).append(
            {"id": nid, "note": text, "user": user},
        )
        return nid


class StubClassifier:
    """Returns a pre-canned classify/reformat payload or raises."""

    def __init__(self, payload=None, classify_raises=None,
                 reformat_text=None, reformat_raises=None):
        self.payload = payload
        self.classify_raises = classify_raises
        self.reformat_text = reformat_text
        self.reformat_raises = reformat_raises
        self.classify_calls: list[dict] = []
        self.reformat_calls: list[str] = []

    async def classify(self, *, ocr_text, tags, doc_types, correspondents,
                       image_data=None, image_mime=None):
        self.classify_calls.append({
            "ocr_text": ocr_text, "tags": tags,
            "doc_types": doc_types, "correspondents": correspondents,
            "image_data": image_data, "image_mime": image_mime,
        })
        if self.classify_raises:
            raise self.classify_raises
        return self.payload or {}

    async def reformat(self, ocr_text):
        self.reformat_calls.append(ocr_text)
        if self.reformat_raises:
            raise self.reformat_raises
        return self.reformat_text


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def seeded_paperless():
    """Paperless with a small Springfield-themed taxonomy pre-seeded.

    Tags carry both categories and the closed-set "Person: X" tags so
    enrich_document's topic/person split has something real to match.
    """
    return StubPaperless(
        tags={
            "Insurance": 1, "Shopping": 2, "Medical": 3,
            "Person: Homer": 10, "Person: Marge": 11, "Person: Bart": 12,
        },
        doc_types={"Invoice": 100, "Receipt": 101, "Letter": 102},
        correspondents={"ADAC": 200, "Kwik-E-Mart": 201},
    )


def _doc(doc_id=42, content="Invoice text from ADAC for car insurance.",
         tags=None, document_type=None):
    """Build a Paperless doc dict in the shape the pipeline expects."""
    return {
        "id": doc_id,
        "content": content,
        "tags": list(tags or []),
        "document_type": document_type,
    }


# ── Happy path ────────────────────────────────────────────────────────────

class TestEnrichHappyPath:
    """A well-formed LLM classification flows through to Paperless."""

    @pytest.mark.asyncio
    async def test_full_classification_applied(self, seeded_paperless):
        classifier = StubClassifier(payload={
            "title": "ADAC - Kfz-Versicherung 2026 EUR 340",
            "date": "2026-03-15",
            "topics": ["Insurance"],
            "persons": ["Homer"],
            "correspondent": "ADAC",
            "document_type": "Invoice",
            "summary": "Annual renewal.",
            "facts": ["EUR 340.00"],
            "action_items": [],
        })
        doc = _doc(doc_id=42, tags=[])

        result = await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=doc,
        )

        assert result.resolved_topics == ["Insurance"]
        assert result.resolved_persons == ["Homer"]
        assert result.resolved_correspondent == "ADAC"
        assert result.resolved_type == "Invoice"
        assert result.created_new == []
        assert result.llm_error is None

        # One PATCH to Paperless with the full update set
        assert len(seeded_paperless.updates) == 1
        doc_id, updates = seeded_paperless.updates[0]
        assert doc_id == 42
        assert updates["title"] == "ADAC - Kfz-Versicherung 2026 EUR 340"
        assert updates["created"] == "2026-03-15"
        assert updates["correspondent"] == 200  # ADAC id
        assert updates["document_type"] == 100  # Invoice id
        # Tag ids — Insurance + Person: Homer
        assert set(updates["tags"]) == {1, 10}

    @pytest.mark.asyncio
    async def test_multiple_topics_and_persons(self, seeded_paperless):
        classifier = StubClassifier(payload={
            "title": "Family health insurance receipt",
            "topics": ["Insurance", "Medical"],
            "persons": ["Homer", "Marge"],
        })
        doc = _doc(doc_id=43)

        result = await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=doc,
        )

        assert result.resolved_topics == ["Insurance", "Medical"]
        assert sorted(result.resolved_persons) == ["Homer", "Marge"]
        _, updates = seeded_paperless.updates[0]
        assert set(updates["tags"]) == {1, 3, 10, 11}  # Insurance, Medical, Homer, Marge


# ── Empty / missing inputs ────────────────────────────────────────────────

class TestEnrichEmptyContent:
    """Empty or tiny OCR text short-circuits without calling the LLM."""

    @pytest.mark.asyncio
    async def test_empty_content_skips_classify(self, seeded_paperless):
        classifier = StubClassifier(payload={"title": "ignored"})
        doc = _doc(content="")

        result = await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=doc,
        )

        assert result.classification == {}
        assert result.resolved_topics == []
        assert classifier.classify_calls == []
        assert seeded_paperless.updates == []

    @pytest.mark.asyncio
    async def test_whitespace_only_skips_classify(self, seeded_paperless):
        classifier = StubClassifier(payload={"title": "ignored"})
        doc = _doc(content="   \n\t  ")

        result = await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=doc,
        )

        assert result.classification == {}
        assert classifier.classify_calls == []


class TestEnrichEmptyClassification:
    """Classifier returned {} — no updates, no mistaken creations."""

    @pytest.mark.asyncio
    async def test_empty_payload_applies_nothing(self, seeded_paperless):
        classifier = StubClassifier(payload={})
        doc = _doc()

        result = await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=doc,
        )

        assert result.classification == {}
        assert result.resolved_topics == []
        assert result.resolved_correspondent is None
        assert seeded_paperless.updates == []
        assert seeded_paperless.created_tags == []


# ── LLM errors ────────────────────────────────────────────────────────────

class TestEnrichLLMErrors:
    """Each LLM exception maps to a structured llm_error tuple."""

    @pytest.mark.asyncio
    async def test_unavailable(self, seeded_paperless):
        classifier = StubClassifier(
            classify_raises=LLMUnavailableError("HTTP 502"),
        )
        result = await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=_doc(),
        )
        assert result.llm_error == ("unavailable", "HTTP 502")
        assert result.classification == {}
        assert seeded_paperless.updates == []

    @pytest.mark.asyncio
    async def test_model_missing(self, seeded_paperless):
        classifier = StubClassifier(
            classify_raises=LLMModelNotFoundError("qwen3.5:14b"),
        )
        result = await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=_doc(),
        )
        assert result.llm_error == ("model_missing", "qwen3.5:14b")

    @pytest.mark.asyncio
    async def test_timeout(self, seeded_paperless):
        classifier = StubClassifier(
            classify_raises=LLMTimeoutError("qwen3.5:14b timed out"),
        )
        result = await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=_doc(),
        )
        assert result.llm_error == ("timeout", "qwen3.5:14b timed out")


# ── Fuzzy matching at the apply step ──────────────────────────────────────

class TestEnrichFuzzyMatching:
    """LLM output goes through matching.py before Paperless touches."""

    @pytest.mark.asyncio
    async def test_correspondent_fuzzy_match_avoids_duplicate(self, seeded_paperless):
        # LLM says "ADAC e.V." — should match existing "ADAC", not create new
        classifier = StubClassifier(payload={
            "title": "Invoice", "correspondent": "ADAC e.V.",
        })
        result = await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=_doc(),
        )
        assert result.resolved_correspondent == "ADAC"
        assert seeded_paperless.created_correspondents == []
        _, updates = seeded_paperless.updates[0]
        assert updates["correspondent"] == 200

    @pytest.mark.asyncio
    async def test_topic_fuzzy_match(self, seeded_paperless):
        # "Shopping Groceries" wouldn't fuzzy-match "Shopping" at word boundary
        # with the prefix semantics — but "Insurance" vs "Insurance" is exact.
        classifier = StubClassifier(payload={"topics": ["insurance"]})
        result = await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=_doc(),
        )
        assert result.resolved_topics == ["Insurance"]
        assert seeded_paperless.created_tags == []


class TestEnrichCreateNew:
    """Unknown tags/types/correspondents get created — except persons."""

    @pytest.mark.asyncio
    async def test_new_topic_tag_created(self, seeded_paperless):
        classifier = StubClassifier(payload={"topics": ["School"]})
        result = await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=_doc(),
        )
        assert result.resolved_topics == ["School"]
        assert seeded_paperless.created_tags == [("School", "#4caf50")]
        assert 'tag "School"' in result.created_new

    @pytest.mark.asyncio
    async def test_new_correspondent_created(self, seeded_paperless):
        classifier = StubClassifier(payload={
            "title": "x", "correspondent": "Springfield Elementary",
        })
        result = await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=_doc(),
        )
        assert result.resolved_correspondent == "Springfield Elementary"
        assert seeded_paperless.created_correspondents == ["Springfield Elementary"]
        assert 'correspondent "Springfield Elementary"' in result.created_new

    @pytest.mark.asyncio
    async def test_new_document_type_created(self, seeded_paperless):
        classifier = StubClassifier(payload={
            "title": "x", "document_type": "Certificate",
        })
        result = await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=_doc(),
        )
        assert result.resolved_type == "Certificate"
        assert seeded_paperless.created_doc_types == ["Certificate"]

    @pytest.mark.asyncio
    async def test_unknown_person_not_created(self, seeded_paperless):
        """Persons are a closed set seeded from users.toml — never mint new."""
        classifier = StubClassifier(payload={"persons": ["Maggie"]})
        result = await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=_doc(),
        )
        assert result.resolved_persons == []
        # No new "Person: Maggie" tag
        assert all(not name.startswith("Person: ") for name, _ in seeded_paperless.created_tags)


# ── Fresh-reprocess semantics ─────────────────────────────────────────────

class TestEnrichFreshReprocess:
    """enrich_document treats each run as a full fresh classification:
    prior tags are dropped, prior document_type is overwritten."""

    @pytest.mark.asyncio
    async def test_prior_tags_cleared(self, seeded_paperless):
        """Old classified tags don't accumulate on reprocess."""
        classifier = StubClassifier(payload={
            "title": "x", "topics": ["Insurance"], "persons": ["Homer"],
        })
        # Doc had an old classification ("Shopping" + Person: Marge) plus a
        # tag id the user added by hand (#999). All three should go; only
        # the new classification remains.
        doc = _doc(tags=[2, 11, 999])  # Shopping, Person: Marge, stray

        result = await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=doc,
        )

        _, updates = seeded_paperless.updates[0]
        assert set(updates["tags"]) == {1, 10}  # Insurance, Person: Homer
        assert result.resolved_topics == ["Insurance"]
        assert result.resolved_persons == ["Homer"]

    @pytest.mark.asyncio
    async def test_tags_cleared_when_llm_returns_no_categories(self, seeded_paperless):
        """LLM returned a classification but no topics/persons — doc ends
        up with no tags. Matches what a fresh upload with the same LLM
        output would produce."""
        classifier = StubClassifier(payload={"title": "x"})
        doc = _doc(tags=[2, 11])  # had old classification

        await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=doc,
        )
        _, updates = seeded_paperless.updates[0]
        assert updates["tags"] == []

    @pytest.mark.asyncio
    async def test_document_type_overwritten(self, seeded_paperless):
        """A prior document_type is replaced with the LLM's pick, not preserved."""
        classifier = StubClassifier(payload={
            "title": "x", "document_type": "Letter",
        })
        doc = _doc(document_type=100)  # Invoice id

        result = await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=doc,
        )

        _, updates = seeded_paperless.updates[0]
        assert updates["document_type"] == 102  # Letter id
        assert result.resolved_type == "Letter"


# ── Title / date edge cases ───────────────────────────────────────────────

class TestEnrichTitleAndDate:

    @pytest.mark.asyncio
    async def test_title_truncated_to_paperless_limit(self, seeded_paperless):
        from pipeline import MAX_TITLE_LENGTH
        long_title = "A" * (MAX_TITLE_LENGTH + 50)
        classifier = StubClassifier(payload={"title": long_title})
        await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=_doc(),
        )
        _, updates = seeded_paperless.updates[0]
        assert len(updates["title"]) == MAX_TITLE_LENGTH

    @pytest.mark.asyncio
    async def test_bad_date_ignored(self, seeded_paperless):
        classifier = StubClassifier(payload={
            "title": "x", "date": "March 2026",  # not ISO
        })
        await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=_doc(),
        )
        _, updates = seeded_paperless.updates[0]
        assert "created" not in updates


# ── Classify input cap ────────────────────────────────────────────────────

class TestEnrichClassifyMaxChars:
    """The classify input cap is configurable and enforced in enrich_document.

    Truncation used to live silently inside Classifier.classify at a
    hardcoded 3000 chars, which quietly chopped long docs (contracts,
    research papers) before the LLM ever saw the bulk of the content.
    The cap is now a pipeline-level concern with a generous default, so
    a deployment with a bigger-context model can lift it in bot.toml.
    """

    @pytest.mark.asyncio
    async def test_long_content_truncated_to_explicit_cap(self, seeded_paperless):
        classifier = StubClassifier(payload={"title": "x"})
        doc = _doc(content="y" * 10000)

        await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=doc,
            classify_max_chars=500,
        )

        (call,) = classifier.classify_calls
        assert len(call["ocr_text"]) == 500

    @pytest.mark.asyncio
    async def test_short_content_passes_through_unchanged(self, seeded_paperless):
        classifier = StubClassifier(payload={"title": "x"})
        doc = _doc(content="Invoice from ADAC")

        await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=doc,
            classify_max_chars=500,
        )

        (call,) = classifier.classify_calls
        assert call["ocr_text"] == "Invoice from ADAC"

    @pytest.mark.asyncio
    async def test_default_cap_is_generous(self, seeded_paperless):
        """Default must be well above the old 3000 so typical contracts and
        multi-page receipts reach the classifier whole."""
        classifier = StubClassifier(payload={"title": "x"})
        doc = _doc(content="z" * 15000)

        await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=doc,
        )

        (call,) = classifier.classify_calls
        assert len(call["ocr_text"]) == 15000


# ── Reformat ──────────────────────────────────────────────────────────────

class TestReformatDocument:

    @pytest.mark.asyncio
    async def test_reformat_success_patches_content(self, seeded_paperless):
        classifier = StubClassifier(reformat_text="# Clean markdown\n\nbody")
        updated = await reformat_document(
            paperless=seeded_paperless, classifier=classifier,
            doc_id=42, ocr_text="messy\nOCR",
        )
        assert updated == "# Clean markdown\n\nbody"
        assert seeded_paperless.updates == [(42, {"content": "# Clean markdown\n\nbody"})]

    @pytest.mark.asyncio
    async def test_reformat_returns_none_leaves_content_alone(self, seeded_paperless):
        classifier = StubClassifier(reformat_text=None)
        updated = await reformat_document(
            paperless=seeded_paperless, classifier=classifier,
            doc_id=42, ocr_text="original",
        )
        assert updated is None
        assert seeded_paperless.updates == []

    @pytest.mark.asyncio
    async def test_reformat_too_short_treated_as_failure(self, seeded_paperless):
        """LLM occasionally returns a token or empty string — guard against it."""
        classifier = StubClassifier(reformat_text="ok")
        updated = await reformat_document(
            paperless=seeded_paperless, classifier=classifier,
            doc_id=42, ocr_text="original",
        )
        assert updated is None
        assert seeded_paperless.updates == []


# ── Summary formatter ─────────────────────────────────────────────────────

class TestSummaryFormatter:
    """_format_classifier_summary is a pure function: classification dict
    plus resolved-parties context → Markdown or None.
    """

    def _fmt(self, classification, *, persons=None, correspondent=None):
        from pipeline import _format_classifier_summary
        return _format_classifier_summary(
            classification,
            resolved_persons=persons or [],
            resolved_correspondent=correspondent,
        )

    def test_full_payload_renders_all_sections(self):
        text = self._fmt(
            {
                "summary": "Kfz-Versicherung Jahresbeitrag.",
                "facts": ["Total: EUR 340", "Policy: #12345"],
                # action_items deliberately included — must NOT appear in output.
                "action_items": [{"action": "pay", "due": "2026-04-30"}],
            },
            persons=["Homer"],
            correspondent="ADAC",
        )
        assert "## Summary\nKfz-Versicherung Jahresbeitrag." in text
        assert "## Facts\n- Total: EUR 340\n- Policy: #12345" in text
        assert "## Parties\nADAC → Homer" in text
        assert "Action" not in text
        assert "pay" not in text

    def test_summary_only(self):
        text = self._fmt({"summary": "Kurze Notiz."})
        assert text == "## Summary\nKurze Notiz."

    def test_facts_skip_empty_strings(self):
        text = self._fmt({"summary": "x", "facts": ["Total: EUR 5", "", "  "]})
        assert text.count("- ") == 1
        assert "Total: EUR 5" in text

    def test_parties_correspondent_only(self):
        text = self._fmt({"summary": "x"}, correspondent="ADAC")
        assert "## Parties\nADAC" in text
        assert "→" not in text

    def test_parties_persons_only(self):
        text = self._fmt({"summary": "x"}, persons=["Homer", "Marge"])
        assert "## Parties\nHomer, Marge" in text
        assert "→" not in text

    def test_returns_none_when_nothing_to_say(self):
        assert self._fmt({}) is None
        assert self._fmt({"summary": "", "facts": []}) is None

    def test_parties_alone_still_writes(self):
        """Even with no summary prose, a sender → recipient line is useful
        context on a bare doc."""
        text = self._fmt({}, persons=["Homer"], correspondent="ADAC")
        assert text == "## Parties\nADAC → Homer"


# ── Summary write path ────────────────────────────────────────────────────

class TestSummaryWrite:
    """enrich_document writes the summary as a Paperless note and replaces
    the bot's prior summary on reclassify without touching human notes.
    """

    @pytest.mark.asyncio
    async def test_summary_written_on_classify(self, seeded_paperless):
        classifier = StubClassifier(payload={
            "title": "x",
            "summary": "Annual premium renewal.",
            "facts": ["Total: EUR 340"],
            "correspondent": "ADAC",
            "persons": ["Homer"],
        })
        result = await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=_doc(doc_id=42),
        )
        assert result.summary is not None
        assert "## Summary\nAnnual premium renewal." in result.summary
        notes = seeded_paperless.notes[42]
        assert len(notes) == 1
        assert notes[0]["note"] == result.summary

    @pytest.mark.asyncio
    async def test_no_summary_when_formatter_returns_none(self, seeded_paperless):
        """Thin classification (no summary, no facts, no parties) writes nothing."""
        classifier = StubClassifier(payload={"title": "x"})
        result = await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=_doc(doc_id=42),
        )
        assert result.summary is None
        assert seeded_paperless.notes.get(42, []) == []

    @pytest.mark.asyncio
    async def test_reclassify_replaces_bot_summary(self, seeded_paperless):
        """A prior bot-owned summary is deleted; the new one lands fresh."""
        # Seed a prior bot note (same user id as the stub's "whoami" = 7)
        old_id = seeded_paperless.seed_note(42, "## Summary\nOld take.", user=7)

        classifier = StubClassifier(payload={
            "title": "x", "summary": "New take.",
        })
        await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=_doc(doc_id=42),
        )

        notes = seeded_paperless.notes[42]
        assert len(notes) == 1
        assert notes[0]["id"] != old_id
        assert "New take." in notes[0]["note"]

    @pytest.mark.asyncio
    async def test_reclassify_leaves_human_notes(self, seeded_paperless):
        """A note written by a different user (human) must survive reclassify."""
        human_id = seeded_paperless.seed_note(42, "My personal note.", user=99)
        bot_old_id = seeded_paperless.seed_note(42, "Old bot summary.", user=7)

        classifier = StubClassifier(payload={
            "title": "x", "summary": "Fresh summary.",
        })
        await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=_doc(doc_id=42),
        )

        ids = {n["id"] for n in seeded_paperless.notes[42]}
        assert human_id in ids          # human note survived
        assert bot_old_id not in ids    # bot's prior summary was swept

    @pytest.mark.asyncio
    async def test_posts_without_sweep_when_user_id_unknown(self, seeded_paperless):
        """If /users/me/ fails, we can't tell bot notes from human ones —
        post the new summary without touching existing notes."""
        seeded_paperless.user_id = None
        seeded_paperless.seed_note(42, "Existing note.", user=99)

        classifier = StubClassifier(payload={
            "title": "x", "summary": "New summary.",
        })
        await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=_doc(doc_id=42),
        )

        notes = seeded_paperless.notes[42]
        assert len(notes) == 2  # both the pre-existing and the new one


# ── Vision multimodal ─────────────────────────────────────────────────────
#
# These tests exercise the multimodal call construction and the lazy
# vision-capability probe in isolation — no live LLM, no aiohttp. We
# patch `_request` to return whatever a fake backend would, and assert
# on the content shape the Classifier would have sent on the wire.

import json as _json  # noqa: E402

from capabilities import ModelCapabilities  # noqa: E402
from pipeline import Classifier  # noqa: E402


def _make_classifier(*, request_results: list, capabilities=None) -> Classifier:
    """Build a Classifier whose `_request` returns canned values in order.

    Each entry is either a string (returned as-is) or an Exception
    instance (raised). Records every (task, content, model_override)
    triple in `c.calls` for assertions.
    """
    c = Classifier(http=None, url="http://stub", key="",
                   bot_name="archivist-bot",
                   capabilities=capabilities or ModelCapabilities())
    c.calls = []  # type: ignore[attr-defined]

    queue = list(request_results)

    async def _stub_request(task, content, *, json_mode=False, model_override=None):
        c.calls.append({"task": task, "content": content,
                        "model_override": model_override})
        if not queue:
            raise AssertionError("Stub _request called more times than seeded")
        nxt = queue.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    c._request = _stub_request  # type: ignore[assignment]
    return c


# `resolve_model("archivist-bot/classifier")` reads stack.models at call
# time — patch it module-side so tests don't need AI_DEFAULT_MODEL set.
@pytest.fixture
def patched_resolve_model(monkeypatch):
    import stack.models
    monkeypatch.setattr(stack.models, "_DEFAULT_MODEL", "stub-model")
    monkeypatch.setattr(stack.models, "_MODELS", {})
    return "stub-model"


class TestMultimodalContentBuilder:
    """`_multimodal_content` is pure — exercise it directly."""

    def test_text_part_first_image_second(self):
        out = Classifier._multimodal_content(
            "describe", b"\x89PNG\r\n", "image/png",
        )
        assert out[0] == {"type": "text", "text": "describe"}
        assert out[1]["type"] == "image_url"

    def test_image_url_is_data_url_with_correct_mime(self):
        out = Classifier._multimodal_content(
            "x", b"hello", "image/jpeg",
        )
        url = out[1]["image_url"]["url"]
        assert url.startswith("data:image/jpeg;base64,")
        # Base64 decodes back to the original bytes.
        import base64
        b64 = url.split(",", 1)[1]
        assert base64.b64decode(b64) == b"hello"


class TestHasVision:
    """`has_vision` cache + probe behaviour."""

    @pytest.mark.asyncio
    async def test_returns_cached_true_without_probing(self, patched_resolve_model):
        caps = ModelCapabilities()
        caps.record_vision("stub-model", True)
        c = _make_classifier(request_results=[], capabilities=caps)
        assert await c.has_vision() is True
        assert c.calls == []  # no probe — cache hit

    @pytest.mark.asyncio
    async def test_returns_cached_false_without_probing(self, patched_resolve_model):
        caps = ModelCapabilities()
        caps.record_vision("stub-model", False)
        c = _make_classifier(request_results=[], capabilities=caps)
        assert await c.has_vision() is False
        assert c.calls == []

    @pytest.mark.asyncio
    async def test_probe_success_caches_true(self, patched_resolve_model):
        caps = ModelCapabilities()
        c = _make_classifier(request_results=["ok"], capabilities=caps)
        assert await c.has_vision() is True
        assert caps.supports_vision("stub-model") is True
        # Probe sent multimodal content with one text + one image part.
        assert isinstance(c.calls[0]["content"], list)
        assert len(c.calls[0]["content"]) == 2

    @pytest.mark.asyncio
    async def test_probe_rejection_caches_false(self, patched_resolve_model):
        # Backend complains about images → that's a definitive "no vision".
        caps = ModelCapabilities()
        err = LLMUnavailableError("HTTP 400: model does not support image input")
        c = _make_classifier(request_results=[err], capabilities=caps)
        assert await c.has_vision() is False
        assert caps.supports_vision("stub-model") is False

    @pytest.mark.asyncio
    async def test_inconclusive_failure_does_not_cache(self, patched_resolve_model):
        # A generic HTTP 500 doesn't tell us anything about capability —
        # don't poison the cache, just say no for this run.
        caps = ModelCapabilities()
        err = LLMUnavailableError("HTTP 500: internal error")
        c = _make_classifier(request_results=[err], capabilities=caps)
        assert await c.has_vision() is False
        assert caps.supports_vision("stub-model") is None  # not cached

    @pytest.mark.asyncio
    async def test_timeout_does_not_cache(self, patched_resolve_model):
        caps = ModelCapabilities()
        err = LLMTimeoutError("model loading")
        c = _make_classifier(request_results=[err], capabilities=caps)
        assert await c.has_vision() is False
        assert caps.supports_vision("stub-model") is None


class TestClassifyWithImage:
    """`classify` attaches an image only when vision is available."""

    @pytest.mark.asyncio
    async def test_text_only_when_no_image(self, patched_resolve_model):
        # Baseline — no image → string content as before.
        c = _make_classifier(request_results=['{"title": "t"}'])
        await c.classify(
            ocr_text="some text", tags={}, doc_types={}, correspondents={},
        )
        assert isinstance(c.calls[0]["content"], str)
        assert "some text" in c.calls[0]["content"]

    @pytest.mark.asyncio
    async def test_image_attached_when_vision_supported(self, patched_resolve_model):
        # Pre-cache vision=True so classify takes the multimodal path
        # without having to probe in this test.
        caps = ModelCapabilities()
        caps.record_vision("stub-model", True)
        c = _make_classifier(
            request_results=['{"title": "t"}'],
            capabilities=caps,
        )
        await c.classify(
            ocr_text="some text", tags={}, doc_types={}, correspondents={},
            image_data=b"\x89PNG\r\n", image_mime="image/png",
        )
        assert isinstance(c.calls[0]["content"], list)
        assert c.calls[0]["content"][0]["type"] == "text"
        assert c.calls[0]["content"][1]["type"] == "image_url"

    @pytest.mark.asyncio
    async def test_image_dropped_when_vision_unsupported(self, patched_resolve_model):
        # Cached vision=False → image is silently dropped, request is
        # text-only. The intent is degradation, not error.
        caps = ModelCapabilities()
        caps.record_vision("stub-model", False)
        c = _make_classifier(
            request_results=['{"title": "t"}'],
            capabilities=caps,
        )
        await c.classify(
            ocr_text="some text", tags={}, doc_types={}, correspondents={},
            image_data=b"\x89PNG\r\n", image_mime="image/png",
        )
        assert isinstance(c.calls[0]["content"], str)

    @pytest.mark.asyncio
    async def test_non_image_mime_ignored(self, patched_resolve_model):
        # Defensive: caller passes image_data but mime says it isn't an
        # image — fall back to text-only rather than risk a malformed
        # multimodal payload.
        caps = ModelCapabilities()
        caps.record_vision("stub-model", True)
        c = _make_classifier(
            request_results=['{"title": "t"}'],
            capabilities=caps,
        )
        await c.classify(
            ocr_text="some text", tags={}, doc_types={}, correspondents={},
            image_data=b"binary", image_mime="application/pdf",
        )
        assert isinstance(c.calls[0]["content"], str)
