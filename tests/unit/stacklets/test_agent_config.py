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


# What exec runs and refuses (plain `stack` lines only, no `list delete
# --yes`) is driven through nanobot's own guard in
# test_agent_reads_for_the_room.py, row 12: nanobot applies its patterns only
# with `restrict_to_workspace` on, which a copy of the matching here missed.


def test_the_shell_finds_python_for_the_stack_client():
    """`stack` in the agent's shell reaches the host through a Python client.

    nanobot 0.3.5 runs each exec command with only HOME, LANG, TERM and the
    keys named in `allowed_env_keys` (`ExecTool._build_env`). The shell still
    finds `stack` on its built-in search path, but the client's
    `#!/usr/bin/env python3` then searches only /bin and /usr/bin, and the
    image's Python is in /usr/local/bin: every `stack` call fails with 127.
    """
    client = CONFIG.parent / "client" / "stack"
    assert client.read_text().startswith("#!/usr/bin/env python3")

    assert "PATH" in _config()["tools"]["exec"].get("allowed_env_keys", [])


def test_the_shell_gives_the_stack_client_the_agents_token():
    """`stack` in the agent's shell sends the agent's token to the host API.

    The API answers only a caller with its own token, and the client reads
    it from `STACK_API_TOKEN`. The exec tool passes only the keys named in
    `allowed_env_keys`, so without it every `stack` call from the shell is
    refused.
    """
    assert "STACK_API_TOKEN" in _config()["tools"]["exec"].get("allowed_env_keys", [])


def test_dream_stays_off():
    """Dream condenses every room's history into files that every room's
    prompt includes, so what one person said in a direct chat could come
    up in the family room. The vault is the agent's only memory."""
    assert _config()["agents"]["defaults"]["dream"]["enabled"] is False
