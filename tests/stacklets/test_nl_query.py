"""Natural-language query helpers — evidence assembly and row rendering.

The archivist's question-mode path delegates the data-shaping pieces
to `nl_query`: merging memory + Paperless hits into a single evidence
list, deduping Paperless source docs against their memory mirrors,
and rendering one row per citation in the format the synthesis prompt
expects. These tests pin those pure functions without standing up a
Matrix bot or talking to an LLM.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs" / "bot"))

from nl_query import (  # noqa: E402
    build_evidence,
    expand_to_full_content,
    extract_citations,
    format_evidence_item,
    is_deferral,
    select_evidence_for_display,
)


# ── build_evidence ────────────────────────────────────────────────────────

class TestBuildEvidence:
    """Combine memory + Paperless hits into one synthesis-ready list."""

    def test_memory_hits_come_first(self):
        # Memory's structured summaries beat raw Paperless notes for
        # synthesis quality, so memory hits lead the evidence list.
        ev = build_evidence(
            memory_results=[{"title": "M", "rel": "f/m.md"}],
            paperless_results=[{"id": 99, "title": "P", "created": "2026-01-01T00:00:00Z"}],
        )
        assert ev[0]["kind"] == "Memory"
        assert ev[1]["kind"] == "Paperless"

    def test_dedups_paperless_when_memory_mirror_present(self):
        # The memory mirror entry carries `paperless_id: 21`; the
        # Paperless hit for doc 21 must drop out so the synthesis
        # prompt doesn't see the same document twice.
        ev = build_evidence(
            memory_results=[{
                "title": "Anthropic Invoice", "rel": "family/x.md",
                "paperless_id": "21",
            }],
            paperless_results=[
                {"id": 21, "title": "Anthropic Invoice"},
                {"id": 22, "title": "Other doc"},
            ],
        )
        kinds_and_ids = [(e["kind"], e.get("doc_id")) for e in ev]
        # Memory entry, then only doc 22 from Paperless.
        assert kinds_and_ids == [("Memory", None), ("Paperless", 22)]

    def test_limit_caps_total_evidence(self):
        ev = build_evidence(
            memory_results=[{"title": f"m{i}", "rel": f"f/{i}.md"} for i in range(6)],
            paperless_results=[{"id": i, "title": f"p{i}"} for i in range(10)],
            limit=5,
        )
        assert len(ev) == 5

    def test_memory_url_built_when_code_url_set(self):
        ev = build_evidence(
            memory_results=[{
                "title": "T", "rel": "family/notes/x.md", "date": "2026-01-01",
            }],
            paperless_results=[],
            code_public_url="https://code.example",
            mirror_org="family",
        )
        assert ev[0]["url"].startswith("https://code.example/")
        assert "family/memory/src/branch/main/family/notes/x.md" in ev[0]["url"]

    def test_paperless_url_built_when_public_url_set(self):
        ev = build_evidence(
            memory_results=[],
            paperless_results=[{"id": 42, "title": "T", "created": "2026-01-01T00:00:00Z"}],
            paperless_public_url="https://paperless.example",
        )
        assert ev[0]["url"] == "https://paperless.example/documents/42/details"

    def test_summary_falls_back_to_excerpt(self):
        # A vault file older than the classifier has no `> [!summary]`
        # callout, so `search_memory` returns "" for `summary`. The
        # excerpt is the next-best signal and should fill the field.
        ev = build_evidence(
            memory_results=[{
                "title": "T", "rel": "f/x.md",
                "summary": "", "excerpt": "first body line that matched the query",
            }],
            paperless_results=[],
        )
        assert "first body line" in ev[0]["summary"]

    def test_paperless_summary_pulled_from_bot_note(self):
        doc = {
            "id": 7, "title": "T", "created": "2026-01-01T00:00:00Z",
            "notes": [{"id": 1, "note": "Invoice summary text\n\n<!-- archivist-bot -->"}],
        }
        ev = build_evidence(
            memory_results=[], paperless_results=[doc],
        )
        assert "Invoice summary text" in ev[0]["summary"]


# ── format_evidence_item ──────────────────────────────────────────────────

class TestFormatEvidenceItem:
    """Render one row of the evidence list with the citation prefix."""

    def test_uses_bracket_prefix_matching_citation_style(self):
        # The synthesis answer cites hits as "[1]", "[2]" -- the row
        # in the evidence list must use the same brackets so the
        # reader can map answer to row at a glance.
        out = format_evidence_item({"title": "T", "url": "https://x"}, 1)
        assert out.startswith("[1] ")

    def test_renders_clickable_title_when_url_present(self):
        out = format_evidence_item({
            "title": "Invoice", "url": "https://paperless.example/documents/42/details",
        }, 1)
        assert "[Invoice](https://paperless.example/documents/42/details)" in out

    def test_bold_fallback_when_no_url(self):
        out = format_evidence_item({"title": "Invoice"}, 1)
        assert "**Invoice**" in out

    def test_meta_includes_kind_date_persons(self):
        out = format_evidence_item({
            "title": "T", "kind": "Memory", "date": "2026-03-22",
            "persons": ["Homer", "Marge"],
        }, 1)
        assert "Memory" in out
        assert "2026-03-22" in out
        assert "Homer, Marge" in out

    def test_meta_includes_doc_id_for_paperless(self):
        out = format_evidence_item({
            "title": "T", "kind": "Paperless", "doc_id": 42,
        }, 1)
        assert "#42" in out


# ── extract_citations ─────────────────────────────────────────────────────

class TestExtractCitations:
    """Parse the `[N]` citation markers the synthesis prompt produces."""

    def test_single_citation(self):
        assert extract_citations("The last one was 2026-03-22 [2].") == [2]

    def test_multiple_brackets(self):
        assert extract_citations("Mentioned in [1] and [3].") == [1, 3]

    def test_combined_bracket(self):
        # Some models combine citations into one set of brackets.
        assert extract_citations("Discussed in [2, 4].") == [2, 4]

    def test_adjacent_brackets(self):
        # Other models stack them: "[2][3]".
        assert extract_citations("See [2][3].") == [2, 3]

    def test_deduplicates_in_first_seen_order(self):
        # The model occasionally cites the same hit twice. The
        # display should not show two copies of the same row.
        assert extract_citations("Both [2] and [2] talk about it.") == [2]

    def test_ignores_non_numeric_brackets(self):
        # Markdown link tails like `](url)` and arbitrary `[foo]`
        # text must not be picked up as citations.
        assert extract_citations("[foo] and [link](http://x)") == []

    def test_returns_empty_on_no_citations(self):
        # A deferral or "I don't know" answer may not cite at all.
        assert extract_citations("I'd need more context to answer.") == []
        assert extract_citations("") == []


# ── select_evidence_for_display ───────────────────────────────────────────

class TestSelectEvidenceForDisplay:
    """Filter evidence rows based on the answer's citations."""

    @staticmethod
    def _evs(n: int) -> list[dict]:
        return [{"title": f"e{i}"} for i in range(1, n + 1)]

    def test_filters_to_cited_subset_preserving_original_numbers(self):
        # The answer says "[2]" -- the displayed list must show row
        # [2] (not renumber it to [1]), otherwise the bracket in the
        # answer no longer points at the right row.
        evs = self._evs(5)
        selected = select_evidence_for_display(evs, citations=[2, 4])
        assert [n for n, _ in selected] == [2, 4]
        assert [ev["title"] for _, ev in selected] == ["e2", "e4"]

    def test_no_citations_falls_back_to_top_n(self):
        # When the answer didn't cite, show the top few hits so the
        # family has something to scan.
        evs = self._evs(5)
        selected = select_evidence_for_display(evs, citations=[])
        assert [n for n, _ in selected] == [1, 2, 3]

    def test_fallback_top_respects_smaller_evidence(self):
        # Fewer hits than fallback_top: show them all.
        evs = self._evs(2)
        selected = select_evidence_for_display(evs, citations=[])
        assert len(selected) == 2

    def test_out_of_range_citation_silently_dropped(self):
        # A hallucinated `[9]` against a 5-hit list must not crash
        # the renderer or surface an empty row.
        evs = self._evs(5)
        selected = select_evidence_for_display(evs, citations=[2, 9])
        assert [n for n, _ in selected] == [2]

    def test_custom_fallback_top(self):
        evs = self._evs(5)
        selected = select_evidence_for_display(
            evs, citations=[], fallback_top=1,
        )
        assert len(selected) == 1
        assert selected[0][0] == 1


