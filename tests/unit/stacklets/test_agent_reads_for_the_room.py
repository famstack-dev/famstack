"""What Stacky may read in a room: only what everyone in the room may read.

Stacky answers in a chat with one person and in rooms with the whole family.
A note in a person's own folder (`marge/notes/...`) is written for that
person alone (`stack.cards.audience.readable`). So Stacky may read it only in
that person's own chat, and never in a room with anyone else in it.

These tests state that from the person's side, whatever the mechanism. A
person asks Stacky in a room. Stacky reads through its memory tools
(`memory_search`) or by running a `stack memory ...` line with exec. The
read passes the host API's allowlist and runs on a fabricated memory (the
Simpsons, `running_memory`) holding Marge's private note, Bart's private
note and one shared note. The test then checks what the answer holds.

Rows (the first four are the card's behavior, the fifth is the admin's):

  1. Bart's chat: Marge's private note is in no answer.      leaks today
  2. The family room: no person's own note, shared cards yes. leaks today
  3. Marge's own chat: her private note is found.             holds, a pin
  4. A room or people unknown: refused, never the admin view. leaks today
  5. The admin's own `stack memory find` on the Mac sees all. holds, a pin

Today Stacky's reads carry no audience, so rows 1, 2 and 4 are strict xfail
under one marker, `STACKY_IGNORES_THE_ROOM`. The change that gives every read
the room's people removes the marker, and the rows then pass.

The one seam: `Room` is how a test says who is in the room. A turn today
carries the sender and the room's key (`turn.py`) and nothing about who else
is there, so `Stacky.in_room` puts the people on the request context as
`room_people`. The change that learns the room's people from Matrix adapts
`in_room` and `host_api` (the host API's side of the shim's wire) and keeps
every row.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import importlib.machinery
import importlib.util
import io
import os
import shlex
import subprocess
import sys
import types
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pytest

from tests.loopback import serving
from tests.memory.unit.instance import running_memory

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# The marker of every row where today's reads leak. Strict, so a row that
# starts to hold fails until its marker goes.
STACKY_IGNORES_THE_ROOM = pytest.mark.xfail(
    strict=True,
    reason="Stacky's reads carry no audience: a room's people do not narrow what it finds",
)

TOOL_MODULES = ("memory_tool", "person_tool", "history_tool", "list_tool", "calendar_tool",
                "sitecustomize", "turn")

RECENT = (date.today() - timedelta(days=2)).isoformat()


# ── the people and the rooms ─────────────────────────────────────────

@dataclass(frozen=True)
class Room:
    """A room Stacky answers in: its key, who is in it, and who is asking."""

    key: str
    people: tuple[str, ...]
    asker: str


BARTS_CHAT = Room("!bart-chat:simpson", ("bart",), "bart")
MARGES_CHAT = Room("!marge-chat:simpson", ("marge",), "marge")
FAMILY_ROOM = Room("!family:simpson", ("homer", "marge", "bart", "lisa"), "marge")
# Nobody knows who is in it: the room's people could not be told.
UNKNOWN_PEOPLE = Room("!somewhere:simpson", (), "bart")


def _note(capture_id: str, title: str, body: str) -> str:
    return (f"---\ntype: note\ntitle: {title}\ndate: \"{RECENT}\"\n"
            f"capture_id: \"${capture_id}\"\n---\n{body}\n")


# Marge's note links to the shared tent note, so the tent note's links hold her card.
MARGES_NOTE = _note("Marge000000001", "Gift for the bowling night",
                    "Marge hides a gift for Homer under the stairs. "
                    "See [the tent](../../family/notes/tent.md).")
BARTS_NOTE = _note("Bart0000000001", "Gift ramp plan", "Bart builds a gift ramp for Lisa.")
SHARED_NOTE = _note("Family0000001", "Camping tent", "The tent for four is in the garage.")

SEED = {"marge/notes/gift.md": MARGES_NOTE,
        "bart/notes/gift.md": BARTS_NOTE,
        "family/notes/tent.md": SHARED_NOTE}

# What each card shows of itself in any answer: its id, its path or its title.
MARGES_CARD = ("Marge00000", "marge/notes", "bowling night")
BARTS_CARD = ("Bart000000", "bart/notes", "ramp plan")
SHARED_CARD = ("Family0000", "family/notes", "Camping tent")


def shows(answer: str, card: tuple[str, ...]) -> bool:
    """Whether the answer names the card, by its id, its path or its title."""
    return any(mark in answer for mark in card)


# ── the components Stacky's reads pass through ───────────────────────

def _load_from_path(name: str, path: Path):
    """Import a script by its path: `famstack-api.py` has a hyphen and the
    client shim has no suffix, so neither imports by name."""
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def api():
    """The host API module whose allowlist gates every command the agent runs."""
    return _load_from_path("famstack_api_for_the_room",
                           REPO_ROOT / "stacklets" / "core" / "famstack-api.py")


@pytest.fixture(scope="module")
def shim():
    """The container-side `stack` shim, the agent's only route to the CLI."""
    return _load_from_path("stack_shim_for_the_room",
                           REPO_ROOT / "stacklets" / "agent" / "client" / "stack")


