"""The agent's nanobot config sets its limits where nanobot reads them.

nanobot resolves generation limits from the active model preset when
`agents.defaults.model_preset` names one; the preset's own
`context_window_tokens` (default 200000) then wins over the same field
under `agents.defaults`. A window set only under defaults is silently
ignored, and token consolidation keeps budgeting against 200000.
"""

from __future__ import annotations

import json
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[3] / "stacklets" / "agent" / "config.json"


def _config() -> dict:
    return json.loads(CONFIG.read_text())


def test_the_context_window_is_set_on_the_active_preset():
    config = _config()
    active = config["agents"]["defaults"]["model_preset"]

    assert config["model_presets"][active]["context_window_tokens"] == 32768


def test_no_context_window_where_nanobot_ignores_it():
    """A second value under defaults would read as the one in effect."""
    assert "context_window_tokens" not in _config()["agents"]["defaults"]


def _blocked_by_exec(command: str) -> bool:
    """Whether nanobot's exec tool refuses `command` under this config.

    nanobot 0.3.5 searches each deny pattern in the lowercased command
    (`ExecTool._guard_command`).
    """
    import re
    patterns = _config()["tools"]["exec"].get("deny_patterns", [])
    return any(re.search(p, command.lower()) for p in patterns)


def test_the_shell_cannot_confirm_deleting_a_list():
    """Only `list_edit` may delete a list with open items, after the person said yes.

    The store takes `--yes` from whoever runs it, and the agent's shell can
    run it too. Without this, the model could skip asking the person.
    """
    assert _blocked_by_exec("stack memory list delete partyliste --yes --as homer")
    assert _blocked_by_exec("stack memory list delete --yes partyliste")
    assert _blocked_by_exec("stack memory list delete partyliste --ye")


def test_the_shell_still_shows_and_deletes_lists_the_store_allows():
    assert not _blocked_by_exec("stack memory list show partyliste")
    assert not _blocked_by_exec("stack memory list delete partyliste --as homer")
