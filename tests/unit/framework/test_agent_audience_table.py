"""One table says how every memory and docs command of the agent reads.

The host API's allowlist (`DOMAIN_ALLOW` in `stacklets/core/famstack-api.py`)
says what the agent may run. `stack.agent_audience` says how each memory and
docs command reads: for the room's people (`AUDIENCE_COMMANDS`), or nothing
private (`NO_PRIVATE_READS`). A memory or docs command in neither runs in the
admin's view, which reads every person's own notes, so the API refuses it.

These tests keep the two lists one table: an allowed memory or docs command
the table does not name fails here before it reaches a room, and every
`stack` line the agent's shipped skills tell it to run still passes the API.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import re
import shlex
import time
from pathlib import Path

import pytest

from stack.agent_audience import AUDIENCE_COMMANDS, NO_PRIVATE_READS, sign

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKSPACE = REPO_ROOT / "stacklets" / "agent" / "workspace"
KEY = "audience-table-key-4b1d"


@pytest.fixture(scope="module")
def api():
    """The host API module; its name has a hyphen, so it loads by its path."""
    path = REPO_ROOT / "stacklets" / "core" / "famstack-api.py"
    loader = importlib.machinery.SourceFileLoader("famstack_api_for_the_table", str(path))
    spec = importlib.util.spec_from_file_location(loader.name, path, loader=loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_every_allowed_memory_or_docs_command_is_in_the_table(api):
    """Given the API's allowlist, then its memory and docs commands are
    exactly the commands the table names: a new one that reads around the
    room's people fails here, and so does a table entry nothing allows."""
    allowed = sorted(entry for entry in api.DOMAIN_ALLOW if entry[0] in ("memory", "docs"))

    assert allowed == sorted(AUDIENCE_COMMANDS + NO_PRIVATE_READS)


def _skill_lines() -> list[str]:
    """Every `stack <stacklet> <command> ...` line in the agent's shipped
    workspace (its AGENTS.md and skills), without the leading `stack`."""
    lines = set()
    for page in WORKSPACE.rglob("*.md"):
        for found in re.findall(r"`stack ([^`]+)`", page.read_text()):
            if len(found.split()) > 1:
                lines.add(found)
    return sorted(lines)


@pytest.mark.parametrize("line", _skill_lines())
def test_every_line_the_shipped_skills_name_passes_the_api(api, line):
    """Given a `stack` line the agent's skills tell it to run, in Bart's
    chat, then the API runs it rather than refusing it."""
    people = sign(["bart"], KEY, now=time.time())

    args, refusal = api.agent_command(f"audience {people} {line}", api.DOMAIN_ALLOW, KEY)

    assert refusal is None, refusal
    assert args[:2] == shlex.split(line)[:2]
