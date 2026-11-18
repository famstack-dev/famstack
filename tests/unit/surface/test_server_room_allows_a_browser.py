"""Allowing a waiting browser from the Server room.

A browser the home address does not know shows a code and waits. The
Server room hears of it: "A browser waits to join famstack: Chrome on a
Mac, since 21:14, code K7MP-4XQD. ... Or reply to this message with:
allow". An admin with no paired device has only the chat, so the room
allows it: a reply `allow` to that notice, or `stack devices allow
<code>`. Only an admin's message allows; a reply takes its code only from
a notice the stack's own account sent, never from a message's text.

    stack devices allow K7MP-4XQD                 Homer's device
    stack devices allow K7MP-4XQD --as marge      Marge's
    allow  /  allow as marge  /  allow shared Living room   (a reply to the notice)

The chain is the real one up to Matrix: the stacker bot as the bot runner
loads it, the host API's `devices_allow` (the dashboard's stand-in,
`tests/dashboard_host.py`, which runs `stack devices allow` on the
instance), and core's tools server on the Mac's address (`MacAddress`).
The notice is the one the tools server sent for the waiting browser,
written by `stack devices notice`. Matrix is a recording client that
also holds the room's events, for the bot to read the message a reply
answers. A last test drives the host API itself (`famstack-api.py`) with
the real `./stack` as a process of its own. Homer is the family's admin;
Marge is not.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from stacklets.core.bot.stacker import StackerBot

from .conftest import tools_server
from .dsl import Browser
from .mac_address import MacAddress
from .setup_stand_in import on_this_mac, stack_in_state
from .test_host_api_family import ask
from .test_join_notice import api_on

pytestmark = pytest.mark.usefixtures("docker_with_no_containers")

HOME = "simpson.local:42000"
SERVER = "simpson"
BOT = f"@stacker-bot:{SERVER}"
HOMER = f"@homer:{SERVER}"
MARGE = f"@marge:{SERVER}"
MAC_CHROME = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

devices = tools_server("devices")
access = tools_server("access")
server = tools_server("server")


class ServerRoom:
    """The Server room as the stacker bot sees it: what it sends is kept
    in `replies`, every message has an event id, and `room_get_event`
    answers with the message an id names, as the homeserver does."""

    def __init__(self, session_dir):
        self.replies: list[str] = []
        self.events: dict[str, SimpleNamespace] = {}
        self.bot = StackerBot(homeserver="http://x", user_id=BOT, password="x",
                              session_dir=str(session_dir))
        self.bot._client = self

    def posted(self, sender: str, content: dict) -> str:
        """A message `sender` posted earlier; its event id."""
        event_id = f"$e{len(self.events)}:{SERVER}"
        self.events[event_id] = SimpleNamespace(sender=sender, event_id=event_id,
                                                source={"content": content})
        return event_id

    async def write(self, sender: str, body: str, *, reply_to: str = "") -> list[str]:
        """`sender` writes `body`, a reply to `reply_to` if given; returns the bot's replies."""
        content = {"body": body}
        if reply_to:
            content["m.relates_to"] = {"m.in_reply_to": {"event_id": reply_to}}
        event_id = self.posted(sender, content)
        before = len(self.replies)
        await self.bot._on_message(SimpleNamespace(room_id="!server:simpson"),
                                   SimpleNamespace(body=body, sender=sender, event_id=event_id,
                                                   server_timestamp=0, source={"content": content}))
        return self.replies[before:]

    # What the bot calls on its Matrix client.
    async def room_send(self, room_id, message_type, content):
        self.replies.append(content["body"])

    async def room_get_event(self, room_id, event_id):
        return SimpleNamespace(event=self.events.get(event_id))

    async def update_receipt_marker(self, room_id, event_id):
        pass


