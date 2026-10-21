"""The card browser on core: its page, and where its reads go.

The page reads the family memory's cards from the memory stacklet, through
core. Only the memory API's read routes are passed through, and of its
writes only the change to one card, with the headers it is decided by.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "stacklets" / "core" / "tools-server"))

from cards_page import asset, forwarded, page, upstream, write_upstream  # noqa: E402

BASE = "http://memory:8090"


def test_the_page_and_its_two_files_are_served():
    assert b"<title>" in page()
    assert asset("cards.css")[0] == "text/css"
    assert asset("cards.js")[0] == "text/javascript"


def test_no_other_file_is():
    assert asset("index.html") is None
    assert asset("../access.py") is None


def test_the_reads_go_to_the_memory_stacklet_with_their_question():
    assert upstream("find", {"q": "zelt", "type": "note"}, BASE) == f"{BASE}/find?q=zelt&type=note"
    assert upstream("card/capture/Abc123", {}, BASE) == f"{BASE}/card/capture/Abc123"
    assert upstream("report", {}, BASE) == f"{BASE}/report"
    assert upstream("media/2026/10/Abc_12-x.m4a", {"audience": "bart"}, BASE) \
        == f"{BASE}/media/2026/10/Abc_12-x.m4a?audience=bart"
    assert upstream("fonts/inter-latin-wght-normal.woff2", {}, BASE).endswith(".woff2")


def test_nothing_else_reaches_it():
    assert upstream("../etc/passwd", {}, BASE) is None
    assert upstream("card/../../secret", {}, BASE) is None
    assert upstream("admin", {}, BASE) is None
    assert upstream("fonts/x.ttf", {}, BASE) is None


def test_a_card_is_read_by_its_id_for_its_version():
    assert upstream("cards/person-bart", {}, BASE) == f"{BASE}/cards/person-bart"


def test_a_change_goes_to_the_one_card_it_is_about():
    assert write_upstream("cards/person-bart", BASE) == f"{BASE}/cards/person-bart"


def test_no_other_route_takes_a_change():
    assert write_upstream("push", BASE) is None
    assert write_upstream("card/person-bart", BASE) is None
    assert write_upstream("cards/person-bart/history", BASE) is None
    assert write_upstream("cards/..", BASE) is None


def test_a_change_carries_the_token_the_version_and_the_person_and_nothing_else():
    """The memory stacklet decides a change by these; core holds no token and adds none."""
    sent = {"authorization": "Bearer t", "if-match": '"abc"', "famstack-actor": "marge",
            "content-type": "application/merge-patch+json", "cookie": "session=1", "host": "x"}

    assert forwarded(sent) == {"authorization": "Bearer t", "content-type": "application/merge-patch+json",
                               "if-match": '"abc"', "famstack-actor": "marge"}
