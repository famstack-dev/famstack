"""Links to a card survive the card moving.

A link posted into a Matrix room is permanent, so the question that
decides its design is not "where is this today" but "what about it will
still be true in two years". For a card the answer is its card id and
almost nothing else: the vault path carries the bucket, the topic slug
and often the title slug, and every one of those changes under ordinary
use -- a capture re-scopes when a second person joins the room, a topic
gets renamed, a title is rewritten by a correction, a diary card moves
to the day a family member says it happened.

These tests pin that promise end to end for notes, bookmarks and diary
cards: the emitter keys on the card id, the resolver finds the file
wherever it now sits, and moving the file does not change the answer.
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import unquote

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "core" / "tools-server"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs" / "bot"))

from resolver import build_redirect  # noqa: E402
from search_format import memory_hit_url  # noqa: E402
from stack.cards import find_card  # noqa: E402
from stack.links import go_capture, go_diary, go_page, public  # noqa: E402

CAPTURE_ID = "$Efjml6ySCYyWM6xsNwRCSSEund-u2CIywGpgC8u6tvY"
ENTRY_ID = "3f9a0c1b2d"

PAGE = f"""---
type: note
title: Campsite booked at Lake Springfield
capture_id: "{CAPTURE_ID}"
---

# Campsite booked at Lake Springfield
"""

DIARY_CARD = f"""---
type: diary
title: Bart's first bike ride
timestamp: "2026-08-14T18:02:11Z"
date: "2026-08-14"
entry_id: "{ENTRY_ID}"
event_ids:
  - "$ride:home.test"
---

Bart rode to the end of Evergreen Terrace without stabilisers.
"""

LINKS = dict(
    docs_base="http://docs.example", wiki_base="http://wiki.example",
    shared_bucket="family",
)


def _write(brain: Path, rel: str, text: str = PAGE) -> Path:
    path = brain / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TestFindingACardWhereverItSits:

    def test_a_capture_is_found_by_its_card_id(self, tmp_path):
        _write(tmp_path, "family/camping/notes/2026/08/campsite-3a338e.md")

        assert find_card("capture", CAPTURE_ID, root=tmp_path) == (
            "family/camping/notes/2026/08/campsite-3a338e.md"
        )

    def test_a_note_moved_to_another_folder_is_still_found(self, tmp_path):
        """The whole point, in one test.

        Same capture, re-scoped from a personal bucket to the shared one
        and re-slugged by a corrected title. A path-keyed link would now
        be dead; the id-keyed one still lands.
        """
        before = _write(tmp_path, "homer/trip/notes/2026/08/old-title-3a338e.md")
        assert find_card("capture", CAPTURE_ID, root=tmp_path).startswith("homer/")

        before.unlink()
        _write(tmp_path, "family/camping/notes/2026/08/new-title-9f0011.md")

        assert find_card("capture", CAPTURE_ID, root=tmp_path) == (
            "family/camping/notes/2026/08/new-title-9f0011.md"
        )

    def test_a_diary_card_moved_to_another_day_is_still_found(self, tmp_path):
        """A correction can say the entry happened on another day, and
        the day is the card's folder."""
        before = _write(tmp_path, "family/diary/entries/2026/08/2026-08-14-3f9a0c1b2d.md",
                        DIARY_CARD)
        before.unlink()
        _write(tmp_path, "family/diary/entries/2026/07/2026-07-30-3f9a0c1b2d.md",
               DIARY_CARD)

        assert find_card("diary", ENTRY_ID, root=tmp_path) == (
            "family/diary/entries/2026/07/2026-07-30-3f9a0c1b2d.md"
        )

    def test_the_kind_decides_which_id_counts(self, tmp_path):
        """A capture id is not a diary card's id, and the other way round."""
        _write(tmp_path, "family/camping/notes/2026/08/campsite-3a338e.md")
        _write(tmp_path, "family/diary/entries/2026/08/2026-08-14-3f9a0c1b2d.md",
               DIARY_CARD)

        assert find_card("diary", CAPTURE_ID, root=tmp_path) is None
        assert find_card("capture", ENTRY_ID, root=tmp_path) is None

    def test_an_unknown_id_is_not_found(self, tmp_path):
        """404 beats a redirect to whatever happened to be nearby."""
        _write(tmp_path, "family/camping/notes/2026/08/campsite-3a338e.md")

        assert find_card("capture", "$nope", root=tmp_path) is None

    def test_an_unknown_kind_is_not_found(self, tmp_path):
        _write(tmp_path, "family/camping/notes/2026/08/campsite-3a338e.md")

        assert find_card("list", CAPTURE_ID, root=tmp_path) is None

    def test_an_id_quoted_in_the_body_does_not_count(self, tmp_path):
        """Only frontmatter declares what a page *is*.

        A note discussing another capture would otherwise answer to that
        capture's link.
        """
        _write(
            tmp_path, "family/camping/notes/2026/08/chatter.md",
            f"---\ntype: note\n---\n\nSee capture_id: {CAPTURE_ID} for details.\n",
        )

        assert find_card("capture", CAPTURE_ID, root=tmp_path) is None

    def test_a_page_whose_frontmatter_does_not_parse_answers_to_nothing(self, tmp_path):
        """A hand edit gone wrong is skipped, not guessed at."""
        _write(
            tmp_path, "family/camping/notes/2026/08/broken.md",
            f"---\ncapture_id: \"{CAPTURE_ID}\"\n  nested: nope\n---\n",
        )

        assert find_card("capture", CAPTURE_ID, root=tmp_path) is None

    def test_a_missing_brain_is_not_an_error(self, tmp_path):
        """The projection may not exist yet on a fresh install."""
        assert find_card("capture", CAPTURE_ID, root=tmp_path / "nothing") is None


