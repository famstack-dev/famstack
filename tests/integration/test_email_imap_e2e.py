"""End-to-end: a real IMAP server (GreenMail) -> imaplib -> parse_email.

Pins the email ingestion transport against a real IMAP server with fabricated
data only (no real account, no network). The chosen ingestion path is stdlib
`imaplib` fetch + `parse_email`; this proves it round-trips a UTF-8 message
faithfully end to end, which a unit test on fixture strings cannot.

Self-contained: spins up and tears down its own GreenMail container, so it
leaves nothing behind. Skipped when Docker is unavailable.
"""

from __future__ import annotations

import imaplib
import shutil
import smtplib
import subprocess
import sys
import time
from pathlib import Path

import pytest

_LIB_DIR = Path(__file__).resolve().parent.parent.parent / "lib"
sys.path.insert(0, str(_LIB_DIR))

from stack.email_message import parse_email  # noqa: E402
from stack.mail_fetcher import FolderCursor, MailAccount, MailFetcher  # noqa: E402

pytestmark = pytest.mark.skipif(
    shutil.which("docker") is None, reason="docker not available",
)

_IMAGE = "greenmail/standalone:2.1.3"
_NAME = "famstack-greenmail-pytest"
_SMTP_PORT = 3025
_IMAP_PORT = 3143


@pytest.fixture(scope="module")
def greenmail():
    """Run a throwaway GreenMail (auth disabled, test ports), tear it down."""
    subprocess.run(["docker", "rm", "-f", _NAME], capture_output=True)
    started = subprocess.run(
        [
            "docker", "run", "-d", "--name", _NAME,
            "-p", f"{_SMTP_PORT}:3025", "-p", f"{_IMAP_PORT}:3143",
            "-e", (
                "GREENMAIL_OPTS=-Dgreenmail.setup.test.all "
                "-Dgreenmail.hostname=0.0.0.0 -Dgreenmail.auth.disabled"
            ),
            _IMAGE,
        ],
        capture_output=True, text=True,
    )
    if started.returncode != 0:
        pytest.skip(f"could not start GreenMail: {started.stderr.strip()}")
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                imaplib.IMAP4("localhost", _IMAP_PORT).logout()
                break
            except (OSError, EOFError, imaplib.IMAP4.error):
                # connection refused, or socket up but banner not ready yet
                time.sleep(0.5)
        else:
            pytest.skip("GreenMail IMAP did not come up in time")
        yield
    finally:
        subprocess.run(["docker", "rm", "-f", _NAME], capture_output=True)


def test_imap_roundtrip_parses_faithfully(greenmail):
    user = "homer@example.org"
    raw = (
        "From: Springfield School <office@springfield-school.example>\r\n"
        f"To: {user}\r\n"
        "Subject: Elternabend am Freitag\r\n"
        "Message-ID: <e2e-1@springfield-school.example>\r\n"
        "Date: Sat, 21 Jun 2026 09:00:00 +0000\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "\r\n"
        "Bitte das Formular bis Freitag zurücksenden.\r\n"
    ).encode("utf-8")

    # Send through GreenMail's SMTP.
    smtp = smtplib.SMTP("localhost", _SMTP_PORT, timeout=10)
    smtp.sendmail("office@springfield-school.example", [user], raw)
    smtp.quit()
    time.sleep(1)

    # Fetch through GreenMail's IMAP with the stdlib client (the ingestion path).
    imap = imaplib.IMAP4("localhost", _IMAP_PORT)
    imap.login(user, "irrelevant")  # auth disabled
    imap.select("INBOX")
    _, data = imap.search(None, "ALL")
    ids = data[0].split()
    assert ids, "message was not delivered to INBOX"
    _, msgdata = imap.fetch(ids[-1], "(RFC822)")
    fetched = msgdata[0][1]
    imap.logout()

    # The full ingestion contract, end to end against a real IMAP server.
    p = parse_email(fetched)
    assert p.subject == "Elternabend am Freitag"
    assert p.from_name == "Springfield School"
    assert p.from_addr == "office@springfield-school.example"
    assert p.message_id == "e2e-1@springfield-school.example"
    assert p.date == "2026-06-21"
    assert p.body.strip() == "Bitte das Formular bis Freitag zurücksenden."


def _send(user: str, message_id: str, subject: str) -> None:
    raw = (
        f"From: Sender <sender@x.example>\r\nTo: {user}\r\n"
        f"Subject: {subject}\r\nMessage-ID: {message_id}\r\n"
        "Date: Sat, 21 Jun 2026 09:00:00 +0000\r\n\r\nbody\r\n"
    ).encode("utf-8")
    smtp = smtplib.SMTP("localhost", _SMTP_PORT, timeout=10)
    smtp.sendmail("sender@x.example", [user], raw)
    smtp.quit()


