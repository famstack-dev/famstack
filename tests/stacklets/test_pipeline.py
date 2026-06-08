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
    LLMModelNotFoundError,
    LLMTimeoutError,
    LLMUnavailableError,
    _build_synthesize_prompt,
    _format_evidence_block,
    _to_whoosh_query,
    enrich_document,
    extract_bot_summary,
    reformat_document,
)


# ── Synthesis prompt ──────────────────────────────────────────────────────

class TestFormatEvidenceBlock:
    """Render the evidence list the synthesis prompt feeds the LLM."""

    def test_includes_number_kind_date_title(self):
        out = _format_evidence_block([
            {"kind": "Paperless", "date": "2026-03-22",
             "title": "Anthropic Max Plan Invoice",
             "summary": "€90 due 2026-03-22."},
        ])
        assert "[1] Paperless · 2026-03-22 · Anthropic Max Plan Invoice" in out
        assert "€90 due 2026-03-22" in out

    def test_persons_line_when_present(self):
        out = _format_evidence_block([
            {"title": "T", "persons": ["Homer", "Marge"], "summary": "s"},
        ])
        assert "Persons: Homer, Marge" in out

    def test_persons_line_omitted_when_empty(self):
        out = _format_evidence_block([
            {"title": "T", "persons": [], "summary": "s"},
        ])
        assert "Persons:" not in out

    def test_missing_summary_renders_placeholder(self):
        # Without an explicit "(no summary)" marker the model might
        # quietly skip the hit; the placeholder tells it the hit
        # exists but has no body to lean on.
        out = _format_evidence_block([{"title": "T"}])
        assert "(no summary available)" in out

    def test_multi_hit_separated_by_blank_lines(self):
        out = _format_evidence_block([
            {"title": "A", "summary": "alpha"},
            {"title": "B", "summary": "beta"},
        ])
        # Each hit ends with a blank line so the model sees clear
        # boundaries between numbered stanzas.
        assert "alpha\n\n[2]" in out


class TestBuildSynthesizePrompt:
    """The synthesis prompt template."""

    def test_question_and_evidence_embedded(self):
        prompt = _build_synthesize_prompt(
            "When was the last invoice?",
            [{"title": "Invoice", "date": "2026-03-22", "summary": "due"}],
            "en",
            today="2026-05-23",
        )
        assert "When was the last invoice?" in prompt
        assert "[1]" in prompt
        assert "2026-03-22" in prompt

    def test_language_hint_passed_through(self):
        # The household language drives the response language so a
        # German family doesn't get an English answer from an
        # English-speaking model.
        prompt = _build_synthesize_prompt(
            "x?", [{"title": "y", "summary": "z"}], "de",
            today="2026-05-23",
        )
        assert "language: de" in prompt or "Respond in" in prompt

    def test_deferral_pattern_in_rules(self):
        # The "need to read [N] in detail" deferral is the contract
        # the wire-up layer keys off when surfacing follow-up hits.
        prompt = _build_synthesize_prompt(
            "x?", [{"title": "y", "summary": "z"}], "en",
            today="2026-05-23",
        )
        assert "read [N] in detail" in prompt

    def test_today_embedded_for_relative_time(self):
        # The model can't resolve "the last" / "this month" without
        # knowing today's date -- otherwise it answers from its
        # training cutoff and confidently picks the wrong record.
        prompt = _build_synthesize_prompt(
            "Was the last invoice this month?",
            [{"title": "x", "summary": "y"}],
            "en",
            today="2026-05-23",
        )
        assert "2026-05-23" in prompt


# ── Bot-summary extraction from Paperless doc dicts ───────────────────────

