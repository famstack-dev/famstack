"""The agent's reminders skill writes only lines the core API runs.

The nanobot agent learns `stack core schedule` from one skill,
`stacklets/agent/workspace/skills/reminders/SKILL.md`. The model copies
its examples, so a wrong example is a reminder that is refused or never
fires. Each example line goes through the core API's own decision
(`agent_command`, with the rules of `schedule_refusal`), and each time
through the scheduler's own reader (`parse_when`).

A reminder is two entries at the same time, because one entry runs one
command: `remind-<x>` sends the chat message, `remind-<x>-say` says it
aloud. A cancel removes both.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import re
import shlex
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from stack.schedule import parse_when, split_command
from stacklets.messages.cli.send import parse_request

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL = REPO_ROOT / "stacklets" / "agent" / "workspace" / "skills" / "reminders" / "SKILL.md"
SPOKEN = "-say"
TZ = ZoneInfo("Europe/Berlin")
# The examples name the room by a placeholder, which the model fills in from
# the brief's "This room: ..." line; a real-looking id there was copied once.
ROOM_PLACEHOLDER = "<room>"
ROOM = "!UAFrMyKitchen:simpson"


# ── reading the skill ──────────────────────────────────────────────────────

def examples() -> list[list[str]]:
    """Every example: the `stack core schedule` lines under one `example:` head,
    with this room's id where the example has its placeholder.

    The rule lines above the examples come before any `example:` head and are
    left out: their `<placeholders>` are no command.
    """
    groups: list[list[str]] = []
    for line in SKILL.read_text().splitlines():
        line = line.strip()
        if line.startswith("example:"):
            groups.append([])
        elif line.startswith("stack core schedule") and groups:
            groups[-1].append(line.removeprefix("stack ").replace(ROOM_PLACEHOLDER, ROOM))
    return groups


def adds(group: list[str]) -> dict[str, list[str]]:
    """The `add` lines of an example as argvs, by entry name."""
    lines = [shlex.split(line) for line in group]
    return {args[3]: args for args in lines if args[2] == "add"}


# The API module's file name has a hyphen, so it has no package name; it is
# loaded as `tests/unit/stacklets/test_agent_allow.py` loads it.
@pytest.fixture(scope="module")
def api():
    path = REPO_ROOT / "stacklets" / "core" / "famstack-api.py"
    loader = importlib.machinery.SourceFileLoader("famstack_api", str(path))
    spec = importlib.util.spec_from_file_location("famstack_api", path, loader=loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


# ── the tests ──────────────────────────────────────────────────────────────

def test_the_skill_shows_a_reminder_for_each_kind_of_time():
    """Given the skill, then its examples cover `in`, `at`, `daily` and
    `weekly`, the four times the scheduler knows, and a cancel."""
    whens = {args[4].split()[0] for group in examples() for args in adds(group).values()}
    removes = [line for group in examples() for line in group if " remove " in line]

    assert whens == {"in", "at", "daily", "weekly"}
    assert removes


@pytest.mark.parametrize("group", examples(), ids=lambda g: shlex.split(g[0])[3])
def test_each_example_line_passes_the_api(api, group):
    """Given an example of the skill, when the agent runs each of its lines,
    then the core API lets it through, as it would on the rig."""
    for line in group:
        args, refusal = api.agent_command(line, api.DOMAIN_ALLOW, "key")
        assert refusal is None, f"{line}\n{refusal}"


@pytest.mark.parametrize("group", [g for g in examples() if adds(g)],
                         ids=lambda g: shlex.split(g[0])[3])
def test_each_reminder_is_a_message_and_its_spoken_half_at_one_time(group):
    """Given an example that sets a reminder, then it adds the message, sent by
    the agent into this room and mentioning the person, and the `-say` entry
    that speaks it, both at a time the scheduler reads. Each line is read as
    `core schedule add` reads it, and the message as `messages send` reads it.
    One entry runs one command, so the house hears the reminder only through
    its second entry."""
    entries = {name: split_command(args[5:]) for name, args in adds(group).items()}
    whens = {name: args[4] for name, args in adds(group).items()}
    [name] = [n for n in entries if not n.endswith(SPOKEN)]
    (options, command), (spoken_options, spoken) = entries[name], entries[name + SPOKEN]

    assert whens[name] == whens[name + SPOKEN]
    parse_when(whens[name], TZ, datetime(2026, 11, 26, 12, 0, tzinfo=TZ))
    for opts in (options, spoken_options):
        assert {"--title", "--by", "--for"} <= opts.keys()
        assert opts["--for"] == options["--for"]
    assert command[:2] == ["messages", "send"]
    request, error = parse_request(command[2:])
    assert error is None, error
    assert (request.room, request.sender, request.mentions) == (ROOM, "agent", [options["--for"]])
    assert spoken[:2] == ["ai", "say"]


def test_a_cancel_removes_both_entries_of_a_reminder():
    """Given the cancel example, then it removes the message and its `-say`
    half, so nothing is left to speak at the time."""
    [group] = [g for g in examples() if not adds(g)]
    removed = {shlex.split(line)[3] for line in group if " remove " in line}

    [name] = [n for n in removed if not n.endswith(SPOKEN)]
    assert removed == {name, name + SPOKEN}
    assert re.fullmatch(r"remind-[a-z0-9-]+", name)