@pytest.fixture
def host(tmp_path, monkeypatch, httpserver):
    """The Simpsons' stack with the chat running, core's tools server on
    the Mac's address, and Homer the one admin the stacker knows."""
    monkeypatch.setenv("LINK_BASE_URL", f"http://{HOME}/go")
    monkeypatch.setenv("MATRIX_SERVER_NAME", SERVER)
    monkeypatch.setenv("STACK_ADMIN_USER_IDS", "homer")
    for kept in (devices.JOINS, devices.NOTICES):
        kept.forget()
    stand_in = stack_in_state(tmp_path, monkeypatch, httpserver)("chat", mac="simpson.local")
    mac = MacAddress()
    toml = stand_in.root / "stack.toml"
    toml.write_text(toml.read_text().replace("[core]\n", f"[core]\ncards_port = {mac.port}\n", 1))
    yield stand_in
    mac.close()
    for kept in (devices.JOINS, devices.NOTICES):
        kept.forget()


@pytest.fixture
def room(tmp_path, host) -> ServerRoom:
    return ServerRoom(tmp_path / "session")


# ── Words of these tests ─────────────────────────────────────────────────

def waiting(host) -> tuple[Browser, str, dict]:
    """A laptop that opened the home address unknown, the code it shows,
    and the notice the Server room got for it."""
    laptop = Browser(server.app, listener=access.PUBLIC_PORT, host=HOME, user_agent=MAC_CHROME,
                     by_name=True, paired=False)
    page = laptop.opens("/dashboard")
    assert page.status == 401, page
    [code] = page.texts("join-code")
    notices = [p for p in host.posted if p.get("event")]
    assert notices, host.posted
    return laptop, code, notices[-1]


def notice_in(room: ServerRoom, notice: dict, *, sender: str = BOT) -> str:
    """The notice as `stack messages send --event` posts it: its text, and
    its envelope under `dev.famstack.event`. Sent as the stacker bot,
    unless a test fakes it."""
    return room.posted(sender, {"body": notice["text"], "dev.famstack.event": notice["event"]})


def who_is(device: Browser) -> str:
    """The name the dashboard's top bar shows on `device`, "" when it does not open."""
    page = device.opens("/dashboard")
    found = re.search(r'<span class="who">([^<]*)</span>', page.body)
    return found[1] if page.status == 200 and found else ""


def shared_names() -> list[str]:
    listed = on_this_mac().opens("/setup/devices")
    return [row.split(" · ", 1)[1] for row in re.findall(r"<b>([^<]+)</b>", listed.body)
            if row.startswith("Shared · ")]


def allows_asked(host) -> list[dict]:
    """The `devices allow` requests that reached the host API."""
    return [a for a in host.asked if a.get("cmd") == "devices_allow"]


# ── The command in the room ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_admin_allows_a_waiting_browser_with_the_command(room, host):
    """Given a laptop that waits, when Homer writes `stack devices allow
    <code>`, then the laptop is Homer's on its next page, and the one
    reply names the browser and whose it is now."""
    laptop, code, _ = waiting(host)

    replies = await room.write(HOMER, f"stack devices allow {code}")

    assert who_is(laptop) == "Homer"
    assert len(replies) == 1 and "Chrome on a Mac" in replies[0] and "Homer" in replies[0], replies


@pytest.mark.asyncio
async def test_the_command_takes_as_and_shared(room, host):
    """`--as marge` makes the laptop Marge's; `--shared "Living room"`
    makes a second one a shared device of that name."""
    laptop, code, _ = waiting(host)
    await room.write(HOMER, f"stack devices allow {code} --as Marge")
    assert who_is(laptop) == "Marge"

    devices.NOTICES.forget()
    screen, code, _ = waiting(host)
    await room.write(HOMER, f'stack devices allow {code} --shared "Living room"')
    assert screen.opens("/dashboard").status == 200
    assert shared_names() == ["Living room"]