class TestExtractBotSummary:
    """Pull the classifier-written note out of a Paperless doc.

    The note shape is set by `_format_classifier_summary`: prose,
    blank line, bulleted facts, blank line, parties, trailing
    `<!-- archivist-bot -->` marker. Tests pin that the marker is
    stripped and non-bot notes are ignored.
    """

    @staticmethod
    def _bot_note(body: str) -> dict:
        return {
            "id": 1,
            "note": body + "\n\n<!-- archivist-bot -->",
        }

    def test_returns_summary_text(self):
        doc = {"notes": [self._bot_note(
            "Invoice from Anthropic for €90.00.\n\n- Number: MDIIDNBM-0006",
        )]}
        out = extract_bot_summary(doc)
        # Marker is gone; trailing whitespace trimmed.
        assert "<!-- archivist-bot -->" not in out
        assert "Invoice from Anthropic" in out
        assert "MDIIDNBM-0006" in out

    def test_returns_empty_when_no_notes(self):
        assert extract_bot_summary({}) == ""
        assert extract_bot_summary({"notes": []}) == ""

    def test_returns_empty_when_only_user_notes(self):
        # A free-text user note (no marker) must NOT be returned --
        # synthesis would otherwise pull arbitrary human prose into
        # the LLM context.
        doc = {"notes": [{"id": 99, "note": "remember to call Marge"}]}
        assert extract_bot_summary(doc) == ""

    def test_skips_user_note_picks_bot_note(self):
        # Mixed list: the bot note must win regardless of position.
        doc = {"notes": [
            {"id": 99, "note": "free text from a user"},
            self._bot_note("Bot summary body"),
        ]}
        assert "Bot summary body" in extract_bot_summary(doc)

    def test_legacy_summary_heading_still_recognised(self):
        # Pre-marker bot notes used `## Summary` etc; the bot-note
        # detector recognises both shapes so old vaults keep working.
        doc = {"notes": [{
            "id": 1,
            "note": "## Summary\nLegacy summary text.",
        }]}
        out = extract_bot_summary(doc)
        assert "Legacy summary text" in out


# ── Whoosh query translation ──────────────────────────────────────────────

class TestToWhooshQuery:
    """Translate chat search input into Whoosh syntax.

    The bug this guards against: a user types `pangasius` expecting the
    doc titled "Pangasiusfilet" to surface, but Whoosh requires either
    a full-token match or an explicit prefix wildcard. Bare-term
    queries miss anything where the token is a compound the analyzer
    doesn't split.
    """

    def test_appends_wildcard_to_bare_token(self):
        # The whole point: "pangasius" alone won't hit the index, but
        # "pangasius*" will prefix-match "pangasiusfilet".
        assert _to_whoosh_query("pangasius") == "pangasius*"

    def test_each_token_gets_its_own_wildcard(self):
        # Two-word queries narrow the set (Whoosh AND under Paperless's
        # default operator), with both terms allowed to prefix-match.
        assert _to_whoosh_query("radlager auto") == "radlager* auto*"

    def test_passes_through_existing_wildcard(self):
        # If the caller already wrote Whoosh syntax we trust them and
        # don't double-wildcard.
        assert _to_whoosh_query("pangasius*") == "pangasius*"

    def test_passes_through_operators(self):
        # AND/OR/NOT are Whoosh operators -- not search terms -- so they
        # must not get a `*` suffix.
        assert _to_whoosh_query("fisch OR pangasius") == "fisch* OR pangasius*"
        assert _to_whoosh_query("fisch AND NOT pangasius") == "fisch* AND NOT pangasius*"

    def test_passes_through_field_query(self):
        # `field:value` is intentional Whoosh syntax for restricting a
        # search to one indexed field -- leave it alone.
        assert _to_whoosh_query("title:rezept") == "title:rezept"

    def test_passes_through_quoted_phrase(self):
        # Quotes signal an explicit phrase query; wildcarding breaks it.
        assert _to_whoosh_query('"fisch rezept"') == '"fisch rezept"'

    def test_empty_input_unchanged(self):
        # Defensive: a stray "" must NOT become "*" -- a wildcard alone
        # matches every doc, which is the opposite of a no-op.
        assert _to_whoosh_query("") == ""
        assert _to_whoosh_query("   ") == "   "


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
                       images=None, ontology_section="",
                       correspondents_section="", persons_section="",
                       date_filed=None, user_hint=None,
                       initial_classification=None):
        self.classify_calls.append({
            "ocr_text": ocr_text, "tags": tags,
            "doc_types": doc_types, "correspondents": correspondents,
            "images": images, "ontology_section": ontology_section,
            "correspondents_section": correspondents_section,
            "persons_section": persons_section,
            "date_filed": date_filed,
            "user_hint": user_hint,
            "initial_classification": initial_classification,
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

    @pytest.mark.asyncio
    async def test_reprocess_does_not_overwrite_created(self, seeded_paperless):
        # On reprocess the LLM's date is ignored — the doc's existing
        # `created` is either the previous pipeline pass's verdict or a
        # human correction, both better than a fresh hallucinated guess.
        classifier = StubClassifier(payload={
            "title": "x", "date": "2015-02-14",
        })
        await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=_doc(),
            is_reprocess=True,
        )
        # Either no update at all, or one with no `created` key.
        for _, updates in seeded_paperless.updates:
            assert "created" not in updates

    @pytest.mark.asyncio
    async def test_initial_classify_still_writes_created(self, seeded_paperless):
        # Initial classification path is unchanged — the LLM date
        # replaces Paperless's auto-set upload timestamp.
        classifier = StubClassifier(payload={
            "title": "x", "date": "2026-02-14",
        })
        await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=_doc(),
        )
        assert any(
            updates.get("created") == "2026-02-14"
            for _, updates in seeded_paperless.updates
        )

    @pytest.mark.asyncio
    async def test_date_filed_flows_to_classifier(self, seeded_paperless):
        # The caller-supplied anchor (Matrix message timestamp for live
        # uploads, doc.added for reprocess) must reach the classifier so
        # the LLM resolves partial dates against the right reference.
        classifier = StubClassifier(payload={"title": "x"})
        await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=_doc(),
            date_filed="2026-02-15",
        )
        (call,) = classifier.classify_calls
        assert call["date_filed"] == "2026-02-15"

    @pytest.mark.asyncio
    async def test_user_hint_flows_to_classifier(self, seeded_paperless):
        # The user's reply-correction must reach the classifier so the
        # reply-to-reprocess flow actually changes the LLM's output.
        classifier = StubClassifier(payload={"title": "x"})
        await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=_doc(),
            user_hint="this is Marge's, not Homer's",
        )
        (call,) = classifier.classify_calls
        assert call["user_hint"] == "this is Marge's, not Homer's"


