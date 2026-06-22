"""_mail_accounts_env: stack.toml [mail] -> MAIL_ACCOUNTS_JSON for the mail bot.

Passwords come from the secret store via the lookup, never from stack.toml, and
ride embedded in the rendered JSON (the same way other container secrets reach
.env). Pure function — no Stack instance needed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

from stack.stack import _mail_accounts_env  # noqa: E402


def test_no_accounts_is_empty():
    assert _mail_accounts_env({}, lambda n: "") == ""
    assert _mail_accounts_env({"accounts": []}, lambda n: "") == ""


def test_account_embeds_secret_password():
    cfg = {"accounts": [{
        "name": "family", "imap_host": "imap.x", "imap_user": "f@x",
        "folder": "INBOX", "room": "!post:hs",
    }]}
    # The lookup receives the raw account name; uppercasing happens in the
    # real secret-store call wired by _build_template_vars.
    out = json.loads(_mail_accounts_env(cfg, lambda n: "secret" if n == "family" else ""))
    assert len(out) == 1
    a = out[0]
    assert a["imap_host"] == "imap.x"
    assert a["imap_user"] == "f@x"
    assert a["imap_password"] == "secret"
    assert a["folder"] == "INBOX"
    assert a["room"] == "!post:hs"
    assert a["imap_port"] == 993   # default
    assert a["ssl"] is True        # default


def test_account_without_name_skipped():
    cfg = {"accounts": [{"imap_host": "x"}, {"name": "ok", "imap_host": "y"}]}
    out = json.loads(_mail_accounts_env(cfg, lambda n: "p"))
    assert [a["name"] for a in out] == ["ok"]


def test_explicit_port_and_ssl_preserved():
    cfg = {"accounts": [{
        "name": "work", "imap_host": "h", "imap_user": "u",
        "imap_port": 143, "ssl": False, "room": "!r:hs",
    }]}
    a = json.loads(_mail_accounts_env(cfg, lambda n: "p"))[0]
    assert a["imap_port"] == 143
    assert a["ssl"] is False
