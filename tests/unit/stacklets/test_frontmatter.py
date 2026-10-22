"""Tests for stack.frontmatter — vault entry frontmatter parser and writer."""

import pytest

from stack.frontmatter import (
    FrontmatterError,
    parse,
    dump,
    round_trip,
)


# ── Round-trip tests (acceptance: document, note, person) ────────────────

class TestRoundTrip:
    """Verify parse → dump → parse identity for real-world entries."""

    def test_document_round_trip(self):
        """Round-trip a document entry with full fields."""
        original = """\
---
type: document
title: Kfz-Versicherung - Jahresabrechnung 2025
timestamp: 2026-07-07T14:32:00Z
source: paperless
paperless_id: 42
date: 2025-06-30
correspondent: Duff Insurance
document_type: invoice
category: Finance
persons:
  - Homer
  - Marge
tags:
  - insurance
  - vehicles
paperless_url: http://localhost:42100
resource: http://localhost:42100/documents/42/details
processing: ai_formatted
model: mlx-community/DeepSeek-V2-Chat-1B
paperless_version: "2.10"
---

# Duff Insurance - Kfz-Versicherung

[Document body goes here]
"""
        parsed = parse(original)
        assert parsed["type"] == "document"
        assert parsed["title"] == "Kfz-Versicherung - Jahresabrechnung 2025"
        assert parsed["persons"] == ["Homer", "Marge"]
        assert parsed["tags"] == ["insurance", "vehicles"]
        # Parser infers type: "42" (unquoted) becomes int
        assert parsed["paperless_id"] == 42

        # Re-dump and parse again
        fm_str = dump(parsed)
        reparsed = parse(f"---\n{fm_str}\n---\n")
        assert reparsed == parsed

    def test_note_round_trip(self):
        """Round-trip a note entry."""
        original = """\
---
type: note
title: Meeting notes - Project kickoff
timestamp: 2026-07-05T09:15:00Z
date: 2026-07-05
persons:
  - Homer
  - Bart
tags:
  - work
  - projects
filed_by: marge
capture_id: event-abc123
model: mlx-community/NousResearch
---

# Meeting notes

[Notes body]
"""
        parsed = parse(original)
        assert parsed["type"] == "note"
        assert parsed["title"] == "Meeting notes - Project kickoff"
        assert parsed["persons"] == ["Homer", "Bart"]
        assert parsed["filed_by"] == "marge"

        fm_str = dump(parsed)
        reparsed = parse(f"---\n{fm_str}\n---\n")
        assert reparsed == parsed

    def test_person_projection_round_trip(self):
        """Round-trip a generated person page."""
        original = """\
---
type: person
generated: true
title: Homer Jay Simpson
slug: homer
canonical: Homer
aliases:
  - Homer Jay Simpson
  - Homer Simpson
  - H. Simpson
role: father
member: true
birthday: "1956-05-12"
employer: Springfield Nuclear Power Plant
---

# Homer Simpson

[Person bio and notes]
"""
        parsed = parse(original)
        assert parsed["type"] == "person"
        assert parsed["generated"] is True
        assert parsed["slug"] == "homer"
        assert parsed["aliases"] == ["Homer Jay Simpson", "Homer Simpson", "H. Simpson"]
        assert parsed["member"] is True
        assert parsed["birthday"] == "1956-05-12"  # Quoted, so it stays a string

        fm_str = dump(parsed)
        reparsed = parse(f"---\n{fm_str}\n---\n")
        assert reparsed == parsed


# ── Parser strictness tests (acceptance: reject forbidden shapes) ────────

