"""A filing day in the Simpsons' memory, as Homer, Marge and Lisa's pages show it.

On one day the family files the usual things: Homer sends a letter to the
documents room (a bowling league registration with a fee due in eleven
days), and the family's calendar holds a date for the whole family (Lisa's
school concert) and one of Homer's own (the dentist). Then each person
opens the memory on their phone:

- "What changed" on Homer's page lists the letter today, as his change.
  A newsletter Paperless took in by itself, which nobody sent, is the
  archivist's own commit and stays out of it. Marge's reply that corrects
  a filed bill is her change, not the sender's.
- The concert, which names no one, is in "Up next" on every person's page;
  the dentist only on Homer's.
- The letter's card page shows Homer as a person chip that opens his
  page, and its to-do with its day as the memory writes days.
- Each type tile above the sections counts what its section shows; the
  menu counts every list, a fully ticked one too.

The letters are filed as the archivist bot files them: its document
pipeline (`DocumentPipeline.process`, and `.reprocess` for a reply) with
its real mirror, through the curator's API. Paperless and the model are
stand-ins: Paperless holds each letter's text, and the model answers with
the letter's classification. The pages are core's tools server (the
Browser DSL), reading the memory's curator over HTTP on localhost; the
host API is the dashboard's stand-in.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from pathlib import Path

import pytest

from stack.cards.events import Correction
from stack.lists import new_list
from stack.memory import MemoryAPI
from stacklets.docs.bot.document_pipeline import DocumentPipeline
from stacklets.docs.bot.git_mirror import GitMirror
from stacklets.docs.bot.vault_context import VaultContext
from tests.dashboard_host import DashboardHost
from tests.memory.unit.instance import running_memory
from tests.memory.unit.simpsons_cards import PEOPLE

from .conftest import tools_server
from .dsl import Browser, Page

PUBLIC_PORT = tools_server("access").PUBLIC_PORT

# The curator's today is the real one: "What changed" looks back from it
# and "Up next" looks ahead.
TODAY = date.today()
CONCERT = TODAY + timedelta(days=8)
FEE_DUE = TODAY + timedelta(days=11)
DENTIST = TODAY + timedelta(days=13)

LISA = {"family/people/lisa.md": "---\ntype: person\ncard_id: person-lisa\ntitle: Lisa Simpson\n"
                                 "account: lisa\n---\n\n# Lisa Simpson\n"}
DATES = (new_list("Dates", path="family/lists/dates.md", description="The family's dates")
         + f"\n- [ ] 18:00 Lisa's school concert 📅 {CONCERT.isoformat()}\n"
         f"- [ ] 09:00 Dentist [Homer](../people/homer.md) 📅 {DENTIST.isoformat()}\n")
GROCERIES = (new_list("Groceries", path="family/lists/groceries.md") + "\n- [ ] Milk\n- [ ] Duff\n")
# Done: every item ticked. Marge's sewing names her, its one item Homer.
PARTY = new_list("Party", path="family/lists/party.md") + "\n- [x] Balloons\n"
SEWING = (new_list("Sewing", path="family/lists/sewing.md").replace("title: Sewing\n",
                                                                    "title: Sewing\npersons:\n- marge\n")
          + "\n- [ ] Pick up thread [Homer](../people/homer.md)\n")
CARDS = {**PEOPLE, **LISA, "family/lists/dates.md": DATES, "family/lists/groceries.md": GROCERIES,
         "family/lists/party.md": PARTY, "family/lists/sewing.md": SEWING}

LETTER = {"title": "Bowling League Registration", "date": TODAY.isoformat(),
          "correspondent": "Springfield Bowling League", "document_type": "Booking",
          "persons": ["homer"], "summary": "Homer's team is registered for the winter tournament.",
          "action_items": [{"action": "Pay entry fee of 40.00 USD", "due": FEE_DUE.isoformat()}]}
NEWSLETTER = {"title": "Springfield Power Newsletter", "date": TODAY.isoformat(),
              "correspondent": "Springfield Nuclear Power Plant", "document_type": "Letter",
              "persons": ["homer"], "summary": "Safety week at the plant."}
DENTAL_BILL = {"title": "Dental Bill", "date": TODAY.isoformat(),
               "correspondent": "Springfield Dental", "document_type": "Invoice",
               "persons": ["homer"], "summary": "A cleaning."}
# What the model answers to Marge's correction, as the card conversation asks.
CORRECTED = {"title": "Dental Bill for Marge", "persons": {"add": ["Marge"], "remove": ["Homer"]}}
MINE = 'this one is mine, call it "Dental Bill for Marge"'

# Paperless's document ids, and the text it read from each letter.
LETTERS = {45: (LETTER, "Registration for the Pin Pals. Entry fee 40.00 USD."),
           46: (NEWSLETTER, "Safety week at Sector 7G. Wear your badge."),
           47: (DENTAL_BILL, "Springfield Dental. Cleaning, 80.00 USD.")}


class Paperless:
    """Paperless as the document pipeline talks to it: it reads a letter
    sent to it (`LETTERS`, in the order they are sent) and keeps its tags,
    notes and fields."""

    def __init__(self):
        self.sent = iter(LETTERS)
        self.tags: dict[str, int] = {}

    async def upload(self, filename, data, content_type=None):
        return f"task-{filename}"

    async def wait_task(self, task_id):
        return next(self.sent)

    async def get_doc(self, doc_id):
        return {"id": doc_id, "title": f"scan-{doc_id}", "content": LETTERS[doc_id][1]}

    async def get_tags(self):
        return dict(self.tags)

    async def get_doc_types(self):
        return {}

    async def get_correspondents(self):
        return {}

    async def create_tag(self, name, color=None):
        self.tags[name] = len(self.tags) + 1
        return self.tags[name]

    async def create_doc_type(self, name):
        return 1

    async def create_correspondent(self, name):
        return 1

    async def update_doc(self, doc_id, updates):
        return True

    async def list_notes(self, doc_id):
        return []

    async def add_note(self, doc_id, text):
        return None


class Model:
    """The model, answering each letter with its classification, and a
    correction with `CORRECTED`."""

    async def has_vision(self):
        return False

    async def classify(self, *, ocr_text, user_hint=None, **context):
        return dict(next(c for c, text in LETTERS.values() if text == ocr_text))

    async def correct_card(self, messages, *, text, todos=False):
        return dict(CORRECTED)


def archivist_pipeline(memory, data: Path) -> DocumentPipeline:
    """The archivist bot's document pipeline, with its real mirror."""
    vault = VaultContext(language="en", shared_bucket="family")
    mirror = GitMirror(data_dir=data, vault=vault, memory=MemoryAPI.from_container(memory.environ))
    return DocumentPipeline(
        paperless=Paperless(), classifier=Model(), mirror=mirror, bot_name="archivist-bot",
        language="en", classify_enabled=True, reformat_enabled=False, classify_max_chars=10_000,
        vision_max_pdf_pages=5, reformat_max_pdf_pages=5,
        paperless_public_url="http://docs.home.test", link_base_url="http://home.test/go",
        actor="@archivist-bot:simpson", vault=vault)


