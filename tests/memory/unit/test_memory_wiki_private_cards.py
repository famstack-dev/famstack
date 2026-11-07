"""A card in a person's bucket stays out of what the family reads together.

What a person keeps in their own bucket is read only where they are the
only one listening (`stacklets.memory.graph.readable`). The wiki's pages
follow that rule: the home page, another person's page, a correspondent's
page and a page of the family's bucket neither tell the model about such a
card nor derive from it. Its owner's page and their own topics do.

The compile runs as the bot runner's `wiki` command runs it: `wiki.run`
with the model client, over a brain that holds the Simpsons' cards. What
stands in is the model, an HTTP server answering the chat completions API
(`Model`), which records what it was sent.

The cards carry a marker in their summary and in their title, so a test
reads the marker in the prompt and the title and the card id on the page.
A case takes under a second.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from openai import AsyncOpenAI

from stack.ai import models
from stack.ai.client import LLM
from stacklets.memory.bot.cli import wiki
from tests.memory.unit.simpsons_cards import ONTOLOGY, PEOPLE
from tests.model import Model

ANSWER = {"family": "The Simpsons", "welcome": "A loud, loving family. [1]",
          "order": [], "people": {}, "tagline": "A Simpson.",
          "about": "Lives in Springfield. [1]", "summary": "As the cards say. [1]"}
# What a compile from before this rule wrote: the model told a private card.
QUOTING = {**ANSWER, "welcome": "Marge plans SECRET-PLAN. [1]", "about": "Marge plans SECRET-PLAN. [1]",
           "summary": "Marge plans SECRET-PLAN. [1]"}

HOME, HOMER, LISA, MARGE = "index.md", "homer/about.md", "lisa/about.md", "marge/about.md"
TOPIC = "family/itchy-scratchy-land/about.md"
GARDEN = "marge/gardening/about.md"
DUFF, HIBBERT = ("family/correspondents/duff-insurance/about.md",
                 "family/correspondents/dr-hibbert/about.md")
PAGES = (HOME, HOMER, LISA, MARGE, TOPIC, GARDEN, DUFF, HIBBERT)


def _card(kind: str, name: str, marker: str, *, persons: tuple[str, ...] = (),
          tags: tuple[str, ...] = (), correspondent: str = "") -> str:
    """A card with `marker` in its title and its summary; its id is `<kind>-<name>`."""
    fields = [f"type: {kind}", f"card_id: {kind}-{name}", f"title: {marker} {name}",
              'date: "2026-10-14"']
    if correspondent:
        fields.append(f"correspondent: {correspondent}")
    for key, values in (("persons", persons), ("tags", tags)):
        if values:
            fields += [f"{key}:", *(f"  - {v}" for v in values)]
    head = "\n".join(fields)
    return f"---\n{head}\n---\n\n# {marker} {name}\n\n> [!summary]\n> {marker} in the summary.\n"


CARDS = {
    **PEOPLE, "ontology.toml": ONTOLOGY,
    "family/people/lisa.md": "---\ntype: person\ncard_id: person-lisa\ntitle: Lisa Simpson\n"
                             "account: lisa\n---\n\n# Lisa Simpson\n",
    # In a person's bucket. Each names others, as the cards of the finding do.
    "marge/notes/2026/10/plan.md": _card(
        "note", "plan", "SECRET-PLAN", persons=("homer", "lisa", "bart"),
        tags=("itchy-scratchy-land", "gardening")),
    "homer/bookmarks/2026/10/gift.md": _card(
        "bookmark", "gift", "GIFT-IDEA", persons=("lisa",), tags=("gardening",)),
    "homer/emails/2026/10/results.md": _card(
        "email", "results", "PRIVATE-MAIL", persons=("homer",), correspondent="Dr Hibbert"),
    "homer/emails/2026/10/claim.md": _card(
        "email", "claim", "PRIVATE-CLAIM", persons=("homer",), correspondent="Duff Insurance"),
    # A person's own topic.
    "marge/gardening/notes/2026/10/roses.md": _card("note", "roses", "OWN-TOPIC", persons=("marge",)),
    # What the family shares.
    "family/documents/2026/10/policy.md": _card(
        "document", "policy", "FAMILY-DOC", persons=("lisa", "homer"), correspondent="Duff Insurance",
        tags=("itchy-scratchy-land",)),
    "family/diary/entries/2026/10/lake.md": _card("diary", "lake", "FAMILY-DIARY", persons=("lisa",)),
    "family/itchy-scratchy-land/notes/2026/10/tickets.md": _card(
        "note", "tickets", "FAMILY-TOPIC", persons=("lisa",)),
    "family/itchy-scratchy-land/topic.md": (
        "---\ntype: topic\ncard_id: topic-itchy-scratchy-land\ntitle: Itchy Scratchy Land\n---\n\n"
        "# Itchy Scratchy Land\n"),
}
PRIVATE = {"SECRET-PLAN": "note-plan", "GIFT-IDEA": "bookmark-gift",
           "PRIVATE-MAIL": "email-results", "PRIVATE-CLAIM": "email-claim"}
FAMILY = {"FAMILY-DOC": "document-policy", "FAMILY-DIARY": "diary-lake", "FAMILY-TOPIC": "note-tickets"}


class Wiki:
    """The brain, and the compile with the model of one page or of all of them."""

    def __init__(self, root: Path, model: Model, monkeypatch):
        self.brain, vault = root / "brain", root / "vault"
        for base in (self.brain, vault):
            for path, text in CARDS.items():
                (base / path).parent.mkdir(parents=True, exist_ok=True)
                (base / path).write_text(text, encoding="utf-8")
        self.calls_log = root / "wiki-calls.jsonl"
        for name, value in (("BRAIN_REPO_DIR", self.brain), ("MEMORY_VAULT_DIR", vault),
                            ("SHARED_BUCKET", "family"), ("LANGUAGE", "en"),
                            ("WIKI_CALLS_LOG", self.calls_log), ("STACK_TODAY", "2026-10-26")):
            monkeypatch.setenv(name, str(value))
        monkeypatch.setattr(models, "_DEFAULT_MODEL", "stand-in")
        self.model = model
        self.llm = LLM(AsyncOpenAI(base_url=model.url, api_key="none", max_retries=0),
                       namespace="memory-wiki")

    def compile(self, *flags: str) -> str:
        """`wiki` with `flags`, and every prompt the model was sent for it."""
        sent = len(self.model.requests)
        assert asyncio.run(wiki.run(self.llm, list(flags))) == 0
        return json.dumps(self.model.requests[sent:], ensure_ascii=False)

    def asked(self) -> list[str]:
        """The pages the model was asked for since the last call, sorted."""
        if not self.calls_log.exists():
            return []
        calls = [json.loads(ln)["page"] for ln in self.calls_log.read_text().splitlines()]
        self.calls_log.unlink()
        return sorted(calls)

    def page(self, path: str) -> str:
        return (self.brain / path).read_text(encoding="utf-8")

    def has(self, path: str) -> bool:
        return (self.brain / path).exists()


@pytest.fixture
def model(httpserver) -> Model:
    model = Model(httpserver)
    model.answers(ANSWER)
    return model


@pytest.fixture
def the_wiki(tmp_path, model, monkeypatch) -> Wiki:
    return Wiki(tmp_path, model, monkeypatch)


def _reads_none_of(cards: dict[str, str], prompt: str, page: str) -> None:
    """Neither the model's prompt nor the page (references, `derived_from`) has `cards`."""
    for marker, card_id in cards.items():
        assert marker not in prompt, f"{marker} was sent to the model"
        assert marker not in page, f"{marker} is on the page"
        assert card_id not in page, f"{card_id} is in the page's derived_from"


