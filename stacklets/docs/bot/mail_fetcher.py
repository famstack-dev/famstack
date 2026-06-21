"""IMAP fetcher — pulls new email off the server for ingestion.

Stdlib `imaplib` is the chosen ingestion transport (himalaya/mbsync are
swappable behind the same RFC822 seam; see docs/design/agent/email-tools.md).
It is blocking I/O, so the async mail bot calls `fetch_new` via
`asyncio.to_thread`, exactly as the git mirror wraps the sync Forgejo client.

New-message detection is by Message-ID against a caller-supplied seen set, so a
re-run never double-files (ADR-010 dedup). Ingestion is read-only: the folder is
selected `readonly=True`, so server flags are never mutated.
"""

from __future__ import annotations

import imaplib
from dataclasses import dataclass

from extractors import ParsedEmail, parse_email


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


class MailFetcher:
    """Read-only IMAP fetch of new messages, parsed for the pipeline."""

    def __init__(self, account: MailAccount):
        self._a = account

    def fetch_new(self, seen_message_ids: set[str]) -> list[ParsedEmail]:
        """Parsed messages in the folder whose Message-ID is not yet seen.

        Connects, selects the folder read-only, fetches each message's RFC822
        bytes, parses, and drops any whose Message-ID is already in
        ``seen_message_ids``. The caller persists the seen set (the bot's
        cache), so re-running is idempotent. Messages with no Message-ID can't
        be deduped and are always returned — vanishingly rare in real mail.
        """
        client = self._connect()
        try:
            typ, _ = client.select(self._a.folder, readonly=True)
            if typ != "OK":
                return []
            typ, data = client.search(None, "ALL")
            if typ != "OK" or not data or not data[0]:
                return []

            out: list[ParsedEmail] = []
            for num in data[0].split():
                typ, msgdata = client.fetch(num, "(RFC822)")
                if typ != "OK" or not msgdata or not msgdata[0]:
                    continue
                raw = msgdata[0][1]
                if not isinstance(raw, (bytes, bytearray)):
                    continue
                parsed = parse_email(bytes(raw))
                if parsed.message_id and parsed.message_id in seen_message_ids:
                    continue
                out.append(parsed)
            return out
        finally:
            try:
                client.logout()
            except (imaplib.IMAP4.error, OSError):
                pass

    def _connect(self) -> imaplib.IMAP4:
        cls = imaplib.IMAP4_SSL if self._a.ssl else imaplib.IMAP4
        client = cls(self._a.host, self._a.port, timeout=self._a.timeout)
        client.login(self._a.user, self._a.password)
        return client
