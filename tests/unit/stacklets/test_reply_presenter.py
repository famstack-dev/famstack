"""Chat-reply rendering: pure layout, given a translator.

reply_presenter takes a `t(key, **kwargs)` callable and the
classification/enrichment data and returns the text the archivist
sends. Keeping it pure (no Matrix, no messages.yml) means we can pin
the layout AND prove translations flow through, in both languages,
without booting a bot.

What was filed is shown as its card, through the renderer the card
browser and the agent use (ADR-016 §4): the title once, one line of
facts, then the summary, facts and to-dos. The bot sends it as markdown
`body` and as HTML `formatted_body`.
"""

from __future__ import annotations

import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))

from stacklets.docs.bot.reply_presenter import (  # noqa: E402
    render_capture_correction_reply,
    render_capture_reply,
    render_filing_reply,
    render_reprocessed_reply,
)
from stack.matrix.html import matrix_html  # noqa: E402


def _en(key, **kw):
    """English-ish translator stub mirroring messages/archivist.yml shape."""
    templates = {
        "filed": "✅ Filed: {title} (#{doc_id})",
        "new_in_paperless": "🆕 New in Paperless: {items}",
        "reformat_failed": "Note: reformatting skipped — raw OCR kept.",
        "voice_heard": "🎤 What I heard:",
        "capture_title": "Capture",
    }
    return templates.get(key, key).format(**kw)


def _de(key, **kw):
    templates = {
        "filed": "✅ Abgelegt: {title} (#{doc_id})",
    }
    return templates.get(key, key).format(**kw)


FULL = {
    "summary": "Annual car insurance renewal at Duff Insurance.",
    "facts": ["EUR 340.00/year", "Contract KFZ-2026"],
    "action_items": [
        {"action": "Pay invoice", "due": "2026-03-15"},
        {"action": "File copy", "due": ""},
    ],
}


class TestRenderFilingReply:

    def test_full_happy_path(self):
        out = render_filing_reply(
            _en,
            display_title="Duff Insurance - Kfz-Versicherung",
            doc_id=10,
            resolved_persons=["Homer"],
            resolved_type="Invoice",
            resolved_correspondent="Duff Insurance",
            date_applied="2026-03-15",
            classification=FULL,
            created_new=["Insurance"],
            reformat_failed=False,
            link="http://paperless/documents/10/details",
        )
        assert out == """\
✅ Filed: Duff Insurance - Kfz-Versicherung (#10)

2026-03-15 · Homer · Duff Insurance · Invoice

Annual car insurance renewal at Duff Insurance.

- EUR 340.00/year
- Contract KFZ-2026

**To do**

- Pay invoice 📅 2026-03-15
- File copy

🆕 New in Paperless: Insurance

http://paperless/documents/10/details"""

    def test_a_client_without_html_reads_the_markdown_and_one_with_html_the_same_card(self):
        """The reply goes out as `body` (this markdown) and `formatted_body`
        (its HTML, `stack.matrix.html`). The title is the first line only;
        the facts and the to-dos are their own blocks in both."""
        out = render_filing_reply(
            _en, display_title="Duff Insurance - Kfz-Versicherung", doc_id=10,
            resolved_persons=["Homer"], resolved_type="Invoice",
            resolved_correspondent="Duff Insurance", date_applied="2026-03-15",
            classification=FULL, created_new=[], reformat_failed=False, link="",
        )
        html = matrix_html(out)

        assert out.count("Duff Insurance - Kfz-Versicherung") == 1
        assert "<p>2026-03-15 · Homer · Duff Insurance · Invoice</p>" in html
        assert "<li>EUR 340.00/year</li>" in html
        assert "<p><strong>To do</strong></p>\n<ul>\n<li>Pay invoice 📅 2026-03-15</li>" in html

    def test_translator_drives_language(self):
        out = render_filing_reply(
            _de, display_title="Rechnung", doc_id=7,
            resolved_persons=[], resolved_type=None,
            resolved_correspondent=None, date_applied=None,
            classification={"action_items": [{"action": "Bezahlen", "due": ""}]},
            created_new=[], reformat_failed=False, link="", language="de",
        )
        assert "✅ Abgelegt: Rechnung (#7)" in out
        assert "**Zu erledigen**" in out

    def test_a_filing_with_nothing_known_is_its_title_line_alone(self):
        out = render_filing_reply(
            _en, display_title="x", doc_id=1,
            resolved_persons=[], resolved_type=None,
            resolved_correspondent=None, date_applied=None,
            classification={}, created_new=[], reformat_failed=False, link="",
        )
        assert out == "✅ Filed: x (#1)"

    def test_facts_capped_at_five(self):
        out = render_filing_reply(
            _en, display_title="x", doc_id=1,
            resolved_persons=[], resolved_type=None,
            resolved_correspondent=None, date_applied=None,
            classification={"facts": [f"f{i}" for i in range(10)]},
            created_new=[], reformat_failed=False, link="",
        )
        assert out.count("- f") == 5

    def test_actions_capped_at_three(self):
        out = render_filing_reply(
            _en, display_title="x", doc_id=1,
            resolved_persons=[], resolved_type=None,
            resolved_correspondent=None, date_applied=None,
            classification={"action_items": [
                {"action": f"a{i}", "due": ""} for i in range(10)
            ]},
            created_new=[], reformat_failed=False, link="",
        )
        assert sum(1 for i in range(10) if f"a{i}" in out) == 3

    def test_reformat_failed_notice(self):
        out = render_filing_reply(
            _en, display_title="x", doc_id=1,
            resolved_persons=[], resolved_type=None,
            resolved_correspondent=None, date_applied=None,
            classification={}, created_new=[], reformat_failed=True, link="",
        )
        assert "reformatting skipped" in out

    def test_link_omitted_when_empty(self):
        out = render_filing_reply(
            _en, display_title="x", doc_id=1,
            resolved_persons=[], resolved_type=None,
            resolved_correspondent=None, date_applied=None,
            classification={}, created_new=[], reformat_failed=False, link="",
        )
        assert "http" not in out


