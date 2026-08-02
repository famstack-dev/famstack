"""Links to a captured note survive the note moving.

A link posted into a Matrix room is permanent, so the question that
decides its design is not "where is this today" but "what about it will
still be true in two years". For a capture the answer is its id and
almost nothing else: the vault path carries the bucket, the topic slug
and the title slug, and every one of those changes under ordinary use --
a capture re-scopes when a second person joins the room, a topic gets
renamed, a title is rewritten by a correction.

These tests pin that promise end to end: the emitter keys on the id, the
resolver finds the file wherever it now sits, and moving the file does
not change the answer.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "core" / "tools-server"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs" / "bot"))

from capture_index import find_capture  # noqa: E402
from resolver import build_redirect  # noqa: E402
from search_format import memory_hit_url  # noqa: E402
from stack.links import go_capture, public  # noqa: E402

CAPTURE_ID = "$Efjml6ySCYyWM6xsNwRCSSEund-u2CIywGpgC8u6tvY"

PAGE = f"""---
type: note
title: Campsite booked at Lake Springfield
capture_id: {CAPTURE_ID}
---

# Campsite booked at Lake Springfield
"""


def _write(brain: Path, rel: str, text: str = PAGE) -> Path:
    path = brain / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TestFindingACaptureWhereverItSits:

    def test_a_capture_is_found_by_its_id(self, tmp_path):
        _write(tmp_path, "family/camping/notes/2026/08/campsite-3a338e.md")

        assert find_capture(CAPTURE_ID, brain_dir=tmp_path) == (
            "family/camping/notes/2026/08/campsite-3a338e"
        )

    def test_the_answer_follows_the_file_when_it_moves(self, tmp_path):
        """The whole point, in one test.

        Same capture, re-scoped from a personal bucket to the shared one
        and re-slugged by a corrected title. A path-keyed link would now
        be dead; the id-keyed one still lands.
        """
        before = _write(tmp_path, "homer/trip/notes/2026/08/old-title-3a338e.md")
        assert find_capture(CAPTURE_ID, brain_dir=tmp_path).startswith("homer/")

        before.unlink()
        _write(tmp_path, "family/camping/notes/2026/08/new-title-9f0011.md")

        assert find_capture(CAPTURE_ID, brain_dir=tmp_path) == (
            "family/camping/notes/2026/08/new-title-9f0011"
        )

    def test_an_unknown_id_is_not_found(self, tmp_path):
        """404 beats a redirect to whatever happened to be nearby."""
        _write(tmp_path, "family/camping/notes/2026/08/campsite-3a338e.md")

        assert find_capture("$nope", brain_dir=tmp_path) is None

    def test_an_id_quoted_in_the_body_does_not_count(self, tmp_path):
        """Only frontmatter declares what a page *is*.

        A note discussing another capture would otherwise answer to that
        capture's link.
        """
        _write(
            tmp_path, "family/camping/notes/2026/08/chatter.md",
            f"---\ntype: note\n---\n\nSee capture_id: {CAPTURE_ID} for details.\n",
        )

        assert find_capture(CAPTURE_ID, brain_dir=tmp_path) is None

    def test_a_missing_brain_is_not_an_error(self, tmp_path):
        """The projection may not exist yet on a fresh install."""
        assert find_capture(CAPTURE_ID, brain_dir=tmp_path / "nothing") is None


class TestResolvingACaptureLink:

    def test_it_redirects_to_the_wiki_page(self, tmp_path):
        _write(tmp_path, "family/camping/notes/2026/08/campsite-3a338e.md")

        url = build_redirect(
            "capture", [CAPTURE_ID],
            docs_base="http://docs.example", wiki_base="http://wiki.example",
            shared_bucket="family",
            find_capture=lambda cid: find_capture(cid, brain_dir=tmp_path),
        )

        assert url == (
            "http://wiki.example/family/camping/notes/2026/08/campsite-3a338e"
        )

    def test_without_a_lookup_it_declines_rather_than_guessing(self):
        """Core running without the projection mounted must 404, not
        invent a path from the id."""
        assert build_redirect(
            "capture", [CAPTURE_ID],
            docs_base="http://docs.example", wiki_base="http://wiki.example",
            shared_bucket="family",
        ) is None

    def test_the_existing_kinds_are_untouched(self):
        """Guards against the new branch swallowing the old ones."""
        common = dict(
            docs_base="http://docs.example", wiki_base="http://wiki.example",
            shared_bucket="family",
        )
        assert build_redirect("docs", ["247"], **common) == (
            "http://docs.example/documents/247/details"
        )
        assert build_redirect("topic", ["camping"], **common) == (
            "http://wiki.example/family/camping/about"
        )
        assert build_redirect("person", ["homer", "todo"], **common) == (
            "http://wiki.example/homer/todos"
        )


class TestWhichLinkASearchHitGets:

    BASE = "https://home.example.org/go"

    def test_a_capture_is_linked_by_id(self):
        url = memory_hit_url(
            {"rel": "family/camping/notes/2026/08/campsite-3a338e.md",
             "capture_id": CAPTURE_ID},
            link_base_url=self.BASE, code_public_url="http://code.example",
        )

        assert url == public(go_capture(CAPTURE_ID), self.BASE)
        assert "camping" not in url, "the path must not be baked into the link"

    def test_a_page_without_an_id_keeps_the_old_link(self):
        """No regression for hand-written wiki pages.

        They have no id to key on, so they keep the Forgejo blob URL.
        It rots, but a worse link beats none until they grow an id.
        """
        url = memory_hit_url(
            {"rel": "family/correspondents/README.md"},
            link_base_url=self.BASE, code_public_url="http://code.example",
        )

        assert url == (
            "http://code.example/family/memory/src/branch/main/"
            "family/correspondents/README.md"
        )

    def test_no_configured_base_falls_back_rather_than_dropping_the_link(self):
        """Before core renders LINK_BASE_URL there is no /go to point at."""
        url = memory_hit_url(
            {"rel": "family/camping/notes/x.md", "capture_id": CAPTURE_ID},
            link_base_url="", code_public_url="http://code.example",
        )

        assert url.startswith("http://code.example/")
