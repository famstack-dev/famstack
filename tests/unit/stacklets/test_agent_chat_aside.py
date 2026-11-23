"""What the agent's start does with the history nanobot kept for every chat.

nanobot's consolidation appended a summary of every chat to one file in the
agent's workspace, which every chat's file tools read. The agent no longer
writes it (`runtime/own_chat.py`); at each start, what is there is moved out
of the workspace into the agent's data, and nothing is deleted. That the
moved file is out of every chat's reach afterwards is driven through
nanobot's own tools in `test_agent_reads_for_the_room.py`, row 12.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_AGENT = Path(__file__).resolve().parents[3] / "stacklets" / "agent"
SCRIPT = _AGENT / "runtime" / "chat_aside.py"
SEED = _AGENT / "workspace"

HISTORY = '{"cursor": 1, "content": "Marge plans the surprise party"}\n'


def _start(home: Path) -> subprocess.CompletedProcess:
    """The entrypoint's step, as it runs it: `chat_aside.py "$NB" /app/workspace-seed`."""
    return subprocess.run([sys.executable, str(SCRIPT), str(home), str(SEED)],
                          capture_output=True, text=True)


def _named(done: subprocess.CompletedProcess) -> list[str]:
    """The names the start's "not shipped" lines give, as printed."""
    return sorted(line.split("not shipped: ", 1)[1] for line in done.stdout.splitlines()
                  if "not shipped: " in line)


def _home(tmp_path: Path) -> Path:
    """An agent home after a few weeks of today's release."""
    home = tmp_path / "nanobot"
    memory = home / "workspace" / "memory"
    memory.mkdir(parents=True)
    (memory / "history.jsonl").write_text(HISTORY)
    (memory / ".cursor").write_text("1")
    (memory / ".dream_cursor").write_text("1")
    (memory / "HISTORY.md").write_text("[2026-10-01] Homer's 40th\n")
    (home / "workspace" / "rezepte.md").write_text("the family's own file")
    (home / "workspace" / "skills" / "kochen").mkdir(parents=True)
    (home / "workspace" / "skills" / "kochen" / "SKILL.md").write_text("the family's skill")
    return home


def test_the_shared_history_leaves_the_workspace_and_is_kept(tmp_path):
    """Given the shared history and its cursors in the workspace, when the
    agent starts, then none of them is in the workspace and each is kept,
    unchanged, in the agent's chat archive."""
    home = _home(tmp_path)

    assert _start(home).returncode == 0

    memory = home / "workspace" / "memory"
    archive = home / "chat-archive"
    assert sorted(p.name for p in memory.iterdir()) == []
    assert (archive / "history.jsonl").read_text() == HISTORY
    assert sorted(p.name for p in archive.iterdir()) == [
        ".cursor", ".dream_cursor", "HISTORY.md", "history.jsonl"]


def test_the_familys_own_files_and_skills_stay(tmp_path):
    """Given a file and a skill the family put into the workspace, then the
    start leaves both where they are."""
    home = _home(tmp_path)

    _start(home)

    assert (home / "workspace" / "rezepte.md").read_text() == "the family's own file"
    assert (home / "workspace" / "skills" / "kochen" / "SKILL.md").is_file()


def test_the_start_names_files_a_chat_wrote_but_not_their_content(tmp_path):
    """Given files the model wrote into the workspace in an earlier release,
    when the agent starts, then the log names each of them, never what they
    hold, and names nothing the image shipped or nanobot keeps."""
    home = _home(tmp_path)
    workspace = home / "workspace"
    for name in ("SOUL.md", "USER.md", "AGENTS.md"):
        (workspace / name).write_text("seeded")
    (workspace / "skills" / "speaking").mkdir(parents=True)
    (workspace / "skills" / "speaking" / "SKILL.md").write_text("shipped")
    (workspace / "cron").mkdir()
    (workspace / "cron" / "jobs.json").write_text("{}")
    (workspace / "notes").mkdir()
    (workspace / "notes" / "party.md").write_text("Marge plans the surprise party")

    done = _start(home)

    assert _named(done) == ["'notes/party.md'", "'rezepte.md'", "'skills/kochen/SKILL.md'"]
    assert "surprise party" not in done.stdout + done.stderr
    assert (workspace / "notes" / "party.md").is_file()


def test_nanobots_own_files_and_the_installed_skill_are_not_named(tmp_path):
    """Given a workspace as nanobot leaves it after its start (its templates,
    its git store) and the skill the agent's install writes, then the start
    names only the file a chat wrote: a list of nanobot's files would hide it."""
    from nanobot.agent.memory import MemoryStore
    from nanobot.utils.helpers import sync_workspace_templates

    home = tmp_path / "nanobot"
    workspace = home / "workspace"
    workspace.mkdir(parents=True)
    sync_workspace_templates(workspace, silent=True)
    MemoryStore(workspace).git.init()
    (workspace / "skills" / "stack-commands").mkdir(parents=True)
    (workspace / "skills" / "stack-commands" / "SKILL.md").write_text("the allowed commands")
    (workspace / "party.md").write_text("Marge plans the surprise party")

    done = _start(home)

    assert (workspace / ".git").is_dir()
    assert _named(done) == ["'party.md'"]


def test_a_name_with_a_newline_stays_on_its_line(tmp_path):
    """Given a file whose name holds a newline, then the start names it on
    one line, so the name cannot write a log line of its own."""
    home = _home(tmp_path)
    (home / "workspace" / "a\nchat: set aside nothing").write_text("x")

    done = _start(home)

    assert not any(line.startswith("chat: set aside nothing") for line in done.stdout.splitlines())
    assert "'a\\nchat: set aside nothing'" in _named(done)


def test_a_history_that_appears_again_is_moved_again_and_both_are_kept(tmp_path):
    """Given a start that set the history aside, when the file appears again,
    then the next start moves it too, and the first copy stays."""
    home = _home(tmp_path)
    _start(home)
    (home / "workspace" / "memory" / "history.jsonl").write_text("later\n")

    _start(home)

    kept = sorted((home / "chat-archive").glob("history.jsonl*"))
    assert [p.read_text() for p in kept] == [HISTORY, "later\n"]
    assert not (home / "workspace" / "memory" / "history.jsonl").exists()


def test_a_start_with_nothing_to_move_changes_nothing(tmp_path):
    """Given a fresh home, then the start succeeds and makes no archive."""
    home = tmp_path / "nanobot"
    (home / "workspace").mkdir(parents=True)

    assert _start(home).returncode == 0
    assert not (home / "chat-archive").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root moves the file in spite of the mode")
def test_a_history_that_cannot_be_moved_stops_the_start_and_is_named(tmp_path):
    """Given a history the start cannot move, then it fails, says which file,
    and leaves the file where it was: the agent does not start with it in
    reach (entrypoint.sh runs it under `set -e`)."""
    home = _home(tmp_path)
    memory = home / "workspace" / "memory"
    memory.chmod(0o500)
    try:
        done = _start(home)
    finally:
        memory.chmod(0o700)

    assert done.returncode == 1
    assert "history.jsonl" in done.stderr
    assert (memory / "history.jsonl").read_text() == HISTORY


def test_the_agent_runs_the_step_before_nanobot_under_set_e():
    """The step only protects the chats if it runs, and stops the start when it fails."""
    entrypoint = (_AGENT / "entrypoint.sh").read_text()

    assert "\nset -e\n" in entrypoint
    step = entrypoint.index('chat_aside.py "$NB" /app/workspace-seed')
    assert step < entrypoint.index("exec nanobot gateway")
