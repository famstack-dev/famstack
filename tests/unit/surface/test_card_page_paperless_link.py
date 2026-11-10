"""A document's page opens the document in Paperless at today's address.

A document card stores the Paperless address of the day it was filed
(`paperless_url`, `resource`). That address breaks when the hosting moves,
for example from port mode to domain mode. The card's page therefore
makes "Open in Paperless" when it is shown, from the card's Paperless
number and the Paperless address core has today, and never uses the
address stored on the card. It never links `/go/docs/<n>` either, because
that link lands on this same page.

Driven through the tools server's FastAPI app (the Browser DSL), as a
phone on the home network reads the page. The memory is memory's test
instance: a real curator over a bare repository holding the Simpsons'
cards, whose policy was filed in port mode (`http://localhost:42020`).
"""

from __future__ import annotations


import pytest

from tests.memory.unit.instance import running_memory
from tests.memory.unit.simpsons_cards import VAULT

from .conftest import tools_server
from .dsl import Browser

PUBLIC_PORT = tools_server("access").PUBLIC_PORT

POLICY = "document-3f9a0c1b2d"   # paperless_id 72, filed at http://localhost:42020
DOMAIN_MODE = "https://docs.simpson.example"


@pytest.fixture(scope="module")
def memory(tmp_path_factory):
    with running_memory(tmp_path_factory.mktemp("paperless-link"), VAULT) as running:
        yield running


@pytest.fixture
def tools(memory, monkeypatch):
    """Core's tools server, reaching the memory's curator, in UTC."""
    server = tools_server("server")

    monkeypatch.setenv("MEMORY_CARDS_URL", memory.curator)
    monkeypatch.setenv("TIMEZONE", "UTC")
    return server.app


def outside_links(app, monkeypatch, paperless: str) -> list[tuple[str, str]]:
    """The policy's outside links, with core's Paperless address set to `paperless`."""
    server = tools_server("server")

    monkeypatch.setattr(server, "DOCS_PUBLIC_URL", paperless)
    page = Browser(app, listener=PUBLIC_PORT, host="192.168.1.20:42000").opens(f"/card/{POLICY}")
    assert page.status == 200, page
    return page.links("target")


def test_open_in_paperless_uses_todays_address_after_the_hosting_moved(tools, monkeypatch):
    """Given the policy was filed in port mode and the hosting moved to
    domain mode, when a phone opens its page, then "Open in Paperless"
    opens the document at the new address, not the stored one."""
    links = outside_links(tools, monkeypatch, DOMAIN_MODE)

    assert [href for _, href in links] == [f"{DOMAIN_MODE}/documents/72/details"]
    assert links[0][0].startswith("Open in PaperlessPaperless")


def test_without_a_paperless_address_the_page_links_no_paperless(tools, monkeypatch):
    """Given core has no Paperless address (the docs stacklet is not set
    up), then the page shows no Paperless link rather than the stored one."""
    assert outside_links(tools, monkeypatch, "") == []
