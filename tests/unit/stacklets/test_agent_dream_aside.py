"""What the agent's start does with the files nanobot's Dream wrote.

Dream condensed every room's history into SOUL.md, USER.md, MEMORY.md
and skills of its own, and nanobot puts all of them into the prompt of
every room. It is switched off now. What it already wrote is set aside
once, in the agent's data, so the prompt is the shipped one again and
nothing the family may want back is deleted.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_AGENT = _REPO_ROOT / "stacklets" / "agent"
SCRIPT = _AGENT / "runtime" / "dream_aside.py"
SEED = _AGENT / "workspace"


def _seeded(name: str) -> str:
    return (SEED / name).read_text().replace("__AGENT_NAME__", "Stacky")


def _start(home: Path) -> None:
    subprocess.run([sys.executable, str(SCRIPT), str(home), str(SEED), "Stacky"], check=True)


def _home(tmp_path: Path) -> Path:
    """An agent home as the entrypoint seeds it, Dream not yet run."""
    home = tmp_path / "nanobot"
    ws = home / "workspace"
    (ws / "skills" / "family-memory").mkdir(parents=True)
    (ws / "skills" / "family-memory" / "SKILL.md").write_text("shipped")
    (ws / "skills" / "stack-commands").mkdir()
    (ws / "skills" / "stack-commands" / "SKILL.md").write_text("written by the agent's start hook")
    for name in ("SOUL.md", "AGENTS.md", "USER.md"):
        (ws / name).write_text(_seeded(name))
    return home


def _dream(home: Path) -> None:
    """What Dream leaves behind after a few runs."""
    ws = home / "workspace"
    (ws / "memory").mkdir()
    (ws / "memory" / "MEMORY.md").write_text("- `stack memory list show` fails; use read_file\n")
    (ws / "USER.md").write_text(_seeded("USER.md") + "\n- Buys eggs at the market\n")
    (ws / "SOUL.md").write_text(_seeded("SOUL.md") + "\nI keep answers short.\n")
    (ws / "skills" / "shopping-list").mkdir()
    (ws / "skills" / "shopping-list" / "SKILL.md").write_text("12 lists, as of October")


class TestWhatDreamWroteIsSetAside:

    def test_its_notes_leave_the_prompt_and_are_kept(self, tmp_path):
        home = _home(tmp_path)
        _dream(home)

        _start(home)

        ws, aside = home / "workspace", home / "dream-archive"
        assert not (ws / "memory" / "MEMORY.md").exists()
        assert "read_file" in (aside / "MEMORY.md").read_text()
        assert "eggs" in (aside / "USER.md").read_text()
        assert "short" in (aside / "SOUL.md").read_text()

    def test_the_changed_profile_files_go_so_the_seed_comes_back(self, tmp_path):
        """The entrypoint seeds SOUL.md and USER.md when they are missing."""
        home = _home(tmp_path)
        _dream(home)

        _start(home)

        assert not (home / "workspace" / "SOUL.md").exists()
        assert not (home / "workspace" / "USER.md").exists()

    def test_a_skill_dream_made_is_set_aside(self, tmp_path):
        home = _home(tmp_path)
        _dream(home)

        _start(home)

        assert not (home / "workspace" / "skills" / "shopping-list").exists()
        assert (home / "dream-archive" / "skills" / "shopping-list" / "SKILL.md").exists()

    def test_shipped_and_generated_skills_stay(self, tmp_path):
        home = _home(tmp_path)
        _dream(home)

        _start(home)

        skills = home / "workspace" / "skills"
        assert (skills / "family-memory" / "SKILL.md").exists()
        assert (skills / "stack-commands" / "SKILL.md").exists()


class TestWhatStaysWhereItIs:

    def test_files_nobody_changed_stay(self, tmp_path):
        home = _home(tmp_path)

        _start(home)

        ws = home / "workspace"
        for name in ("SOUL.md", "AGENTS.md", "USER.md"):
            assert (ws / name).read_text() == _seeded(name)

    def test_it_happens_once(self, tmp_path):
        """A later edit is the family's own, now that Dream is off."""
        home = _home(tmp_path)
        _start(home)
        soul = home / "workspace" / "SOUL.md"
        soul.write_text(soul.read_text() + "\nWe call him Stacks.\n")

        _start(home)

        assert "Stacks" in soul.read_text()

    def test_a_first_start_with_nothing_seeded_yet(self, tmp_path):
        home = tmp_path / "nanobot"

        _start(home)

        assert (home / "dream-archive").is_dir()