def _derives_from(cards: dict[str, str], page: str) -> None:
    for card_id in cards.values():
        assert card_id in page, f"{card_id} is not in the page's derived_from"


class TestTheHomePage:
    def test_reads_no_card_of_a_persons_bucket(self, the_wiki):
        """Given cards in Marge's and Homer's buckets that name others, when
        the home page compiles, then neither the model nor the page has them."""
        prompt = the_wiki.compile("--home")

        _reads_none_of(PRIVATE, prompt, the_wiki.page(HOME))

    def test_reads_what_the_family_shares(self, the_wiki):
        """Given a family document, a family topic's note and the diary, when
        the home page compiles, then the model reads each and the page derives from each."""
        prompt = the_wiki.compile("--home")

        assert all(marker in prompt for marker in FAMILY)
        _derives_from(FAMILY, the_wiki.page(HOME))


class TestAPersonsPage:
    def test_reads_no_card_of_someone_elses_bucket(self, the_wiki):
        """Given Marge's note and Homer's bookmark naming Lisa, when Lisa's page
        compiles, then the model and the page have neither."""
        prompt = the_wiki.compile("--member", "lisa")

        _reads_none_of(PRIVATE, prompt, the_wiki.page(LISA))

    def test_reads_their_own_bucket(self, the_wiki):
        """Given Marge's note naming Homer, when the pages of both compile, then
        it is on hers and not on his; his own cards are on his."""
        prompt = the_wiki.compile("--member", "marge")
        assert "SECRET-PLAN" in prompt
        _derives_from({"SECRET-PLAN": "note-plan"}, the_wiki.page(MARGE))

        prompt = the_wiki.compile("--member", "homer")
        _reads_none_of({"SECRET-PLAN": "note-plan"}, prompt, the_wiki.page(HOMER))
        assert "GIFT-IDEA" in prompt and "PRIVATE-MAIL" in prompt

    def test_reads_what_the_family_shares_about_them(self, the_wiki):
        prompt = the_wiki.compile("--member", "lisa")

        assert all(marker in prompt for marker in FAMILY)
        _derives_from(FAMILY, the_wiki.page(LISA))