# ── Submitter fallback ────────────────────────────────────────────────────

class TestEnrichSubmitterFallback:
    """When the classifier returns no persons, the document is attributed
    to whoever uploaded it. Honours the prompt's `never invent names`
    rule — the uploader is real, the LLM's guess is not.
    """

    @pytest.mark.asyncio
    async def test_falls_back_to_submitter_when_persons_empty(self, seeded_paperless):
        classifier = StubClassifier(payload={
            "title": "Bergchalet Refugium Martius",
            "persons": [],  # doc has no names, classifier obeys the rule
        })
        await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=_doc(),
            submitter_mxid="@homer:test.local",
        )
        # Paperless updates carry the Homer person tag from the fallback.
        _, updates = seeded_paperless.updates[0]
        assert seeded_paperless.tags["Person: Homer"] in updates["tags"]

    @pytest.mark.asyncio
    async def test_no_fallback_when_classifier_named_persons(self, seeded_paperless):
        # Classifier produced explicit names — the submitter fallback
        # does NOT override the LLM's correct identification.
        classifier = StubClassifier(payload={
            "title": "x",
            "persons": ["Marge"],
        })
        await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=_doc(),
            submitter_mxid="@homer:test.local",  # different from Marge
        )
        _, updates = seeded_paperless.updates[0]
        assert seeded_paperless.tags["Person: Marge"] in updates["tags"]
        assert seeded_paperless.tags["Person: Homer"] not in updates["tags"]

    @pytest.mark.asyncio
    async def test_no_fallback_when_submitter_unknown(self, seeded_paperless):
        # No submitter_mxid (CLI reprocess, legacy archivist) — empty
        # persons stays empty rather than guessing.
        classifier = StubClassifier(payload={
            "title": "x",
            "persons": [],
        })
        await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=_doc(),
        )
        _, updates = seeded_paperless.updates[0]
        # No Person tags in the update set.
        person_tag_ids = {
            seeded_paperless.tags[t] for t in seeded_paperless.tags
            if t.startswith("Person: ")
        }
        assert not (person_tag_ids & set(updates["tags"]))

    @pytest.mark.asyncio
    async def test_submitter_unrecognised_passes_through(self, seeded_paperless):
        # An mxid for a non-family-member (e.g., a guest user with no
        # Person tag) — the fallback returns None and persons stays empty.
        classifier = StubClassifier(payload={
            "title": "x",
            "persons": [],
        })
        await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=_doc(),
            submitter_mxid="@milhouse:test.local",
        )
        _, updates = seeded_paperless.updates[0]
        person_tag_ids = {
            seeded_paperless.tags[t] for t in seeded_paperless.tags
            if t.startswith("Person: ")
        }
        assert not (person_tag_ids & set(updates["tags"]))


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