# ── is_deferral ───────────────────────────────────────────────────────────

class TestIsDeferral:
    """Detect the "need to read [N]" pattern the synthesizer emits
    when summaries aren't enough."""

    def test_canonical_phrase_triggers(self):
        # The exact phrasing the synthesis prompt asks the model to
        # use. This must trigger every time.
        assert is_deferral("I'd need to read [2] in detail to answer that.")

    def test_paraphrase_triggers(self):
        # Some models stray from the exact prompt wording. The
        # detector matches the core "need to read" phrase so a
        # paraphrase still resolves to a deep-dive turn.
        assert is_deferral("Would need to read [2] more carefully.")

    def test_case_insensitive(self):
        assert is_deferral("NEED TO READ [3] more.")

    def test_plain_answer_does_not_trigger(self):
        # A real answer must NOT be treated as a deferral, even when
        # it happens to mention reading.
        assert not is_deferral("The last invoice was 2026-03-22 [2].")
        assert not is_deferral("Lisa is reading more this year.")

    def test_empty_does_not_trigger(self):
        assert not is_deferral("")
        assert not is_deferral(None)  # type: ignore[arg-type]


# ── expand_to_full_content ────────────────────────────────────────────────

class TestExpandToFullContent:
    """Re-feed cited docs with full body text for the deep-dive turn."""

    def test_memory_summary_replaced_with_file_body(self, tmp_path):
        # Write a memory file with frontmatter + body. The expander
        # should read the file and return the body only (no YAML).
        p = tmp_path / "x.md"
        p.write_text(
            "---\ntitle: t\ndate: 2026-01-01\n---\n\n# t\n\nBody prose here.\n",
            encoding="utf-8",
        )
        memory_results = [{"rel": "x.md", "path": p}]
        selected = [(2, {"kind": "Memory", "rel": "x.md", "summary": "short"})]

        expanded = expand_to_full_content(selected, memory_results, [])
        assert len(expanded) == 1
        # YAML header is gone; body prose is in.
        assert "title: t" not in expanded[0]["summary"]
        assert "Body prose here." in expanded[0]["summary"]

    def test_paperless_summary_replaced_with_content_field(self):
        selected = [(1, {"kind": "Paperless", "doc_id": 42, "summary": "note text"})]
        paperless_results = [{
            "id": 42, "content": "Full OCR text of the document.",
        }]
        expanded = expand_to_full_content(selected, [], paperless_results)
        assert expanded[0]["summary"] == "Full OCR text of the document."

    def test_truncates_at_max_chars(self, tmp_path):
        # A 5000-char body must be cut to max_chars + ellipsis so a
        # 30-page contract doesn't blow the LLM context.
        p = tmp_path / "long.md"
        p.write_text("---\n---\n\n" + ("A" * 5000), encoding="utf-8")
        selected = [(1, {"kind": "Memory", "rel": "long.md"})]
        expanded = expand_to_full_content(
            selected, [{"rel": "long.md", "path": p}], [], max_chars=100,
        )
        assert len(expanded[0]["summary"]) <= 110  # 100 + "\n..."
        assert expanded[0]["summary"].endswith("\n...")

    def test_falls_back_to_existing_summary_when_load_fails(self):
        # If the source can't be loaded (file moved between search
        # and expand, doc dict missing), keep the existing summary
        # rather than erasing it. The second-turn LLM still has
        # something to work with.
        selected = [(1, {"kind": "Memory", "rel": "missing.md", "summary": "fallback text"})]
        expanded = expand_to_full_content(
            selected, [],  # no matching memory result
            [],
        )
        assert expanded[0]["summary"] == "fallback text"

    def test_does_not_mutate_input_dicts(self):
        # The original evidence list is rendered in the first turn;
        # mutating it would corrupt that display. Expander returns
        # new dicts.
        original = {"kind": "Paperless", "doc_id": 1, "summary": "orig"}
        selected = [(1, original)]
        paperless_results = [{"id": 1, "content": "new content"}]
        expand_to_full_content(selected, [], paperless_results)
        assert original["summary"] == "orig"