class TestACorrespondentsPage:
    def test_lists_only_the_familys_documents(self, the_wiki):
        """Given a family document and Homer's private email from Duff, when
        Duff's page compiles, then it lists the document only."""
        the_wiki.compile("--correspondent", "Duff Insurance")

        page = the_wiki.page(DUFF)
        assert "FAMILY-DOC" in page
        _reads_none_of({"PRIVATE-CLAIM": "email-claim"}, "", page)

    def test_a_correspondent_only_private_cards_name_gets_no_page(self, the_wiki):
        """Given only Homer's private email names Dr Hibbert, when the wiki
        compiles, then the family has no page about Dr Hibbert."""
        the_wiki.compile()

        assert not the_wiki.has(HIBBERT)


class TestATopicsPage:
    def test_of_the_family_reads_no_persons_card_that_names_it(self, the_wiki):
        """Given Marge's note tagged with a family topic, when its page compiles,
        then the model and the page do not have the note."""
        prompt = the_wiki.compile("--topic", "itchy-scratchy-land")

        page = the_wiki.page(TOPIC)
        _reads_none_of(PRIVATE, prompt, page)
        assert "FAMILY-TOPIC" in prompt
        assert "FAMILY-DOC" in prompt, "a family card elsewhere that names the topic stays"

    def test_of_a_person_keeps_their_cards_and_no_one_elses(self, the_wiki):
        """Given Marge's topic, her note tagged with it and Homer's bookmark
        tagged with it, when her topic's page compiles, then it has hers only."""
        prompt = the_wiki.compile("--topic", "gardening")

        page = the_wiki.page(GARDEN)
        assert "OWN-TOPIC" in prompt and "SECRET-PLAN" in prompt
        _derives_from({"SECRET-PLAN": "note-plan"}, page)
        _reads_none_of({"GIFT-IDEA": "bookmark-gift"}, prompt, page)


class TestPagesThatAlreadyQuoteSuchACard:
    """An older compile told the model every card. No migration runs."""

    @pytest.fixture
    def older_pages(self, the_wiki, model, monkeypatch) -> Wiki:
        with monkeypatch.context() as older:
            older.setattr(wiki, "readable", lambda *args, **kwargs: True)
            older.setattr(wiki, "_PERSON_PAGE_STAMP", "<!-- person page: 4 -->")
            model.answers(QUOTING)
            the_wiki.compile()
        for page in (HOME, HOMER, LISA, TOPIC):
            assert "SECRET-PLAN" in the_wiki.page(page)
        assert "PRIVATE-MAIL" in the_wiki.page(HIBBERT)
        the_wiki.asked()
        model.answers(ANSWER)
        return the_wiki

    def test_the_next_compile_leaves_it_out(self, older_pages):
        """Given pages an older compile wrote from a private card, when the
        sweep runs with nothing else changed, then the model is asked again,
        is not sent the card or the old text, and the pages lose both."""
        for page, flags in ((HOME, ["--home"]), (LISA, ["--member", "lisa"]),
                            (TOPIC, ["--topic", "itchy-scratchy-land"])):
            prompt = older_pages.compile(*flags)
            assert older_pages.asked() == [page]
            _reads_none_of(PRIVATE, prompt, older_pages.page(page))

    def test_the_old_text_of_a_person_page_is_not_sent_to_the_model(self, older_pages):
        """Given Homer's page, whose text an older compile wrote from Marge's
        note, when it compiles, then the model does not get that text as the
        page to keep, and the page loses it."""
        prompt = older_pages.compile("--member", "homer")

        assert older_pages.asked() == [HOMER]
        assert "SECRET-PLAN" not in prompt
        assert "SECRET-PLAN" not in older_pages.page(HOMER)

    def test_a_correspondents_page_with_only_private_cards_goes(self, older_pages):
        """Given a page an older compile wrote about Dr Hibbert, when his name
        is compiled, then the page is gone, and the card's folder neighbours stay."""
        older_pages.compile("--correspondent", "Dr Hibbert", "--home")

        assert not older_pages.has(HIBBERT)
        assert older_pages.has(DUFF)

    def test_no_page_compiles_again_and_again(self, older_pages):
        """Given the next compile wrote the pages, when the sweep runs again,
        then the model is asked for nothing."""
        older_pages.compile()
        older_pages.asked()

        older_pages.compile()

        assert older_pages.asked() == []