# ── Classify prompt construction ──────────────────────────────────────────

class TestClassifyPromptDateAnchor:
    """The classify prompt carries a `Date filed:` anchor and a
    date-resolution rule so the LLM resolves partial dates from
    documents like a chalet booking that only prints "14 FEBRUAR"
    without a year. Callers feed the relevant date — Matrix message
    timestamp for live uploads, Paperless `added` for reprocess —
    so reprocessing weeks later doesn't shift the anchor."""

    def test_date_filed_appears_when_supplied(self):
        from pipeline import _build_classify_prompt
        prompt = _build_classify_prompt(
            ocr_text="x", person_names=[], category_tags=[],
            doc_types=[], correspondents=[],
            date_filed="2026-02-15",
        )
        assert "Date filed: 2026-02-15" in prompt

    def test_date_filed_defaults_to_today(self):
        # No `date_filed=` → the prompt falls back to the system
        # date so the LLM still has an anchor. Callers should pass a
        # specific date when one is available.
        from pipeline import _build_classify_prompt
        prompt = _build_classify_prompt(
            ocr_text="x", person_names=[], category_tags=[],
            doc_types=[], correspondents=[],
        )
        assert "Date filed:" in prompt

    def test_date_resolution_rule_present(self):
        from pipeline import _build_classify_prompt
        prompt = _build_classify_prompt(
            ocr_text="x", person_names=[], category_tags=[],
            doc_types=[], correspondents=[],
            date_filed="2026-05-20",
        )
        # Rule must tell the LLM to use the reference date for missing
        # years and to consider forward-looking documents (bookings,
        # reservations) vs. backward-looking ones (invoices, receipts).
        assert "DATE:" in prompt
        assert "forward-looking" in prompt
        assert "backward-looking" in prompt


class TestClassifyPromptUserHint:
    """The user's accompanying note -- a fresh upload caption, a scan
    session opener, or a reply-to-correct on a prior filing -- rides
    on the prompt as a `User context` block. It is treated as
    supplementary evidence that shapes the summary, disambiguates
    persons/correspondent, and steers the title -- without
    overriding direct document evidence."""

    def test_hint_appears_when_supplied(self):
        from pipeline import _build_classify_prompt
        prompt = _build_classify_prompt(
            ocr_text="x", person_names=[], category_tags=[],
            doc_types=[], correspondents=[],
            user_hint="actually this is for Marge, not Homer",
        )
        assert "User context" in prompt
        assert "actually this is for Marge, not Homer" in prompt
        # The framing must steer the LLM toward weaving the note into
        # the summary rather than treating it as a verbatim quote.
        assert "SUMMARY" in prompt
        assert "supplementary evidence" in prompt

    def test_no_block_when_hint_is_missing(self):
        from pipeline import _build_classify_prompt
        prompt = _build_classify_prompt(
            ocr_text="x", person_names=[], category_tags=[],
            doc_types=[], correspondents=[],
        )
        # Default classification path produces the same prompt as before;
        # the user-context section only appears when the human spoke.
        assert "User context — what the human" not in prompt

    def test_blank_hint_is_treated_as_missing(self):
        from pipeline import _build_classify_prompt
        prompt = _build_classify_prompt(
            ocr_text="x", person_names=[], category_tags=[],
            doc_types=[], correspondents=[],
            user_hint="   \n  ",
        )
        assert "User context — what the human" not in prompt