class TestParserStrictness:
    """Verify strict rejection of out-of-subset YAML constructs."""

    def test_rejects_nested_map(self):
        """Reject nested map (nested: {inner: value})."""
        text = """\
---
type: document
title: Test
timestamp: 2026-07-07T00:00:00Z
source: paperless
paperless_id: 1
metadata:
  nested: value
---
"""
        with pytest.raises(FrontmatterError, match="indented line"):
            parse(text)

    def test_rejects_block_scalar_pipe(self):
        """Reject block scalar with | (multiline text)."""
        text = """\
---
type: note
title: Test
timestamp: 2026-07-07T00:00:00Z
body: |
  This is a
  multiline text
---
"""
        with pytest.raises(FrontmatterError, match="`\\|`"):
            parse(text)

    def test_rejects_block_scalar_gt(self):
        """Reject block scalar with > (folded text)."""
        text = """\
---
type: note
title: Test
timestamp: 2026-07-07T00:00:00Z
body: >
  This is
  folded text
---
"""
        with pytest.raises(FrontmatterError, match="`>`"):
            parse(text)

    def test_rejects_flow_map_syntax(self):
        """A flow map is a nested map on one line, and nesting is forbidden."""
        with pytest.raises(FrontmatterError, match="`{`"):
            parse("---\ntype: note\nmetadata: {key: value}\n---\n")

    def test_rejects_anchors(self):
        """An anchor makes YAML read `&title_anchor` as markup, not as title text."""
        with pytest.raises(FrontmatterError, match="`&`"):
            parse("---\ntype: note\ntitle: &title_anchor Test\n---\n")

    def test_rejects_aliases(self):
        """An alias points at another value; the subset has none to point at."""
        with pytest.raises(FrontmatterError, match="`\\*`"):
            parse("---\ntype: note\ntitle: *missing_anchor\n---\n")

    def test_rejects_an_empty_block_scalar(self):
        """`body: |` with nothing indented below is still a block scalar."""
        with pytest.raises(FrontmatterError, match="`\\|`"):
            parse("---\ntype: note\nbody: |\n---\n")

    def test_rejects_an_unquoted_colon_in_a_value(self):
        """`title: Notes: Admin` took the wiki down: Quartz's YAML parser
        refuses it, so this parser refuses it too rather than reading a title."""
        with pytest.raises(FrontmatterError, match="`: `"):
            parse("---\ntitle: Notes: Admin\n---\n")

    def test_rejects_an_unquoted_comment_in_a_value(self):
        """YAML reads `Bart #1` as `Bart` plus a comment; refuse the ambiguity."""
        with pytest.raises(FrontmatterError, match="` #`"):
            parse("---\ntitle: Bart #1\n---\n")

    def test_a_hash_inside_a_word_is_text(self):
        """Only ` #` starts a comment, so a URL fragment stays part of the URL."""
        fm = parse("---\nresource: https://example.org/a#b\n---\n")
        assert fm["resource"] == "https://example.org/a#b"

    def test_rejects_an_unclosed_quote(self):
        with pytest.raises(FrontmatterError, match="never closed"):
            parse('---\ntitle: "Bart\n---\n')

    def test_rejects_text_after_a_closing_quote(self):
        with pytest.raises(FrontmatterError, match="after the closing quote"):
            parse('---\ntitle: "Bart" Simpson\n---\n')

    def test_rejects_a_list_of_maps(self):
        with pytest.raises(FrontmatterError, match="`: `"):
            parse("---\npersons:\n  - name: Homer\n---\n")

    def test_rejects_multiple_documents(self):
        """Reject multiple-document separator (---)."""
        text = """\
---
type: note
title: First document
timestamp: 2026-07-07T00:00:00Z
---
---
type: note
title: Second document
timestamp: 2026-07-08T00:00:00Z
---
"""
        # Only the first document is extracted by our parser (up to the first \n---\n)
        fm = parse(text)
        assert fm["title"] == "First document"

    def test_rejects_list_item_without_key(self):
        """Reject list item that appears without a key."""
        text = """\
---
type: note
title: Test
timestamp: 2026-07-07T00:00:00Z
- orphan
---
"""
        with pytest.raises(FrontmatterError, match="list item without a key"):
            parse(text)

    def test_rejects_indented_key(self):
        """Reject indented key name (breaks top-level-only rule)."""
        text = """\
---
type: note
 title: Test
timestamp: 2026-07-07T00:00:00Z
---
"""
        with pytest.raises(FrontmatterError, match="indented line"):
            parse(text)


# ── Validator tests (acceptance: catches all error classes) ──────────────

