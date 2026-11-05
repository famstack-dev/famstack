"""A bookmark's wiki page leads with the page it bookmarked.

A bookmark is kept for the page it points to, so its wiki page shows that
page first, between the page head and the card's text (`BookmarkTarget`),
by its site and a short form of its address. `bookmarkLink` in
`quartz/components/bookmarkLink.ts` decides what that block shows, from the
card's frontmatter (`resource`, written by the archivist).

The module is TypeScript and runs inside the wiki's Quartz build; its
function is called here through Node, which runs TypeScript by stripping
its types. Without Node the test is skipped.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

MODULE = (Path(__file__).resolve().parents[3]
          / "stacklets" / "memory" / "quartz" / "components" / "bookmarkLink.ts")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")


def _target(frontmatter: dict) -> dict | None:
    """`bookmarkLink(frontmatter)`, as the wiki's bookmark block calls it."""
    script = (f"import {{ bookmarkLink }} from {json.dumps(MODULE.as_uri())};\n"
              "const input = JSON.parse(await new Response(process.stdin).text());\n"
              "console.log(JSON.stringify(bookmarkLink(input) ?? null));\n")
    done = subprocess.run(["node", "--input-type=module", "-e", script],
                          input=json.dumps(frontmatter),
                          capture_output=True, text=True, timeout=30, check=True)
    return json.loads(done.stdout)


def test_a_bookmark_shows_its_site_and_its_address_as_a_person_reads_it():
    href = "https://en.wikipedia.org/wiki/List_of_The_Simpsons_episodes_(seasons_1%E2%80%9320)"

    assert _target({"type": "bookmark", "title": "Season ratings", "resource": href}) == {
        "site": "en.wikipedia.org",
        "address": "en.wikipedia.org/wiki/List_of_The_Simpsons_episodes_(seasons_1–20)",
        "href": href,
    }


def test_the_address_drops_www_the_scheme_a_trailing_slash_and_the_fragment():
    out = _target({"type": "bookmark",
                   "resource": "http://www.simpsons.example/episodes/?season=3#top"})

    assert out["site"] == "simpsons.example"
    assert out["address"] == "simpsons.example/episodes?season=3"


def test_a_long_address_is_cut_and_the_link_keeps_all_of_it():
    href = "https://simpsons.example/" + "springfield-" * 10
    out = _target({"type": "bookmark", "resource": href})

    assert len(out["address"]) == 72 and out["address"].endswith("…")
    assert out["href"] == href


def test_a_bookmark_without_a_resource_shows_no_link():
    assert _target({"type": "bookmark", "title": "Lost link"}) is None


def test_a_resource_that_is_not_a_web_address_shows_no_link():
    assert _target({"type": "bookmark", "resource": "javascript:alert(1)"}) is None
    assert _target({"type": "bookmark", "resource": "(capture)"}) is None


def test_notes_emails_and_documents_show_no_link():
    for kind in ("note", "email", "document"):
        assert _target({"type": kind, "resource": "https://example.org/a"}) is None
