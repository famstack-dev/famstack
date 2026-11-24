"""A card's history says what each filing and each correction was.

A card page's "History" lists who did what to the card, newest first
("Homer corrected it · today"). Under a filing and a correction a quieter
line says more, read from the source the change brought into the card's
sidecar (ADR-018):

    Homer corrected it · today
        "The title shall be Fuel receipt"
    Homer filed it · today
        a photo, in Homer's chat

A change that brought no source the card still holds (a card from before
the sidecars, the machine's "read it again", a source taken out later) keeps
its one line. The history quotes only what the card page shows: a card the
device may not read has no history either (`test_memory_card_page.py`).

Driven through the tools server's FastAPI app (the Browser DSL). The
memory is memory's test instance: a real curator over a bare repository,
each change a commit in Forgejo with the subject its writer uses.
"""

from __future__ import annotations

import json

import pytest

from tests.memory.unit.instance import running_memory
from tests.memory.unit.simpsons_cards import VAULT

from .conftest import tools_server
from .dsl import Browser, Page

CARDS_PORT = tools_server("access").CARDS_PORT
PUBLIC_PORT = tools_server("access").PUBLIC_PORT

FUEL, BILL, OLD, DROPPED = "diary-f0e1d2c3b4", "note-b1b2b3b4b5", "note-01d01d01d0", "diary-d0d1d2d3d4"
SENT = "2026-11-20T18:00:00+00:00"
FUEL_AT = "homer/diary/entries/2026/11/2026-11-20-f0e1d2c3b4"
BILL_AT = "family/notes/2026/11/power-bill"
OLD_AT = "family/notes/2026/11/old-note"
DROPPED_AT = "homer/diary/entries/2026/11/2026-11-21-d0d1d2d3d4"
PHOTO = "2026/11/pump.png"
PDF = "2026/11/bill.pdf"
CORRECTION = "The title shall be Fuel receipt"
LONG = ("The title shall be Fuel receipt, and the date is the twentieth, not the twenty-first, "
        "because I paid at the pump on Friday evening after work.")


def _source(role: str, kind: str, event: str, *, channel: str = "chat", by: str = "homer") -> dict:
    return {"role": role, "person": by, "time": SENT, "channel": channel, "kind": kind, "id": event}


def _card(card_type: str, card_id: str, title: str, at: str, quotes: list[str],
          sources: list[dict] | None, *, text: str = "At the pump.") -> dict[str, str]:
    """A card and, when `sources` is given, its sidecar: the text, then
    `## Sources` with one quote per source."""
    shown = "\n\n".join("\n".join(f"> {line}" for line in q.split("\n")) for q in quotes)
    card = (f"---\ntype: {card_type}\ncard_id: {card_id}\ntitle: {title}\ndate: \"2026-11-20\"\n"
            f"persons:\n  - homer\n---\n\n# {title}\n\n{text}\n\n## Sources\n\n{shown}\n")
    files = {f"{at}.md": card}
    if sources is not None:
        files[f"{at}.json"] = json.dumps({"card_id": card_id, "filed_by": "homer", "sources": sources,
                                          "made": {"model": ""}}, indent=2) + "\n"
    return files


PUMP = _source("original", "image", "$pump")
PHOTO_QUOTE = f"![[/media/{PHOTO}]]\nFilled up the car."