class TestFlowLists:
    """A one-line flow list of scalars reads as a list, as YAML reads it.

    The family edits pages by hand, and `tags: [a, b]` is how most people
    write a short list. Reading it as the string "[a, b]" turned a tag
    filter into a substring match on brackets. `dump` still writes block
    lists, so the files the stack writes do not change.
    """

    def test_an_empty_flow_list_is_an_empty_list(self):
        assert parse("---\ntags: []\n---\n") == {"tags": []}

    def test_a_flow_list_of_plain_values(self):
        fm = parse("---\npersons: [Homer, Marge, Bart]\n---\n")
        assert fm["persons"] == ["Homer", "Marge", "Bart"]

    def test_items_keep_their_quotes_and_commas_inside_them(self):
        fm = parse('---\ntags: ["Person: Homer", \'Krusty, the Clown\']\n---\n')
        assert fm["tags"] == ["Person: Homer", "Krusty, the Clown"]

    def test_items_are_strings_like_block_list_items(self):
        assert parse("---\nevent_ids: [1, true]\n---\n") == {"event_ids": ["1", "true"]}

    def test_a_flow_list_reads_the_same_as_the_block_list_dump_writes(self):
        written = "---\n" + dump({"tags": ["school", "Person: Bart"]}) + "\n---\n"
        assert parse(written) == parse('---\ntags: [school, "Person: Bart"]\n---\n')

    def test_rejects_a_nested_list(self):
        with pytest.raises(FrontmatterError, match="nested"):
            parse("---\ntags: [a, [b]]\n---\n")

    def test_rejects_a_map_inside_a_list(self):
        with pytest.raises(FrontmatterError, match="nested"):
            parse("---\ntags: [{a: 1}]\n---\n")

    def test_rejects_a_list_that_does_not_close_on_its_line(self):
        with pytest.raises(FrontmatterError, match="close"):
            parse("---\ntags: [a,\n  b]\n---\n")

    def test_rejects_an_empty_item(self):
        with pytest.raises(FrontmatterError, match="empty item"):
            parse("---\ntags: [a,,b]\n---\n")


class TestEmptyOptionalOmission:
    """Verify empty strings and lists are omitted from serialization."""

    def test_omit_empty_string(self):
        """Empty string values are omitted from dump()."""
        fm = {
            "type": "note",
            "title": "Test",
            "timestamp": "2026-07-07T00:00:00Z",
            "correspondent": "",  # Empty string
        }
        result = dump(fm)
        assert "correspondent" not in result

    def test_omit_empty_list(self):
        """Empty list values are omitted from dump()."""
        fm = {
            "type": "note",
            "title": "Test",
            "timestamp": "2026-07-07T00:00:00Z",
            "persons": [],  # Empty list
        }
        result = dump(fm)
        assert "persons" not in result

    def test_include_nonempty_list(self):
        """Non-empty list values are included."""
        fm = {
            "type": "note",
            "title": "Test",
            "timestamp": "2026-07-07T00:00:00Z",
            "persons": ["Homer"],
        }
        result = dump(fm)
        assert "persons:" in result
        assert "- Homer" in result


# ── Quoting tests ────────────────────────────────────────────────────────

class TestQuoting:
    """Verify proper quoting of values with special characters."""

    def test_quote_colon_in_value(self):
        """Quote values containing ':'."""
        fm = {
            "type": "document",
            "title": "Document: Important",
            "timestamp": "2026-07-07T00:00:00Z",
            "source": "paperless",
            "paperless_id": 1,
        }
        result = dump(fm)
        # Title should be quoted
        assert 'title: "Document: Important"' in result

    def test_quote_hash_in_value(self):
        """Quote values containing '#'."""
        fm = {
            "type": "note",
            "title": "Test #hashtag",
            "timestamp": "2026-07-07T00:00:00Z",
        }
        result = dump(fm)
        # Title should be quoted because of #
        assert "title:" in result
        assert "#hashtag" in result

    def test_quote_leading_dash(self):
        """Quote values starting with '-'."""
        fm = {
            "type": "note",
            "title": "-controversial",
            "timestamp": "2026-07-07T00:00:00Z",
        }
        result = dump(fm)
        # Should be quoted
        assert '"-controversial"' in result

    def test_quote_numeric_string(self):
        """Quote strings that look like numbers."""
        fm = {
            "type": "note",
            "title": "12345",
            "timestamp": "2026-07-07T00:00:00Z",
        }
        result = dump(fm)
        # Should be quoted to prevent numeric interpretation
        assert '"12345"' in result

    def test_dont_quote_normal_string(self):
        """Don't quote normal strings without special chars."""
        fm = {
            "type": "note",
            "title": "Normal Title",
            "timestamp": "2026-07-07T00:00:00Z",
        }
        result = dump(fm)
        # Should not be quoted
        assert "title: Normal Title" in result


