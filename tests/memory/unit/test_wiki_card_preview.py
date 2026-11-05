"""The link preview of a card's wiki page shows its summary.

A chat client previews the card page a filing's answer links to
(`/go/<card id>` redirects to the wiki). The wiki's `CardSummary`
transformer sets the page's social description from the card's text:
the first paragraph of its summary callout, or its first plain
paragraph, never its meta block (which would read "About Bart Started
2026-11-05 · Kind email …").

The transformer is TypeScript and runs inside the wiki's Quartz build;
its one function, `cardDescription`, is called here through Node, which
runs TypeScript by stripping its types. Without Node the test is skipped.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from stack.briefing import render_briefing

PLUGIN = (Path(__file__).resolve().parents[3]
          / "stacklets" / "memory" / "quartz" / "plugins" / "cardSummary.ts")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")

# The start of an email card as the memory writes it: a heading, the meta
# block the preview used to show, then the briefing callout.
EMAIL_CARD = """---
type: email
card_id: email-63b32c39f8
title: Class 4b Parent Meeting
---
# Class 4b Parent Meeting

> [!info] About [[family/people/bart|Bart]] · **Started** 2026-11-05
> **Kind** email · office@springfield-elementary.example

{briefing}
"""


def _description(markdown: str) -> str | None:
    """`cardDescription(markdown)`, as the wiki's build calls it."""
    script = (f"import {{ cardDescription }} from {json.dumps(PLUGIN.as_uri())};\n"
              "const input = await new Response(process.stdin).text();\n"
              "console.log(JSON.stringify(cardDescription(input) ?? null));\n")
    done = subprocess.run(["node", "--input-type=module", "-e", script], input=markdown,
                          capture_output=True, text=True, timeout=30, check=True)
    return json.loads(done.stdout)


def test_an_email_cards_preview_is_its_summary_not_its_meta_block():
    briefing = render_briefing(
        summary="Parents' evening of class 4b on **Friday** at 19:00 in Room 12.",
        facts=["Room 12"], action_items=[{"action": "Bring the form", "due": "2026-11-06"}])

    assert _description(EMAIL_CARD.format(briefing=briefing)) == \
        "Parents' evening of class 4b on Friday at 19:00 in Room 12."


def test_a_note_without_a_summary_is_previewed_by_its_words():
    note = ("---\ntype: note\ntitle: Winter tyres\n---\n# Winter tyres\n\n"
            "> [!info] About [[family/people/homer|Homer]]\n\n"
            "The car still runs on summer tyres. Call [Lanley's Garage](x) by Friday.\n")

    assert _description(note) == \
        "The car still runs on summer tyres. Call Lanley's Garage by Friday."


def test_a_long_summary_is_cut_at_a_word():
    summary = "The trip costs EUR 385 per child and is paid in two parts. " * 8
    out = _description(EMAIL_CARD.format(briefing=render_briefing(
        summary=summary, facts=None, action_items=None)))

    assert out.endswith(" …") and len(out) <= 302


def test_a_page_with_nothing_but_a_heading_keeps_quartzs_own():
    assert _description("---\ntype: topic\n---\n# Camping\n") is None