async def a_filing_day(pipeline: DocumentPipeline) -> list[str]:
    """Homer sends the registration, Paperless takes in the newsletter by
    itself, Homer sends the dental bill, and Marge replies to its filing
    that it is hers. Returns the three cards' ids."""
    sent = [await pipeline.process(filename="registration.txt", display_name="registration.txt",
                                   file_data=b"scan", submitter_mxid="@homer:simpson"),
            await pipeline.process(filename="newsletter.txt", display_name="newsletter.txt",
                                   file_data=b"scan"),
            await pipeline.process(filename="dental.txt", display_name="dental.txt",
                                   file_data=b"scan", submitter_mxid="@homer:simpson")]
    await pipeline.reprocess(doc_id=47, corrections=[Correction("marge", "", MINE)],
                             filed={**sent[2].envelope["data"]}, changed_by="@marge:simpson")
    return [s.envelope["data"]["card_id"] for s in sent]


@pytest.fixture(scope="module")
def filing_day(tmp_path_factory):
    """The memory after the day's filing: the calendar and the groceries,
    and the letters the archivist filed (`a_filing_day`). Yields the
    running memory and the three letters' card ids."""
    root = tmp_path_factory.mktemp("filing-day")
    with running_memory(root / "data", CARDS) as memory, pytest.MonkeyPatch.context() as env:
        # The archivist reads people and lists from the curator's clone, as
        # the bot runner mounts it.
        env.setenv("MEMORY_VAULT_DIR", str(memory.vault))
        env.setenv("LANGUAGE", "en")
        cards = asyncio.run(a_filing_day(archivist_pipeline(memory, root / "archivist")))
        yield memory, cards


@pytest.fixture
def tools(filing_day, tmp_path, monkeypatch):
    """Core's tools server, reaching the memory's curator, in UTC, with the
    host API answering for the Simpsons."""
    memory, _ = filing_day
    host = DashboardHost.in_state("services", root=tmp_path / "instance")
    monkeypatch.setenv("STACK_API_HOST", "127.0.0.1")
    monkeypatch.setenv("STACK_API_PORT", str(host.serve()))
    monkeypatch.setenv("MEMORY_CARDS_URL", memory.curator)
    monkeypatch.setenv("TIMEZONE", "UTC")
    yield tools_server("server").app
    host.close()


@pytest.fixture
def letter(filing_day) -> str:
    return filing_day[1][0]


def commit_of(memory, title: str) -> str:
    """`author | committer | subject` of the one commit whose subject names `title`."""
    [found] = [c for c in memory.forgejo.new_commits() if c.split(" | ")[2].endswith(f'"{title}"')
               or c.split(" | ")[2].endswith(title)]
    return found


def phone(app, person: str) -> Browser:
    """A phone on the home network, paired as `person`."""
    return Browser(app, listener=PUBLIC_PORT, host="192.168.1.20:42000", person=person)


def opens(browser: Browser, path: str) -> Page:
    page = browser.opens(path)
    assert page.status == 200, page
    return page