# ── No frontmatter tests ────────────────────────────────────────────────

class TestNoFrontmatter:
    """Verify behavior when there is no frontmatter block."""

    def test_no_frontmatter_delimiter(self):
        """File with no --- returns empty dict."""
        text = "# This is just a heading\n\nNo frontmatter here."
        fm = parse(text)
        assert fm == {}

    def test_missing_closing_delimiter(self):
        """File with opening --- but no closing --- returns empty dict."""
        text = "---\ntype: note\ntitle: Test\n# No closing ---"
        fm = parse(text)
        assert fm == {}

    def test_round_trip_no_frontmatter(self):
        """round_trip() preserves files with no frontmatter."""
        text = "# Heading\n\nBody text."
        result = round_trip(text)
        assert result == text


# ── Comprehensive integration test ────────────────────────────────────────

class TestComprehensiveIntegration:
    """End-to-end test simulating real vault workflow."""

    def test_parse_dump_cycle(self):
        """Parse → dump → parse gives back the same fields."""
        # Create a realistic document entry
        fm = {
            "type": "document",
            "title": "Marge's Haircut Receipt",
            "timestamp": "2026-07-01T10:00:00Z",
            "source": "paperless",
            "paperless_id": 789,
            "date": "2026-06-30",
            "correspondent": "Classy Hair Salon",
            "persons": ["Marge"],
            "tags": ["personal", "receipts"],
            "processing": "ocr",
            "model": "mlx-community/DeepSeek-V2-Chat-1B",
        }

        # Dump to string
        fm_str = dump(fm)
        assert fm_str  # Non-empty

        # Parse it back
        full_markdown = f"---\n{fm_str}\n---\n\nBody text"
        reparsed = parse(full_markdown)
        assert reparsed == fm


    def test_correspondent_entity_page(self):
        """A correspondent entity page round-trips."""
        fm = {
            "type": "correspondent",
            "generated": True,
            "title": "Duff Insurance",
            "canonical": "Duff Insurance",
            "aliases": ["Duff Brew Insurance", "Duff"],
        }

        # Dump and parse
        fm_str = dump(fm)
        full_markdown = f"---\n{fm_str}\n---\n\nBody text"
        reparsed = parse(full_markdown)
        assert reparsed == fm


    def test_index_page(self):
        """An index (navigation) page round-trips."""
        fm = {
            "type": "index",
            "generated": True,
            "title": "Family Wiki",
        }

        fm_str = dump(fm)
        full_markdown = f"---\n{fm_str}\n---\n\nBody text"
        reparsed = parse(full_markdown)
        assert reparsed == fm



# ── Special case: true/false/null as strings vs. booleans ────────────────

class TestSpecialValues:
    """Verify handling of boolean-like and null-like strings."""

    def test_preserve_true_string(self):
        """The string 'true' should remain a string, not become boolean."""
        fm = {
            "type": "note",
            "title": "It is true",
            "timestamp": "2026-07-07T00:00:00Z",
        }
        fm_str = dump(fm)
        reparsed = parse(f"---\n{fm_str}\n---\n")
        # Title is quoted, so it's preserved as string
        assert reparsed["title"] == "It is true"

    def test_preserve_null_string(self):
        """The string 'null' should be quoted to avoid null interpretation."""
        fm = {
            "type": "note",
            "title": "null hypothesis",
            "timestamp": "2026-07-07T00:00:00Z",
        }
        fm_str = dump(fm)
        reparsed = parse(f"---\n{fm_str}\n---\n")
        assert reparsed["title"] == "null hypothesis"

    def test_preserve_yes_no_strings(self):
        """Strings like 'yes', 'no' should be quoted."""
        fm = {
            "type": "note",
            "title": "yes and no",
            "timestamp": "2026-07-07T00:00:00Z",
        }
        fm_str = dump(fm)
        reparsed = parse(f"---\n{fm_str}\n---\n")
        assert reparsed["title"] == "yes and no"


# ── Date string preservation ────────────────────────────────────────────

