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
 10. No memory file in the container: no mount brings one in, the skill
     names no path into the memory, and a path the model writes reaches
     nothing of Marge's.
 11. The brief: it knows the speaker's profile and the room's topic, read
     for the room's people.
 12. What is said in one chat stays in that chat: no tool of Bart's chat
     (read_file, list_dir, grep, find_files, exec, message) returns a word
     of Marge's own chat, of what an earlier release kept for every chat,
     or of the runtime's key; no write leaves her words where another chat
     reads them; and every `stack` line Stacky is told to run still runs.
     These rows drive nanobot's real tools (`nanobot_agent.py`).

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
import importlib
import importlib.machinery
import importlib.util
import os
import shlex
import socket
import subprocess
import threading
import time
import sys
import types
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pytest

from tests.loopback import serving
from tests.memory.unit.instance import running_memory
from stack.agent_audience import AUDIENCE_KEY, sign
from stack.agent_confirm import CONFIRM_ENV, confirm_delete
from tests.unit.stacklets.nanobot_agent import RUNTIME_KEY as AGENT_PROCESS_KEY
from tests.unit.stacklets.nanobot_agent import STACK_RAN, fabricated_home, running_agent

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
    label: str = ""


BARTS_CHAT = Room("!bart-chat:simpson", ("bart",), "bart")
MARGES_CHAT = Room("!marge-chat:simpson", ("marge",), "marge")
FAMILY_ROOM = Room("!family:simpson", ("homer", "marge", "bart", "lisa"), "marge", "Simpsons")
# The family's topic room, as the archivist names one.
CAMPING_ROOM = Room("!camping:simpson", ("homer", "marge", "bart", "lisa"), "marge",
                    "Thema: Camping")
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

# Marge's page, in her own folder: what `memory_person` reads. Its tagline
# is what the brief takes as her essence.
MARGES_PAGE = ("---\ntitle: Marge\nslug: marge\ncanonical: Marge\n---\n\n"
               "# Marge\n\n> Keeps the spare key in the blue vase.\n")

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
            metadata={"room": room.label or room.key,
                      **({"room_people": list(room.people)} if room.people else {})})
        context.current_request_context = lambda: request
        self.request = request
        return self

    def briefs(self) -> list[str]:
        """The brief nanobot's runtime gives the model with the room's next message."""
        return sys.modules["brief"].brief_lines(self.request)

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

    def deletes_list(self, list_: str, *, confirmed: bool | None = None,
                     turn: str = "t1") -> str:
        """What the `list_edit` tool answers to deleting `list_` in the
        message `turn` of the room, `confirmed` as the model passes it."""
        self.request.turn_id = turn
        return asyncio.run(sys.modules["list_tool"].ListEditTool().execute(
            op="delete", list=list_, confirmed=confirmed))


def host_api(memory, api, shim):
    """The host API's side of the shim's wire, as a handler for `serving`.

    It does what `handle_plaintext` does for the agent: the API's own
    `agent_command` turns the line into the command or a refusal, then the
    command runs and the answer is the text, with a status line on failure.
    It runs the command on the test's memory, not through the host's
    `./stack` (which would reach whatever runs on this Mac). One command at a
    time: the API runs each in a process of its own, while this one captures
    the test process's output, which two commands at once would share.
    """
    one_at_a_time = threading.Lock()

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
        with one_at_a_time:
            code, text = run_command(memory, args)
        if code:
            text = f"{text.rstrip(chr(10))}\n{shim.EXIT_MARKER} {code}\n"
        conn.sendall(text.encode())
    return handle


def run_command(memory, args: list[str]) -> tuple[int, str]:
    """`./stack <args>` on the memory's instance, as `handle_plaintext` runs
    it: the CLI's real entry point in a process of its own, so the API's
    rules are tested against the reading of the line that runs (`main()`
    takes `--json` out, for one). Its exit code and what it printed."""
    done = subprocess.run(
        [sys.executable, "-m", "stack", *args], capture_output=True, text=True, timeout=60,
        cwd=str(REPO_ROOT),
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "lib"),
             "STACK_DIR": str(memory.stack.instance_dir)})
    return done.returncode, done.stdout or done.stderr


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


# ── 10. No memory file in the container ─────────────────────────────

AGENT_DIR = REPO_ROOT / "stacklets" / "agent"


