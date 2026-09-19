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

CONFIG = Path(__file__).resolve().parents[2] / "stacklets" / "agent" / "config.json"


def _config() -> dict:
    return json.loads(CONFIG.read_text())


def test_the_context_window_is_set_on_the_active_preset():
    config = _config()
    active = config["agents"]["defaults"]["model_preset"]

    assert config["model_presets"][active]["context_window_tokens"] == 32768


def test_no_context_window_where_nanobot_ignores_it():
    """A second value under defaults would read as the one in effect."""
    assert "context_window_tokens" not in _config()["agents"]["defaults"]
