"""Discovery picks up multiple bots per stacklet via *.bot.toml siblings.

Core ships stacker-bot (`bot.toml`) and mail-bot (`mail.bot.toml`) from one
`bot/` dir; `_bot_toml_files` is what makes that work.
"""

from __future__ import annotations

import sys
from pathlib import Path

_RUNNER = Path(__file__).resolve().parent.parent.parent / "stacklets" / "core" / "bot-runner"
sys.path.insert(0, str(_RUNNER))

from main import _bot_toml_files  # noqa: E402


def test_primary_and_extra_declarations(tmp_path):
    d = tmp_path / "bot"
    d.mkdir()
    (d / "bot.toml").write_text("id = 'stacker-bot'")
    (d / "mail.bot.toml").write_text("id = 'mail-bot'")
    names = [f.name for f in _bot_toml_files(d)]
    assert names == ["bot.toml", "mail.bot.toml"]  # primary first


def test_primary_not_double_counted(tmp_path):
    d = tmp_path / "bot"
    d.mkdir()
    (d / "bot.toml").write_text("id = 'a-bot'")
    # bot.toml must not also match the *.bot.toml glob.
    assert [f.name for f in _bot_toml_files(d)] == ["bot.toml"]


def test_extra_only(tmp_path):
    d = tmp_path / "bot"
    d.mkdir()
    (d / "mail.bot.toml").write_text("id = 'mail-bot'")
    assert [f.name for f in _bot_toml_files(d)] == ["mail.bot.toml"]


def test_missing_dir_is_empty(tmp_path):
    assert _bot_toml_files(tmp_path / "nope") == []