# The test instance's data dir, as `stack up agent` renders `{data_dir}`.
DATA_DIR = Path("/Users/simpson/famstack-data")


def _agent_mounts(compose_text: str, data_dir: Path = DATA_DIR) -> list[tuple[Path, str]]:
    """Every bind mount in `compose_text` as `(host path, container path)`: its
    variables rendered from the agent's `[env.defaults]` for `data_dir`, a
    relative path resolved from the stacklet's folder, as Docker resolves it."""
    import tomllib
    import yaml

    defaults = tomllib.loads((AGENT_DIR / "stacklet.toml").read_text())["env"]["defaults"]
    rendered = {name: str(value).replace("{data_dir}", str(data_dir))
                for name, value in defaults.items()}
    mounts = []
    for service in yaml.safe_load(compose_text)["services"].values():
        for volume in service.get("volumes", []):
            host, container = volume.split(":")[:2]
            for name, value in rendered.items():
                host = host.replace("${" + name + "}", value)
            assert "${" not in host, f"{host} is not rendered from the stacklet's defaults"
            mounts.append((Path(os.path.normpath(AGENT_DIR / host)), container))
    return mounts


# What the agent's container needs from the host, and nothing else: its own
# data dir, its runtime shims, the three shared modules and the `stack` shim.
AGENT_NEEDS = {
    DATA_DIR / "agent",
    AGENT_DIR / "runtime",
    REPO_ROOT / "lib" / "stack" / "name_trigger.py",
    REPO_ROOT / "lib" / "stack" / "agent_audience.py",
    REPO_ROOT / "lib" / "stack" / "agent_confirm.py",
    AGENT_DIR / "client" / "stack",
}


def _mount_refusals(compose_text: str) -> list[str]:
    """Why each mount of `compose_text` may not be in the agent's container.

    SAFETY: a mount outside what the agent needs is refused, and so, by
    name, are the memory, every folder above it (the data dir) and the
    stack's own folder, whose `.stack/` holds the secrets.
    """
    memory = DATA_DIR / "memory"
    refused = []
    for host, container in _agent_mounts(compose_text):
        if host == memory or host in memory.parents or memory in host.parents:
            refused.append(f"{host} -> {container}: the memory or a folder that holds it")
        elif host == REPO_ROOT:
            refused.append(f"{host} -> {container}: the stack's own folder")
        elif host not in AGENT_NEEDS:
            refused.append(f"{host} -> {container}: not something the agent needs")
    return refused


AGENT_COMPOSE = (AGENT_DIR / "docker-compose.yml").read_text()


def test_no_mount_brings_a_memory_file_into_the_agents_container():
    """Given the agent's compose file as `stack up agent` renders it, then each
    mount is one the agent needs, and none holds the memory.

    Why it matters: nanobot's file tools (read_file, grep, find_files,
    list_dir) and exec reach every file in the container, around every
    audience rule. With the memory mounted, `read_file ./vault/marge/...`
    in Bart's chat returned Marge's private note word for word. Without it,
    the memory is read only through the commands, for the room's people.
    """
    assert _agent_mounts(AGENT_COMPOSE), "the agent has mounts: its home, its runtime, its shim"
    assert _mount_refusals(AGENT_COMPOSE) == []


def _with_mount(volume: str) -> str:
    """The agent's compose file with one more mount, as a later change could add it."""
    anchor = "      - ${AGENT_DATA_DIR}:/home/nanobot/.nanobot\n"
    assert anchor in AGENT_COMPOSE
    return AGENT_COMPOSE.replace(anchor, f"{anchor}      - {volume}\n")


@pytest.mark.parametrize("volume", [
    "${AGENT_DATA_DIR}/../memory/brain:/home/nanobot/.nanobot/workspace/vault:ro",
    "${AGENT_DATA_DIR}/..:/data:ro",
    "${AGENT_DATA_DIR}/../memory:/memory:ro",
    "../..:/stack:ro",
    "../memory:/stacklet:ro",
])
def test_the_mount_check_refuses_a_mount_that_reaches_the_memory_or_the_stack(volume):
    """Given a mount of the memory, of the data dir above it, of the stack's
    own folder or of anything the agent does not need, then the check above
    refuses it: a check that cannot fail proves nothing."""
    assert _mount_refusals(_with_mount(volume))