class TestRenderReprocessedReply:

    def test_title_and_the_cards_line_of_facts(self):
        out = render_reprocessed_reply(
            _en, title="Duff Insurance Invoice", doc_id=42,
            resolved_persons=["Homer"],
            resolved_type="Invoice", resolved_correspondent="Globex",
        )
        assert out == "reprocessed\n\nHomer · Globex · Invoice"  # _en falls back to the bare key

    def test_no_facts_line_when_empty(self):
        out = render_reprocessed_reply(
            _en, title="x", doc_id=1,
            resolved_persons=[],
            resolved_type=None, resolved_correspondent=None,
        )
        assert out == "reprocessed"



class TestRenderCaptureReply:
    """A filed note, bookmark, email or voice memo is answered with the
    card's glance (`card_glance`): the type's icon, the title as the link
    to the card, and no footer link of its own."""

    def test_the_title_is_the_link_to_the_card(self):
        out = render_capture_reply(
            _en, source_title_hint=None, link="https://home.example/go/card/note-1",
            classification={"title": "Tent", "persons": ["homer"],
                            "summary": "A seam split.", "tags": ["camping", "repair"]},
            captured_at="2026-10-13",
        )
        assert out.splitlines()[0] == "📝 [Tent](https://home.example/go/card/note-1)"
        assert "\n👤 Homer · 📅 Tue 13 Oct" in out
        assert out.endswith("🏷️ camping · repair")
        assert out.count("https://") == 1

    def test_the_card_type_picks_the_icon(self):
        for card_type, icon in (("email", "📧"), ("bookmark", "🔖"), ("note", "📝")):
            out = render_capture_reply(_en, source_title_hint=None, link="",
                                       classification={"title": "x"}, card_type=card_type)
            assert out == f"{icon} x"

    def test_a_bookmark_names_its_site_as_a_link_to_the_page(self):
        out = render_capture_reply(
            _en, source_title_hint=None, link="https://home.example/go/card/bookmark-1",
            classification={"title": "Season ratings", "persons": ["marge"]},
            card_type="bookmark", captured_at="2026-10-13",
            source="https://www.simpsons.example/wiki/ratings",
        )
        lines = out.splitlines()
        assert lines[0] == "🔖 [Season ratings](https://home.example/go/card/bookmark-1)"
        assert ("👤 Marge · 📅 Tue 13 Oct · "
                "🔗 [simpsons.example](https://www.simpsons.example/wiki/ratings)") in lines

    def test_a_note_or_an_email_names_no_site(self):
        for card_type in ("note", "email"):
            out = render_capture_reply(
                _en, source_title_hint=None, link="https://home.example/go/x-1",
                classification={"title": "x"}, card_type=card_type,
                source="https://example.org/a",
            )
            assert out == f"{dict(note='📝', email='📧')[card_type]} [x](https://home.example/go/x-1)"

    def test_a_bookmark_without_a_web_address_names_no_site(self):
        out = render_capture_reply(_en, source_title_hint=None, link="",
                                   classification={"title": "x"}, card_type="bookmark",
                                   source="(capture)")
        assert out == "🔖 x"

    def test_title_falls_back_to_hint_then_capture(self):
        out = render_capture_reply(_en, source_title_hint="Reddit thread",
                                   classification={}, link="")
        assert out == "📝 Reddit thread"
        out2 = render_capture_reply(_en, source_title_hint=None, classification={}, link="")
        assert out2 == "📝 Capture"

    def test_transcript_renders_block_quoted_above_the_card(self):
        """Voice captures echo the transcript so the sender can spot a
        mistranscription without opening the vault. It goes above the
        card so it's the first thing they read."""
        transcript = "Reminder: book Bart's prescription before Friday"
        out = render_capture_reply(
            _en, source_title_hint=None,
            classification={"title": "Bart prescription pickup"},
            link="https://home.example/go/card/note-2",
            transcript=transcript,
        )
        assert "What I heard" in out
        assert f"> {transcript}" in out
        assert out.index(transcript) < out.index("📝 [Bart prescription pickup]")

    def test_note_string_action_items_render_as_open_boxes(self):
        """A note's action items are plain strings (documents give dicts);
        both show in the reply."""
        out = render_capture_reply(
            _en, source_title_hint=None,
            classification={"title": "Camping list",
                            "action_items": ["book the pitch", "pack the stove"]},
            link="",
        )
        assert "- [ ] book the pitch\n- [ ] pack the stove" in out

    def test_transcript_multiline_keeps_each_line_quoted(self):
        """Element renders consecutive '> ' lines as one quote block;
        each transcript line gets its own '> ' prefix so a 3-sentence
        memo stays visually a single quote."""
        transcript = "First sentence.\nSecond sentence.\nThird sentence."
        out = render_capture_reply(
            _en, source_title_hint=None,
            classification={"title": "Multi-sentence memo"},
            link="", transcript=transcript,
        )
        assert "> First sentence.\n> Second sentence.\n> Third sentence." in out

    def test_no_transcript_omits_header(self):
        out = render_capture_reply(_en, source_title_hint="Reddit thread",
                                   classification={"title": "x"}, link="",
                                   transcript=None)
        assert "What I heard" not in out