class TestClassifyPromptVisionRule:
    """When the bot attaches an image, the OCR text may contain template
    footers or hidden text layers that conflict with what the model sees.
    The prompt directs the model to prefer the image for conflicts."""

    def test_vision_over_ocr_rule_present(self):
        from pipeline import _build_classify_prompt
        prompt = _build_classify_prompt(
            ocr_text="x", person_names=[], category_tags=[],
            doc_types=[], correspondents=[],
        )
        assert "VISION VS OCR" in prompt
        assert "prefer the image" in prompt


class TestClassifyPromptPersonsRule:
    """The persons rule forbids inventing names. When the document
    specifies a group only by count or role (e.g., '2 Erwachsene,
    2 Kinder') the LLM must return []; the system falls back to the
    submitter rather than letting the model guess."""

    def test_no_invention_rule_present(self):
        from pipeline import _build_classify_prompt
        prompt = _build_classify_prompt(
            ocr_text="x", person_names=[], category_tags=[],
            doc_types=[], correspondents=[],
        )
        # Must explicitly forbid guessing — references group counts as
        # the canonical "do not invent" case.
        assert "NEVER guess" in prompt
        assert "2 Erwachsene, 2 Kinder" in prompt
        # Mentions the submitter fallback so the model knows empty
        # is the right answer when names aren't in the text.
        assert "fallback" in prompt.lower()


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
        # No section headings — prose, bulleted facts, parties line all
        # separated by blank lines. The structure is obvious from shape.
        assert "Kfz-Versicherung Jahresbeitrag." in text
        assert "- Total: EUR 340\n- Policy: #12345" in text
        assert "ADAC → Homer" in text
        # Headings the old format used must NOT appear — they forced an
        # English label onto non-English content.
        assert "## Summary" not in text
        assert "## Facts" not in text
        assert "## Parties" not in text
        # Action items never made it into the Paperless note.
        assert "Action" not in text
        assert "pay" not in text

    def test_summary_only(self):
        text = self._fmt({"summary": "Kurze Notiz."})
        # Prose first, marker last. Nothing else.
        assert text == "Kurze Notiz.\n\n<!-- archivist-bot -->"

    def test_facts_skip_empty_strings(self):
        text = self._fmt({"summary": "x", "facts": ["Total: EUR 5", "", "  "]})
        # Only the real fact gets a bullet — empty strings drop out.
        # Counting bullet lines in the body before the marker (the
        # marker itself contains a dash-space and would otherwise be
        # counted).
        body = text.rsplit("\n\n<!--", 1)[0]
        assert body.count("\n- ") == 1
        assert "Total: EUR 5" in text

    def test_parties_correspondent_only(self):
        text = self._fmt({"summary": "x"}, correspondent="ADAC")
        assert "ADAC" in text
        assert "→" not in text

    def test_parties_persons_only(self):
        text = self._fmt({"summary": "x"}, persons=["Homer", "Marge"])
        assert "Homer, Marge" in text
        assert "→" not in text

    def test_returns_none_when_nothing_to_say(self):
        assert self._fmt({}) is None
        assert self._fmt({"summary": "", "facts": []}) is None

    def test_parties_alone_still_writes(self):
        """Even with no summary prose, a sender → recipient line is useful
        context on a bare doc."""
        text = self._fmt({}, persons=["Homer"], correspondent="ADAC")
        assert text == "ADAC → Homer\n\n<!-- archivist-bot -->"

    def test_sections_separated_by_blank_lines(self):
        # Without headings, blank lines do the work of section
        # boundaries. Two newlines between every block.
        text = self._fmt(
            {"summary": "Prose.", "facts": ["A", "B"]},
            persons=["Homer"], correspondent="ADAC",
        )
        # Prose → blank → facts → blank → parties → blank → marker.
        assert text == (
            "Prose.\n\n"
            "- A\n- B\n\n"
            "ADAC → Homer\n\n"
            "<!-- archivist-bot -->"
        )

    def test_marker_trails_the_note(self):
        # The sweep on next reprocess looks for the marker anywhere
        # in the note (it lives on the last line in current output).
        text = self._fmt({"summary": "x"})
        assert text.endswith("<!-- archivist-bot -->")


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
        assert "Annual premium renewal." in result.summary
        assert "- Total: EUR 340" in result.summary
        assert "ADAC → Homer" in result.summary
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
        """A prior classifier-shaped note is deleted; the new one lands fresh."""
        old_id = seeded_paperless.seed_note(
            42, "## Summary\nOld take.", user=7,
        )

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
    async def test_reclassify_sweeps_multiple_prior_bot_notes(self, seeded_paperless):
        """Every classifier-shaped note is removed — not just one — so
        repeated reprocessing can't accumulate stale summaries."""
        first = seeded_paperless.seed_note(42, "## Summary\nFirst pass.", user=7)
        second = seeded_paperless.seed_note(42, "## Facts\n- old", user=7)

        classifier = StubClassifier(payload={
            "title": "x", "summary": "Third pass.",
        })
        await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=_doc(doc_id=42),
        )

        ids = {n["id"] for n in seeded_paperless.notes[42]}
        assert first not in ids
        assert second not in ids
        # New summary lands.
        assert any("Third pass." in n["note"] for n in seeded_paperless.notes[42])

    @pytest.mark.asyncio
    async def test_reclassify_leaves_human_notes(self, seeded_paperless):
        """A free-text user note (no `##` heading) survives reprocessing
        regardless of who Paperless says wrote it. The content signature
        is the source of truth, not ownership."""
        human_id = seeded_paperless.seed_note(42, "My personal note.", user=99)
        bot_old_id = seeded_paperless.seed_note(
            42, "## Summary\nOld bot summary.", user=7,
        )

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
    async def test_sweep_is_independent_of_user_field(self, seeded_paperless):
        """Even when Paperless returns no usable user_id (the ownership
        check is offline), classifier-shaped notes are still swept.
        That's the fix for the bug where repeated reprocessing piled
        up stale summaries."""
        seeded_paperless.user_id = None
        seeded_paperless.seed_note(42, "## Summary\nOld summary.", user=99)

        classifier = StubClassifier(payload={
            "title": "x", "summary": "New summary.",
        })
        await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=_doc(doc_id=42),
        )

        notes = seeded_paperless.notes[42]
        # Old swept, new added — exactly one note remains.
        assert len(notes) == 1
        assert "New summary." in notes[0]["note"]

    @pytest.mark.asyncio
    async def test_marker_tagged_notes_are_swept(self, seeded_paperless):
        """A note carrying the marker is removed even when its content
        doesn't match the legacy heading shape — the marker is the
        forward-compatible signal."""
        marker_id = seeded_paperless.seed_note(
            42,
            "Some new shape we might add later.\n\n<!-- archivist-bot -->",
            user=7,
        )
        classifier = StubClassifier(payload={
            "title": "x", "summary": "Fresh take.",
        })
        await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=_doc(doc_id=42),
        )
        ids = {n["id"] for n in seeded_paperless.notes[42]}
        assert marker_id not in ids

    @pytest.mark.asyncio
    async def test_new_notes_carry_the_marker(self, seeded_paperless):
        """Fresh classifier notes ship with the marker so the next
        reprocess can identify and sweep them."""
        classifier = StubClassifier(payload={
            "title": "x", "summary": "First pass.",
        })
        await enrich_document(
            paperless=seeded_paperless, classifier=classifier, doc=_doc(doc_id=42),
        )
        (note,) = seeded_paperless.notes[42]
        assert note["note"].endswith("<!-- archivist-bot -->")