def test_the_agents_container_gets_no_memory_path_in_its_environment():
    """Given the agent's compose file, then no variable hands its container a
    path into the memory, which a shell could follow."""
    compose = (AGENT_DIR / "docker-compose.yml").read_text()

    assert "MEMORY_" not in compose


def test_the_agents_skill_names_no_file_path_into_the_memory():
    """Given the agent's workspace (its skills and rules), then no text tells
    the model to read or write a file of the memory: it reads through its
    memory tools and `stack memory` commands."""
    texts = {path: path.read_text() for path in (AGENT_DIR / "workspace").rglob("*.md")}

    assert texts
    for path, text in texts.items():
        assert "vault/" not in text, path.relative_to(AGENT_DIR)
        assert "read_file(page)" not in text, path.relative_to(AGENT_DIR)


# With the memory mounted, a file tool found Marge's note and her own topic by
# their paths in Bart's chat. Without the mount no file tool finds them; these
# are the same paths, through the commands the model may still run.
PATHS_TO_MARGES = {
    "her note by its path": "memory card marge/notes/gift.md",
    "her note through the shared folder": "memory card family/../marge/notes/gift.md",
    "her topic through the shared folder": "memory topic ../marge/geschenke",
}


@pytest.mark.parametrize("line", PATHS_TO_MARGES)
def test_in_barts_chat_a_path_to_marges_memory_reaches_nothing_of_hers(stacky, memory, line):
    """Given Marge's note and her own topic `Geschenke`, when a line in Bart's
    chat names them by a path, then nothing of them is in the answer. The
    answer may repeat the path the line named: that is Bart's own text."""
    topic = memory.brain / "marge" / "geschenke" / "about.md"
    topic.parent.mkdir(parents=True)
    topic.write_text("---\ntitle: Geschenke\n---\n# Geschenke\n\nA bowling ball for Homer.\n")

    answer = stacky.in_room(BARTS_CHAT).execs(PATHS_TO_MARGES[line])

    assert "bowling night" not in answer and "Marge00000" not in answer
    assert "for Homer" not in answer


# ── 10b. A page the agent writes leaves no copy behind ──────────────

def _stand_in_stack(bin_dir: Path, buffer: Path, exit_code: int) -> None:
    """A `stack` on PATH that answers as the host's `memory write` does: it
    reads the page the runtime left for it, then exits with `exit_code`."""
    bin_dir.mkdir()
    script = bin_dir / "stack"
    # Unquoted, so the shell drops the blanks macOS's `wc` pads the count with.
    script.write_text(f"#!/bin/sh\necho read $(wc -c < '{buffer}') bytes\nexit {exit_code}\n")
    script.chmod(0o755)


@pytest.mark.parametrize("exit_code", [0, 1], ids=["written", "refused"])
def test_a_page_the_agent_writes_leaves_no_copy_where_exec_reads_it(
        nanobot, tmp_path, monkeypatch, exit_code):
    """Given Marge's chat, when Stacky writes her private note, then the host
    command reads the page, and afterwards no copy of it is left in the
    agent's data dir, also when the write failed.

    Why it matters: exec reads every file in the container. A page left in
    the data dir would give the next room the last page written, word for
    word, around the room's audience.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    buffer = tmp_path / ".nanobot" / ".write-buffer"
    _stand_in_stack(tmp_path / "bin", buffer, exit_code)
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    nanobot()

    answer = sys.modules["vault_write"].write_page("marge/notes/gift.md", MARGES_NOTE)

    assert f"read {len(MARGES_NOTE.encode())} bytes" in answer
    assert not buffer.exists()


# ── 11. The brief ────────────────────────────────────────────────────

@pytest.fixture
def camping(memory):
    """The family's topic Camping: its page as the wiki compiles it into the
    brain, and a packing list for it with an open item, made in Forgejo."""
    page = memory.brain / "family" / "camping" / "about.md"
    page.parent.mkdir(parents=True)
    page.write_text("---\ntitle: Camping\n---\n# Camping\n\nThe family's trips.\n")
    memory.edited_in_forgejo({"family/lists/packliste.md":
                              "---\ntype: list\ntitle: Packliste\ntopics:\n  - camping\n---\n\n"
                              "- [ ] Zelt\n",
                              "family/lists/einkauf.md":
                              "---\ntype: list\ntitle: Einkauf\ntopics:\n  - einkauf\n---\n\n"
                              "- [ ] Milk\n"},
                             by="marge", subject="a packing list for camping")


def test_in_her_own_chat_the_brief_knows_marges_profile(stacky):
    """Given Marge's page, when Marge writes in her own chat, then the brief
    the model gets names her and her page's essence."""
    brief = "\n".join(stacky.in_room(MARGES_CHAT).briefs())

    assert "marge" in brief
    assert "blue vase" in brief