class TestRenderCaptureCorrectionReply:
    """The answer to a correction: one line naming what changed, then the
    card as it is now, with every tag; folded into `<details>` in the HTML,
    open in the plain body."""

    BEFORE = {"title": "Tent", "persons": ["homer"], "tags": ["camping"]}
    LINK = "https://home.example/go/card/note-1"

    def test_one_line_of_changes_then_the_card_as_it_is_now(self):
        after = {**self.BEFORE, "title": "Torn tent", "persons": ["homer", "bart"],
                 "tags": ["camping", "repair"], "summary": "A seam split."}
        out = render_capture_correction_reply(_en, before=self.BEFORE, after=after,
                                              link=self.LINK)
        assert out.body == (
            "updated_capture\n\n📝 [Torn tent](https://home.example/go/card/note-1)"
            "\n\n👤 Homer, Bart\n\n> A seam split.\n\n🏷️ camping · repair")

    def test_the_line_names_the_changes_with_the_linked_title(self):
        words = {"updated_capture": "🔁 Updated {title}: {changes}",
                 "change_renamed": "renamed", "change_person_added": "{name} added",
                 "change_tag_added": "tag {tag} added", "card_now": "The card now"}
        t = lambda key, **kw: words.get(key, key).format(**kw)  # noqa: E731
        after = {**self.BEFORE, "title": "Torn tent", "persons": ["homer", "bart"],
                 "tags": ["camping", "repair"]}
        out = render_capture_correction_reply(t, before=self.BEFORE, after=after,
                                              link=self.LINK)
        assert out.body.splitlines()[0] == (
            "🔁 Updated [Torn tent](https://home.example/go/card/note-1): "
            "renamed, Bart added, tag repair added")

    def test_the_html_folds_the_card_into_details(self):
        after = {**self.BEFORE, "tags": ["camping", "repair"]}
        out = render_capture_correction_reply(_en, before=self.BEFORE, after=after,
                                              link=self.LINK)
        line, card = out.body.split("\n\n", 1)
        assert out.html == (f"{matrix_html(line)}\n<details><summary>card_now</summary>\n"
                            f"{matrix_html(card)}\n</details>")

    def test_every_tag_is_shown(self):
        tags = [f"tag{n}" for n in range(7)]
        out = render_capture_correction_reply(
            _en, before=self.BEFORE, after={**self.BEFORE, "tags": tags}, link=self.LINK)
        assert out.body.endswith("🏷️ " + " · ".join(tags))

    def test_an_unchanged_card_says_so(self):
        out = render_capture_correction_reply(_en, before=self.BEFORE,
                                              after=dict(self.BEFORE), link="")
        assert out.body.startswith("unchanged_capture\n\n📝 Tent")