@pytest.fixture(scope="module")
def memory(tmp_path_factory):
    """The Simpsons' memory, and then, one commit each:

    - Homer files a photo in his own chat; then he corrects the entry
      twice, the second time at length.
    - Homer files a power bill that came by email, as a PDF.
    - A note from before the sidecars; a migration gives it one.
    - Homer files a photo and corrects it; a later change takes the
      correction out of the card again.
    """
    with running_memory(tmp_path_factory.mktemp("card-history"), VAULT) as running:
        def change(subject: str, files: dict[str, str], *, committer: str = "archivist-bot",
                   by: str = "homer") -> None:
            running.edited_in_forgejo(files, by=by, subject=subject, committer=committer)

        change("learn: Fuel", _card("diary", FUEL, "Fuel", FUEL_AT, [PHOTO_QUOTE], [PUMP]))
        change("correct: Fuel receipt", _card(
            "diary", FUEL, "Fuel receipt", FUEL_AT, [PHOTO_QUOTE, CORRECTION],
            [PUMP, _source("note", "text", "$fix")]), committer="memory-bot")
        change("correct: Fuel receipt of Friday", _card(
            "diary", FUEL, "Fuel receipt", FUEL_AT, [PHOTO_QUOTE, CORRECTION, LONG],
            [PUMP, _source("note", "text", "$fix"), _source("note", "text", "$fix2")]),
            committer="memory-bot")
        change("capture: Power bill", _card(
            "note", BILL, "Power bill", BILL_AT, [f"[bill.pdf](/media/{PDF})"],
            [_source("original", "file", "<bill-7@power.example>", channel="email")]))
        change("capture: Old note", _card("note", OLD, "Old note", OLD_AT, ["Filled up."], None))
        change("cards: give each note its sidecar", _card(
            "note", OLD, "Old note", OLD_AT, ["Filled up."], [_source("original", "text", "$old")],
            text="At the pump, in the new format."),
            committer="memory-bot", by="memory-bot")
        change("learn: Pump", _card("diary", DROPPED, "Pump", DROPPED_AT, [PHOTO_QUOTE], [PUMP]))
        change("correct: Pump at the station", _card(
            "diary", DROPPED, "Pump at the station", DROPPED_AT, [PHOTO_QUOTE, LONG],
            [PUMP, _source("note", "text", "$long")]), committer="memory-bot")
        change("Update the pump entry", _card("diary", DROPPED, "Pump", DROPPED_AT, [PHOTO_QUOTE], [PUMP]))
        yield running


@pytest.fixture
def tools(memory, monkeypatch):
    monkeypatch.setenv("MEMORY_CARDS_URL", memory.curator)
    monkeypatch.setenv("TIMEZONE", "UTC")
    return tools_server("server").app


def history(app, card_id: str, *, language: str = "en") -> Page:
    """The History fragment of `card_id`, as the Mac's browser loads it."""
    page = Browser(app, listener=CARDS_PORT, host="127.0.0.1:42002",
                   language=language).opens(f"/card/{card_id}/history")
    assert page.status == 200, page
    return page


def lines(page: Page) -> list[tuple[str, str]]:
    """Each event as (its first line without the day, its quieter line or "")."""
    found = []
    for event in page.texts("person") + page.texts("machine"):
        said = next((s for s in page.texts("said") if event.endswith(s)), "")
        found.append((event.removesuffix(said).split("·")[0].strip(), said))
    return found


# ── A correction ──────────────────────────────────────────────────────────

def test_a_correction_is_quoted_under_corrected_it(tools):
    """Given Homer's entry corrected with "The title shall be Fuel
    receipt", when its History opens, then under "Homer corrected it" a
    quieter line quotes the correction."""
    assert ("Homer corrected it", f"“{CORRECTION}”") in lines(history(tools, FUEL))


def test_a_long_correction_is_cut_to_about_120_characters(tools):
    """Given a correction of 140 characters, then its quote is cut to
    about 120 and ends in "…", because the history is a list of lines,
    and the card shows the whole text."""
    newest = history(tools, FUEL).texts("said")[0]

    assert newest.startswith("“The title shall be Fuel receipt, and the date")
    assert newest.endswith("…”") and len(newest) <= 124


def test_a_correction_the_card_no_longer_holds_is_not_quoted(tools):
    """Given a correction a later change took out of the card, then its
    line stays "corrected it" alone: the history shows only what the
    card page shows."""
    page = history(tools, DROPPED)

    assert ("Homer corrected it", "") in lines(page)
    assert not [s for s in page.texts("said") if "pump on Friday" in s]


# ── A filing ──────────────────────────────────────────────────────────────

def test_a_filing_says_what_and_where(tools):
    """Given a photo Homer sent in his own chat, then under "Homer filed
    it" a quieter line says "a photo, in Homer's chat"."""
    assert lines(history(tools, FUEL))[-1] == ("Homer filed it", "a photo, in Homer's chat")


def test_a_pdf_by_email_says_so(tools):
    """Given a power bill filed from an email's PDF, then its filing says
    "a PDF, by email". It is the card's one change: nobody corrected it,
    so nothing else is said (the resting state)."""
    assert lines(history(tools, BILL)) == [("Homer filed it", "a PDF, by email")]


def test_a_change_without_a_source_keeps_its_one_line(tools):
    """Given a note filed before the sidecars and given its sidecar by a
    migration, then neither line has a second one: the filing brought no
    source, and the migration is the machine's."""
    assert [said for _, said in lines(history(tools, OLD))] == ["", ""]


def test_in_german_the_lines_are_german(tools):
    english, german = history(tools, BILL), history(tools, BILL, language="de")

    assert english.texts("said") != german.texts("said") != []
