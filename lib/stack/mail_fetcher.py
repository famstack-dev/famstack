"""IMAP fetcher — pulls new email off the server for ingestion.

Stdlib `imaplib` is the chosen ingestion transport (himalaya/mbsync are
swappable behind the same RFC822 seam; see docs/design/agent/email-tools.md).
It is blocking I/O, so the async mail bot calls `fetch_new` via
`asyncio.to_thread`, exactly as the git mirror wraps the sync Forgejo client.

Fetching is incremental by IMAP UID: a per-folder watermark (`FolderCursor`)
remembers the highest UID we've downloaded, and each poll asks only for UIDs
above it — so a steady-state poll over a 50k-message inbox transfers the one
new message, not the whole folder. Message-ID dedup against a caller-supplied
seen set stays as the idempotency backstop (ADR-010): it survives a UIDVALIDITY
reset, the `N:*` search quirk, and a re-fetched batch after a post failure.
Ingestion is read-only: the folder is selected `readonly=True`, so server flags
are never mutated.
"""

from __future__ import annotations

import imaplib
import os
import re
from dataclasses import dataclass
from datetime import date

from stack.email_message import ParsedEmail, parse_email

# IMAP SEARCH dates are DD-Mon-YYYY with English month abbreviations (RFC 3501),
# independent of the host locale — so build the month from a fixed table rather
# than strftime, which would localize "Jun" to the server's language.
_IMAP_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


def _imap_since(iso: str | None) -> str | None:
    """An ISO ``YYYY-MM-DD`` floor as an IMAP ``DD-Mon-YYYY`` date.

    Returns None for an empty or malformed value — a bad `since` must not
    silently widen or break the search; the fetch falls back to no date floor.
    """
    if not iso:
        return None
    try:
        d = date.fromisoformat(iso.strip())
    except ValueError:
        return None
    return f"{d.day:02d}-{_IMAP_MONTHS[d.month - 1]}-{d.year}"


_INTERNALDATE_RE = re.compile(
    rb'INTERNALDATE "(\d{2})-(\w{3})-(\d{4})', re.IGNORECASE,
)
_MONTH_NUM = {m.lower(): i for i, m in enumerate(_IMAP_MONTHS, start=1)}


def _internaldate(fetch_meta) -> str | None:
    """The server-received date (INTERNALDATE) from a FETCH response line.

    ``fetch_meta`` is the metadata half of an imaplib FETCH tuple, e.g.
    ``b'1 (INTERNALDATE "15-Mar-2025 08:30:00 +0000" RFC822 {...}'``. We take
    the calendar date as the server recorded it (its own offset), to stay
    consistent with the Date-header path and independent of the host timezone.
    Returns YYYY-MM-DD, or None when no INTERNALDATE is present.
    """
    if not fetch_meta:
        return None
    if isinstance(fetch_meta, str):
        fetch_meta = fetch_meta.encode("latin-1", "replace")
    m = _INTERNALDATE_RE.search(fetch_meta)
    if not m:
        return None
    month = _MONTH_NUM.get(m.group(2).decode("ascii", "replace").lower())
    if not month:
        return None
    return f"{int(m.group(3)):04d}-{month:02d}-{int(m.group(1)):02d}"


def _since_widened(new: str | None, old: str | None) -> bool:
    """True when the date floor moved *earlier* (a wider, more inclusive window).

    ISO dates compare chronologically as strings. ``None`` means "no floor" —
    the widest possible window. Widening (or dropping the floor) must re-scan
    the now-in-range older mail; narrowing needs no re-fetch. ``old is None``
    is already unbounded, so nothing widens it.
    """
    if new == old:
        return False
    if old is None:
        return False
    if new is None:
        return True
    return new < old


@dataclass
class MailAccount:
    """Connection details for one IMAP account + folder."""

    host: str
    port: int
    user: str
    password: str
    folder: str = "INBOX"
    ssl: bool = True
    timeout: int = 30
    # The configured account name (stack.toml [[mail.accounts]] name).
    # Carried so the bot can tag a message with where it came from.
    name: str = ""
    # Optional backfill floor (ISO YYYY-MM-DD). When set, the fetch is bounded
    # to messages the server received on/after this date — so a fresh account
    # can ingest existing mail from a chosen point instead of the whole folder.
    since: str | None = None


