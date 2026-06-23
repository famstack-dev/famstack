"""Mail bot — fetches email and posts it into the bound Matrix room.

A *core ingestion capability*, not a stacklet: email is another inbound
channel, like the Matrix interface itself, so it lives beside the framework
rather than as its own service. Credential note: this bot holds IMAP creds, so
long-term it should run in an egress-scoped container; the class is identical
wherever it runs, and isolation is a security-hardening step (gated by the
email security review), not a code change.

It is a thin transport adapter. It does NOT classify or write the vault. For
each new message it posts a *twofold source message* (see
`MicroBot.post_source_message`): a human-readable body plus a
`dev.famstack.source` block carrying the verbatim text and per-message fields.
The archivist then files it from the room exactly as it files a pasted URL —
one capture path, no duplicated pipeline.

Two pieces of state, persisted so restarts stay idempotent:
  - seen Message-IDs — new-mail detection (ADR-010 dedup).
  - thread_root -> Matrix root event id — so an email conversation folds onto
    one Matrix thread (every reply posted under its root).

Config comes from the environment (rendered from `stack.toml [mail]`; passwords
from the secret store), never from chat — a credential must not transit Matrix.
"""

from __future__ import annotations

import asyncio
import json
import os

from loguru import logger

from email_reply_parser import EmailReplyParser

from microbot import MicroBot
from stack.email_message import defang_links
from stack.mail_fetcher import FolderCursor, MailAccount, MailFetcher