@pytest.mark.parametrize("room", {"the family room": FAMILY_ROOM, "the camping room": CAMPING_ROOM})
def test_where_others_listen_the_brief_holds_nothing_of_marges_page(stacky, camping, room):
    """Given Marge's page in her own folder, when she writes in a room with the
    family, then the brief names her but holds nothing of her page: the model
    could repeat it to everyone in the room."""
    brief = "\n".join(stacky.in_room({"the family room": FAMILY_ROOM,
                                      "the camping room": CAMPING_ROOM}[room]).briefs())

    assert "marge" in brief
    assert "blue vase" not in brief


def test_in_barts_chat_the_brief_holds_nothing_of_marges(stacky):
    brief = "\n".join(stacky.in_room(BARTS_CHAT).briefs())

    assert "blue vase" not in brief and not shows(brief, MARGES_CARD)


def test_in_a_topic_room_the_brief_names_the_topic_and_its_open_lists(stacky, camping):
    """Given the family's topic Camping and its packing list, when someone
    writes in `Thema: Camping`, then the brief names the topic, the command
    for its overview and the topic's list with open items, and no other list."""
    brief = "\n".join(stacky.in_room(CAMPING_ROOM).briefs())

    assert "'camping'" in brief
    assert "stack memory topic camping" in brief
    assert "packliste" in brief
    assert "einkauf" not in brief


def test_a_room_kept_only_for_lists_is_its_topic_without_an_overview(stacky, camping):
    """Given the shopping list of the topic `einkauf`, which has no page yet,
    when someone writes in `Thema: Einkauf`, then the brief names the topic,
    so the list tool adds to its lists, and offers no overview it lacks."""
    room = Room("!einkauf:simpson", FAMILY_ROOM.people, "homer", "Thema: Einkauf")
    brief = "\n".join(stacky.in_room(room).briefs())

    assert "'einkauf'" in brief and "einkauf" in brief.split("lists with open items:")[-1]
    assert "stack memory topic einkauf" not in brief


def test_a_room_nobody_is_known_in_gets_no_brief_from_the_memory(stacky, camping):
    """Given a room whose people nobody could tell, then the brief reads
    nothing of the memory: the API would refuse it, and the admin's view is
    never the fallback."""
    brief = "\n".join(stacky.in_room(UNKNOWN_PEOPLE).briefs())

    assert "blue vase" not in brief and "packliste" not in brief


# ── 12. What is said in one chat stays in that chat ──────────────────
#
# These rows drive nanobot's own tools, as the gateway registers them under
# the shipped config.json and runtime, in a process of their own
# (`tests/unit/stacklets/nanobot_agent.py`). The agent's home holds what
# Marge said in her own chat (its session, a summary nanobot's
# consolidation archives) and what an earlier release left in the home
# (the shared history, Dream's archive, a write buffer, an attachment of
# hers). Bart then asks Stacky for any of it, by every tool the model has.

MARGES_ROOM = "!marge-and-stacky:simpson"
BARTS_ROOM = "!bart-and-stacky:simpson"

# Each is said or kept in one place only, so an answer that holds it came from there.
SAID_IN_HER_CHAT = "bowling bag for Homer"
SUMMARY_OF_HER_CHAT = "Marge plans the surprise party"
OLD_SHARED_HISTORY = "Rusty Barnacle on the twelfth"
OLD_DREAM_NOTE = "skateboard with flames"
OLD_WRITE_BUFFER = "Bleeding Gums Murphy notes"
HER_ATTACHMENT = "Geheime Geschenkliste von Marge"
EVERY_WORD = (SAID_IN_HER_CHAT, SUMMARY_OF_HER_CHAT, OLD_SHARED_HISTORY, OLD_DREAM_NOTE,
              OLD_WRITE_BUFFER, HER_ATTACHMENT, AGENT_PROCESS_KEY)

FAMILY_FILE = "rezepte.md"
FAMILY_TEXT = "Grandma's meatloaf: 2 eggs"