def account_from_entry(entry: dict) -> "MailAccount | None":
    """Build a ``MailAccount`` from one rendered ``MAIL_ACCOUNTS_JSON`` entry.

    The single parser shared by the mail bot and the ``stack core mail``
    diagnostic, so connection details are read one way. The password rides in
    the entry (``imap_password``, rendered from the secret store) or falls back
    to ``MAIL_<NAME>_IMAP_PASSWORD`` in the env. Returns None when a required
    field (name, host, user, password) is missing — the caller decides whether
    that is a skip or an error. The ``room`` binding is the bot's concern and
    is read separately.
    """
    name = (entry.get("name") or "").strip()
    host = entry.get("imap_host")
    user = entry.get("imap_user")
    if not (name and host and user):
        return None
    password = entry.get("imap_password") or os.environ.get(
        f"MAIL_{name.upper()}_IMAP_PASSWORD", "",
    )
    if not password:
        return None
    return MailAccount(
        host=host,
        port=int(entry.get("imap_port") or 993),
        user=user,
        password=password,
        folder=entry.get("folder") or "INBOX",
        ssl=str(entry.get("ssl", "true")).lower() != "false",
        name=name,
        since=(entry.get("since") or None),
    )


@dataclass
class FolderCursor:
    """Per-folder UID watermark for incremental fetch.

    IMAP UIDs are stable within a folder *only while* its UIDVALIDITY is
    unchanged. We remember the validity we synced against and the highest UID
    we've downloaded. The next poll fetches strictly above ``last_uid``. If the
    server reports a different ``uidvalidity`` (the folder was recreated — rare
    but real), every stored UID is meaningless, so the fetcher drops the
    watermark to 0 and rescans; Message-ID dedup keeps the rescan from
    re-filing. The bot owns the object and persists it; the fetcher advances it
    on a clean download, and the bot rolls it back on a post failure.

    ``since`` records the date floor this watermark was built against. When the
    configured floor is *widened* (moved earlier), the watermark would hide the
    newly-in-range older mail, so the fetcher resets it and rescans the wider
    window — the documented "start narrow, then widen" backfill workflow.
    """

    uidvalidity: int | None = None
    last_uid: int = 0
    since: str | None = None