@pytest.mark.asyncio
async def test_a_member_who_is_no_admin_is_refused(room, host):
    """Given Marge, who is no admin, when she writes the command, then the
    bot refuses before anything reaches the host, and the laptop waits."""
    laptop, code, _ = waiting(host)

    replies = await room.write(MARGE, f"stack devices allow {code}")

    assert len(replies) == 1
    assert allows_asked(host) == []
    assert who_is(laptop) == ""


@pytest.mark.asyncio
async def test_with_no_admins_configured_nobody_allows(room, host, monkeypatch):
    """Given no admins configured, which opens the other commands to
    everyone, `devices allow` stays closed: nothing reaches the host."""
    monkeypatch.setenv("STACK_ADMIN_USER_IDS", "")
    laptop, code, _ = waiting(host)

    await room.write(HOMER, f"stack devices allow {code}")

    assert allows_asked(host) == []
    assert who_is(laptop) == ""


@pytest.mark.asyncio
async def test_a_code_no_browser_waits_with_changes_nothing(room, host):
    """Given a laptop that waits, when Homer writes another code, then
    the reply is an error and the laptop still waits."""
    laptop, code, _ = waiting(host)

    replies = await room.write(HOMER, "stack devices allow 2345-6789")

    assert len(replies) == 1
    assert who_is(laptop) == ""


# ── A reply to the notice ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_reply_allow_to_the_notice_allows_its_browser_for_the_admin(room, host):
    """Given the Server room's notice of a waiting laptop, when Homer
    replies `allow` to it, then the laptop is Homer's: the code came from
    the notice."""
    laptop, code, notice = waiting(host)
    notice_id = notice_in(room, notice)

    replies = await room.write(HOMER, "allow", reply_to=notice_id)

    assert who_is(laptop) == "Homer"
    assert [a["code"] for a in allows_asked(host)] == [code.replace("-", "")]
    assert len(replies) == 1 and "Homer" in replies[0], replies


@pytest.mark.asyncio
@pytest.mark.parametrize("reply, person", [("allow as marge", "Marge"),
                                           ("Allow as Marge", "Marge")])
async def test_a_reply_allow_as_names_the_person(room, host, reply, person):
    laptop, _, notice = waiting(host)

    await room.write(HOMER, reply, reply_to=notice_in(room, notice))

    assert who_is(laptop) == person


@pytest.mark.asyncio
async def test_a_reply_allow_shared_makes_a_shared_device(room, host):
    laptop, _, notice = waiting(host)

    await room.write(HOMER, "allow shared Living room", reply_to=notice_in(room, notice))

    assert laptop.opens("/dashboard").status == 200
    assert shared_names() == ["Living room"]


@pytest.mark.asyncio
async def test_a_reply_allow_to_a_fake_notice_allows_nothing(room, host):
    """Given Marge posts a copy of the notice herself, text and envelope,
    when Homer replies `allow` to her message, then the bot refuses and
    nothing reaches the host: only the stack's own notice gives a code."""
    laptop, _, notice = waiting(host)
    fake = notice_in(room, notice, sender=MARGE)

    replies = await room.write(HOMER, "allow", reply_to=fake)

    assert len(replies) == 1
    assert allows_asked(host) == []
    assert who_is(laptop) == ""


@pytest.mark.asyncio
async def test_a_reply_allow_to_another_message_of_the_bot_allows_nothing(room, host):
    """Given a message of the bot that is no waiting notice, even one
    whose text names the code, a reply `allow` to it allows nothing."""
    laptop, code, _ = waiting(host)
    other = room.posted(BOT, {"body": f"Done. code {code}"})

    replies = await room.write(HOMER, "allow", reply_to=other)

    assert len(replies) == 1
    assert allows_asked(host) == []
    assert who_is(laptop) == ""


@pytest.mark.asyncio
async def test_a_reply_allow_from_a_member_who_is_no_admin_is_refused(room, host):
    laptop, _, notice = waiting(host)

    await room.write(MARGE, "allow", reply_to=notice_in(room, notice))

    assert allows_asked(host) == []
    assert who_is(laptop) == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("sender, word", [(HOMER, "allow"), (MARGE, "dismiss")])