def _what_an_earlier_release_left(home: Path) -> None:
    """The files a home of today's release holds after a few weeks."""
    nanobot = home / ".nanobot"
    memory = nanobot / "workspace" / "memory"
    memory.mkdir(parents=True)
    (memory / "history.jsonl").write_text(
        '{"cursor": 1, "timestamp": "2026-11-20 10:00", "content": "'
        + OLD_SHARED_HISTORY + '", "session_key": "matrix:' + MARGES_ROOM + '"}\n')
    (memory / ".cursor").write_text("1")
    (memory / ".dream_cursor").write_text("1")
    (nanobot / "dream-archive").mkdir()
    (nanobot / "dream-archive" / "USER.md").write_text(f"- Bart gets a {OLD_DREAM_NOTE}\n")
    (nanobot / ".write-buffer").write_text(f"# Geschenke\n- {OLD_WRITE_BUFFER}\n")
    media = nanobot / "media" / "matrix"
    media.mkdir(parents=True)
    (media / "AbCdEf_geschenke.txt").write_text(HER_ATTACHMENT)
    # Stands in for /proc/<pid>/environ: a file outside the workspace that
    # the runtime's user can read and that holds the runtime's key. macOS
    # has no /proc; on Linux the rows below also try the real one.
    (home / "runtime.environ").write_text(f"STACK_AUDIENCE_KEY={AGENT_PROCESS_KEY}\n")
    (nanobot / "workspace" / FAMILY_FILE).write_text(FAMILY_TEXT)


@pytest.fixture(scope="module")
def agent(tmp_path_factory):
    """Stacky's tools after Marge talked to it in her own chat."""
    root = tmp_path_factory.mktemp("agent")
    home = fabricated_home(root)
    _what_an_earlier_release_left(home)
    with running_agent(root, home) as agent:
        attachment = str(agent.nanobot / "media" / "matrix" / "AbCdEf_geschenke.txt")
        agent.said(MARGES_ROOM, "marge", f"Please remember: the {SAID_IN_HER_CHAT}.",
                   media=[attachment])
        agent.summarised(MARGES_ROOM, f"{SUMMARY_OF_HER_CHAT} with a {SAID_IN_HER_CHAT}.")
        yield agent


def _bart(agent):
    return agent.in_room(BARTS_ROOM, "bart", ["bart"])


def _marge(agent):
    return agent.in_room(MARGES_ROOM, "marge", ["marge"])


def _her_session(agent) -> str:
    """The file nanobot keeps Marge's chat in (named by its session key, encoded)."""
    return str(next(p for p in (agent.nanobot / "sessions").rglob("*.jsonl")
                    if SAID_IN_HER_CHAT in p.read_text()))


# The tool calls a model makes when asked for "any file of yours".
BARTS_ASKS = {
    "read_file the shared history": lambda a: _bart(a).calls(
        "read_file", path="memory/history.jsonl"),
    "read_file her session": lambda a: _bart(a).calls("read_file", path=_her_session(a)),
    "read_file Dream's archive": lambda a: _bart(a).calls(
        "read_file", path=str(a.nanobot / "dream-archive" / "USER.md")),
    "read_file the write buffer": lambda a: _bart(a).calls(
        "read_file", path=str(a.nanobot / ".write-buffer")),
    "read_file her attachment": lambda a: _bart(a).calls(
        "read_file", path=str(a.nanobot / "media" / "matrix" / "AbCdEf_geschenke.txt")),
    "read_file the runtime's environment": lambda a: _bart(a).calls(
        "read_file", path=str(a.home / "runtime.environ")),
    "read_file /proc/1/environ": lambda a: _bart(a).calls("read_file", path="/proc/1/environ"),
    "list_dir the agent's home": lambda a: _bart(a).calls("list_dir", path=str(a.nanobot),
                                                          recursive=True),
    "list_dir the media": lambda a: _bart(a).calls(
        "list_dir", path=str(a.nanobot / "media" / "matrix")),
    "grep the workspace": lambda a: _bart(a).calls("grep", pattern="Marge|Bart|Homer", path="."),
    "grep the agent's home": lambda a: _bart(a).calls(
        "grep", pattern="Marge|Bart|Homer|STACK_", path=str(a.home)),
    "find_files the agent's home": lambda a: _bart(a).calls(
        "find_files", pattern="*", path=str(a.nanobot)),
    "exec cat the shared history": lambda a: _bart(a).execs("cat memory/history.jsonl"),
    "exec cat her session": lambda a: _bart(a).execs(f"cat {_her_session(a)}"),
    "exec grep the home": lambda a: _bart(a).execs("grep -r Marge ~"),
    "exec the runtime's environment": lambda a: _bart(a).execs(
        "cat ~/runtime.environ /proc/*/environ"),
    "exec python reads a file": lambda a: _bart(a).execs(
        "python3 -c \"print(open('memory/history.jsonl').read())\""),
    "exec a file behind a stack line": lambda a: _bart(a).execs(
        "stack memory list show; cat memory/history.jsonl"),
    "exec a file inside a stack line": lambda a: _bart(a).execs(
        "stack memory card \"$(cat memory/history.jsonl)\""),
    "exec a file on its own line": lambda a: _bart(a).execs(
        "stack memory list show\ncat memory/history.jsonl"),
    "exec a file through a pipe": lambda a: _bart(a).execs(
        "stack memory list show | cat ../dream-archive/USER.md"),
}


