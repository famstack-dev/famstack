"""A card's page in the wiki says who sent each source, and when.

A card made from things people sent ends with `## Sources`, one quote
per thing, and its sidecar `<stem>.json` holds one entry per quote in the
same order (ADR-018). The wiki does not serve the sidecar, because it
also names rooms, Matrix ids and file names. While the wiki builds, its
`SourceSenders` transformer reads the sidecar beside the card and puts a
line above each quote: the sender's first name, a link to their page when
there is one, and the time it was sent in the wiki's language. Nothing
else from the sidecar reaches the page.

The transformer is TypeScript and runs inside the wiki's Quartz build;
its pure function, `sourceSenders`, is called here through Node, which
runs TypeScript by stripping its types. Without Node the test is skipped.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from stack.cards.sources import ORIGINAL, REPLY, Source, render_sources

PLUGIN = (Path(__file__).resolve().parents[3]
          / "stacklets" / "memory" / "quartz" / "plugins" / "sourceSenders.ts")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")

# The people with a page in the wiki: the folder of the page and its title.
PAGES = {"marge": "Marge Simpson", "homer": "Homer Simpson"}

SOURCES = [
    Source(ORIGINAL, "marge", "2026-11-04T18:20:00+00:00", "chat", "text", "$a1",
           quote="Packing list for the lake: tent, sleeping bags, marshmallows"),
    Source(REPLY, "marge", "2026-11-04T18:31:00+00:00", "chat", "text", "$a2",
           quote="Add Maggie's sun hat"),
]


def _note(sources: list[Source]) -> str:
    """A note card as the memory writes it: fields, its words, then the sources."""
    return ("---\ntype: note\ntitle: Camping packing list\n---\n"
            "# Camping packing list\n\nTent, sleeping bags, marshmallows, Maggie's sun hat.\n\n"
            + render_sources(sources) + "\n")


def _sidecar(sources: list[Source]) -> str:
    """The sidecar beside that card, with the fields the page must not show."""
    return json.dumps({"card_id": "note-dda636", "filed_by": "marge",
                       "sources": [s.record() for s in sources],
                       "made": {"at": "2026-11-04T18:32:00+00:00", "model": "qwen"}})


def _senders(markdown: str, sidecar: str | None, locale: str = "en-US") -> list | None:
    """`sourceSenders(...)`, as the wiki's build calls it, in UTC."""
    script = (f"import {{ sourceSenders }} from {json.dumps(PLUGIN.as_uri())};\n"
              "const a = JSON.parse(await new Response(process.stdin).text());\n"
              "const out = sourceSenders(a.markdown, a.sidecar, (id) => a.pages[id], a.locale);\n"
              "console.log(JSON.stringify(out ?? null));\n")
    given = json.dumps({"markdown": markdown, "sidecar": sidecar, "pages": PAGES, "locale": locale})
    done = subprocess.run(["node", "--input-type=module", "-e", script], input=given,
                          capture_output=True, text=True, timeout=30, check=True,
                          env={**os.environ, "TZ": "UTC"})
    return json.loads(done.stdout)


def test_each_quote_is_signed_with_the_senders_first_name_and_time():
    assert _senders(_note(SOURCES), _sidecar(SOURCES)) == [
        {"name": "Marge", "href": "/marge/about.md", "when": "Nov 4, 2026, 6:20 PM"},
        {"name": "Marge", "href": "/marge/about.md", "when": "Nov 4, 2026, 6:31 PM"},
    ]


def test_the_time_is_written_in_the_wikis_language():
    when = _senders(_note(SOURCES), _sidecar(SOURCES), locale="de-DE")[0]["when"]

    assert when == "4. Nov. 2026, 18:20"


def test_a_sender_without_a_page_is_named_without_a_link():
    sources = [Source(ORIGINAL, "grampa", "2026-11-04T09:00:00+00:00", "chat", "text", "$b1",
                      quote="Bring the folding chairs")]

    assert _senders(_note(sources), _sidecar(sources)) == \
        [{"name": "Grampa", "when": "Nov 4, 2026, 9:00 AM"}]


def test_a_source_sent_at_an_unknown_time_shows_only_the_name():
    sources = [Source(ORIGINAL, "homer", "unknown", "cli", "text", "cli:1", quote="Beer cooler")]

    assert _senders(_note(sources), _sidecar(sources)) == \
        [{"name": "Homer", "href": "/homer/about.md"}]


def test_a_card_without_a_sidecar_shows_its_quotes_as_they_are():
    assert _senders(_note(SOURCES), None) is None


def test_quotes_and_sources_that_do_not_pair_up_show_the_quotes_as_they_are():
    assert _senders(_note(SOURCES), _sidecar(SOURCES[:1])) is None


def test_a_sidecar_that_is_not_json_shows_the_quotes_as_they_are():
    assert _senders(_note(SOURCES), "{not json") is None