@pytest.fixture
def memory(tmp_path):
    """The fabricated memory: Marge's and Bart's own notes and one shared note."""
    with running_memory(tmp_path, SEED) as running:
        yield running


@pytest.fixture
def tools(monkeypatch, nanobot_stub):
    """Stacky's memory search tool, imported against a stub nanobot."""
    for name, module in nanobot_stub().items():
        monkeypatch.setitem(sys.modules, name, module)
    for name in TOOL_MODULES:
        monkeypatch.delitem(sys.modules, name, raising=False)
    yield importlib.import_module("memory_tool").MemorySearchTool
    for name in TOOL_MODULES:
        sys.modules.pop(name, None)


# ── Stacky, as a person meets it ─────────────────────────────────────

class Stacky:
    """Stacky in one room, reading the memory as its runtime does.

        stacky = Stacky(tools, bin_dir, port, monkeypatch)
        stacky.in_room(BARTS_CHAT)
        stacky.searches("gift")            # the memory_search tool's answer
        stacky.execs("memory find gift")   # a line the model runs with exec

    Both reads start a real `stack` process: the container-side shim itself,
    on `PATH`, talking to `host_api` below instead of the host's API.
    """

    def __init__(self, search_tool, bin_dir: Path, port: int, monkeypatch):
        self.search_tool = search_tool
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setenv("STACK_API_ADDR", f"127.0.0.1:{port}")
        monkeypatch.delenv("STACK_API_TOKEN", raising=False)
        self.in_room(None)

    def in_room(self, room: Room | None) -> "Stacky":
        """The next reads happen in `room`; None is a read outside any turn.

        Sets what nanobot's request context answers, on the stub nanobot a
        fixture builds for this one test."""
        context = sys.modules["nanobot.agent.tools.context"]
        request = None if room is None else types.SimpleNamespace(
            turn_id="t1", sender_id=f"@{room.asker}:simpson", session_key=f"matrix:{room.key}",
            chat_id=room.key, room_people=room.people)
        context.current_request_context = lambda: request
        return self

    def searches(self, query: str) -> str:
        """What the `memory_search` tool answers to `query`, as the model reads it."""
        return asyncio.run(self.search_tool().execute(query=query))

    def execs(self, line: str) -> str:
        """What exec prints for `stack <line>`."""
        done = subprocess.run(["stack", *shlex.split(line)], capture_output=True, text=True,
                              timeout=60)
        return done.stdout + done.stderr


def host_api(memory, api, shim):
    """The host API's side of the shim's wire, as a handler for `serving`.

    It does what `handle_plaintext` does for the agent: read the line, refuse
    what `DOMAIN_ALLOW` does not list, run the rest, answer with the text and
    a status line on failure. It runs the command on the test's memory, not
    through the host's `./stack` (which would reach whatever runs on this Mac).
    """
    def handle(conn) -> None:
        line = b""
        while not line.endswith(b"\n"):
            chunk = conn.recv(4096)
            if not chunk:
                break
            line += chunk
        args = shlex.split(line.decode())
        if any(args[:len(p)] == p for p in api.DOMAIN_ALLOW):
            code, text = run_command(memory, args)
        else:
            code, text = 126, f"error: '{' '.join(args[:2])}' is not allowed\n"
        if code:
            text = f"{text.rstrip(chr(10))}\n{shim.EXIT_MARKER} {code}\n"
        conn.sendall(text.encode())
    return handle


def run_command(memory, args: list[str]) -> tuple[int, str]:
    """`./stack <args>` on the memory's instance: its exit code and what it printed."""
    printed = io.StringIO()
    code, answer = 0, {}
    with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(printed):
        try:
            answer = memory.stack.run_cli_command(args[0], args[1], args[2:]) or {}
        except SystemExit as stop:
            code = stop.code if isinstance(stop.code, int) else 1
    if "error" in answer:
        code = code or 1
    return code, printed.getvalue() + str(answer.get("error", ""))


@pytest.fixture
def stacky(memory, api, shim, tools, tmp_path, monkeypatch):
    """Stacky, with a real `stack` shim on `PATH` that talks to `host_api`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "stack").symlink_to(REPO_ROOT / "stacklets" / "agent" / "client" / "stack")
    with serving(host_api(memory, api, shim)) as port:
        yield Stacky(tools, bin_dir, port, monkeypatch)


# Every way Stacky reads a card of the memory, as a function of Stacky.
# `memory_search` is the tool, `memory search` and `memory find` the lines the
# model runs with exec. `card` and `links` have their own tests.
READS_BY_WORDS = {
    "the memory_search tool": lambda s, words: s.searches(words),
    "exec memory search": lambda s, words: s.execs(f"memory search {words}"),
    "exec memory find": lambda s, words: s.execs(f"memory find {words}"),
}


# ── 1. Bart's chat ───────────────────────────────────────────────────

@pytest.mark.parametrize("read", READS_BY_WORDS)
@STACKY_IGNORES_THE_ROOM
def test_in_barts_chat_marges_private_note_is_found_by_no_search(stacky, read):
    """Given Marge's private note and Bart alone with Stacky, when Bart's words
    match it, then the answer does not show it.

    Why it matters: the note is Marge's gift for Homer, written for Marge alone.
    """
    answer = READS_BY_WORDS[read](stacky.in_room(BARTS_CHAT), "gift")

    assert not shows(answer, MARGES_CARD)


@STACKY_IGNORES_THE_ROOM
def test_in_barts_chat_marges_private_note_is_not_opened_by_its_id(stacky):
    """Given Bart knows the id of Marge's card, when Stacky opens it for him,
    then the card's text is not in the answer."""
    answer = stacky.in_room(BARTS_CHAT).execs("memory card capture/Marge00000")

    assert "gift for Homer" not in answer


