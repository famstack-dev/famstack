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

from stack.mail_fetcher import (  # noqa: E402
    _imap_since,
    _internaldate,
    _parse_list_line,
    _since_widened,
    account_from_entry,
)
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


def test_since_floor_carried_when_set():
    cfg = {"accounts": [{
        "name": "work", "imap_host": "h", "imap_user": "u",
        "room": "!r:hs", "since": "2026-01-01",
    }]}
    a = json.loads(_mail_accounts_env(cfg, lambda n: "p"))[0]
    assert a["since"] == "2026-01-01"


def test_since_omitted_when_absent():
    cfg = {"accounts": [{
        "name": "work", "imap_host": "h", "imap_user": "u", "room": "!r:hs",
    }]}
    a = json.loads(_mail_accounts_env(cfg, lambda n: "p"))[0]
    assert "since" not in a  # no floor -> full-folder backfill on first poll


class TestImapSince:
    """ISO date -> IMAP DD-Mon-YYYY, locale-independent."""

    def test_converts_iso_to_imap_date(self):
        assert _imap_since("2026-06-01") == "01-Jun-2026"
        assert _imap_since("2026-12-25") == "25-Dec-2026"

    def test_empty_or_none_is_none(self):
        assert _imap_since(None) is None
        assert _imap_since("") is None

    def test_malformed_is_none_not_an_exception(self):
        # A bad floor must not break or silently widen the search.
        assert _imap_since("not-a-date") is None
        assert _imap_since("2026-13-01") is None


class TestInternaldate:
    """INTERNALDATE -> YYYY-MM-DD, the fallback for a message with no Date
    header so old mail dates by receipt, not by processing time."""

    def test_parses_fetch_metadata_line(self):
        line = b'1 (INTERNALDATE "15-Mar-2025 08:30:00 +0000" RFC822 {123}'
        assert _internaldate(line) == "2025-03-15"

    def test_takes_date_in_its_own_offset_not_host_tz(self):
        # Date portion is read literally — no host-local conversion, so this
        # is stable regardless of the TZ the suite runs in.
        line = b'7 (INTERNALDATE "02-Jan-2024 23:59:59 +1300" RFC822 {9}'
        assert _internaldate(line) == "2024-01-02"

    def test_none_when_absent_or_empty(self):
        assert _internaldate(b"1 (RFC822 {123}") is None
        assert _internaldate(b"") is None
        assert _internaldate(None) is None


class TestSinceWidened:
    """Whether a new date floor is wider (earlier) than the applied one --
    the trigger to reset the UID watermark and re-scan older mail."""

    def test_earlier_floor_is_widening(self):
        assert _since_widened("2026-01-01", "2026-06-16") is True

    def test_later_floor_is_narrowing(self):
        assert _since_widened("2026-06-16", "2026-01-01") is False

    def test_same_floor_no_change(self):
        assert _since_widened("2026-01-01", "2026-01-01") is False

    def test_dropping_the_floor_widens(self):
        # Removing `since` entirely opens the window to the whole folder.
        assert _since_widened(None, "2026-01-01") is True

    def test_adding_a_floor_to_unbounded_does_not_widen(self):
        # Already unbounded (no floor) -> narrowing to a date needs no re-scan.
        assert _since_widened("2026-01-01", None) is False
        assert _since_widened(None, None) is False


class TestAccountFromEntry:
    """The shared MAIL_ACCOUNTS_JSON-entry parser (mail bot + `stack core mail`)."""

    def test_builds_account_with_embedded_password(self):
        acc = account_from_entry({
            "name": "work", "imap_host": "imap.x", "imap_port": 143,
            "imap_user": "u@x", "imap_password": "secret", "folder": "Archive",
            "ssl": False, "since": "2026-01-01",
        })
        assert acc is not None
        assert (acc.host, acc.port, acc.user, acc.password) == ("imap.x", 143, "u@x", "secret")
        assert acc.folder == "Archive" and acc.ssl is False
        assert acc.name == "work" and acc.since == "2026-01-01"

    def test_defaults_port_folder_ssl(self):
        acc = account_from_entry({
            "name": "fam", "imap_host": "h", "imap_user": "f@x", "imap_password": "p",
        })
        assert acc.port == 993 and acc.folder == "INBOX" and acc.ssl is True

    def test_password_from_env_fallback(self, monkeypatch):
        monkeypatch.setenv("MAIL_FAM_IMAP_PASSWORD", "from-env")
        acc = account_from_entry({"name": "fam", "imap_host": "h", "imap_user": "f@x"})
        assert acc is not None and acc.password == "from-env"

    def test_none_without_required_fields(self):
        assert account_from_entry({"imap_host": "h", "imap_user": "u"}) is None  # no name
        assert account_from_entry({"name": "x", "imap_user": "u", "imap_password": "p"}) is None  # no host

    def test_none_without_password(self, monkeypatch):
        monkeypatch.delenv("MAIL_X_IMAP_PASSWORD", raising=False)
        assert account_from_entry({"name": "x", "imap_host": "h", "imap_user": "u"}) is None


class TestParseListLine:
    """IMAP LIST response line -> (flags, folder name)."""

    def test_simple_quoted_name(self):
        assert _parse_list_line(b'(\\HasNoChildren) "/" "INBOX"') == ("\\HasNoChildren", "INBOX")

    def test_special_use_flags_and_bracketed_name(self):
        flags, name = _parse_list_line(b'(\\HasChildren \\Noselect) "/" "[Gmail]"')
        assert "\\Noselect" in flags and name == "[Gmail]"

    def test_nested_gmail_name(self):
        flags, name = _parse_list_line(b'(\\All \\HasNoChildren) "/" "[Gmail]/All Mail"')
        assert name == "[Gmail]/All Mail"

    def test_unquoted_name(self):
        assert _parse_list_line(b'(\\HasNoChildren) "." Archive') == ("\\HasNoChildren", "Archive")

    def test_nil_delimiter(self):
        assert _parse_list_line(b'(\\Noselect) NIL "Shared"')[1] == "Shared"

    def test_unparseable_falls_back_to_raw(self):
        assert _parse_list_line(b'garbage line') == ("", "garbage line")
