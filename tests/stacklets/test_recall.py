"""Recall — the one place that decides if a chat message needs LLM help.

These tests pin the rule the family will learn ("end with `?` for a
smart search") and the regex assembly that follows it. The Classifier
interaction is exercised through a tiny stand-in that implements the
same `rewrite_query(question, section, lang)` shape -- no http server,
no mocking lib internals; just a hand-written replacement that lets us
control what "the LLM returns" deterministically.

The LLM transport itself (Classifier._request hitting an OpenAI-compat
endpoint) belongs to the Classifier's own test surface, not this one.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "core" / "bot-runner"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs" / "bot"))

from recall import resolve_search_query  # noqa: E402


# ── Stand-in classifier ─────────────────────────────────────────────────

class _StubClassifier:
    """Smallest possible classifier surface for recall tests.

    Implements only `rewrite_query` because that's all `recall` calls.
    The `keywords` attribute is what we want returned; `raises`, when
    set, is raised instead. `calls` records arguments so we can assert
    on prompt routing without inspecting a real LLM.
    """

    def __init__(self, keywords: list[str] | None = None, raises: Exception | None = None):
        self.keywords = keywords or []
        self.raises = raises
        self.calls: list[tuple[str, str, str]] = []

    async def rewrite_query(self, question, ontology_section, lang):
        self.calls.append((question, ontology_section, lang))
        if self.raises is not None:
            raise self.raises
        return list(self.keywords)


# ── Trigger ─────────────────────────────────────────────────────────────

class TestQuestionTrigger:
    """Question mode runs only when message ends with `?` and classifier exists."""

    @pytest.mark.asyncio
    async def test_no_question_mark_skips_rewrite(self):
        c = _StubClassifier(keywords=["should", "not", "run"])
        memory, paperless, kw = await resolve_search_query(
            "Allianz", classifier=c, ontology_section="(...)", language="de",
        )
        # Literal path: both queries equal the input (the Paperless
        # client wildcards the bare token downstream), no keywords,
        # classifier never called. This is the fast path the family
        # hits 99% of the time.
        assert memory == "Allianz"
        assert paperless == "Allianz"
        assert kw == []
        assert c.calls == []

    @pytest.mark.asyncio
    async def test_question_mark_triggers_rewrite(self):
        c = _StubClassifier(keywords=["Impfung", "MMR", "Auffrischung"])
        memory, paperless, kw = await resolve_search_query(
            "Wann hatte Bart MMR?",
            classifier=c, ontology_section="(...)", language="de",
        )
        assert kw == ["Impfung", "MMR", "Auffrischung"]
        # Memory walker reads Python regex; alternation uses `|`.
        assert memory == "Impfung|MMR|Auffrischung"
        # Paperless reads Whoosh; alternation uses ` OR `. The bare
        # tokens get wildcarded by PaperlessAPI._to_whoosh_query.
        assert paperless == "Impfung OR MMR OR Auffrischung"
        assert len(c.calls) == 1

    @pytest.mark.asyncio
    async def test_trailing_whitespace_tolerated(self):
        # The user typed "?   " by accident -- still a question. We
        # rstrip before testing the suffix so this case behaves the
        # same as a clean "?".
        c = _StubClassifier(keywords=["k"])
        _, _, kw = await resolve_search_query(
            "What about Lisa?   \n",
            classifier=c, ontology_section="", language="en",
        )
        assert kw == ["k"]

    @pytest.mark.asyncio
    async def test_classifier_missing_skips_rewrite(self):
        # Archivist boots without a classifier when AI is unconfigured
        # (no openai_url). recall must not crash; it must fall back
        # to literal so a question still surfaces *something*.
        memory, paperless, kw = await resolve_search_query(
            "Wann hatte Bart MMR?",
            classifier=None, ontology_section="(...)", language="de",
        )
        assert memory == "Wann hatte Bart MMR?"
        assert paperless == "Wann hatte Bart MMR?"
        assert kw == []


# ── Failure handling ────────────────────────────────────────────────────

class TestFailureFallback:
    """Any LLM-side failure or empty result falls back to the literal query."""

    @pytest.mark.asyncio
    async def test_empty_keywords_falls_back(self):
        # The LLM was reachable but returned no usable keywords --
        # treat it the same as an unreachable LLM: search the literal
        # question text. Bad recall is better than no recall.
        c = _StubClassifier(keywords=[])
        memory, paperless, kw = await resolve_search_query(
            "Where is the thing?",
            classifier=c, ontology_section="", language="en",
        )
        assert memory == "Where is the thing?"
        assert paperless == "Where is the thing?"
        assert kw == []

    @pytest.mark.asyncio
    async def test_exception_during_rewrite_falls_back(self):
        # A programming bug in the prompt builder, a transport hiccup
        # that slipped past Classifier's own catch -- recall must
        # never propagate. Family asks a question, family gets an
        # answer (even if it's "no results"), never a stack trace.
        c = _StubClassifier(raises=RuntimeError("kaboom"))
        memory, paperless, kw = await resolve_search_query(
            "Anything?",
            classifier=c, ontology_section="", language="en",
        )
        assert memory == "Anything?"
        assert paperless == "Anything?"
        assert kw == []


# ── Regex assembly ──────────────────────────────────────────────────────

class TestRegexAssembly:
    """The shape the search walkers see when rewrite succeeds."""

    @pytest.mark.asyncio
    async def test_alternation_joined_with_pipe(self):
        c = _StubClassifier(keywords=["Auto", "KFZ", "Versicherung"])
        memory, paperless, _ = await resolve_search_query(
            "Autoversicherung?",
            classifier=c, ontology_section="", language="de",
        )
        # Memory side: regex `|` alternation. Order preserved -- the
        # LLM's first guess is its best guess, and a smart walker can
        # later weight by position if we want to.
        assert memory == "Auto|KFZ|Versicherung"
        # Paperless side: Whoosh ` OR ` alternation, same order.
        assert paperless == "Auto OR KFZ OR Versicherung"

    @pytest.mark.asyncio
    async def test_regex_metachars_escaped(self):
        # A chatty LLM might return "C++" or "Lisa's" or even "(foo)".
        # If we passed those through raw to the memory side, the
        # alternation regex would blow up at compile time and the
        # search would silently fail. re.escape neutralizes them
        # before the join.
        c = _StubClassifier(keywords=["C++", "Lisa's", "(foo)"])
        memory, _, _ = await resolve_search_query(
            "Anything?",
            classifier=c, ontology_section="", language="en",
        )
        import re as _re
        # Compile + matches all three escaped tokens in the same text.
        compiled = _re.compile(memory)
        assert compiled.search("got C++ done") is not None
        assert compiled.search("Lisa's room") is not None
        assert compiled.search("inside (foo) block") is not None
