"""The card pages build every address in one place.

A card has one address for its whole life (`/card/<card id>`), and every
link of the card pages leads to an address of that family: a card, its
history and full text, a stored file. One module
spells them (`memory_ui/addresses.py`), so the next change to the address
touches one file and no page is left with dead links.

The pages are driven through the tools server's FastAPI app (the Browser
DSL), as a phone on the home network reads them, against memory's test
instance with its media store. The structure check reads the package's
source, because "no other module spells the address" is a property of the
code, not of one page.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tests.memory.unit.instance import running_memory
from tests.memory.unit.simpsons_cards import VAULT

from .conftest import TOOLS_SERVER, tools_server
from .dsl import Browser

PUBLIC_PORT = tools_server("access").PUBLIC_PORT

PACKAGE = TOOLS_SERVER / "memory_ui"

# A photo whose file name needs quoting in an address, and a note made
# from it whose text shows the same photo again.
GREETINGS = "note-6a7b8c9d0e"
GREETINGS_PNG = "2026/11/GrüßeVomSee.png"
GREETINGS_CARD = (
    f"---\ntype: note\ncard_id: {GREETINGS}\ntitle: Greetings from the lake\n"
    "date: \"2026-11-08\"\npersons:\n  - marge\n---\n\n# Greetings from the lake\n\n"
    f"The boys on the jetty.\n\n![[/media/{GREETINGS_PNG}]]\n\n"
    f"## Sources\n\n> ![[/media/{GREETINGS_PNG}]]\n>\n> Bart and Lisa at the lake.\n")
GREETINGS_SIDECAR = (
    '{"card_id": "%s", "filed_by": "marge", "sources": [{"role": "original", "person": "marge", '
    '"time": "2026-11-08T10:00:00Z", "channel": "chat", "kind": "image", "id": "$EvGreet"}], '
    '"made": {"at": "2026-11-08T10:00:09Z", "model": "qwen3"}}\n' % GREETINGS)
NOTE_PATH = "family/notes/2026/11/greetings-6a7b8c.md"


@pytest.fixture(scope="module")
def memory(tmp_path_factory):
    cards = {**VAULT, NOTE_PATH: GREETINGS_CARD,
             NOTE_PATH.removesuffix(".md") + ".json": GREETINGS_SIDECAR}
    with running_memory(tmp_path_factory.mktemp("memory-addresses"), cards) as running:
        running.keeps_file(GREETINGS_PNG, b"\x89PNG\r\n\x1a\n", filename="IMG_0500.png",
                           mime="image/png", size=8, kind="image")
        yield running


@pytest.fixture
def tools(memory, monkeypatch):
    """Core's tools server, reaching the memory's curator, in UTC."""
    server = tools_server("server")

    monkeypatch.setenv("MEMORY_CARDS_URL", memory.curator)
    monkeypatch.setenv("TIMEZONE", "UTC")
    return server.app


def phone(app, language: str = "en") -> Browser:
    return Browser(app, listener=PUBLIC_PORT, host="192.168.1.20:42000", language=language)


# ── One module spells the address ─────────────────────────────────────────

def test_no_module_but_the_address_module_spells_a_memory_address():
    """Given the card pages, then every link to a card, its history,
    its text and a media file comes from the address module: no
    other module of the package has `/card` in its code. Docs and
    comments may show an address as an example."""
    spelled = [f"{path.name}:{line}" for path in sorted(PACKAGE.glob("*.py"))
               if path.name != "addresses.py"
               for line in _strings_with(path, "/card")]
    assert spelled == []


def _strings_with(path: Path, text: str) -> list[int]:
    """The lines of `path`'s code strings that hold `text`, docstrings left out."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = {id(node.body[0].value) for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and node.body and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)}
    return [node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docs and text in node.value]


# ── A stored file has one address ─────────────────────────────────────────

def test_a_file_whose_name_needs_quoting_has_one_address_in_its_panel_and_in_the_text(tools):
    """Given a photo note whose file name holds an umlaut, and
    whose text shows the same photo, when its page opens, then the panel's
    link and the text's image read the file at the same, quoted address."""
    page = phone(tools).opens(f"/card/{GREETINGS}")
    assert page.status == 200, page
    address = "/card/api/media/2026/11/Gr%C3%BC%C3%9FeVomSee.png"
    assert page.links("photo") == [("Open the image ↗", address)]
    files = re.findall(r'(?:src|href)="(/card/api/media/[^"]*)"', page.body)
    assert len(files) >= 3 and set(files) == {address}   # the panel's image and link, the text's image


# ── The side bar ──────────────────────────────────────────────────────────

@pytest.mark.parametrize(("language", "label"), [("en", "‹ Dashboard"), ("de", "‹ Übersicht")])
def test_the_side_bar_holds_the_mark_and_the_way_back_only(tools, language, label):
    """Given a card page in English or German, then its side bar holds the
    famstack mark and one link, back to the dashboard ("Dashboard" or
    "Übersicht"): no title link to an overview, no search field, no
    search button."""
    page = phone(tools, language).opens(f"/card/{GREETINGS}")
    assert [("".join(t.split()), h) for t, h in page.links("side")] == [
        ("".join(label.split()), "/dashboard")]
    assert page.texts("fs-brand") == ["famstack"]
    assert 'type="search"' not in page.body and 'role="search"' not in page.body
    assert "searchbtn" not in page.body
