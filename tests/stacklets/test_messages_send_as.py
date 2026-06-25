"""`resolve_login` — the shared `--as <user>` credential lookup that lets
`stack messages send` and `upload` post as a family member instead of the bot.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "messages" / "cli"))

from _matrix import resolve_login  # noqa: E402


class TestResolveLogin:
    def test_family_member_reads_their_password(self):
        secrets = {"global__USER_MARGE_PASSWORD": "s3cret"}
        assert resolve_login("marge", secrets) == ("marge", "s3cret", None)

    def test_missing_family_password_is_an_error(self):
        user, pw, err = resolve_login("homer", {})
        assert user is None and pw is None
        assert "homer" in err and "USER_HOMER_PASSWORD" in err

    def test_default_sender_is_stacker_bot(self):
        secrets = {"core__STACKER_BOT_PASSWORD": "botpass"}
        assert resolve_login(None, secrets) == ("stacker-bot", "botpass", None)

    def test_no_bot_password_is_an_error(self):
        user, pw, err = resolve_login(None, {})
        assert user is None and "stacker-bot" in err