class MailFetcher:
    """Read-only, UID-incremental IMAP fetch, parsed for the pipeline."""

    def __init__(self, account: MailAccount):
        self._a = account

    def fetch_new(
        self, seen_message_ids: set[str], cursor: FolderCursor | None = None,
    ) -> list[ParsedEmail]:
        """Parsed messages with a UID above the cursor, not yet seen.

        Selects the folder read-only, then UID-searches for everything above
        ``cursor.last_uid`` and downloads only those. On return, ``cursor`` is
        advanced to the highest UID present in the folder (so an all-deduped or
        empty poll still moves the watermark forward — otherwise a UIDVALIDITY
        reset would degrade to a full rescan every poll). Each returned message
        carries its ``uid`` so the bot can advance/roll back the watermark by
        post outcome.

        ``cursor`` defaults to a throwaway (full-scan, no persistence) so
        callers that only want Message-ID dedup keep the old behaviour.
        Messages already in ``seen_message_ids`` are dropped; those with no
        Message-ID can't be deduped and are always returned (rare in real mail).
        """
        cursor = cursor if cursor is not None else FolderCursor()
        client = self._connect()
        try:
            typ, _ = client.select(self._a.folder, readonly=True)
            if typ != "OK":
                return []

            # A changed (or first-seen) UIDVALIDITY invalidates the watermark.
            validity = self._uidvalidity(client)
            if validity is not None and validity != cursor.uidvalidity:
                cursor.uidvalidity = validity
                cursor.last_uid = 0

            # A widened date floor must re-open the lower UID range so the
            # older now-in-range mail is fetched; narrowing keeps the watermark.
            # Normalize an invalid floor to None (no floor) for the comparison.
            configured_since = self._a.since if _imap_since(self._a.since) else None
            if _since_widened(configured_since, cursor.since):
                cursor.last_uid = 0
            cursor.since = configured_since

            lo = cursor.last_uid + 1
            criteria = f"UID {lo}:*"
            since = _imap_since(self._a.since)
            if since:
                # Server-side date floor (INTERNALDATE); ANDs with the UID
                # range, so backfill stays bounded and incremental polls cheap.
                criteria = f"{criteria} SINCE {since}"
            typ, data = client.uid("SEARCH", criteria)
            if typ != "OK" or not data or not data[0]:
                return []
            uids = sorted(int(u) for u in data[0].split())
            if not uids:
                return []
            # `N:*` always returns at least the highest UID even when it is
            # below N, so filter to strictly-new UIDs before downloading.
            highest = uids[-1]
            new_uids = [u for u in uids if u > cursor.last_uid]

            out: list[ParsedEmail] = []
            for uid in new_uids:
                typ, msgdata = client.uid("FETCH", str(uid), "(RFC822 INTERNALDATE)")
                if typ != "OK" or not msgdata or not msgdata[0]:
                    continue
                raw = msgdata[0][1]
                if not isinstance(raw, (bytes, bytearray)):
                    continue
                parsed = parse_email(bytes(raw))
                parsed.uid = uid
                # "The date we got the email": prefer the Date header, but a
                # message without one must still date by when the server
                # received it (INTERNALDATE) — never by when the bot happened
                # to process it. Matters when backfilling an old folder.
                if parsed.date is None:
                    parsed.date = _internaldate(msgdata[0][0])
                if parsed.message_id and parsed.message_id in seen_message_ids:
                    continue
                out.append(parsed)

            # We've now accounted for every UID up to the folder's highest.
            cursor.last_uid = max(cursor.last_uid, highest)
            return out
        finally:
            try:
                client.logout()
            except (imaplib.IMAP4.error, OSError):
                pass

    @staticmethod
    def _uidvalidity(client: imaplib.IMAP4) -> int | None:
        """The selected folder's UIDVALIDITY (RFC 3501 SELECT response)."""
        typ, data = client.response("UIDVALIDITY")
        if typ == "UIDVALIDITY" and data and data[0]:
            try:
                return int(data[0])
            except (TypeError, ValueError):
                return None
        return None

    def _connect(self) -> imaplib.IMAP4:
        cls = imaplib.IMAP4_SSL if self._a.ssl else imaplib.IMAP4
        client = cls(self._a.host, self._a.port, timeout=self._a.timeout)
        client.login(self._a.user, self._a.password)
        return client

    def probe(self) -> dict:
        """Connect and report the server's folders for diagnosing config.

        Powers ``stack core mail``: logs in (so a bad host/credential surfaces
        as the raised exception), lists every folder with its flags — the real
        IMAP names, which often differ from the webmail labels (Gmail's
        ``[Gmail]/All Mail``, a localized ``Gesendet``, nested paths) — and
        counts the configured folder so the admin can confirm the `folder`
        value points where they think. Read-only. Raises on connection or
        auth failure; the caller renders the per-account result.
        """
        client = self._connect()
        try:
            folders: list[tuple[str, str]] = []
            typ, data = client.list()
            if typ == "OK":
                for line in data or []:
                    if line:
                        folders.append(_parse_list_line(line))
            count = None
            typ, data = client.select(self._a.folder, readonly=True)
            if typ == "OK" and data and data[0] is not None:
                try:
                    count = int(data[0])
                except (TypeError, ValueError):
                    count = None
            return {"folders": folders, "folder": self._a.folder, "count": count}
        finally:
            try:
                client.logout()
            except (imaplib.IMAP4.error, OSError):
                pass


_LIST_RE = re.compile(r'^\((?P<flags>[^)]*)\)\s+(?:"[^"]*"|NIL)\s+(?P<name>.+)$')


def _parse_list_line(line) -> tuple[str, str]:
    """Parse an IMAP LIST response line into ``(flags, folder_name)``.

    A line looks like ``(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"``:
    flags in parens, a hierarchy delimiter, then the (often quoted) name.
    Falls back to the raw line as the name when the shape is unexpected.
    Modified UTF-7 in names is left as-is (rare for the folder-picking use).
    """
    s = line.decode("utf-8", "replace") if isinstance(line, (bytes, bytearray)) else str(line)
    m = _LIST_RE.match(s.strip())
    if not m:
        return ("", s.strip())
    name = m.group("name").strip()
    if len(name) >= 2 and name[0] == '"' and name[-1] == '"':
        name = name[1:-1]
    return (m.group("flags").strip(), name)