def test_mailfetcher_fetches_new_and_dedups(greenmail):
    user = "marge@example.org"  # own INBOX, isolated from the other test
    _send(user, "<m1@h>", "First")
    _send(user, "<m2@h>", "Second")
    time.sleep(1)

    fetcher = MailFetcher(MailAccount(
        host="localhost", port=_IMAP_PORT, user=user, password="x", ssl=False,
    ))

    first = fetcher.fetch_new(set())
    assert {p.message_id for p in first} == {"m1@h", "m2@h"}

    # Already-seen Message-IDs are dropped -> idempotent re-run.
    again = fetcher.fetch_new({"m1@h"})
    assert {p.message_id for p in again} == {"m2@h"}

    nothing = fetcher.fetch_new({"m1@h", "m2@h"})
    assert nothing == []


def test_mailfetcher_uid_incremental(greenmail):
    """With a persistent cursor, a second poll fetches only what arrived
    since the first — proving the whole folder isn't re-downloaded."""
    user = "lisa@example.org"  # own INBOX, isolated from the other tests
    _send(user, "<u1@h>", "First")
    time.sleep(1)

    fetcher = MailFetcher(MailAccount(
        host="localhost", port=_IMAP_PORT, user=user, password="x", ssl=False,
    ))
    cursor = FolderCursor()

    first = fetcher.fetch_new(set(), cursor)
    assert {p.message_id for p in first} == {"u1@h"}
    assert first[0].uid is not None
    # The watermark advanced past the first message and pinned UIDVALIDITY.
    assert cursor.last_uid == first[0].uid
    assert cursor.uidvalidity is not None

    _send(user, "<u2@h>", "Second")
    time.sleep(1)

    # Empty seen set: a full rescan would return BOTH. Incremental returns
    # only the message above the watermark.
    second = fetcher.fetch_new(set(), cursor)
    assert {p.message_id for p in second} == {"u2@h"}

    # Nothing new -> empty, watermark unchanged.
    high = cursor.last_uid
    assert fetcher.fetch_new(set(), cursor) == []
    assert cursor.last_uid == high


def test_mailfetcher_since_floor(greenmail):
    """A `since` floor bounds the fetch to mail received on/after that date.

    GreenMail stamps fresh mail with INTERNALDATE = now, so a future floor
    excludes it and a past floor includes it — proving SINCE is applied."""
    user = "maggie@example.org"  # own INBOX, isolated from the other tests
    _send(user, "<s1@h>", "Existing")
    time.sleep(1)

    def fetcher(since):
        return MailFetcher(MailAccount(
            host="localhost", port=_IMAP_PORT, user=user, password="x",
            ssl=False, since=since,
        ))

    # Floor in the future -> the just-delivered message is below it.
    assert fetcher("2099-01-01").fetch_new(set(), FolderCursor()) == []

    # Floor in the past -> the message is included.
    got = fetcher("2000-01-01").fetch_new(set(), FolderCursor())
    assert {p.message_id for p in got} == {"s1@h"}


def test_mailfetcher_widening_since_refetches_older(greenmail):
    """The 'start narrow, then widen' workflow: narrowing keeps the watermark,
    widening resets it so the now-in-range older mail is fetched again."""
    user = "clancy@example.org"  # own INBOX, isolated from the other tests
    _send(user, "<w1@h>", "One")
    _send(user, "<w2@h>", "Two")
    time.sleep(1)

    cursor = FolderCursor()

    def fetch(since):
        f = MailFetcher(MailAccount(
            host="localhost", port=_IMAP_PORT, user=user, password="x",
            ssl=False, since=since,
        ))
        return f.fetch_new(set(), cursor)

    # Wide floor: both fetched, watermark advances to the top.
    assert {p.message_id for p in fetch("2000-01-01")} == {"w1@h", "w2@h"}
    assert cursor.last_uid > 0

    # Narrowing (future floor): no reset, watermark held -> nothing re-fetched.
    high = cursor.last_uid
    assert fetch("2099-01-01") == []
    assert cursor.last_uid == high

    # Widening back: watermark reset, the older mail comes back (empty seen
    # set, so a full rescan is what proves the reset happened).
    assert {p.message_id for p in fetch("2000-01-01")} == {"w1@h", "w2@h"}


def test_mailfetcher_dates_no_date_header_by_internaldate(greenmail):
    """A message with no Date header is still dated (by INTERNALDATE), not
    left None to fall back to the processing date downstream."""
    user = "abe@example.org"  # own INBOX, isolated from the other tests
    raw = (
        f"From: Sender <s@x.example>\r\nTo: {user}\r\n"
        "Subject: No date header\r\nMessage-ID: <nd@h>\r\n"
        "\r\nbody without a date\r\n"  # deliberately no Date header
    ).encode("utf-8")
    smtp = smtplib.SMTP("localhost", _SMTP_PORT, timeout=10)
    smtp.sendmail("s@x.example", [user], raw)
    smtp.quit()
    time.sleep(1)

    fetcher = MailFetcher(MailAccount(
        host="localhost", port=_IMAP_PORT, user=user, password="x", ssl=False,
    ))
    got = fetcher.fetch_new(set(), FolderCursor())
    assert len(got) == 1
    # parse_email alone yields no date; the fetcher backfills from INTERNALDATE.
    assert got[0].date is not None
    assert len(got[0].date) == 10 and got[0].date[4] == "-"  # YYYY-MM-DD
