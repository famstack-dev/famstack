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

  1. Bart's chat: Marge's private note is in no answer.
  2. The family room: no person's own note, shared cards yes.
  3. Marge's own chat: her private note is found.
  4. A room or people unknown: refused, never the admin view.
  5. The admin's own `stack memory find` on the Mac sees all.
  6. A person's page: read only in that person's own chat.
  7. An audience the shell names itself (its environment, its own line, a
     changed or expired signature): nothing of Marge's in Bart's chat.
  8. The history of a person's folder: shown only in that person's own chat.
  9. A list in a person's folder: no list verb names its items elsewhere.

A last row pins a known limit, strict xfail: a shell that reads the
runtime's own environment holds its key, and signs any people with it.

The one seam: `Room` is how a test says who is in the room. A message
carries the people in its room in its metadata (`room_people.py`, filled
from the Matrix client's members), and nanobot copies that metadata into the
request context its tools run in. `Stacky.in_room` sets that request
context. Exec's environment is the one nanobot's `ExecTool` builds under the
runtime's shims, which sign the room's people with the runtime's key
(`RUNTIME_KEY`), and `host_api` decides each line with the host API's own
`agent_command`, checking with the same key.
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
import socket
import subprocess
import time
import sys
import types
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pytest

from tests.loopback import serving
from tests.memory.unit.instance import running_memory

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

RECENT = (date.today() - timedelta(days=2)).isoformat()


# The key the runtime signs a room's people with, as `stack up agent` makes
# it; the host API checks each memory read against it.
RUNTIME_KEY = "runtime-audience-key-5c1e"


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

# Marge's page, in her own folder: what `memory_person` reads.
MARGES_PAGE = ("---\ntitle: Marge\nslug: marge\ncanonical: Marge\n---\n\n"
               "# Marge\n\nKeeps the spare key in the blue vase.\n")

SEED = {"marge/notes/gift.md": MARGES_NOTE,
        "bart/notes/gift.md": BARTS_NOTE,
        "family/notes/tent.md": SHARED_NOTE,
        "marge/about.md": MARGES_PAGE}

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
def runtime(nanobot):
    """Stacky's runtime: the stub nanobot with every shim of `sitecustomize` on it."""
    return nanobot()


# ── Stacky, as a person meets it ─────────────────────────────────────

class Stacky:
    """Stacky in one room, reading the memory as its runtime does.

        stacky = Stacky(runtime, bin_dir, port, monkeypatch)
        stacky.in_room(BARTS_CHAT)
        stacky.searches("gift")            # the memory_search tool's answer
        stacky.reads_page("marge")         # the memory_person tool's answer
        stacky.execs("memory find gift")   # a line the model runs with exec

    Every read starts a real `stack` process: the container-side shim itself,
    on `PATH`, talking to `host_api` below instead of the host's API.
    """

    def __init__(self, runtime, bin_dir: Path, port: int, monkeypatch):
        self.runtime, self.port = runtime, port
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setenv("STACK_API_ADDR", f"127.0.0.1:{port}")
        monkeypatch.delenv("STACK_API_TOKEN", raising=False)
        monkeypatch.setenv("STACK_AUDIENCE_KEY", RUNTIME_KEY)
        # The address is the test's own port; in the container the shim's default.
        runtime["nanobot.agent.tools.shell"].ExecTool.allowed_env_keys = ["PATH", "STACK_API_ADDR"]
        self.in_room(None)

    def in_room(self, room: Room | None) -> "Stacky":
        """The next reads happen in `room`; None is a read outside any turn.

        Sets what nanobot's request context answers, on the stub nanobot a
        fixture builds for this one test: the message's metadata carries the
        room's people as the Matrix shim puts them there, none when nobody
        knows who is in the room."""
        context = sys.modules["nanobot.agent.tools.context"]
        request = None if room is None else types.SimpleNamespace(
            turn_id="t1", sender_id=f"@{room.asker}:simpson", session_key=f"matrix:{room.key}",
            chat_id=room.key,
            metadata={"room_people": list(room.people)} if room.people else {})
        context.current_request_context = lambda: request
        return self

    def searches(self, query: str) -> str:
        """What the `memory_search` tool answers to `query`, as the model reads it."""
        return asyncio.run(sys.modules["memory_tool"].MemorySearchTool().execute(query=query))

    def reads_page(self, name: str) -> str:
        """What the `memory_person` tool answers for `name`."""
        return asyncio.run(sys.modules["person_tool"].MemoryPersonTool().execute(name=name))

    def execs(self, line: str, **env: str) -> str:
        """What exec prints for `stack <line>`, run in the environment nanobot's
        exec tool builds for the turn; `env` is what the line sets itself
        (`STACK_AUDIENCE=marge stack ...`)."""
        done = subprocess.run(["stack", *shlex.split(line)], capture_output=True, text=True,
                              timeout=60, env={**self.exec_env(), **env})
        return done.stdout + done.stderr

    def exec_env(self) -> dict[str, str]:
        """The environment nanobot's exec tool builds for a command of the turn."""
        return self.runtime["nanobot.agent.tools.shell"].ExecTool()._build_env()

    def writes_line(self, line: str) -> str:
        """What the API answers to `line`, written to its port by a shell
        itself, past the shim, as any program holding the exec token can."""
        with socket.create_connection(("127.0.0.1", self.port), timeout=60) as conn:
            conn.sendall(f"{line}\n".encode())
            conn.shutdown(socket.SHUT_WR)
            return b"".join(iter(lambda: conn.recv(4096), b"")).decode()

    def asks_history(self, scope: str) -> str:
        """What the `memory_history` tool answers for `scope`."""
        return asyncio.run(sys.modules["history_tool"].MemoryHistoryTool().execute(scope=scope))

    def edits_list(self, op: str, items: list[str], list_: str | None = None) -> str:
        """What the `list_edit` tool answers to `op` on `items` of `list_`."""
        return asyncio.run(sys.modules["list_tool"].ListEditTool().execute(
            op=op, list=list_, items=items))


def host_api(memory, api, shim):
    """The host API's side of the shim's wire, as a handler for `serving`.

    It does what `handle_plaintext` does for the agent: the API's own
    `agent_command` turns the line into the command or a refusal, then the
    command runs and the answer is the text, with a status line on failure.
    It runs the command on the test's memory, not through the host's
    `./stack` (which would reach whatever runs on this Mac).
    """
    def handle(conn) -> None:
        line = b""
        while not line.endswith(b"\n"):
            chunk = conn.recv(4096)
            if not chunk:
                break
            line += chunk
        args, refusal = api.agent_command(line.decode(), api.DOMAIN_ALLOW, RUNTIME_KEY)
        if refusal:
            conn.sendall(refusal.encode())
            return
        code, text = run_command(memory, args)
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
def stacky(memory, api, shim, runtime, tmp_path, monkeypatch):
    """Stacky, with a real `stack` shim on `PATH` that talks to `host_api`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "stack").symlink_to(REPO_ROOT / "stacklets" / "agent" / "client" / "stack")
    with serving(host_api(memory, api, shim)) as port:
        yield Stacky(runtime, bin_dir, port, monkeypatch)


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
def test_in_barts_chat_marges_private_note_is_found_by_no_search(stacky, read):
    """Given Marge's private note and Bart alone with Stacky, when Bart's words
    match it, then the answer does not show it.

    Why it matters: the note is Marge's gift for Homer, written for Marge alone.
    """
    answer = READS_BY_WORDS[read](stacky.in_room(BARTS_CHAT), "gift")

    assert not shows(answer, MARGES_CARD)


def test_in_barts_chat_marges_private_note_is_not_opened_by_its_id(stacky):
    """Given Bart knows the id of Marge's card, when Stacky opens it for him,
    then the card's text is not in the answer."""
    answer = stacky.in_room(BARTS_CHAT).execs("memory card capture/Marge00000")

    assert "gift for Homer" not in answer


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


# ── 6. A person's page ───────────────────────────────────────────────

NOT_HERS_ALONE = {"Bart's chat": BARTS_CHAT, "the family room": FAMILY_ROOM,
                  "nobody known": UNKNOWN_PEOPLE}


@pytest.mark.parametrize("room", NOT_HERS_ALONE)
def test_marges_page_is_not_read_where_anyone_else_may_listen(stacky, room):
    """Given Marge's page, compiled from her own notes too, when Stacky reads it
    in a room where she is not alone, then its text is not in the answer."""
    answer = stacky.in_room(NOT_HERS_ALONE[room]).reads_page("marge")

    assert "blue vase" not in answer


def test_in_her_own_chat_marge_reads_her_page(stacky):
    answer = stacky.in_room(MARGES_CHAT).reads_page("marge")

    assert "blue vase" in answer



# ── 7. An audience the shell makes up ───────────────────────────────

def _forged_signature(stacky) -> str:
    """The audience Bart's turn gives a command, with Marge as its people."""
    given = stacky.in_room(BARTS_CHAT).exec_env().get("STACK_AUDIENCE", "")
    return "marge" + given[len("bart"):]


def _expired_signature() -> str:
    """Marge's audience as the runtime signs it, signed long enough ago to be over."""
    from stack.agent_audience import sign
    return sign(["marge"], RUNTIME_KEY, now=time.time() - 3600)


FORGED = {
    "STACK_AUDIENCE=marge":
        lambda s: s.execs("memory find gift", STACK_AUDIENCE="marge"),
    "the audience word on the shim's line":
        lambda s: s.execs("audience marge memory find gift", STACK_AUDIENCE=""),
    "a raw line to the API's port":
        lambda s: s.writes_line("audience marge memory find gift"),
    "Bart's signature with Marge as its people":
        lambda s: s.execs("memory find gift", STACK_AUDIENCE=_forged_signature(s)),
    "Marge's signature, expired":
        lambda s: s.execs("memory find gift", STACK_AUDIENCE=_expired_signature()),
}


@pytest.mark.parametrize("forge", FORGED)
def test_in_barts_chat_an_audience_the_shell_names_reads_nothing_of_marges(stacky, forge):
    """Given Bart's chat, when a command names Marge as its audience in any way
    the shell can (its environment, its own line, a signature it changed or
    kept), then nothing of Marge's is in the answer: the audience is the one
    the runtime signed for the room."""
    answer = FORGED[forge](stacky.in_room(BARTS_CHAT))

    assert not shows(answer, MARGES_CARD)


# ── 8. What changed in a person's folder ─────────────────────────────

@pytest.fixture
def marge_wrapped_it(memory):
    """Marge's own change to her private note, in Forgejo, named in its subject."""
    memory.edited_in_forgejo({"marge/notes/gift.md": MARGES_NOTE + "Wrapped in blue paper.\n"},
                             by="marge", subject="Gift for the bowling night: wrapped")


HISTORY = {
    "the memory_history tool": lambda s: s.asks_history("marge"),
    "exec memory history": lambda s: s.execs("memory history marge"),
}


@pytest.mark.parametrize("read", HISTORY)
def test_in_barts_chat_the_history_of_marges_folder_shows_nothing_of_hers(
        stacky, marge_wrapped_it, read):
    answer = HISTORY[read](stacky.in_room(BARTS_CHAT))

    assert not shows(answer, MARGES_CARD)


@pytest.mark.parametrize("read", HISTORY)
def test_in_her_own_chat_marge_sees_what_changed_in_her_folder(stacky, marge_wrapped_it, read):
    answer = HISTORY[read](stacky.in_room(MARGES_CHAT))

    assert shows(answer, MARGES_CARD)


# ── 9. A list in a person's folder ───────────────────────────────────

@pytest.fixture
def marges_list(memory):
    """Marge's own list, in her folder, made in Forgejo, beside a shared one:
    a path can only leave a folder that is there (`family/lists/..`)."""
    memory.edited_in_forgejo({"marge/lists/geschenke.md":
                              "---\ntype: list\ntitle: Geschenke\n---\n\n- [ ] Bowling ball for Homer\n",
                              "family/lists/einkauf.md":
                              "---\ntype: list\ntitle: Einkauf\n---\n\n- [ ] Milk\n"},
                             by="marge", subject="a list of her own")


LIST_VERBS = {
    "exec list tick on her list": lambda s: s.execs("memory list tick geschenke zzz --as bart"),
    "exec list tick on no list named": lambda s: s.execs("memory list tick Bowling --as bart"),
    "the list_edit tool": lambda s: s.edits_list("tick", ["zzz"], "geschenke"),
    # `*/lists/<slug>.md` is the glob: `../marge/geschenke` names no file,
    # `../../marge/lists/geschenke` names hers through `family/lists/`.
    # `tick` takes its first word as a list only when it is a list's name,
    # so a path there is read as an item.
    "exec list show through the shared folder":
        lambda s: s.execs("memory list show ../marge/geschenke"),
    "exec list tick through the shared folder":
        lambda s: s.execs("memory list tick ../marge/geschenke zzz --as bart"),
    "exec list show through a list folder to hers":
        lambda s: s.execs("memory list show ../../marge/lists/geschenke"),
    "exec list tick through a list folder to hers":
        lambda s: s.execs("memory list tick ../../marge/lists/geschenke zzz --as bart"),
}


@pytest.mark.parametrize("verb", LIST_VERBS)
def test_in_barts_chat_no_list_verb_shows_marges_list(stacky, marges_list, verb):
    """Given Marge's own list, when a list verb in Bart's chat names it or
    looks for an item, then its title and items are in no answer, also not
    in a refusal that names the lists or items to choose from."""
    answer = LIST_VERBS[verb](stacky.in_room(BARTS_CHAT))

    assert "for Homer" not in answer and "Geschenke" not in answer


def test_in_barts_chat_a_write_through_the_shared_folder_into_marges_is_refused(
        stacky, memory):
    """Given a page named as a path that leaves the shared folder for
    Marge's, when Bart's chat writes it, then it is refused and nothing is
    written."""
    before = memory.new_commits()
    answer = stacky.in_room(BARTS_CHAT).execs(
        "memory write family/../marge/notes/x.md --by bart")

    assert "read only in" in answer
    assert memory.new_commits() == before


# ── A known limit ────────────────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason=(
    "the exec shell runs as the runtime's user: it can read the key from the runtime's "
    "environment (/proc/1/environ) and sign with agent_audience.sign, which is on PYTHONPATH"))
def test_a_shell_that_reads_the_runtimes_environment_reads_nothing_of_marges(stacky):
    """Given Bart's chat, when the shell takes the key from the runtime's own
    environment and signs Marge as the audience, then it should read nothing
    of hers. It does today: this row turns green, and fails as strict, the
    day the memory tools run where the shell cannot reach them."""
    from stack.agent_audience import sign
    key = os.environ["STACK_AUDIENCE_KEY"]   # what /proc/1/environ holds in the container
    answer = stacky.in_room(BARTS_CHAT).execs(
        "memory find gift", STACK_AUDIENCE=sign(["marge"], key, now=time.time()))

    assert not shows(answer, MARGES_CARD)
