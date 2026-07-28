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
from stack.mail_fetcher import (
    FolderCursor,
    MailAccount,
    MailFetcher,
    account_from_entry,
)


def _attachment_msgtype(content_type: str) -> str:
    """Matrix msgtype for an attachment's MIME type.

    The archivist files m.image/m.audio/m.file; everything that isn't an
    image or audio clip (PDFs, docs, spreadsheets) rides as a generic m.file.
    """
    ct = (content_type or "").lower()
    if ct.startswith("image/"):
        return "m.image"
    if ct.startswith("audio/"):
        return "m.audio"
    return "m.file"


class MailBot(MicroBot):
    name = "mail-bot"

    def __init__(self, *, homeserver, user_id, password, session_dir, **config):
        super().__init__(homeserver, user_id, password, session_dir, **config)
        self._interval = int(os.environ.get("MAIL_POLL_INTERVAL", "120"))
        # Drop automated/marketing mail before the room (default on). From
        # stack.toml [mail] filter_noise via MAIL_FILTER_NOISE.
        self._filter_noise = os.environ.get("MAIL_FILTER_NOISE", "true").lower() != "false"
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

        Connection details are parsed by the shared `account_from_entry`
        (the same parser `stack core mail` uses); the bot adds the `room`
        binding. A missing connection field/password or no room -> the account
        is skipped with a warning rather than crashing the bot.
        """
        account = account_from_entry(entry)
        room = entry.get("room")
        if account is None or not room:
            logger.warning(
                "[mail-bot] skipping incomplete mail account (need name, "
                "imap_host, imap_user, password, room): {}",
                {k: entry.get(k) for k in ("name", "imap_host", "imap_user", "room")},
            )
            return (None, None)
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
            # Drop automated/bulk/marketing mail before it reaches the room —
            # a family brain has no use for newsletters and noreply notices.
            # Mark it seen so it isn't re-evaluated; the watermark already
            # advanced past it, so this is just belt-and-suspenders.
            if self._filter_noise and parsed.noise:
                if parsed.message_id:
                    self._seen.add(parsed.message_id)
                logger.info("[mail-bot] skipping noise (bulk/automated): {!r}",
                            parsed.subject or "(no subject)")
                continue
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
        # The timeline entry is a compact card; it carries the verbatim
        # raw_content for the archivist, so the visible card stays scannable.
        event_id = await self.post_source_message(
            room_id,
            body=self._card(parsed),
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
        thread_parent = root or event_id
        # Full body as the first item in the thread (the human read-full view).
        # Plain message, no source block — the archivist ignores it and files
        # from the card's raw_content. line_breaks so the email's own newlines
        # survive markdown.
        body = self._full_body_text(parsed)
        if body:
            await self._send(room_id, body, thread_root_event_id=thread_parent,
                             line_breaks=True)
        await self._post_attachments(parsed, room_id, thread_parent,
                                     source_event=event_id)
        return True

    async def _post_attachments(
        self, parsed, room_id: str, thread_parent: str, *, source_event: str,
    ) -> None:
        """Post the email's attachments as Matrix files under its thread.

        Each lands as an `m.file`/`m.image` the archivist files through its
        existing binary-capture path (vault summary/text extraction). The
        subject rides as the caption so the capture has context. Best-effort:
        a failed upload is logged, not retried — the text message is already
        recorded as seen, and retrying would re-post it.
        """
        # Mark each file as a bot-posted email attachment so the archivist
        # files it on behalf of the source (no bot-as-person) with email
        # provenance, instead of attributing it to the mail bot. `source_event`
        # is the generic back-reference to this email's source card, so a
        # consumer can regroup an email's attachments without parsing our
        # email-specific fields (message_id et al.).
        marker = {self.ATTACHMENT_KEY: {
            "source": "email",
            "source_event": source_event,
            "from": parsed.from_addr or "",
            "subject": parsed.subject or "",
            "message_id": parsed.message_id or "",
            "thread_root": parsed.thread_root or "",
        }}
        for att in parsed.attachments:
            ev = await self.send_file(
                room_id,
                data=att.data,
                filename=att.filename,
                mimetype=att.content_type,
                msgtype=_attachment_msgtype(att.content_type),
                caption=parsed.subject or None,
                thread_root_event_id=thread_parent,
                metadata=marker,
            )
            if ev is None:
                logger.warning(
                    "[mail-bot] attachment '{}' ({}) failed to post",
                    att.filename, att.content_type,
                )

    # ── Rendering ────────────────────────────────────────────────────────

    def _card(self, p) -> str:
        """The timeline card: subject, sender, date, attachment count.

        The scannable inbox-row view. The full body rides as a threaded reply
        (see `_post`) so the timeline stays clean; the archivist files from the
        card's machine `raw_content`, not this visible text, so trimming it to
        a card is free.
        """
        subject = p.subject or "(no subject)"
        sender = p.from_name or p.from_addr or "unknown sender"
        if p.from_name and p.from_addr:
            sender = f"{p.from_name} ({p.from_addr})"
        meta = [f"**From** {sender}"]
        if p.date:
            meta.append(f"**Date** {p.date}")
        if p.attachments:
            n = len(p.attachments)
            meta.append(f"📎 {n} attachment" + ("s" if n != 1 else ""))
        return f"📧 **{subject}**\n\n> " + " · ".join(meta)

    def _full_body_text(self, p) -> str:
        """The full email body for the threaded reply.

        Links defanged (a phishing URL shows as plain, non-clickable text);
        `_post` sends it with `line_breaks` so the email's own newlines survive
        markdown. The verbatim `raw_content` on the card stays untouched.
        """
        return defang_links(self._raw_content(p).strip())

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
                    since=c.get("since"),
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
                name: {
                    "uidvalidity": c.uidvalidity,
                    "last_uid": c.last_uid,
                    "since": c.since,
                }
                for name, c in self._cursors.items()
            },
        }))
        tmp.replace(self._state_file)