async def test_allow_or_dismiss_that_replies_to_nothing_is_ordinary_chat(room, host, sender, word):
    """A bare `allow` or `dismiss` that answers no message is chat: no
    reply from the bot, nothing reaches the host, the laptop waits."""
    laptop, _, _ = waiting(host)

    replies = await room.write(sender, word)

    assert replies == []
    assert [a for a in host.asked if a.get("cmd", "").startswith("devices_")] == []
    assert who_is(laptop) == ""


@pytest.mark.asyncio
async def test_ordinary_chat_that_starts_with_another_word_is_ignored(room, host):
    """A message that is no command and no `allow` gets no reply."""
    assert await room.write(HOMER, "allowance for Bart is due") == []


# ── Dismiss ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_reply_dismiss_to_the_notice_ends_the_request(room, host):
    """Given the notice of a waiting laptop, when Homer replies `dismiss`
    to it, then the room says the request ended, the laptop is not let
    in, and the notice's code allows nothing any more."""
    laptop, code, notice = waiting(host)
    notice_id = notice_in(room, notice)

    replies = await room.write(HOMER, "dismiss", reply_to=notice_id)

    assert len(replies) == 1 and code in replies[0], replies
    assert who_is(laptop) == ""
    await room.write(HOMER, "allow", reply_to=notice_id)
    assert who_is(laptop) == ""


@pytest.mark.asyncio
async def test_the_dismiss_command_ends_the_request(room, host):
    laptop, code, _ = waiting(host)

    await room.write(HOMER, f"stack devices dismiss {code}")
    await room.write(HOMER, f"stack devices allow {code}")

    assert who_is(laptop) == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("sender, fake", [(MARGE, False), (HOMER, True)])
async def test_dismiss_from_no_admin_or_to_a_fake_notice_ends_nothing(room, host, sender, fake):
    """Marge, who is no admin, replying `dismiss` to the notice, and Homer
    replying `dismiss` to Marge's copy of it, both end nothing: the same
    rules as `allow`."""
    laptop, code, notice = waiting(host)
    replied = notice_in(room, notice, sender=MARGE if fake else BOT)

    await room.write(sender, "dismiss", reply_to=replied)

    assert [a for a in host.asked if a.get("cmd") == "devices_dismiss"] == []
    await room.write(HOMER, f"stack devices allow {code}")
    assert who_is(laptop) == "Homer"


# ── The host API ─────────────────────────────────────────────────────────

def test_the_host_api_runs_the_command_that_allows(host, tmp_path, monkeypatch):
    """Given a laptop that waits, when the host API gets `devices_allow`
    with its code and a person, then `./stack devices allow` runs as a
    process of its own and the laptop is that person's. A field that reads
    as an option never reaches the command line."""
    api = api_on(tmp_path, host.root, monkeypatch)
    laptop, code, _ = waiting(host)

    assert "error" in ask(api, {"cmd": "devices_allow", "code": code, "as": "--shared"})
    reply = ask(api, {"cmd": "devices_allow", "code": code, "as": "marge"})

    assert reply.get("person") == "marge", reply
    assert who_is(laptop) == "Marge"


def test_the_host_api_runs_the_command_that_dismisses(host, tmp_path, monkeypatch):
    """`devices_dismiss` runs `./stack devices dismiss <code>`: the request
    ends and its code allows nothing afterwards. A code that reads as an
    option never reaches the command line."""
    api = api_on(tmp_path, host.root, monkeypatch)
    laptop, code, _ = waiting(host)

    assert "error" in ask(api, {"cmd": "devices_dismiss", "code": "--help"})
    assert ask(api, {"cmd": "devices_dismiss", "code": code}).get("ok") is True

    assert "error" in ask(api, {"cmd": "devices_allow", "code": code, "as": "homer"})
    assert who_is(laptop) == ""