# ── Vision multimodal ─────────────────────────────────────────────────────
#
# These tests exercise the multimodal call construction and the lazy
# vision-capability probe in isolation — no live LLM, no aiohttp. We
# patch `_request` to return whatever a fake backend would, and assert
# on the content shape the Classifier would have sent on the wire.


from capabilities import ModelCapabilities  # noqa: E402
from pipeline import Classifier, ImageAttachment  # noqa: E402


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
            "describe",
            [ImageAttachment(data=b"\x89PNG\r\n", mime="image/png")],
        )
        assert out[0] == {"type": "text", "text": "describe"}
        assert out[1]["type"] == "image_url"

    def test_image_url_is_data_url_with_correct_mime(self):
        out = Classifier._multimodal_content(
            "x",
            [ImageAttachment(data=b"hello", mime="image/jpeg")],
        )
        url = out[1]["image_url"]["url"]
        assert url.startswith("data:image/jpeg;base64,")
        # Base64 decodes back to the original bytes.
        import base64
        b64 = url.split(",", 1)[1]
        assert base64.b64decode(b64) == b"hello"

    def test_multiple_images_become_separate_parts(self):
        # Each ImageAttachment gets its own image_url part — the model
        # processes pages independently rather than stacking on our side.
        out = Classifier._multimodal_content(
            "describe",
            [
                ImageAttachment(data=b"page1", mime="image/png"),
                ImageAttachment(data=b"page2", mime="image/png"),
                ImageAttachment(data=b"page3", mime="image/png"),
            ],
        )
        # 1 text + 3 images = 4 parts.
        assert len(out) == 4
        assert out[0]["type"] == "text"
        assert all(p["type"] == "image_url" for p in out[1:])


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
    """`classify` attaches images only when vision is available."""

    @pytest.mark.asyncio
    async def test_text_only_when_no_images(self, patched_resolve_model):
        # Baseline — no images → string content as before.
        c = _make_classifier(request_results=['{"title": "t"}'])
        await c.classify(
            ocr_text="some text", tags={}, doc_types={}, correspondents={},
        )
        assert isinstance(c.calls[0]["content"], str)
        assert "some text" in c.calls[0]["content"]

    @pytest.mark.asyncio
    async def test_images_attached_when_vision_supported(self, patched_resolve_model):
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
            images=[ImageAttachment(data=b"\x89PNG\r\n", mime="image/png")],
        )
        assert isinstance(c.calls[0]["content"], list)
        assert c.calls[0]["content"][0]["type"] == "text"
        assert c.calls[0]["content"][1]["type"] == "image_url"

    @pytest.mark.asyncio
    async def test_multiple_images_all_attached(self, patched_resolve_model):
        # N pages → N image_url parts in the request, alongside the prompt.
        caps = ModelCapabilities()
        caps.record_vision("stub-model", True)
        c = _make_classifier(
            request_results=['{"title": "t"}'],
            capabilities=caps,
        )
        await c.classify(
            ocr_text="some text", tags={}, doc_types={}, correspondents={},
            images=[
                ImageAttachment(data=b"p1", mime="image/png"),
                ImageAttachment(data=b"p2", mime="image/png"),
            ],
        )
        parts = c.calls[0]["content"]
        assert len(parts) == 3  # text + 2 images
        assert sum(1 for p in parts if p["type"] == "image_url") == 2

    @pytest.mark.asyncio
    async def test_images_dropped_when_vision_unsupported(self, patched_resolve_model):
        # Cached vision=False → images are silently dropped, request is
        # text-only. The intent is degradation, not error.
        caps = ModelCapabilities()
        caps.record_vision("stub-model", False)
        c = _make_classifier(
            request_results=['{"title": "t"}'],
            capabilities=caps,
        )
        await c.classify(
            ocr_text="some text", tags={}, doc_types={}, correspondents={},
            images=[ImageAttachment(data=b"\x89PNG\r\n", mime="image/png")],
        )
        assert isinstance(c.calls[0]["content"], str)

    @pytest.mark.asyncio
    async def test_non_image_mime_filtered(self, patched_resolve_model):
        # Defensive: caller passes an attachment but mime says it isn't
        # an image — that single attachment is filtered out, falling back
        # to text-only rather than risk a malformed multimodal payload.
        caps = ModelCapabilities()
        caps.record_vision("stub-model", True)
        c = _make_classifier(
            request_results=['{"title": "t"}'],
            capabilities=caps,
        )
        await c.classify(
            ocr_text="some text", tags={}, doc_types={}, correspondents={},
            images=[ImageAttachment(data=b"binary", mime="application/pdf")],
        )
        assert isinstance(c.calls[0]["content"], str)


