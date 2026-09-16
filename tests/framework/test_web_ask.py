"""What `stack web ask` promises before any model is involved.

The command is one search plus one model call. That leaves exactly one
piece of pure logic worth pinning: what evidence the model is shown,
how it is numbered, and what it is told to do when the evidence does
not cover the question.

The last of those is the whole trustworthiness of the feature. A model
that quietly answers from its own memory produces something that reads
exactly like a good answer and is not one, and no amount of testing
downstream catches it. So "I don't know" is named in the prompt as a
correct response, and that instruction is asserted here rather than
left to survive a future prompt tweak by luck.

Driven against `tests/fixtures/web/searxng-response.json`, a recorded
response from the real SearXNG container, so these run with no network
and no model.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stack.web.ask import (
    DEFAULT_SOURCES,
    Source,
    build_prompt,
    render_sources,
    sources_from,
)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "web" / "searxng-response.json"


@pytest.fixture(scope="module")
def results() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["results"]


# ── Evidence selection ────────────────────────────────────────────────

class TestSourcesAreNumberedAndTrimmed:
    def test_real_results_become_numbered_sources(self, results):
        sources = sources_from(results)

        assert sources
        assert [s.n for s in sources] == list(range(1, len(sources) + 1))
        assert all(s.url.startswith("http") for s in sources)

    def test_the_limit_is_honoured(self, results):
        """The model's context is the budget being spent here."""
        assert len(sources_from(results, limit=3)) == 3

    def test_the_default_limit_is_applied(self, results):
        assert len(sources_from(results)) <= DEFAULT_SOURCES

    def test_long_snippets_are_truncated_with_an_ellipsis(self):
        long = [{"url": "https://e.com/a", "title": "T", "content": "x" * 900}]
        [source] = sources_from(long, snippet_chars=100)

        assert len(source.snippet) == 100
        assert source.snippet.endswith("…")

    def test_snippet_whitespace_is_flattened(self):
        """Search snippets arrive with newlines and runs of spaces. Left
        alone they break the numbered layout the prompt relies on."""
        messy = [{"url": "https://e.com/a", "title": "T",
                  "content": "one\n\n  two\t\tthree"}]
        [source] = sources_from(messy)

        assert source.snippet == "one two three"

    def test_a_result_with_no_url_is_dropped(self):
        """Every claim has to be traceable. A source the family cannot
        open is worse than one fewer source."""
        mixed = [
            {"url": "", "title": "no link", "content": "text"},
            {"url": "https://e.com/a", "title": "real", "content": "text"},
        ]
        [source] = sources_from(mixed)

        assert source.title == "real"
        assert source.n == 1, "numbering must close the gap, not skip"

    def test_a_result_with_no_snippet_is_kept(self):
        """The title alone is sometimes the answer -- "Immich 2.1
        released" needs no snippet to be useful."""
        titled = [{"url": "https://e.com/a", "title": "Immich 2.1 released",
                   "content": ""}]
        [source] = sources_from(titled)

        assert source.title == "Immich 2.1 released"
        assert source.snippet == ""


# ── The prompt ────────────────────────────────────────────────────────

class TestThePromptShowsItsEvidence:
    def test_every_source_appears_with_its_number_and_url(self, results):
        sources = sources_from(results, limit=4)
        prompt = build_prompt("Which is better for a family?", sources)

        for source in sources:
            assert f"[{source.n}]" in prompt
            assert source.url in prompt
            if source.snippet:
                assert source.snippet in prompt

    def test_the_question_is_present(self, results):
        prompt = build_prompt("Which handles RAW files?", sources_from(results))
        assert "Which handles RAW files?" in prompt

    def test_the_question_comes_last(self, results):
        """A local model reading a long block of snippets attends better
        to what came last, and the instructions are what it needs first."""
        question = "Which handles RAW files?"
        prompt = build_prompt(question, sources_from(results))

        assert prompt.rstrip().endswith(question)

    def test_the_question_is_stripped(self, results):
        prompt = build_prompt("  spaced out  ", sources_from(results))
        assert prompt.rstrip().endswith("Question: spaced out")


class TestThePromptLicensesRefusal:
    """The load-bearing instruction. If a prompt rewrite drops it, the
    command starts inventing answers that look correct."""

    def test_not_knowing_is_named_as_a_valid_answer(self, results):
        prompt = build_prompt("anything", sources_from(results))
        lowered = prompt.lower()

        assert "do not contain the answer" in lowered
        assert "correct and useful response" in lowered

    def test_outside_knowledge_is_forbidden(self, results):
        prompt = build_prompt("anything", sources_from(results))
        assert "own knowledge" in prompt.lower()

    def test_citing_is_by_number_not_url(self, results):
        """A model asked to cite URLs writes plausible ones from memory.
        Numbers map back to a list we printed ourselves."""
        prompt = build_prompt("anything", sources_from(results))
        assert "by number" in prompt.lower()


# ── Output ────────────────────────────────────────────────────────────

class TestSourcesArePrintedEitherWay:
    def test_sources_render_with_number_title_and_url(self):
        rendered = render_sources([
            Source(1, "Immich vs PhotoPrism", "https://e.com/a", "snippet"),
        ])

        assert "[1]" in rendered
        assert "Immich vs PhotoPrism" in rendered
        assert "https://e.com/a" in rendered

    def test_a_source_with_no_title_falls_back_to_its_url(self):
        rendered = render_sources([Source(1, "", "https://e.com/a", "")])
        assert "https://e.com/a" in rendered

    def test_real_results_all_render(self, results):
        sources = sources_from(results)
        rendered = render_sources(sources)

        for source in sources:
            assert source.url in rendered


class TestThePromptGuardsAgainstMislabelledFigures:
    """A number with the wrong noun on it is worse than no number.

    The case that produced these rules: asked how many tonnes of meat
    Germany consumes, the sources give 6.37 Mt *Verbrauch* (which counts
    losses, industrial use and pet food) and 4.44 Mt *Verzehr* (what
    people actually eat). Those are different measures for different
    years, and an answer that picks one and calls it the other is
    confidently wrong while looking well sourced.
    """

    def test_figures_must_carry_the_source_term_and_period(self, results):
        prompt = build_prompt("how many tonnes?", sources_from(results))
        lowered = prompt.lower()

        assert "source's own term" in lowered
        assert "never relabel" in lowered
        assert "period" in lowered

    def test_two_measures_must_both_be_given(self, results):
        prompt = build_prompt("how many tonnes?", sources_from(results))
        assert "two different things" in prompt.lower()

    def test_the_answer_follows_the_question_language(self, results):
        """A German question got a German answer before this was pinned,
        but by luck: the instructions are English throughout."""
        prompt = build_prompt("Wie viel?", sources_from(results))
        assert "language the question was asked in" in prompt.lower()


class TestTheModelIsToldTheDate:
    """Search results are full of undated figures. Telling the model the
    date beats the earlier approach of forbidding it to discuss recency,
    which left it unable to say which of two years was the later one."""

    def test_the_date_is_in_the_prompt(self, results):
        import datetime as dt

        prompt = build_prompt("anything", sources_from(results), today=dt.date(2026, 9, 16))
        assert "2026-09-16" in prompt

    def test_the_date_is_injected_not_read_inside(self, results):
        """Passed in so the prompt is a pure function of its inputs and
        these tests do not change meaning tomorrow."""
        import datetime as dt

        a = build_prompt("q", sources_from(results), today=dt.date(2020, 1, 1))
        b = build_prompt("q", sources_from(results), today=dt.date(2026, 9, 16))
        assert a != b
        assert "2020-01-01" in a
