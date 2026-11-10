"""The memory pages on core: their files, and where a browser's reads go.

The page reads the family memory's cards from the memory stacklet, through
core. Only the memory API's read routes are passed through, and of its
writes only the change to one card, with the headers it is decided by.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "stacklets" / "core" / "tools-server"))

from cards_page import BROWSER_ROUTES, MAC_ROUTES, asset, forwarded, write_upstream  # noqa: E402
from cards_page import upstream as _upstream  # noqa: E402

BASE = "http://memory:8090"


def upstream(path, query, base):
    """A read through `/cards/api`, this Mac's path to the whole store."""
    return _upstream(path, query, base, routes=MAC_ROUTES)


def from_a_browser(path):
    """A read through `/card/api`, open to the home network."""
    return _upstream(path, {}, BASE, routes=BROWSER_ROUTES)


def test_the_pages_two_files_are_served():
    assert asset("memory.css")[0] == "text/css"
    assert asset("memory.js")[0] == "text/javascript"


def test_no_other_file_is():
    assert asset("cards.js") is None
    assert asset("__init__.py") is None
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


def test_a_browser_reads_only_a_pages_media_and_fonts():
    """The pages render on the server, so a device on the home network
    needs only their media and fonts; the store's files, paths and sync
    state stay with this Mac."""
    assert from_a_browser("media/2026/10/Abc_12-x.m4a") == f"{BASE}/media/2026/10/Abc_12-x.m4a"
    assert from_a_browser("fonts/inter-latin-wght-normal.woff2").endswith(".woff2")
    for route in ("find", "people", "history", "report", "card/person-bart", "cards/person-bart",
                  "cards/person-bart/versions", "links/family", "pending", "files/facts.toml",
                  "tree/family", "paths", "media/../../etc/passwd"):
        assert from_a_browser(route) is None, route


def test_a_card_is_read_by_its_id_for_its_version():
    assert upstream("cards/person-bart", {}, BASE) == f"{BASE}/cards/person-bart"


def test_the_card_store_is_read_for_what_waits_a_cards_versions_a_file_and_a_directory():
    """The host's `stack status`, a refile and a write read these from the card store."""
    assert upstream("pending", {}, BASE) == f"{BASE}/pending"
    assert upstream("cards/note-3f2a1b9c0d/versions", {}, BASE) == f"{BASE}/cards/note-3f2a1b9c0d/versions"
    assert upstream("files/family/lists/camping.md", {}, BASE) == f"{BASE}/files/family/lists/camping.md"
    assert upstream("files/family/../../etc/passwd", {}, BASE) is None
    assert upstream("tree/family/lists", {}, BASE) == f"{BASE}/tree/family/lists"
    assert upstream("tree/family/../..", {}, BASE) is None
    assert upstream("paths", {}, BASE) == f"{BASE}/paths"
    assert upstream("paths/family", {}, BASE) == f"{BASE}/paths/family"


def test_a_change_goes_to_the_one_card_it_is_about():
    assert write_upstream("PATCH", "cards/person-bart", BASE) == f"{BASE}/cards/person-bart"


def test_a_write_goes_to_the_card_store():
    """The host's writes to the memory are commits of the card store, and
    a command that reads right after an edit in Forgejo asks it to sync."""
    assert write_upstream("POST", "commits", BASE) == f"{BASE}/commits"
    assert write_upstream("POST", "sync", BASE) == f"{BASE}/sync"


def test_no_other_route_takes_a_change():
    assert write_upstream("PATCH", "push", BASE) is None
    assert write_upstream("PATCH", "card/person-bart", BASE) is None
    assert write_upstream("PATCH", "cards/person-bart/history", BASE) is None
    assert write_upstream("PATCH", "cards/..", BASE) is None
    assert write_upstream("PATCH", "commits", BASE) is None


def test_the_host_cannot_post_anything_but_a_write():
    """Forgejo's push notice stays on the stack network."""
    assert write_upstream("POST", "push", BASE) is None
    assert write_upstream("POST", "cards/person-bart", BASE) is None
    assert write_upstream("DELETE", "cards/person-bart", BASE) is None


def test_a_change_carries_the_token_the_version_and_the_person_and_nothing_else():
    """The memory stacklet decides a change by these; core holds no token and adds none."""
    sent = {"authorization": "Bearer t", "if-match": '"abc"', "famstack-actor": "marge",
            "content-type": "application/merge-patch+json", "cookie": "session=1", "host": "x"}

    assert forwarded(sent) == {"authorization": "Bearer t", "content-type": "application/merge-patch+json",
                               "if-match": '"abc"', "famstack-actor": "marge"}