@pytest.mark.parametrize("ask", BARTS_ASKS)
def test_in_barts_chat_no_tool_returns_a_word_of_marges_chat(agent, ask):
    """Given Marge talked to Stacky in her own chat, and an earlier release
    left the shared history, Dream's archive and a write buffer in the
    agent's home, when Bart's chat asks for any file of the agent, then no
    answer holds a word of hers, and none holds the runtime's key."""
    answer = BARTS_ASKS[ask](agent)

    assert not [word for word in EVERY_WORD if word in answer], answer


def test_marges_chat_keeps_its_own_conversation(agent):
    """Given Marge talked to Stacky in her own chat, then her chat's session
    still holds what she said: a chat remembers itself as before."""
    history = agent.request(op="history", room=MARGES_ROOM)["history"]

    assert SAID_IN_HER_CHAT in history


def test_a_summary_of_one_chat_is_kept_in_no_file_another_chat_reaches(agent):
    """Given nanobot's consolidation archived a summary of Marge's chat, then
    no file in the agent's workspace holds it: the workspace is all the file
    tools reach."""
    holders = [p for p in agent.workspace.rglob("*")
               if p.is_file() and SUMMARY_OF_HER_CHAT in p.read_text(errors="ignore")]

    assert holders == []


def test_in_her_own_chat_marge_reads_her_attachment(agent):
    """Given Marge sent a file in her own chat, then her chat may read it."""
    answer = _marge(agent).calls(
        "read_file", path=str(agent.nanobot / "media" / "matrix" / "AbCdEf_geschenke.txt"))

    assert HER_ATTACHMENT in answer


def test_in_barts_chat_marges_attachment_is_not_sent(agent):
    """Given Marge's attachment, when Bart's chat sends it with the message
    tool, then it is refused: sending would show it in his room."""
    answer = _bart(agent).calls(
        "message", content="here", channel="matrix", chat_id=BARTS_ROOM,
        media=[str(agent.nanobot / "media" / "matrix" / "AbCdEf_geschenke.txt")])

    assert answer.startswith("Error")


def test_a_path_written_into_a_chat_opens_no_attachment(tmp_path):
    """Given Marge's attachment, when Bart writes its path into his own chat,
    then his chat still cannot read it: only a file nanobot stored as a
    message's attachment belongs to a chat, never a path in its text."""
    home = fabricated_home(tmp_path)
    _what_an_earlier_release_left(home)
    with running_agent(tmp_path, home) as agent:
        attachment = str(agent.nanobot / "media" / "matrix" / "AbCdEf_geschenke.txt")
        agent.said(MARGES_ROOM, "marge", "the list", media=[attachment])
        agent.said(BARTS_ROOM, "bart", f"Stacky, read {attachment}")

        answer = _bart(agent).calls("read_file", path=attachment)

    assert HER_ATTACHMENT not in answer


def test_the_familys_own_file_in_the_workspace_stays_readable(agent):
    """Given a file the family put into the agent's workspace, then it stays
    where it is, and the agent still reads it."""
    answer = _bart(agent).calls("read_file", path=FAMILY_FILE)

    assert FAMILY_TEXT in answer