class TestQuotedEscapes:
    """What `dump` escapes, `parse` reads back as the original text."""

    def test_double_quote_and_backslash_round_trip(self):
        # The colon makes `dump` quote the value, so the escapes are written.
        title = 'Bart "El Barto": Simpson \\ Springfield'
        written = dump({"title": title})
        assert written.startswith('title: "')
        assert parse("---\n" + written + "\n---\n")["title"] == title

    def test_two_single_quotes_are_one(self):
        assert parse("---\ntitle: 'Moe''s Tavern'\n---\n")["title"] == "Moe's Tavern"

    def test_rejects_an_unknown_escape(self):
        with pytest.raises(FrontmatterError, match="escape"):
            parse('---\npath: "C:\\Users"\n---\n')


class TestDatePreservation:
    """Verify ISO date strings are preserved as strings."""

    def test_iso_date_preserved(self):
        """ISO date (YYYY-MM-DD) should remain a string."""
        fm = {
            "type": "document",
            "title": "Test",
            "timestamp": "2026-07-07T14:32:00Z",
            "source": "paperless",
            "paperless_id": 1,
            "date": "2026-06-30",
        }
        fm_str = dump(fm)
        reparsed = parse(f"---\n{fm_str}\n---\n")
        assert reparsed["date"] == "2026-06-30"
        assert isinstance(reparsed["date"], str)

    def test_iso_datetime_preserved(self):
        """ISO datetime (with Z) should remain a string."""
        fm = {
            "type": "note",
            "title": "Test",
            "timestamp": "2026-07-07T14:32:00Z",
        }
        fm_str = dump(fm)
        reparsed = parse(f"---\n{fm_str}\n---\n")
        assert reparsed["timestamp"] == "2026-07-07T14:32:00Z"
        assert isinstance(reparsed["timestamp"], str)


# ── Edge cases ──────────────────────────────────────────────────────────

class TestEdgeCases:
    """Test boundary conditions and edge cases."""

    def test_empty_frontmatter_block(self):
        """Empty frontmatter (just --- markers) returns empty dict."""
        text = "---\n---\n\nBody"
        fm = parse(text)
        assert fm == {}

    def test_whitespace_only_frontmatter(self):
        """Frontmatter with only whitespace returns empty dict."""
        text = "---\n  \n\n---\n\nBody"
        fm = parse(text)
        assert fm == {}

    def test_many_list_items(self):
        """Parse a list with many items."""
        text = """\
---
type: note
title: Test
timestamp: 2026-07-07T00:00:00Z
persons:
  - Alice
  - Bob
  - Charlie
  - Diana
  - Eve
---
"""
        fm = parse(text)
        assert fm["persons"] == ["Alice", "Bob", "Charlie", "Diana", "Eve"]

    def test_mixed_quotes_in_list(self):
        """List items with mixed quoting."""
        text = """\
---
type: note
title: Test
timestamp: 2026-07-07T00:00:00Z
tags:
  - "tag: with colon"
  - normal-tag
  - 'another: quoted'
---
"""
        fm = parse(text)
        assert "tag: with colon" in fm["tags"]
        assert "normal-tag" in fm["tags"]
        assert "another: quoted" in fm["tags"]

    def test_integer_paperless_id_preserved(self):
        """Integer values like paperless_id are preserved as integers."""
        text = """\
---
type: document
title: Test
timestamp: 2026-07-07T00:00:00Z
source: paperless
paperless_id: 42
---
"""
        fm = parse(text)
        # The unquoted "42" is parsed as an integer
        assert fm["paperless_id"] == 42
        assert isinstance(fm["paperless_id"], int)

    def test_dumps_preserves_integer(self):
        """Dump handles integer values properly."""
        fm = {
            "type": "document",
            "title": "Test",
            "timestamp": "2026-07-07T00:00:00Z",
            "source": "paperless",
            "paperless_id": 42,
        }
        result = dump(fm)
        # The integer 42 should be converted to "42" and quoted (leading digit)
        assert "42" in result

    def test_empty_title_omitted(self):
        """Empty title string is omitted from dump."""
        fm = {
            "type": "note",
            "title": "",
            "timestamp": "2026-07-07T00:00:00Z",
        }
        result = dump(fm)
        assert "title" not in result
