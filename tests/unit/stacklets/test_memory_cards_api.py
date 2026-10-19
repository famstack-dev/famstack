"""The family memory's cards as JSON: what a card page reads.

Pages that show the family memory (core's card browser, later the
dashboard) get the same cards `stack memory find`, `card` and `links`
print, as JSON over HTTP from the memory stacklet. Driven here over real
HTTP, against a real git repository with commits made as the people who
made them.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "lib"))
sys.path.insert(0, str(_REPO / "stacklets" / "memory"))
sys.path.insert(0, str(Path(__file__).parent))

from cards_api import CardsAPI, serve  # noqa: E402
from test_memory_graph_cli import RECENT, Memory, _note  # noqa: E402


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path)
    m.write("family/people/lisa.md", "---\ntype: person\ntitle: Lisa Simpson\n---\n"
            "Lisa plays the saxophone.\n", by="marge", message="chore(people): marge described Lisa")
    m.compile("lisa/about.md", "---\ntype: person\ntitle: Lisa Simpson\ngenerated: true\n---\n"
              "<!-- begin: generated -->\nLisa, 8, wants to be president.\n<!-- end: generated -->\n")
    m.write("family/camping/about.md", "---\ntype: topic\ntitle: Camping\nslug: camping\n---\n"
            "The family's camping trips.\n\n![[/media/2026/10/Abc123Def456XYZ.m4a]]\n", by="homer", message="feat(topics): homer added camping")
    m.write("family/camping/zelt.md",
            _note("Abc123Def456XYZ", "Zelt ist kaputt", RECENT,
                  body="Das Zelt ist gerissen.",
                  persons=["Lisa"]),
            by="homer", message="feat(notes): homer filed Zelt ist kaputt")
    m.write("family/lists/markt.md", "---\ntype: list\ntitle: Wochenmarkt\n---\n"
            "- [ ] Kohlrabi\n- [x] Eier\n", by="marge", message="feat(lists): marge started Wochenmarkt")
    m.write("marge/notes/aquarell.md", _note("Marge000000001", "Barts Aquarell", RECENT,
                                             body="![[/media/2026/10/Marge000000001.png]]"),
            by="marge", message="feat(notes): marge filed Barts Aquarell")
    return m


PHOTO = b"\x89PNG Barts Aquarell"
RECORDING = b"ftypM4A Homer on the tent"


@pytest.fixture
def api(memory, tmp_path):
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    (fonts / "inter.woff2").write_bytes(b"wOF2 not really")
    media = tmp_path / "memory" / "media"
    (media / "2026" / "10").mkdir(parents=True)
    (media / "2026" / "10" / "Marge000000001.png").write_bytes(PHOTO)
    (media / "2026" / "10" / "Abc123Def456XYZ.m4a").write_bytes(RECORDING)
    (tmp_path / "memory" / "secrets.toml").write_text("password = 'not for the page'\n")
    server = serve(CardsAPI(vault=memory.vault, brain=memory.brain, fonts=fonts, media=media),
                   host="127.0.0.1", port=0)
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def _get(base: str, path: str) -> tuple[int, dict | bytes]:
    try:
        with urllib.request.urlopen(base + path) as r:
            body = r.read()
            return r.status, json.loads(body) if "json" in r.headers["Content-Type"] else body
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


class TestFindingCards:

    def test_words_find_cards_with_what_a_list_row_shows(self, api):
        status, found = _get(api, "/find?q=zelt")

        assert status == 200
        [card] = [c for c in found["cards"] if c["id"] == "capture/Abc123Def4"]
        assert card["type"] == "note" and card["title"] == "Zelt ist kaputt"
        assert card["day"] == RECENT and card["version"] and card["links"] >= 1

    def test_the_types_there_are_come_with_their_counts(self, api):
        _, found = _get(api, "/find?q=")

        assert found["types"]["note"] == 2 and found["types"]["list"] == 1

    def test_a_type_narrows_the_result(self, api):
        _, found = _get(api, "/find?q=&type=topic")

        assert [c["id"] for c in found["cards"]] == ["topic/camping"]

    def test_a_person_narrows_it_to_the_cards_about_them(self, api):
        _, found = _get(api, "/find?q=&person=lisa")

        assert {c["id"] for c in found["cards"]} == {"capture/Abc123Def4", "person/lisa"}


class TestOpeningACard:

    def test_a_card_has_its_fields_text_history_and_links(self, api):
        status, card = _get(api, "/card/capture/Abc123Def4")

        assert status == 200
        assert card["text"] == "Das Zelt ist gerissen."
        assert card["meta"]["capture_id"] == "$Abc123Def456XYZ"
        assert card["changes"][0]["who"] == "homer"
        assert {c["id"] for c in card["links_out"]} == {"person/lisa", "topic/camping"}

    def test_a_list_comes_with_its_items(self, api):
        _, card = _get(api, "/card/list/markt")

        assert [(i["text"], i["done"]) for i in card["items"]] == [("Kohlrabi", False), ("Eier", True)]

    def test_the_cards_around_one_come_nearest_first(self, api):
        _, around = _get(api, "/links/person/lisa?depth=2")

        assert around["around"][0]["distance"] == 1
        [camping] = [a for a in around["around"] if a["card"]["id"] == "topic/camping"]
        assert camping["distance"] == 2 and camping["via"] == "capture/Abc123Def4"

    def test_an_unknown_card_is_not_found(self, api):
        assert _get(api, "/card/capture/nothere")[0] == 404

    def test_a_person_comes_with_the_view_compiled_about_them(self, api):
        """The person is their file; the wiki's page on them is reached from
        it, marked as a view, with the link that opens it in the wiki."""
        _, card = _get(api, "/card/person/lisa")

        assert card["path"] == "family/people/lisa.md"
        [view] = card["views"]
        assert view["view"] == "person" and view["path"] == "lisa/about.md"
        assert view["text"] == "Lisa, 8, wants to be president."
        assert view["go"] == "/go/person/lisa"

    def test_a_view_is_no_card(self, api):
        assert _get(api, "/card/lisa/about.md")[0] == 404
        _, found = _get(api, "/find?q=president")
        assert found["cards"] == []


class TestWhoIsLooking:

    def test_a_persons_own_card_is_not_for_others(self, api):
        _, found = _get(api, "/find?q=aquarell&audience=bart,homer")

        assert found["cards"] == []
        assert _get(api, "/card/capture/Marge00000?audience=bart")[0] == 404

    def test_it_is_for_them_alone(self, api):
        _, found = _get(api, "/find?q=aquarell&audience=marge")

        assert [c["title"] for c in found["cards"]] == ["Barts Aquarell"]

    def test_the_format_report_is_the_admins_whoever_is_looking(self, api):
        """It says what needs fixing in the whole memory; Marge's own note
        counts in it even when the page is viewed as Bart."""
        _, as_admin = _get(api, "/report")
        _, as_bart = _get(api, "/report?audience=bart")

        assert as_bart == as_admin and as_admin["cards"] == 5


class TestWhatChanged:

    def test_the_latest_changes_come_first_with_their_cards(self, api):
        _, history = _get(api, "/history")

        latest = history["changes"][0]
        assert latest["who"] == "marge" and latest["subject"].endswith("Barts Aquarell")
        assert latest["cards"][0]["id"] == "capture/Marge00000"

    def test_a_new_filing_is_there_on_the_next_read(self, api, memory):
        memory.write("family/camping/plane.md", _note("Plane0000001", "Neue Plane", RECENT),
                     by="lisa", message="feat(notes): lisa filed Neue Plane")

        _, found = _get(api, "/find?q=plane")

        assert [c["title"] for c in found["cards"]] == ["Neue Plane"]


class TestTheMediaACardEmbeds:
    """A record embeds its photo or recording from the media archive
    (`![[/media/...]]`); the page shows it from here."""

    def _file(self, base: str, path: str) -> tuple[int, str, bytes]:
        try:
            with urllib.request.urlopen(base + path) as r:
                return r.status, r.headers["Content-Type"], r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers["Content-Type"], e.read()

    def test_a_photo_is_served_as_the_image_it_is(self, api):
        assert self._file(api, "/media/2026/10/Marge000000001.png") == (200, "image/png", PHOTO)

    def test_a_recording_is_served_as_audio(self, api):
        status, kind, body = self._file(api, "/media/2026/10/Abc123Def456XYZ.m4a?audience=bart")

        assert (status, kind, body) == (200, "audio/mp4", RECORDING)

    def test_nothing_outside_the_archive_is_served(self, api):
        """The archive sits next to the stacklet's other data."""
        for path in ("/media/../secrets.toml", "/media/2026/../../secrets.toml",
                     "/media/%2e%2e/secrets.toml", "/media//etc/passwd"):
            status, _, body = self._file(api, path)
            assert status == 404 and b"not for the page" not in body, path

    def test_another_persons_photo_is_not_served_when_viewing_as_someone_else(self, api):
        """Marge's note is hers, so its photo is too: viewing as Bart must
        not open it, and does not say it exists."""
        assert self._file(api, "/media/2026/10/Marge000000001.png?audience=bart")[0] == 404
        assert self._file(api, "/media/2026/10/Marge000000001.png?audience=marge")[0] == 200

    def test_a_file_no_card_embeds_is_the_admins_alone(self, api, tmp_path):
        (tmp_path / "memory" / "media" / "2026" / "10" / "Orphan0001.png").write_bytes(PHOTO)

        assert self._file(api, "/media/2026/10/Orphan0001.png?audience=homer")[0] == 404
        assert self._file(api, "/media/2026/10/Orphan0001.png")[0] == 200


def test_the_famstack_faces_are_served_and_nothing_else(api):
    assert _get(api, "/fonts/inter.woff2") == (200, b"wOF2 not really")
    assert _get(api, "/fonts/../../etc/passwd")[0] == 404
