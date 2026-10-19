"""SearchService — query resolution, dual search, formatting.

The service does the work (no Matrix) and returns the final reply text,
calling an injected `announce` only for the mid-flow "looking deeper"
status. These pin the scope logic, the no-results path, literal
formatting, and when an answer triggers the deep dive. The model is
scripted (it is the external boundary); whether a real model follows
the prompt is the e2e rig's question.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
# `stacklets/` for the sibling `memory.lib`; the bot dir for search_service
# and its peers (nl_query, recall, search_format) — mirrors archivist's own
# sys.path setup so the module imports standalone in tests.
sys.path.insert(0, str(_REPO_ROOT / "stacklets"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs" / "bot"))

from search_service import SearchService  # noqa: E402


def _t(key, **kw):
    q = kw.get("query", "")
    return f"{key}:{q}" if q else key


class FakePaperless:
    def __init__(self, results=None):
        self._results = results or []

    async def search(self, query):
        return list(self._results)


class FakeClassifier:
    """Literal/no-result paths never call the classifier."""


class _FakeVault:
    def ontology(self):
        return None
    def ontology_section(self, language=None):
        return ""
    def correspondents_section(self):
        return ""


def _service(paperless, *, shared_bucket="family"):
    return SearchService(
        classifier=FakeClassifier(),
        paperless=paperless,
        t=_t,
        language="en",
        code_public_url="http://code",
        mirror_org="family",
        link_base_url="http://home.test/go",
        shared_bucket=shared_bucket,
        vault=_FakeVault(),
    )


class _FakeNotifier:
    async def status(self, key, **kwargs):
        pass


class TestScopesForSender:

    def test_unknown_sender_gets_shared_only(self):
        svc = _service(FakePaperless())
        assert svc.scopes_for_sender(None) == ["family/"]

    def test_known_sender_adds_their_entity(self):
        svc = _service(FakePaperless())
        assert svc.scopes_for_sender("@marge:home.local") == ["family/", "marge/"]

    def test_sender_equal_to_bucket_not_duplicated(self):
        svc = _service(FakePaperless(), shared_bucket="family")
        assert svc.scopes_for_sender("@family:home") == ["family/"]


class TestScopesWithTopicBucket:
    """A `?` query asked in a topic room scopes the memory search to
    that topic's bucket only. The room is the implicit `--topic` flag;
    no shared bucket, no personal bucket. The user can still reach
    wider search by asking outside the topic room (or by passing
    `--global` to the CLI)."""

    def test_topic_bucket_overrides_sender_defaults(self):
        svc = _service(FakePaperless())
        scopes = svc.scopes_for_sender(
            "@homer:home", topic_bucket="camping",
        )
        assert scopes == ["camping/"]

    def test_topic_bucket_overrides_for_unknown_sender(self):
        """An unknown sender in a topic room still gets the topic's
        scope. The room's room state already vouches for who belongs
        here; there's no leakage."""
        svc = _service(FakePaperless())
        scopes = svc.scopes_for_sender(None, topic_bucket="camping")
        assert scopes == ["camping/"]

    def test_no_topic_bucket_falls_back_to_default(self):
        """The default scoping rules apply when no topic_bucket is
        passed -- normal rooms behave exactly as before."""
        svc = _service(FakePaperless())
        assert svc.scopes_for_sender(
            "@marge:home", topic_bucket=None,
        ) == ["family/", "marge/"]

    def test_personal_topic_bucket_passes_through_intact(self):
        """A personal topic (`homer/camping`) is a nested bucket;
        the scope writer must not strip the slash inside."""
        svc = _service(FakePaperless())
        scopes = svc.scopes_for_sender(
            "@homer:home", topic_bucket="homer/camping",
        )
        assert scopes == ["homer/camping/"]


