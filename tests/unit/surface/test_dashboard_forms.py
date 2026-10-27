"""A stacklet's setup forms answered in the dashboard, its secrets on this Mac only.

A stacklet whose first `up` still asks questions in forms is added from
the page: its Add opens the forms, and the page posts the answers so far
to `POST /dashboard/api/configure/<id>`, which relays them to the host
API's `configure` and answers with the next form, the same form with the
reason under a field, or ready, after which the page starts the job.

A secret (a `writeOnly` field) is answered only from a page opened on this
Mac. From any other device the route walks the forms itself, forwarding
only answers to fields of forms without a secret; when a form asks for a
secret it answers with the terminal command instead, and a secret posted
anyway never reaches the host API.

Driven through the tools server's FastAPI app (the Browser DSL), as this
Mac and as a phone open it. The host API is the stand-in, at its edge:
`stack.live.client.call` plays the forms of `shoebox`, an extension
stacklet that asks what the backup asks (a disk, its passphrase when the
disk is encrypted, a time), and notes every request.
"""

from __future__ import annotations

import json
import re
import sys

import pytest

from .conftest import LIB, STACKLETS
from .dsl import Browser
from .test_dashboard_setup import FAMILY, listing

TOOLS = STACKLETS / "core" / "tools-server"
for _path in (LIB, TOOLS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from access import PUBLIC_PORT  # noqa: E402
from stack.live import client  # noqa: E402


def form(fid: str, field: str, **spec) -> dict:
    return {"id": fid, "title": fid, "text": "", "errors": {},
            "schema": {"type": "object", "required": [field],
                       "properties": {field: {"type": "string", "title": field, **spec}}}}


DISK = form("shoebox.disk", "disk", examples=["backup-vault"], default="backup-vault")
PASSPHRASE = form("shoebox.passphrase", "passphrase", writeOnly=True)
TIME = form("shoebox.time", "time", format="time")


class HostAPI:
    """The host API, playing shoebox's forms for an encrypted disk."""

    def __init__(self, stack_list: dict):
        self.stack_list, self.asked = stack_list, []

    def call(self, request: dict, timeout: float) -> dict:
        self.asked.append(request)
        if request.get("cmd") == "list":
            return self.stack_list
        if request.get("cmd") == "family":
            return FAMILY
        if request.get("cmd") != "configure":
            return {"error": "not stood in"}
        answers = request.get("answers", {})
        if answers.get("disk") not in (None, "backup-vault"):
            return {"form": {**DISK, "errors": {"disk": "That disk is not mounted."}}}
        for needed, shown in (("disk", DISK), ("passphrase", PASSPHRASE), ("time", TIME)):
            if needed not in answers:
                return {"form": shown}
        return {"ready": True}


@pytest.fixture
def api(tmp_path, monkeypatch):
    stand_in = HostAPI(listing(tmp_path, monkeypatch, ("core", "messages")))
    monkeypatch.setattr(client, "call", stand_in.call)
    return stand_in


def this_mac() -> Browser:
    import server
    return Browser(server.app, listener=PUBLIC_PORT, host="localhost:42000")


def phone() -> Browser:
    import server
    return Browser(server.app, listener=PUBLIC_PORT, host="192.168.1.20:42000")


def configured(api) -> list[dict]:
    return [r for r in api.asked if r.get("cmd") == "configure"]


# ── The relay ────────────────────────────────────────────────────────────

def test_on_this_mac_the_answers_go_to_the_host_api_as_they_are(api):
    status, answer = this_mac().posts("/dashboard/api/configure/shoebox",
                                      {"answers": {"disk": "backup-vault"}})

    assert status == 200
    assert answer["form"]["id"] == "shoebox.passphrase"
    assert configured(api) == [{"cmd": "configure", "stacklet": "shoebox",
                                "answers": {"disk": "backup-vault"}}]


def test_a_wrong_answer_comes_back_on_its_field(api):
    status, answer = this_mac().posts("/dashboard/api/configure/shoebox",
                                      {"answers": {"disk": "old-disk"}})

    assert status == 200
    assert answer["form"]["errors"] == {"disk": "That disk is not mounted."}


def test_the_last_answer_says_ready(api):
    _, answer = this_mac().posts("/dashboard/api/configure/shoebox", {"answers": {
        "disk": "backup-vault", "passphrase": "correct horse", "time": "03:00"}})

    assert answer == {"ready": True}


# ── Secrets stay on this Mac ─────────────────────────────────────────────

def test_on_a_phone_a_form_asking_for_a_secret_gives_the_terminal_command(api):
    status, answer = phone().posts("/dashboard/api/configure/shoebox",
                                   {"answers": {"disk": "backup-vault"}})

    assert status == 200
    assert "form" not in answer
    assert answer["terminal"] == "stack up shoebox"
    assert answer["message"]


def test_on_a_phone_a_secret_sent_anyway_never_reaches_the_host_api(api):
    phone().posts("/dashboard/api/configure/shoebox", {"answers": {
        "disk": "backup-vault", "passphrase": "correct horse", "time": "03:00"}})

    assert "correct horse" not in json.dumps(api.asked)


def test_on_a_phone_a_form_without_secrets_is_answered_there(api):
    _, answer = phone().posts("/dashboard/api/configure/shoebox", {"answers": {"disk": "old-disk"}})

    assert answer["form"]["id"] == "shoebox.disk"
    assert answer["form"]["errors"] == {"disk": "That disk is not mounted."}


def test_only_a_stacklet_id_is_configured(api):
    status, _ = this_mac().posts("/dashboard/api/configure/..%2Fcore", {"answers": {}})

    assert status in (400, 404)
    assert configured(api) == []


# ── The tile ─────────────────────────────────────────────────────────────

def tile(page, sid: str) -> str:
    found = re.search(rf'<article class="tile" data-id="{sid}".*?</article>', page.body, re.S)
    return found[0] if found else ""


def test_a_stacklet_whose_questions_are_forms_is_added_from_the_page(api):
    """Local AI asks whether to set up its engine, in a form: its Add opens it."""
    ai = tile(this_mac().opens("/dashboard"), "ai")

    assert 'class="tile-add"' in ai and "data-forms" in ai


def test_a_stacklet_that_asks_for_itself_keeps_its_terminal_command(api):
    for s in api.stack_list["stacklets"]:
        if s["id"] == "ai":
            s["asks_in_forms"] = False

    ai = tile(this_mac().opens("/dashboard"), "ai")

    assert 'class="tile-add"' not in ai
    assert '<code class="tile-command">stack up ai</code>' in ai


def test_opening_the_page_asks_for_no_form(api):
    this_mac().opens("/dashboard")

    assert configured(api) == []
