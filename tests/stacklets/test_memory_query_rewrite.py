"""Turning a family question into something the vault can answer.

`search_memory` reads a Python regex. A person asks "What do we still
need to buy for the camping trip?", which as a regex asks for those
exact words, adjacent, and matches nothing. The gap between the two is
this module's subject: a model reads the question against the family's
ontology, answers with keywords that would literally appear in a
matching file, and `keywords_to_regex` renders them for the walker.

It lives in `memory.lib` because memory owns the vault, and therefore
owns what a query means against it. It used to live in the archivist,
where it worked, and every other caller of the same vault got nothing.
The last class here is about that: both callers now share one rewrite,
and a test says so out loud.

The model is a hand-written stand-in that records what it was asked
and returns what the test wants back. Memory never opens an LLM
client; callers hand it theirs, so a stub is the honest shape of the
collaborator, not a mock of one.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs" / "bot"))

from memory.lib import (  # noqa: E402
    build_rewrite_prompt,
    keywords_to_regex,
    parse_rewrite_response,
    rewrite_query,
)
from stack.ai.client import LLMTimeoutError, LLMUnavailableError  # noqa: E402


# ── Stand-in model ──────────────────────────────────────────────────────

class _StubLLM:
    """The `stack.ai.client.LLM` surface `rewrite_query` actually uses.

    One method, because one method is all memory calls. `response` is
    what the model "says"; `raises`, when set, is raised instead so a
    test can play the unreachable endpoint. `calls` records the role
    and prompt, which is how we assert the rewrite asks for JSON
    rather than trusting that it does.
    """

    def __init__(self, *, response: str = '{"keywords": []}',
                 raises: Exception | None = None):
        self._response = response
        self._raises = raises
        self.calls: list[dict] = []

    async def complete(self, role, prompt, *, images=None,
                       json_mode=False, model_override=None, temperature=None):
        self.calls.append({"role": role, "prompt": prompt, "json_mode": json_mode})
        if self._raises is not None:
            raise self._raises
        return self._response


def _llm_saying(*keywords: str) -> _StubLLM:
    return _StubLLM(response=json.dumps({"keywords": list(keywords)}))


# ── The prompt ──────────────────────────────────────────────────────────

class TestBuildRewritePrompt:
    """The recall prompt: question + ontology section in, prompt string out."""

    @staticmethod
    def _build(question="Wann hatte Bart MMR?", section="(...topics...)", lang="de"):
        return build_rewrite_prompt(question, section, lang)

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


# ── The parser ──────────────────────────────────────────────────────────

class TestParseRewriteResponse:
    """The keyword extractor parser — what the recall layer trusts."""

    @staticmethod
    def _parse(raw):
        return parse_rewrite_response(raw)

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


# ── The hop itself ──────────────────────────────────────────────────────

class TestRewriteQuery:
    """Question in, keywords out, with the caller's model doing the work."""

    @pytest.mark.asyncio
    async def test_returns_the_keywords_the_model_chose(self):
        # The whole point of the hop: "when was Bart vaccinated" leaves
        # as words that appear in the German vaccination record.
        llm = _llm_saying("Impfung", "MMR", "Auffrischung")
        keywords = await rewrite_query(
            "When did Bart get vaccinated?", llm=llm,
            ontology_section="- Medical (Gesundheit)", language="de",
        )
        assert keywords == ["Impfung", "MMR", "Auffrischung"]

    @pytest.mark.asyncio
    async def test_asks_the_recall_role_for_json(self):
        # Role names route the request to the model the family
        # configured for recall, and JSON mode is what the parser is
        # written against. Both are part of the request, not details
        # of it: a rewrite asked in prose mode parses to nothing.
        llm = _llm_saying("Auto")
        await rewrite_query("Autoversicherung?", llm=llm)
        assert llm.calls[0]["role"] == "recall"
        assert llm.calls[0]["json_mode"] is True

    @pytest.mark.asyncio
    async def test_the_family_ontology_reaches_the_model(self):
        # A caller passes the vault's own ontology so the keywords come
        # back in the vocabulary this family files under. If it stopped
        # reaching the prompt, recall would still "work" and quietly
        # get worse, which is the failure we most want pinned.
        llm = _llm_saying("Versicherung")
        await rewrite_query(
            "Was kostet die Versicherung?", llm=llm,
            ontology_section="- Insurance (Versicherung)", language="de",
        )
        assert "- Insurance (Versicherung)" in llm.calls[0]["prompt"]

    @pytest.mark.asyncio
    async def test_unreachable_model_returns_no_keywords(self):
        # The family's AI stacklet is down, or was never set up. The
        # caller falls back to the literal query on an empty list, so
        # a question still searches for something.
        llm = _StubLLM(raises=LLMUnavailableError("no endpoint"))
        assert await rewrite_query("Anything?", llm=llm) == []

    @pytest.mark.asyncio
    async def test_slow_model_returns_no_keywords(self):
        # Same contract for a timeout: recall degrades, it never
        # propagates an exception into a search.
        llm = _StubLLM(raises=LLMTimeoutError("took too long"))
        assert await rewrite_query("Anything?", llm=llm) == []

    @pytest.mark.asyncio
    async def test_off_shape_answer_returns_no_keywords(self):
        # The model was reachable and answered with prose. Nothing to
        # search for, so the caller gets the same empty list it gets
        # from a dead endpoint and treats both the same way.
        llm = _StubLLM(response="Sure! Here are some keywords: Auto, KFZ.")
        assert await rewrite_query("Autoversicherung?", llm=llm) == []