# ── Query rewrite (recall mode) ────────────────────────────────────────

class TestBuildRewritePrompt:
    """The recall prompt: question + ontology section in, prompt string out."""

    @staticmethod
    def _build(question="Wann hatte Bart MMR?", section="(...topics...)", lang="de"):
        from pipeline import _build_rewrite_prompt
        return _build_rewrite_prompt(question, section, lang)

    def test_question_appears_verbatim(self):
        # The LLM needs the literal phrasing to pick the right keywords;
        # any rewriting on our side would defeat the point.
        prompt = self._build(question="Was kostet die Auto-Versicherung?")
        assert "Was kostet die Auto-Versicherung?" in prompt

    def test_ontology_section_embedded(self):
        # Synonym and translation expansion is the ontology's job, so
        # the rendered section has to land inside the prompt.
        prompt = self._build(section="- Insurance (Versicherung)")
        assert "- Insurance (Versicherung)" in prompt

    def test_language_hint_present(self):
        # The hint primes the model to answer in the document language;
        # without it we'd get English keywords for a German vault.
        prompt = self._build(lang="de")
        assert "de" in prompt

    def test_json_output_contract_present(self):
        # The parser only knows two shapes. The prompt has to ask for
        # one of them explicitly.
        prompt = self._build()
        assert "JSON" in prompt
        assert "keywords" in prompt


