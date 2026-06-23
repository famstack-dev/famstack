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

from stack.mail_fetcher import _imap_since, _internaldate, _since_widened  # noqa: E402
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