WRITES = {
    "write_file a note": lambda t: t.calls("write_file", path="notes/marge.md",
                                           content=SAID_IN_HER_CHAT),
    "write_file a skill": lambda t: t.calls("write_file", path="skills/gifts/SKILL.md",
                                            content=SAID_IN_HER_CHAT),
    "edit_file a profile": lambda t: t.calls("edit_file", path="USER.md", old_text="#",
                                             new_text=f"# {SAID_IN_HER_CHAT}"),
    "apply_patch a note": lambda t: t.calls("apply_patch", edits=[
        {"path": "notes/marge.md", "old_text": "", "new_text": SAID_IN_HER_CHAT}]),
}


@pytest.mark.parametrize("write", WRITES)
def test_in_marges_chat_nothing_is_written_where_another_chat_reads(agent, write):
    """Given Marge's chat, when the model writes a plain file, a skill or a
    profile into the agent's workspace, then it is refused and no file of
    the workspace holds her words: every chat reads the workspace, and a
    skill or profile goes into every chat's prompt."""
    answer = WRITES[write](_marge(agent))

    assert answer.startswith("Error"), answer
    assert not [p for p in agent.workspace.rglob("*")
                if p.is_file() and SAID_IN_HER_CHAT in p.read_text(errors="ignore")]


# Every kind of line the shipped skills, AGENTS.md, the brief and the
# admin's `[agent] allow` tell Stacky to run with exec.
STACK_LINES = (
    "stack memory topic camping",
    "stack memory list show",
    "stack memory list show einkauf",
    "stack memory card family/notes/2026/11/zelt-fur-vier-b5d77b.md",
    "stack memory card b5d77b",
    "stack memory list delete partyliste --as homer",
    "stack docs show 12 --content",
    'stack ai say "Das Essen ist fertig, kommt runter!"',
    "stack ai say \"Homer & Marge's party; at 7?\" --language de --volume 30",
    "stack ai say 'Der Donut kostet $2, Homer' --language en",
    "stack kettle status",
)


@pytest.mark.parametrize("line", STACK_LINES)
def test_the_stack_lines_stacky_is_told_to_run_still_run(agent, line):
    """Given a line the skills or the admin's allowed commands tell Stacky
    to run with exec, then it reaches the `stack` client with its words."""
    answer = _bart(agent).execs(line)

    assert answer.startswith(f"{STACK_RAN} {line.split(' ', 1)[1].split(' ')[0]}"), answer


# ── 13. Only the person's yes deletes a list ───────────────────────────
#
# The list store deletes a list with open items only with `--yes`. Only
# `list_edit` may pass it, after the person said yes in a later message:
# the runtime signs that confirmation for the one list, and the host API
# runs a `--yes` only with it. A shell line cannot make one, because the
# key is not in its environment.

PARTY_LIST = "family/lists/partyliste.md"


@pytest.fixture
def party_list(memory):
    """The family's party list with two open items, beside the shopping list."""
    memory.edited_in_forgejo({PARTY_LIST: "---\ntype: list\ntitle: Partyliste\n---\n\n"
                                          "- [ ] Luftballons\n- [ ] Girlanden\n",
                              "family/lists/einkauf.md":
                              "---\ntype: list\ntitle: Einkauf\n---\n\n- [ ] Milk\n"},
                             by="marge", subject="a list for the party")


def _party_list_is_there(memory) -> bool:
    return PARTY_LIST in memory.pages("family/lists")


def test_after_the_persons_yes_the_tool_deletes_the_list(stacky, memory, party_list):
    """Given the party list with open items, when Homer asks to delete it and
    says yes in his next message, then `list_edit` deletes it."""
    homer = stacky.in_room(Room("!family:simpson", FAMILY_ROOM.people, "homer"))
    homer.deletes_list("partyliste", turn="t1")
    assert _party_list_is_there(memory)

    homer.deletes_list("partyliste", confirmed=True, turn="t2")

    assert not _party_list_is_there(memory)


SHELL_CONFIRMS = (
    "memory list delete partyliste --as homer --yes",
    "memory list delete partyliste --as homer '--yes'",
    "memory 'list' delete partyliste --as homer --yes",
    "memory list delete partyliste --as 'homer\n' --yes",
    "memory list delete --ye partyliste --as homer",
    # The CLI takes `--json` out wherever it stands, before the command reads its words.
    "memory list --json delete partyliste --as homer --yes",
    "memory list delete --json partyliste --as homer --yes",
    "memory list delete partyliste --as homer --yes --json",
)