@STACKY_IGNORES_THE_ROOM
def test_in_barts_chat_a_link_does_not_lead_to_marges_private_note(stacky):
    """Given the shared tent note is linked from Marge's note, when Stacky
    follows its links in Bart's chat, then Marge's card is not among them."""
    answer = stacky.in_room(BARTS_CHAT).execs("memory links capture/Family0000")

    assert not shows(answer, MARGES_CARD)


def test_in_barts_chat_his_own_note_and_the_shared_one_are_found(stacky):
    """Given Bart alone with Stacky, then his own note and the shared tent note
    are found: narrowing the read must not make Stacky blind."""
    stacky.in_room(BARTS_CHAT)

    assert shows(stacky.searches("gift"), BARTS_CARD)
    assert shows(stacky.execs("memory find tent"), SHARED_CARD)


# ── 2. The family room ───────────────────────────────────────────────

@pytest.mark.parametrize("read", READS_BY_WORDS)
@STACKY_IGNORES_THE_ROOM
def test_in_the_family_room_no_persons_own_note_is_found(stacky, read):
    """Given the family room (Marge asks, with Homer, Bart and Lisa in it), when
    the words match her own note and Bart's, then neither is in the answer.

    Why it matters: Marge's own note is hers alone, and the room reads the answer.
    """
    answer = READS_BY_WORDS[read](stacky.in_room(FAMILY_ROOM), "gift")

    assert not shows(answer, MARGES_CARD) and not shows(answer, BARTS_CARD)


@pytest.mark.parametrize("read", READS_BY_WORDS)
def test_in_the_family_room_the_shared_cards_are_found(stacky, read):
    """Given the family room, then a shared card is found as always."""
    answer = READS_BY_WORDS[read](stacky.in_room(FAMILY_ROOM), "tent")

    assert shows(answer, SHARED_CARD)


# ── 3. Marge's own chat ──────────────────────────────────────────────

@pytest.mark.parametrize("read", READS_BY_WORDS)
def test_in_her_own_chat_marge_finds_her_private_note(stacky, read):
    """Given Marge alone with Stacky, then her private note is found.

    A pin: the narrowing of the other rooms must leave her own chat as it is.
    """
    answer = READS_BY_WORDS[read](stacky.in_room(MARGES_CHAT), "gift")

    assert shows(answer, MARGES_CARD)


def test_in_her_own_chat_marge_opens_her_card_by_its_id(stacky):
    """Given Marge alone with Stacky, then opening her card by its id shows its text."""
    answer = stacky.in_room(MARGES_CHAT).execs("memory card capture/Marge00000")

    assert "gift for Homer" in answer


def test_in_her_own_chat_the_links_of_the_shared_tent_lead_to_her_card(stacky):
    """Given Marge alone with Stacky, then the links of the shared tent note,
    which her note links to, name her card."""
    answer = stacky.in_room(MARGES_CHAT).execs("memory links capture/Family0000")

    assert shows(answer, MARGES_CARD)


# ── 4. A room or people unknown ──────────────────────────────────────

UNKNOWN = {"outside any turn": None, "a room whose people are not known": UNKNOWN_PEOPLE}


@pytest.mark.parametrize("room", UNKNOWN)
@pytest.mark.parametrize("read", READS_BY_WORDS)
@STACKY_IGNORES_THE_ROOM
def test_a_read_with_no_known_people_is_refused_not_answered_from_the_admins_view(
        stacky, read, room):
    """Given a read where nobody can say who is in the room, then no private note
    is in the answer: the read is refused, it does not fall back to the admin's
    view that reads every card."""
    answer = READS_BY_WORDS[read](stacky.in_room(UNKNOWN[room]), "gift")

    assert not shows(answer, MARGES_CARD) and not shows(answer, BARTS_CARD)


# ── 5. The admin's own view ──────────────────────────────────────────

def test_the_admins_own_find_on_the_mac_still_sees_every_card(memory):
    """Given the admin runs `stack memory find` on the Mac, with no audience,
    then every card is found, private notes included.

    A pin: the narrowing is for Stacky's reads, not for the admin's terminal.
    """
    _, answer = run_command(memory, ["memory", "find", "gift"])

    assert shows(answer, MARGES_CARD) and shows(answer, BARTS_CARD)
