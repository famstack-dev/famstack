"""Tests for the capture-specific classifier prompt.

Captures don't fight for a place in the Paperless ontology — that's a
job for the dream-cycle wiki rebuild. At capture time we want a
focused prompt that returns:

  - title       (scannable, under 80 chars)
  - summary     (length scales with input: short paste → 1-2 sentences,
                long content → 200-400 words)
  - facts       (3-6 key facts)
  - tags        (free-form, biased toward existing tags in use)
  - persons     (family members this is for/about)

Deliberately absent: `correspondent`, `document_type`, `category`,
`action_items`. No ontology section either. Action items in
particular stay out: a pasted Reddit thread should not manufacture
a todo for the household.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent
                       / "stacklets" / "docs" / "bot"))

from pipeline import _build_capture_prompt  # noqa: E402


COMMON = dict(
    text="(test capture content)",
    person_names=["Homer", "Marge"],
)


# ── Prompt structure ─────────────────────────────────────────────────────

class TestPromptStructure:
    """The capture prompt is intentionally smaller than the document
    classify prompt — fewer fields, no taxonomy coupling."""

    def test_includes_text_body(self):
        prompt = _build_capture_prompt(**COMMON)
        assert "(test capture content)" in prompt

    def test_advertises_summary_field(self):
        prompt = _build_capture_prompt(**COMMON)
        assert "summary" in prompt

    def test_advertises_tags_field(self):
        prompt = _build_capture_prompt(**COMMON)
        assert "tags" in prompt

    def test_advertises_persons_field(self):
        prompt = _build_capture_prompt(**COMMON)
        assert "persons" in prompt

    def test_does_not_advertise_action_items_field(self):
        """A bookmarked Reddit thread or saved article is not a todo.
        Dropping `action_items` from the capture schema means the LLM
        can't manufacture chores from passive reading material."""
        prompt = _build_capture_prompt(**COMMON)
        assert "action_items" not in prompt

    def test_no_correspondent_field(self):
        """Captures don't fit the Paperless correspondent model — an
        article doesn't have a sender, it has a URL. Dream-cycle
        rebuild can derive author/domain later if needed."""
        prompt = _build_capture_prompt(**COMMON)
        assert '"correspondent"' not in prompt
        assert "correspondent_aliases" not in prompt
        assert "correspondent_facts" not in prompt

    def test_no_document_type_field(self):
        prompt = _build_capture_prompt(**COMMON)
        assert '"document_type"' not in prompt

    def test_no_ontology_section(self):
        """The Paperless ontology (Insurance/Tax/Vehicle/...) doesn't
        fit captures. Tags grow organically from what users save."""
        prompt = _build_capture_prompt(**COMMON)
        assert "Topics (pick the best match)" not in prompt
        assert "Document types" not in prompt


# ── Family members ───────────────────────────────────────────────────────

class TestFamilyMembers:
    """Person attribution stays: `persons:` is load-bearing for interest
    derivation. The family-members list comes from Paperless person
    tags (same source as the documents pipeline)."""

    def test_family_members_in_prompt(self):
        prompt = _build_capture_prompt(**COMMON)
        assert "Homer" in prompt
        assert "Marge" in prompt


# ── Existing tags ────────────────────────────────────────────────────────

class TestExistingTags:
    """A capture-specific tag vocabulary grows from prior captures.
    Feeding the current set into the prompt biases the LLM toward
    consistency ("LLMs" reused, not reinvented as "llm" / "Large
    Language Models")."""

    def test_existing_tags_render_as_json_array(self):
        prompt = _build_capture_prompt(
            **COMMON, existing_tags=["LLMs", "Local Inference", "Privacy"],
        )
        # JSON array — same shape the documents classify prompt uses
        # for its tag lists. The LLM treats this as a vocabulary.
        assert '"LLMs"' in prompt
        assert '"Local Inference"' in prompt

    def test_prompt_steers_toward_reuse(self):
        prompt = _build_capture_prompt(
            **COMMON, existing_tags=["LLMs"],
        )
        # The prompt must explicitly tell the LLM to prefer existing
        # tags — without the instruction, free-form generation drifts.
        lower = prompt.lower()
        assert "prefer" in lower or "reuse" in lower

    def test_empty_tags_means_free_form(self):
        prompt = _build_capture_prompt(**COMMON, existing_tags=[])
        # Cold start — no existing vocabulary. The prompt still works;
        # the LLM gets to invent the seed tags.
        assert "tags" in prompt


# ── Tag-quality bias ─────────────────────────────────────────────────────


class TestTagQualityBias:
    """The capture classifier defaults to generic categories ('Travel')
    when nothing pushes it. These pins keep the prompt biased toward
    content-specific tags so 'find my camping notes next year' works."""

    def test_enforces_minimum_three_tags(self):
        """A single generic tag ('Travel') is the failure mode we hit
        in practice. Pin the minimum so the rule survives future edits."""
        prompt = _build_capture_prompt(**COMMON)
        # The exact phrasing varies; what must NOT slip is the floor.
        # We pin the literal "MINIMUM 3" since that's the strongest form.
        assert "MINIMUM 3" in prompt

    def test_advertises_specific_over_generic_bias(self):
        """A short rule isn't enough; concrete contrast examples push
        the model harder than abstract instructions. The camping vs
        travel pair is the canonical demo for this rule."""
        prompt = _build_capture_prompt(**COMMON)
        # Two contrast pairs is the bar -- one feels like an example,
        # multiple feels like a pattern.
        assert "'camping' beats 'travel'" in prompt
        assert "PREFER SPECIFIC" in prompt or "Prefer specific" in prompt

    def test_includes_german_tag_examples(self):
        """The family is bilingual; tags follow the content's language.
        Without German examples, the model defaults to English tags on
        German voice memos -- which then fail to match German search."""
        prompt = _build_capture_prompt(**COMMON)
        # At least one German content-specific tag in the examples
        # (rotates between voice-memo and document scenarios in real use).
        assert "wäschesack" in prompt or "campingurlaub" in prompt

    def test_retrieval_test_framing_in_rules(self):
        """The rules block frames 'is this a good tag' as the retrieval
        test: would the user type this six months from now? Keeping
        that framing anchors the model to user intent over tidiness."""
        prompt = _build_capture_prompt(**COMMON)
        assert "six months from now" in prompt