def hrefs(page: Page, section: str) -> set[str]:
    return {href for _, href in page.within("data-section", section).links()}


def as_the_memory_writes(day: date) -> str:
    """A day as the memory's pages write it in English: "30 November",
    with the year only when it is not this year."""
    text = f"{day.day} {day:%B}"
    return text if day.year == TODAY.year else f"{text} {day.year}"


def up_next(page: Page) -> str:
    return page.within("data-section", "up_next").text


# ── What changed ──────────────────────────────────────────────────────────

def test_a_letter_filed_today_for_homer_is_in_what_changed_as_his(tools, filing_day, letter):
    """Given a letter Homer sent to the documents room, which the archivist
    filed today, when Homer opens My memory, then "What changed" lists the
    letter: the filing is his change, made through the archivist's account.

    The archivist's own work (a migration, a rebuild) stays out of "What
    changed"; a filing someone asked for is that person's."""
    memory, _ = filing_day
    page = opens(phone(tools, "homer"), "/memory")

    assert f"/card/{letter}" in hrefs(page, "changed")
    assert commit_of(memory, "Bowling League Registration").startswith("homer ")


def test_a_letter_nobody_sent_is_the_archivists_and_not_in_what_changed(tools, filing_day):
    """Given a newsletter about Homer that Paperless took in by itself, when
    Homer opens My memory, then "What changed" does not list it: nobody
    sent it, so its commit is the archivist's own, which is the machine's."""
    memory, (_, newsletter, _) = filing_day
    page = opens(phone(tools, "homer"), "/memory")

    assert f"/card/{newsletter}" not in hrefs(page, "changed")
    assert commit_of(memory, "Springfield Power Newsletter").startswith("archivist-bot ")


def test_a_reply_that_corrects_a_letter_is_the_repliers_change(filing_day):
    """Given the dental bill Homer sent, when Marge replies to its filing
    that it is hers, then the correction's commit is by Marge, through the
    archivist's account: the history shows her change, not Homer's."""
    memory, _ = filing_day

    corrected = commit_of(memory, "Dental Bill for Marge")
    assert corrected.startswith("marge ")
    assert corrected.split(" | ")[1].startswith("archivist-bot ")


# ── Up next ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/memory", "/memory/person/lisa", "/memory/person/marge"])
def test_a_date_that_names_no_one_is_in_up_next_on_every_persons_page(tools, path):
    """Given the family's calendar with Lisa's school concert, which names
    no one, and Homer's dentist, when Homer opens My memory, Lisa's or
    Marge's page, then each has the concert in "Up next": a date for no one
    in particular is everyone's, also on a list where another date names
    Homer."""
    assert "Lisa's school concert" in up_next(opens(phone(tools, "homer"), path))


def test_a_date_that_names_one_person_is_only_on_their_page(tools):
    homers = up_next(opens(phone(tools, "homer"), "/memory"))
    lisas = up_next(opens(phone(tools, "homer"), "/memory/person/lisa"))

    assert "Dentist" in homers
    assert "Dentist" not in lisas


# ── The letter's card page ────────────────────────────────────────────────

def test_the_letters_card_shows_homer_as_a_chip_to_his_page(tools, letter):
    page = opens(phone(tools, "homer"), f"/card/{letter}")

    assert "/memory/person/homer" in {href for _, href in page.links("chip")}


def test_the_letters_to_do_reads_as_words_with_its_day(tools, letter):
    """Given the letter's to-do "Pay entry fee of 40.00 USD" due in eleven
    days, when its card page opens, then the to-do reads with its day as
    the memory writes days ("30 November"), not as the card's markup
    ("📅 2026-11-30")."""
    body = opens(phone(tools, "homer"), f"/card/{letter}").within("class", "body").text

    assert "Pay entry fee of 40.00 USD" in body
    assert as_the_memory_writes(FEE_DUE) in body
    assert "📅" not in body
    assert FEE_DUE.isoformat() not in body


# ── The counts ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("person", ["homer", "marge"])
def test_the_lists_tile_counts_what_the_lists_section_shows(tools, person):
    """Given the calendar (one date names Homer), the groceries (name no
    one), the sewing (names Marge, its item Homer) and the party list with
    every item ticked, when Homer or Marge opens My memory, then the lists
    tile and the lists section both say 3: the open lists that are theirs.
    The menu says 4, because it counts every list, as the lists' own page
    shows them."""
    page = opens(phone(tools, person), "/memory")

    tile = page.within("data-type", "list").texts("n")
    section = page.within("data-section", "lists").texts("fl-count")
    menu = page.within("href", "/memory/kind/list").texts("n")
    assert tile == section == ["3"]
    assert menu == ["4"]


def test_a_list_that_names_marge_is_hers_whoever_its_items_name(tools):
    """Given the sewing list, whose persons name Marge and whose one item
    names Homer, when Marge opens My memory, then the list is in her lists:
    a list that names a person is theirs."""
    lists = opens(phone(tools, "marge"), "/memory").within("data-section", "lists").text

    assert "Sewing" in lists
