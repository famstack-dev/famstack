"""Prompt-builder behaviour when an ontology section is supplied.

The classifier prompt has two vocabulary modes:

  - **Legacy / fallback.** No ontology section — the prompt lists the
    flat names pulled from Paperless (`Existing topic tags: [...]`,
    `Existing document types: [...]`). This is what the bot used
    before the memory stacklet existed; it's the safety net when
    Forgejo isn't reachable on first classify.
  - **Ontology-aware.** An ontology section is supplied — the prompt
    embeds it directly. Synonyms ride inline ("Insurance (policy,
    coverage)") so the LLM can pick a canonical name even when the
    OCR text uses an alternative phrasing.

These tests pin both modes. They don't call the LLM — they exercise
`_build_classify_prompt` and assert on the rendered text.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent
                       / "stacklets" / "docs" / "bot"))

from pipeline import _build_classify_prompt  # noqa: E402


COMMON = dict(
    ocr_text="(test content)",
    person_names=["Homer"],
    category_tags=["Insurance", "Tax"],
    doc_types=["Invoice", "Letter"],
    correspondents=["AOK"],
)


class TestLegacyFallback:
    """Without an ontology section, the prompt uses the Paperless flat lists."""

    def test_uses_existing_topic_tags_line(self):
        prompt = _build_classify_prompt(**COMMON)
        assert 'Existing topic tags: ["Insurance", "Tax"]' in prompt

    def test_uses_existing_document_types_line(self):
        prompt = _build_classify_prompt(**COMMON)
        assert 'Existing document types: ["Invoice", "Letter"]' in prompt

    def test_correspondents_line_present_in_either_mode(self):
        prompt = _build_classify_prompt(**COMMON)
        assert 'Existing correspondents: ["AOK"]' in prompt


class TestOntologyAware:
    """With an ontology section, the prompt embeds it verbatim and drops
    the legacy `Existing topic tags` / `Existing document types` lines."""

    def test_embeds_ontology_section_verbatim(self):
        section = (
            "Topics (pick the best match):\n"
            "  - Insurance (policy, coverage)\n"
            "  - Tax\n"
            "\n"
            "Document types:\n"
            "  - Invoice (bill)\n"
        )
        prompt = _build_classify_prompt(**COMMON, ontology_section=section)
        assert section in prompt

    def test_drops_legacy_topic_tags_line_when_ontology_present(self):
        section = "Topics:\n  - X\n"
        prompt = _build_classify_prompt(**COMMON, ontology_section=section)
        assert "Existing topic tags:" not in prompt

    def test_drops_legacy_document_types_line_when_ontology_present(self):
        section = "Topics:\n  - X\n"
        prompt = _build_classify_prompt(**COMMON, ontology_section=section)
        assert "Existing document types:" not in prompt

    def test_keeps_family_members_and_correspondents_lines(self):
        section = "Topics:\n  - X\n"
        prompt = _build_classify_prompt(**COMMON, ontology_section=section)
        # Persons + correspondents are dynamic (Paperless) and stay
        # alongside the ontology — they're not part of the ontology.
        assert 'Family members: ["Homer"]' in prompt
        assert 'Existing correspondents: ["AOK"]' in prompt


class TestCorrespondentsBlock:
    """When a correspondents section is supplied (canonical + aliases
    from the wiki), it replaces the flat `Existing correspondents:`
    json line and teaches the LLM canonical names + their aliases."""

    def test_embeds_section_verbatim(self):
        section = (
            "Existing correspondents (canonical; aliases in parens):\n"
            "  - ADAC (ADAC Ortsverband Manzell)\n"
            "  - AOK\n"
        )
        prompt = _build_classify_prompt(**COMMON, correspondents_section=section)
        assert section in prompt

    def test_drops_legacy_existing_correspondents_line(self):
        section = "Existing correspondents (canonical; aliases in parens):\n  - ADAC\n"
        prompt = _build_classify_prompt(**COMMON, correspondents_section=section)
        # The flat `Existing correspondents: ["AOK"]` line is suppressed.
        assert 'Existing correspondents: ["AOK"]' not in prompt

    def test_falls_back_to_legacy_line_when_empty(self):
        prompt = _build_classify_prompt(**COMMON, correspondents_section="")
        assert 'Existing correspondents: ["AOK"]' in prompt


class TestNewSchemaFields:
    """The schema gained correspondent_aliases and correspondent_facts.
    The strip-suffix examples and the wiki-aware rule need to be
    visible in the prompt so the LLM is teachable."""

    def test_schema_advertises_correspondent_aliases(self):
        prompt = _build_classify_prompt(**COMMON)
        assert "correspondent_aliases" in prompt

    def test_schema_advertises_correspondent_facts(self):
        prompt = _build_classify_prompt(**COMMON)
        assert "correspondent_facts" in prompt

    def test_prompt_includes_strip_suffix_examples(self):
        prompt = _build_classify_prompt(**COMMON)
        # The strip-suffix rule must be concrete to be useful.
        assert "ADAC Ortsverband Manzell" in prompt
        assert "ADAC" in prompt
        assert "Burns Industries" in prompt


class TestRealOntologyShape:
    """End-to-end-ish: build an Ontology from the same TOML shape memory
    ships and feed its rendered section through the prompt. Catches any
    accidental escaping or formatting mismatch."""

    def test_uses_synonyms_inline(self):
        # Import lazily — keeps the other tests independent of the lib path.
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent
                               / "lib"))
        from stack.ontology import Ontology

        ont = Ontology.loads(
            "[topic.insurance]\n"
            "names = { en = 'Insurance' }\n"
            "synonyms = { en = ['policy', 'coverage'] }\n"
            "\n"
            "[doctype.invoice]\n"
            "names = { en = 'Invoice' }\n"
        )
        section = ont.classifier_prompt_section("en")

        prompt = _build_classify_prompt(**COMMON, ontology_section=section)
        assert "Insurance (policy, coverage)" in prompt
        assert "Invoice" in prompt