class MailBot(MicroBot):
    name = "mail-bot"

    def __init__(self, *, homeserver, user_id, password, session_dir, **config):
        super().__init__(homeserver, user_id, password, session_dir, **config)
        self._interval = int(os.environ.get("MAIL_POLL_INTERVAL", "120"))
        # [(MailAccount, room_id), ...] — one entry per configured mailbox.
        self._accounts = self._load_accounts()
        self._state_file = self._session_dir / f"{self.name}-mail-state.json"
        self._seen, self._threads, self._cursors = self._load_state()
        self._poll_task: asyncio.Task | None = None
        # Indirection so tests inject a fake fetcher; production builds a real
        # read-only IMAP fetcher per account.
        self._fetcher_factory = MailFetcher

    # ── Config (from env, rendered from stack.toml [mail]) ────────────────

    def _load_accounts(self) -> list[tuple[MailAccount, str]]:
        raw = os.environ.get("MAIL_ACCOUNTS_JSON", "").strip()
        if not raw:
            return []
        try:
            entries = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning("[mail-bot] bad MAIL_ACCOUNTS_JSON: {}", e)
            return []
        out: list[tuple[MailAccount, str]] = []
        for entry in entries if isinstance(entries, list) else []:
            acc, room = self._account_from_entry(entry)
            if acc and room:
                out.append((acc, room))
        return out

    def _account_from_entry(self, entry: dict):
        """One `[[mail.accounts]]` entry -> (MailAccount, room_id).

        The password is never in the entry (it lives in the secret store);
        it is read from ``MAIL_<NAME>_IMAP_PASSWORD``. Missing required fields
        or password -> the account is skipped with a warning rather than
        crashing the bot.
        """
        name = (entry.get("name") or "").strip()
        host = entry.get("imap_host")
        user = entry.get("imap_user")
        room = entry.get("room")
        if not (name and host and user and room):
            logger.warning(
                "[mail-bot] account missing name/imap_host/imap_user/room: {}",
                entry,
            )
            return (None, None)
        # Password comes embedded in the rendered JSON (from the secret
        # store, via stack.toml render); the env var is a manual/test
        # override. Never read from chat.
        pw_env = f"MAIL_{name.upper()}_IMAP_PASSWORD"
        password = entry.get("imap_password") or os.environ.get(pw_env, "")
        if not password:
            logger.warning(
                "[mail-bot] no password for account '{}' (set mail__{}_IMAP_PASSWORD)",
                name, name.upper(),
            )
            return (None, None)
        account = MailAccount(
            host=host,
            port=int(entry.get("imap_port") or 993),
            user=user,
            password=password,
            folder=entry.get("folder") or "INBOX",
            ssl=str(entry.get("ssl", "true")).lower() != "false",
            name=name,
            since=(entry.get("since") or None),
        )
        return (account, room)

    # ── Lifecycle ────────────────────────────────────────────────────────

    def register_callbacks(self, client) -> None:
        """Start the IMAP poll loop — once per launch.

        Deliberately here and not in `on_first_sync`: that hook is gated by a
        `.welcomed` marker to fire once *ever* (for one-time welcomes), so a
        poller started there silently stops running after the first restart.
        `register_callbacks` runs on every launch, inside `start()`'s event
        loop, so the background task comes back with the bot.

        v1 registers no message handlers (post-only); the config conversation
        lands later, and credentials never come through chat.
        """
        if not self._accounts:
            logger.info("[mail-bot] no accounts configured; idle")
            return
        if self._poll_task is None:
            logger.info(
                "[mail-bot] polling {} account(s) every {}s",
                len(self._accounts), self._interval,
            )
            self._poll_task = asyncio.create_task(self._poll_loop())

    async def on_room_joined(self, room_id: str) -> None:
        """Introduce the bot and name the mailbox that feeds this room.

        Self-explaining UX: when invited, the bot states what it will deliver
        here so the family isn't left guessing which inbox is wired up. Bound
        mailboxes (from stack.toml [mail]) that route to this room are named
        with their folder; if none route here, it says so plainly.
        """
        matches = [acc for acc, room in self._accounts if room == room_id]
        if matches:
            lines = ["mail bot here. I'll deliver new email into this room:"]
            for acc in matches:
                since = f", from {acc.since}" if acc.since else ""
                lines.append(
                    f"- **{acc.user}**, folder `{acc.folder}`{since} "
                    f"(checked every {self._interval}s)"
                )
            await self._send(room_id, "\n".join(lines))
        else:
            await self._send(
                room_id,
                "mail bot here, but no mailbox in `stack.toml [mail]` routes "
                "to this room yet, so I won't deliver anything. Add this "
                "room's id to a `[[mail.accounts]]` entry and restart.",
            )

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                await self._poll_once()
            except Exception as e:
                logger.warning("[mail-bot] poll cycle failed: {}", e)
            await asyncio.sleep(self._interval)

    async def _poll_once(self) -> None:
        for account, room_id in self._accounts:
            await self._poll_account(account, room_id)

    async def _poll_account(self, account: MailAccount, room_id: str) -> None:
        """Fetch new mail for one account and post each message to its room.

        Blocking IMAP runs in a thread (like the git mirror). The fetcher only
        downloads UIDs above this folder's watermark and advances it to the
        folder's highest UID. Messages are posted oldest-UID-first so a thread's
        root is in place before its replies. On the first post failure the
        watermark is rolled back below that message and the loop stops, so the
        failure and everything after it is re-fetched next poll (no silent
        drop); the Message-ID seen set dedups anything already delivered.
        """
        cursor = self._cursors.setdefault(account.name, FolderCursor())
        fetcher = self._fetcher_factory(account)
        new = await asyncio.to_thread(fetcher.fetch_new, set(self._seen), cursor)
        if not new:
            self._save_state()  # the watermark may have advanced past dedups
            return
        new.sort(key=lambda p: (p.uid if p.uid is not None else 0, p.date or ""))
        for parsed in new:
            if not await self._post(parsed, room_id, account):
                if parsed.uid is not None:
                    cursor.last_uid = min(cursor.last_uid, parsed.uid - 1)
                break
        self._save_state()

    async def _post(self, parsed, room_id: str, account: MailAccount) -> bool:
        """Post one message; True on success, False if the send failed.

        A False return tells the caller to hold the watermark so the message is
        retried; the archivist's mid: marker keeps a retried double-post from
        duplicating the vault section.
        """
        root = self._threads.get(parsed.thread_root) if parsed.thread_root else None
        event_id = await self.post_source_message(
            room_id,
            body=self._human_body(parsed),
            source="email",
            raw_content=self._raw_content(parsed),
            fields=self._source_fields(parsed, account),
            thread_root_event_id=root,
        )
        if event_id is None:
            return False
        if parsed.thread_root and parsed.thread_root not in self._threads:
            self._threads[parsed.thread_root] = event_id
        if parsed.message_id:
            self._seen.add(parsed.message_id)
        return True

    # ── Rendering ────────────────────────────────────────────────────────

    def _human_body(self, p) -> str:
        """The human-facing view: an email-styled header, then the message.

        Subject on its own line (with an envelope marker so it reads as
        mail), a blockquote carrying sender + date, then the body. Blank
        lines force real paragraph breaks — single newlines collapse in
        Matrix's markdown, which is what made the first cut look cramped.
        """
        subject = p.subject or "(no subject)"
        sender = p.from_name or p.from_addr or "unknown sender"
        if p.from_name and p.from_addr:
            sender = f"{p.from_name} ({p.from_addr})"
        meta = [f"**From** {sender}"]
        if p.date:
            meta.append(f"**Date** {p.date}")
        parts = [f"📧 **{subject}**", "", "> " + " · ".join(meta)]
        fragment = self._raw_content(p).strip()
        if fragment:
            # Defang links so a phishing URL shows as plain, non-clickable
            # text revealing where it really points.
            parts += ["", defang_links(fragment)]
        return "\n".join(parts)

    def _raw_content(self, p) -> str:
        """The reproducibility anchor: this message's own text.

        Quoted history is dropped (the Matrix thread already holds the prior
        messages, so re-quoting would duplicate the conversation in every
        section).
        """
        return self._strip_reply(p.body)

    @staticmethod
    def _strip_reply(body: str) -> str:
        """Just this message's text — quoted history and signature removed.

        `email-reply-parser` extracts the latest reply across the common
        client quoting styles. Falls back to the full body when parsing
        yields nothing (a quote-only message, an unrecognised format) so the
        bot never posts an empty message.
        """
        if not body:
            return ""
        try:
            reply = EmailReplyParser.parse_reply(body)
        except Exception:
            return body
        return reply.strip() or body

    @staticmethod
    def _source_fields(p, account: MailAccount) -> dict:
        fields: dict = {}
        if p.from_addr:
            fields["from"] = p.from_addr
        if p.subject:
            fields["subject"] = p.subject
        if p.message_id:
            fields["message_id"] = p.message_id
        if p.thread_root:
            fields["thread_root"] = p.thread_root
        if p.date:
            fields["captured_at"] = p.date
        # Provenance: which mailbox + folder this arrived in, so the
        # archivist can tag it (filter "all work mail", "everything in
        # Schule").
        if account.name:
            fields["account"] = account.name
        if account.folder:
            fields["folder"] = account.folder
        return fields

    # ── State (seen Message-IDs + thread roots) ──────────────────────────

    def _load_state(
        self,
    ) -> tuple[set[str], dict[str, str], dict[str, FolderCursor]]:
        if not self._state_file.exists():
            return (set(), {}, {})
        try:
            data = json.loads(self._state_file.read_text())
            cursors = {
                name: FolderCursor(
                    uidvalidity=c.get("uidvalidity"),
                    last_uid=int(c.get("last_uid") or 0),
                )
                for name, c in (data.get("cursors") or {}).items()
            }
            return (
                set(data.get("seen") or []),
                dict(data.get("threads") or {}),
                cursors,
            )
        except (json.JSONDecodeError, OSError, AttributeError, TypeError) as e:
            logger.warning("[mail-bot] bad state file ({}), starting empty", e)
            return (set(), {}, {})

    def _save_state(self) -> None:
        self._session_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._state_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({
            "seen": sorted(self._seen),
            "threads": self._threads,
            "cursors": {
                name: {"uidvalidity": c.uidvalidity, "last_uid": c.last_uid}
                for name, c in self._cursors.items()
            },
        }))
        tmp.replace(self._state_file)