class TestParseRewriteResponse:
    """The keyword extractor parser — what the recall layer trusts."""

    @staticmethod
    def _parse(raw):
        from pipeline import _parse_rewrite_response
        return _parse_rewrite_response(raw)

    def test_object_form(self):
        # The contract shape: {"keywords": [...]} -- the format the
        # prompt explicitly asks for.
        assert self._parse('{"keywords": ["Auto", "KFZ"]}') == ["Auto", "KFZ"]

    def test_bare_array_fallback(self):
        # Some smaller models forget the wrapper. Honoring a bare
        # array means a question doesn't lose recall just because
        # the model skipped the keys.
        assert self._parse('["Auto", "KFZ"]') == ["Auto", "KFZ"]

    def test_strips_empty_strings(self):
        # An empty string in the alternation regex matches every line.
        # Drop them before the join — better to lose a slot than to
        # turn the search into a "match everything" query.
        assert self._parse('{"keywords": ["Auto", "", "  ", "KFZ"]}') == ["Auto", "KFZ"]

    def test_strips_whitespace_around_keywords(self):
        # Models occasionally pad with spaces; not worth a re-prompt.
        assert self._parse('{"keywords": ["  Auto  ", "KFZ\\n"]}') == ["Auto", "KFZ"]

    def test_coerces_numbers_to_strings(self):
        # A year keyword like 2026 comes back as a JSON number. Recall
        # against a date is legitimate ("documents from 2026"), so
        # accept and stringify rather than drop.
        assert self._parse('{"keywords": [2026, "Steuer"]}') == ["2026", "Steuer"]

    def test_caps_at_six(self):
        # A runaway model that returns twenty keywords would build an
        # absurd alternation; cap defensively so the regex compile
        # stays cheap.
        many = '{"keywords": ["a","b","c","d","e","f","g","h","i","j"]}'
        assert self._parse(many) == ["a", "b", "c", "d", "e", "f"]

    def test_invalid_json_returns_empty(self):
        # No keywords means: literal-query fallback. The recall layer
        # is best-effort, never a gate.
        assert self._parse("not json") == []
        assert self._parse("") == []
        assert self._parse("{") == []

    def test_wrong_shape_returns_empty(self):
        # Object without "keywords" key, scalar response — same outcome
        # as a parse failure. Don't try to guess the model's intent.
        assert self._parse('{"foo": "bar"}') == []
        assert self._parse('"just a string"') == []
        assert self._parse('42') == []