class TestRun:

    @pytest.mark.asyncio
    async def test_no_results(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MEMORY_VAULT_DIR", raising=False)
        svc = _service(FakePaperless(results=[]))
        reply = await svc.run(query="Duff Insurance", sender="@homer:test", notifier=_FakeNotifier())
        assert reply.startswith("search_no_results")

    @pytest.mark.asyncio
    async def test_literal_paperless_hits(self, monkeypatch):
        monkeypatch.delenv("MEMORY_VAULT_DIR", raising=False)
        hits = [
            {"id": 10, "title": "Duff Insurance Invoice", "created": "2026-03-15"},
            {"id": 11, "title": "Duff Insurance Renewal", "created": "2025-03-01"},
        ]
        svc = _service(FakePaperless(results=hits))
        reply = await svc.run(query="Duff Insurance", sender="@homer:test", notifier=_FakeNotifier())
        # Literal mode: a Paperless section header + one line per hit.
        assert "search_paperless_results" in reply
        assert "Duff Insurance Invoice" in reply
        assert "Duff Insurance Renewal" in reply
        # No question-mode rewrite header on a literal query.
        assert "search_rewritten" not in reply


# ── Deep dive ─────────────────────────────────────────────────────────────

_SCHOOL_LETTER = {
    "id": 27,
    "title": "Springfield Elementary: welcome letter",
    "created": "2026-09-01",
    "notes": [{"note": "Welcome letter for the new school year. Parents' evening on 18 September."}],
    "content": "Dear parents, ... For questions call the office, Telefonnummer 555-0134.",
}


class ScriptedClassifier:
    """Stands in for the model: returns the scripted answers in order
    and records the evidence each synthesis call was given."""

    def __init__(self, *answers):
        self._answers = list(answers)
        self.synthesis_calls: list[list[dict]] = []

    async def rewrite_query(self, query, ontology_section, language):
        return ["Schule", "Telefonnummer"]

    async def synthesize_answer(self, question, evidence, lang="en"):
        self.synthesis_calls.append(evidence)
        return self._answers.pop(0)


class _RecordingNotifier:
    def __init__(self):
        self.statuses: list[str] = []

    async def status(self, key, **kwargs):
        self.statuses.append(key)


def _question_service(classifier, *, language="de"):
    return SearchService(
        classifier=classifier,
        paperless=FakePaperless(results=[_SCHOOL_LETTER]),
        t=lambda key, **kw: f"{key}:{kw['answer']}" if "answer" in kw else key,
        language=language,
        code_public_url="http://code",
        mirror_org="family",
        link_base_url="http://home.test/go",
        shared_bucket="family",
        vault=_FakeVault(),
    )


async def _ask(svc, notifier, query="Wie ist die Telefonnummer von Barts Schule?"):
    return await svc.run(query=query, sender="@marge:test", notifier=notifier)


class TestDeepDive:
    """When the summaries are not enough, the archivist reads the cited
    documents in full and answers from their text, in any household
    language. One deep dive at most, and the family never sees the
    marker the model uses to ask for it."""

    @pytest.fixture(autouse=True)
    def _no_memory(self, monkeypatch):
        monkeypatch.delenv("MEMORY_VAULT_DIR", raising=False)

    @pytest.mark.asyncio
    async def test_german_deferral_reads_the_document_and_answers_from_it(self):
        model = ScriptedClassifier(
            "Ich müsste [1] detailliert lesen, um die Telefonnummer zu erfahren.\n"
            "NEED_TO_READ [1]",
            "Die Telefonnummer der Schule ist 555-0134 [1].",
        )
        notifier = _RecordingNotifier()
        reply = await _ask(_question_service(model), notifier)

        assert notifier.statuses == ["search_looking_deeper"]
        assert "555-0134" in model.synthesis_calls[1][0]["summary"]
        assert "search_answer:Die Telefonnummer der Schule ist 555-0134 [1]." in reply
        assert "NEED_TO_READ" not in reply

    @pytest.mark.asyncio
    async def test_english_deferral_phrase_still_reads_the_document(self):
        model = ScriptedClassifier(
            "I'd need to read [1] in detail to answer that.",
            "The school's phone number is 555-0134 [1].",
        )
        notifier = _RecordingNotifier()
        reply = await _ask(
            _question_service(model, language="en"), notifier,
            query="What is the phone number of Bart's school?",
        )

        assert notifier.statuses == ["search_looking_deeper"]
        assert "search_answer:The school's phone number is 555-0134 [1]." in reply

    @pytest.mark.asyncio
    async def test_second_deferral_shows_the_first_answer_without_the_marker(self):
        first = "Ich müsste [1] detailliert lesen, um das zu beantworten."
        model = ScriptedClassifier(
            f"{first}\nNEED_TO_READ [1]",
            "Ich müsste [1] noch genauer lesen.\nNEED_TO_READ [1]",
        )
        reply = await _ask(_question_service(model), _RecordingNotifier())

        assert len(model.synthesis_calls) == 2
        assert f"search_answer:{first}" in reply
        assert "Springfield Elementary: welcome letter" in reply
        assert "NEED_TO_READ" not in reply

    @pytest.mark.asyncio
    async def test_answer_from_the_summaries_takes_one_pass(self):
        model = ScriptedClassifier("Der erste Elternabend ist am 18. September [1].")
        notifier = _RecordingNotifier()
        reply = await _ask(
            _question_service(model), notifier,
            query="Wann ist der erste Elternabend?",
        )

        assert len(model.synthesis_calls) == 1
        assert notifier.statuses == []
        assert "search_answer:Der erste Elternabend ist am 18. September [1]." in reply