@pytest.mark.parametrize("line", SHELL_CONFIRMS)
def test_the_shell_cannot_confirm_deleting_a_list(stacky, memory, party_list, line):
    """Given the party list with open items, when the model runs a delete
    with `--yes` through exec, in any spelling the shell reads as one, then
    the list stays: the person was never asked."""
    stacky.in_room(FAMILY_ROOM).execs(line)

    assert _party_list_is_there(memory)


def _confirmed_line(list_: str, *, now: float) -> str:
    """A delete of the party list as the shim sends it, with a confirmation
    the runtime signed for `list_` at `now`."""
    people = sign(FAMILY_ROOM.people, RUNTIME_KEY, now=time.time())
    confirmation = confirm_delete(list_, RUNTIME_KEY, now=now)
    return (f"audience {people} confirmed {confirmation} "
            "memory list delete partyliste --yes --as homer")


def test_a_confirmation_for_this_list_deletes_it(stacky, memory, party_list):
    """The control for the two below: the line they change deletes the list."""
    stacky.writes_line(_confirmed_line("partyliste", now=time.time()))

    assert not _party_list_is_there(memory)


def test_a_confirmation_for_another_list_deletes_nothing(stacky, memory, party_list):
    """Given Homer confirmed deleting the shopping list, then that
    confirmation does not delete the party list."""
    stacky.writes_line(_confirmed_line("einkauf", now=time.time()))

    assert _party_list_is_there(memory)


def test_a_confirmation_that_is_over_deletes_nothing(stacky, memory, party_list):
    """Given a confirmation kept from an hour ago, then it deletes nothing."""
    stacky.writes_line(_confirmed_line("partyliste", now=time.time() - 3600))

    assert _party_list_is_there(memory)


def test_the_key_is_not_in_the_shells_environment(stacky, monkeypatch):
    """Given the runtime holds the key, and a confirmation in its own
    environment, then neither reaches a command the model runs with exec."""
    monkeypatch.setenv(CONFIRM_ENV, confirm_delete("partyliste", RUNTIME_KEY, now=time.time()))

    env = stacky.in_room(FAMILY_ROOM).exec_env()

    assert AUDIENCE_KEY not in env and CONFIRM_ENV not in env
    assert not any(RUNTIME_KEY in value for value in env.values())


def test_a_command_exec_runs_gets_neither_the_key_nor_a_confirmation(agent):
    """Given nanobot's own exec tool with the agent's runtime, when the model
    runs a `stack` line, then its environment holds no audience key and no
    confirmation to delete a list."""
    answer = _bart(agent).execs("stack memory list show")

    assert answer.startswith(STACK_RAN)
    assert AGENT_PROCESS_KEY not in answer
    assert AUDIENCE_KEY not in answer and CONFIRM_ENV not in answer


def test_a_reminder_cannot_delete_a_list_later(stacky, memory, party_list):
    """Given the party list with open items, when the model sets a reminder
    that deletes it with `--yes`, then the scheduler's run of that reminder
    leaves the list: a reminder is never the person's yes, and it runs as
    the admin, past the host API."""
    added = stacky.in_room(FAMILY_ROOM).execs(
        'core schedule add remind-party "in 10m" -- '
        "memory list delete partyliste --as homer --yes")

    ran = run_command(memory, ["core", "schedule", "run", "remind-party"])

    assert _party_list_is_there(memory), (added, ran)


@pytest.mark.parametrize("signature", ("\u00b2:abc", "\u0663:abc"))
def test_a_confirmation_with_digits_of_another_script_is_refused(stacky, memory, party_list,
                                                                 signature):
    """Given an expiry in digits `int` does not read (a superscript two, an
    Arabic-Indic three), then the API refuses the delete and answers."""
    people = sign(FAMILY_ROOM.people, RUNTIME_KEY, now=time.time())
    answer = stacky.writes_line(f"audience {people} confirmed {signature} "
                                "memory list delete partyliste --yes --as homer")

    assert "list_edit" in answer
    assert _party_list_is_there(memory)


def test_an_audience_with_digits_of_another_script_reads_nothing(stacky):
    """Given a signed audience whose expiry is not ASCII digits, then the
    API answers with a refusal instead of failing on the line."""
    answer = stacky.writes_line("audience marge:\u00b2:abc memory find gift")

    assert "error" in answer