# ── Rendering for the walker ────────────────────────────────────────────

class TestKeywordsToRegex:
    """The string `search_memory` gets handed."""

    def test_alternation_joined_with_pipe(self):
        # Python regex alternation. Order is the model's ranking and
        # survives the join.
        assert keywords_to_regex(["Auto", "KFZ", "Versicherung"]) == \
            "Auto|KFZ|Versicherung"

    def test_metacharacters_are_escaped(self):
        # "C++", "Lisa's" and "(foo)" are all plausible keywords and
        # all break a raw join: an unbalanced paren fails to compile,
        # and `search_memory` answers a bad regex with an empty result
        # set. A silent no-hits is the worst failure mode we have.
        pattern = keywords_to_regex(["C++", "Lisa's", "(foo)"])
        compiled = re.compile(pattern)
        assert compiled.search("got C++ done")
        assert compiled.search("Lisa's room")
        assert compiled.search("inside (foo) block")

    def test_a_single_keyword_is_itself(self):
        assert keywords_to_regex(["Grillfest"]) == "Grillfest"


# ── One rewrite, every caller ───────────────────────────────────────────

class TestBothCallersShareTheRewrite:
    """The gap that hid the original bug, pinned.

    `resolve_search_query` had eight passing tests while the agent got
    nothing from the same vault, because nothing checked that the other
    caller shared the contract. These do: the archivist's classifier
    and the chat-side resolver both have to end up in this module, so a
    future caller inherits the fix instead of rediscovering the bug.
    """

    @pytest.mark.asyncio
    async def test_the_archivist_classifier_asks_memory(self):
        # `Classifier.rewrite_query` is the archivist's entry point. It
        # must be memory's prompt and memory's parser with the bot's
        # own LLM handed over, not a second copy that can drift.
        from pipeline import Classifier

        llm = _llm_saying("Impfung", "MMR")
        keywords = await Classifier(llm).rewrite_query(  # type: ignore[arg-type]
            "Wann hatte Bart MMR?", "- Medical (Gesundheit)", "de",
        )

        assert keywords == ["Impfung", "MMR"]
        assert llm.calls[0]["prompt"] == build_rewrite_prompt(
            "Wann hatte Bart MMR?", "- Medical (Gesundheit)", "de",
        )

    @pytest.mark.asyncio
    async def test_the_chat_resolver_renders_memorys_regex(self):
        # recall.py keeps the `?` trigger and the Paperless rendering,
        # but the string the vault walker reads comes from here. Same
        # keywords in, byte-identical pattern out.
        from recall import resolve_search_query

        class _Classifier:
            async def rewrite_query(self, question, ontology_section, lang):
                return ["C++", "Lisa's"]

        memory_regex, paperless_query, keywords = await resolve_search_query(
            "Anything?", classifier=_Classifier(),  # type: ignore[arg-type]
            ontology_section="", language="en",
        )

        assert memory_regex == keywords_to_regex(keywords)
        # Paperless stays the archivist's business: Whoosh reads ` OR `,
        # not `|`, and that rendering deliberately did not move.
        assert paperless_query == "C++ OR Lisa's"