class TestResolvingACardLink:

    def _redirect(self, kind, card_id, brain):
        return build_redirect(
            kind, [card_id], **LINKS,
            find_card=lambda k, cid: find_card(k, cid, root=brain),
        )

    def test_a_capture_link_redirects_to_the_wiki_page(self, tmp_path):
        _write(tmp_path, "family/camping/notes/2026/08/campsite-3a338e.md")

        assert self._redirect("capture", CAPTURE_ID, tmp_path) == (
            "http://wiki.example/family/camping/notes/2026/08/campsite-3a338e"
        )

    def test_a_diary_link_redirects_to_the_wiki_page(self, tmp_path):
        _write(tmp_path, "family/diary/entries/2026/08/2026-08-14-3f9a0c1b2d.md",
               DIARY_CARD)

        assert self._redirect("diary", ENTRY_ID, tmp_path) == (
            "http://wiki.example/family/diary/entries/2026/08/2026-08-14-3f9a0c1b2d"
        )

    def test_an_id_no_card_carries_is_a_404(self, tmp_path):
        _write(tmp_path, "family/camping/notes/2026/08/campsite-3a338e.md")

        assert self._redirect("diary", "0000000000", tmp_path) is None

    def test_without_a_lookup_it_declines_rather_than_guessing(self):
        """Core running without the projection mounted must 404, not
        invent a path from the id."""
        assert build_redirect("capture", [CAPTURE_ID], **LINKS) is None
        assert build_redirect("diary", [ENTRY_ID], **LINKS) is None

    def test_the_existing_kinds_are_untouched(self):
        """Guards against the card branch swallowing the old ones."""
        assert build_redirect("docs", ["247"], **LINKS) == (
            "http://docs.example/documents/247/details"
        )
        assert build_redirect("topic", ["camping"], **LINKS) == (
            "http://wiki.example/family/camping/about"
        )
        assert build_redirect("person", ["homer", "todo"], **LINKS) == (
            "http://wiki.example/homer/todos"
        )
        assert build_redirect("list", ["einkaufsliste"], **LINKS) == (
            "http://wiki.example/family/lists/einkaufsliste"
        )


class TestTheLinkAnEditAnswersWith:
    """An edit to a page answers with the page's link. A card is linked
    by its card id, so the link outlives the next move; a topic, a
    person or a list by its name, as before."""

    def test_a_note_is_linked_by_its_capture_id(self):
        path = "homer/notes/2026/09/errands.md"
        assert go_page(path, {"type": "note", "capture_id": CAPTURE_ID}) == (
            go_capture(CAPTURE_ID))

    def test_a_bookmark_is_linked_by_its_capture_id(self):
        path = "family/camping/bookmarks/2026/08/lake-map.md"
        assert go_page(path, {"type": "bookmark", "capture_id": CAPTURE_ID}) == (
            go_capture(CAPTURE_ID))

    def test_a_diary_card_is_linked_by_its_entry_id(self):
        path = "family/diary/entries/2026/08/2026-08-14-3f9a0c1b2d.md"
        assert go_page(path, {"type": "diary", "entry_id": ENTRY_ID}) == go_diary(ENTRY_ID)

    def test_the_link_does_not_carry_the_path(self, tmp_path):
        """And it opens the card through the resolver."""
        rel = "homer/notes/2026/09/errands-77aa01.md"
        _write(tmp_path, rel)
        link = public(go_page(rel, {"capture_id": CAPTURE_ID}), "https://home.example.org/go")

        assert "errands" not in link
        kind, card_id = link.removeprefix("https://home.example.org/go/").split("/")
        assert build_redirect(
            kind, [unquote(card_id)], **LINKS,
            find_card=lambda k, cid: find_card(k, cid, root=tmp_path),
        ) == "http://wiki.example/homer/notes/2026/09/errands-77aa01"

    def test_a_card_without_its_id_gets_no_link(self):
        """No id means no stable route: no link beats one that breaks."""
        assert go_page("homer/notes/2026/09/errands.md", {"type": "note"}) == ""
        assert go_page("homer/notes/2026/09/errands.md") == ""

    def test_topics_people_and_lists_keep_their_named_links(self):
        assert go_page("family/camping/about.md", {"type": "topic"}) == (
            "/topic/family/camping")
        assert go_page("homer/about.md", {"type": "person"}) == "/person/homer"
        assert go_page("family/lists/einkaufsliste.md", {"type": "list"}) == (
            "/list/einkaufsliste")


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
