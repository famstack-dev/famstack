"""The card browser on core: its page, and where its reads go.

The page reads the family memory's cards from the memory stacklet, through
core, GET only. Only the memory API's read routes are passed through.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "stacklets" / "core" / "tools-server"))

from cards_page import asset, page, upstream  # noqa: E402

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
