"""archivist_sources: a message another bot fetched (an email), filed as a card.

    sources = ArchivistSources(bot, rooms=rooms, captures=captures,
                               services=lambda: services)
    await sources.file_source(room, event, event.source["content"]["dev.famstack.source"])
    pages = await sources.collect_attachments(room_id, card_event_id)

The mail bot fetches an email and posts it into a room as a message
with a `dev.famstack.source` block: the raw original, its sender, the
mailbox and the folder. The archivist files it through the same capture
pipeline a pasted link uses, into the room's topic or the room's scope,
and answers on the email itself: ✅ and the card in the email's thread,
or ❌ and the reason.

Only bots may post a source. The email is untrusted data, never
instructions: its links are defanged before the card stores them, and
the classifier summarises it rather than obeying it.

`collect_attachments` reads the attachments a producer threaded under
its source card (`dev.famstack.attachment`), for 📎, which files the
whole email as one Paperless document (`archivist_reactions.py`).
This module reads only the generic source contract; the email's own
fields stay with the mail bot.
"""

from __future__ import annotations

from typing import Callable

from loguru import logger

from stack.bot import CHECK, CROSS
from stack.email_message import defang_links

from .archivist_captures import NO_MENTIONS, card_type, shown_in_chat
from .reasons import why
from .reply_presenter import render_capture_reply
from .source_archive import SourceAttachment

__all__ = ["ArchivistSources"]


# The provenance tags the archivist puts on a filed email: who sent it,
# and the mailbox and folder it came from. The card keeps them for
# filtering; a capture's reply does not show them, because the email
# right above it in the thread says the same. The card stores them slugged
# (`mailbox-family`, `EMAIL_PROVENANCE_SLUGS`), so a correction reads them
# back in that form.
EMAIL_SENDER_TAG = "Sender: "
EMAIL_MAILBOX_TAG = "Mailbox: "
EMAIL_FOLDER_TAG = "Folder: "

# Capture statuses that mean the email is in the memory.
EMAIL_FILED_STATUSES = ("captured", "reclassified")


class ArchivistSources:
    """Files what another bot posted as a source, and reads its attachments.

        sources = ArchivistSources(bot, rooms=rooms, captures=captures,
                                   services=lambda: services)
        await sources.file_source(room, event, {"source": "email", "raw_content": ...})
        # ✅ on the email and the email's card in its thread
    """

    def __init__(self, bot, *, rooms, captures, services: Callable[[], object]):
        self._bot = bot
        self._rooms = rooms
        self._captures = captures
        self._services = services

    async def collect_attachments(
        self, room_id: str, card_id: str, max_pages: int = 5,
    ) -> list[SourceAttachment]:
        """Gather the attachments a producer threaded under a source card.

        Attachments are ordinary `m.file`/`m.image` events whose
        `dev.famstack.attachment` marker back-references this card via
        `source_event`. We page the timeline backwards (attachments are posted
        just after the card, so they are newer) collecting matches until we
        reach the card itself. Bounded by `max_pages` so a busy room can't
        make the walk run forever; if the card scrolled out of reach we file
        whatever we found, since the body page always survives.
        """
        if self._bot._client is None:
            return []
        out: list[SourceAttachment] = []
        start = ""
        for _ in range(max_pages):
            try:
                resp = await self._bot._client.room_messages(
                    room_id, start=start, limit=50,
                    message_filter={"types": ["m.room.message"]},
                )
            except Exception as e:
                logger.debug("[archivist] source attachment scan failed: {}", e)
                break
            chunk = getattr(resp, "chunk", None) or []
            if not chunk:
                break
            for ev in chunk:
                attachment = await self._attachment_of(ev, card_id)
                if attachment is not None:
                    out.append(attachment)
                if getattr(ev, "event_id", None) == card_id:
                    return out
            start = getattr(resp, "end", None) or ""
            if not start:
                break
        return out

    async def _attachment_of(self, ev, card_id: str) -> SourceAttachment | None:
        """The attachment an event carries for the card, downloaded, or None
        when the event is no attachment of it."""
        c = (getattr(ev, "source", {}) or {}).get("content", {}) or {}
        marker = c.get(self._bot.ATTACHMENT_KEY)
        if not (isinstance(marker, dict) and marker.get("source_event") == card_id):
            return None
        url = c.get("url", "")
        if not (isinstance(url, str) and url.startswith("mxc://")):
            return None
        data = await self._bot._download_media(url)
        if not data:
            return None
        return SourceAttachment(
            filename=c.get("filename") or c.get("body") or "attachment",
            mimetype=(c.get("info") or {}).get("mimetype") or "",
            data=data,
        )


    # ── Inbound source events (mail bot, future ingest channels) ──────────

    async def file_source(self, room, event, source: dict) -> None:
        """Fold a source-bot ingest message (mail bot today) into the vault.

        The mail bot posts a `dev.famstack.source` message carrying the raw
        original (see MicroBot.post_source_message); the archivist files it
        through the same CapturePipeline a pasted URL uses — one capture
        path, no duplicated pipeline. Only `source == "email"` is handled
        today; other kinds are ignored until their consumer lands.

        Trust boundary (revisited in the email security review): only bot
        senders may emit source events, so a family member cannot spoof an
        ingest. The raw email is untrusted *data*, never instructions — the
        classifier must treat it as content to summarise, not commands to
        obey.
        """
        if source.get("source") != "email":
            return
        sender_local = event.sender.split(":")[0].lstrip("@")
        if not sender_local.endswith("-bot"):
            logger.warning(
                "[archivist] ignoring dev.famstack.source from non-bot {}",
                event.sender,
            )
            return
        raw = source.get("raw_content") or ""
        if not raw.strip():
            return
        # Defang links before the body is stored/rendered so a phishing URL
        # in the vault entry is plain, non-clickable text. The faithful
        # version stays on the source event's raw_content (reproducibility).
        body = defang_links(raw)

        seed_topics = _provenance_tags(source)
        bucket = await self._email_bucket(room, event.sender, seed_topics)
        outcome = await self._services().capture.capture_email(
            subject=source.get("subject"),
            body=body,
            message_id=source.get("message_id"),
            thread_root=source.get("thread_root"),
            sender_mxid=event.sender,
            from_addr=source.get("from"),
            from_name=source.get("from_name"),
            captured_at=source.get("captured_at"),
            bucket=bucket,
            seed_topics=seed_topics or None,
        )
        logger.info(
            "[archivist] email folded ({}) -> {}",
            outcome.status, outcome.vault_path,
        )

        await self._confirm_email_filing(room.room_id, event, outcome)

    async def _email_bucket(self, room, sender: str, seed_topics: list[str]) -> str:
        """The bucket an email is filed in; adds the room's topic tags to
        `seed_topics`."""
        # Scope-aware placement: email inherits the bucket of the room it
        # lands in, then nests under emails/ (kind="email"). The family room
        # has no binding -> shared_bucket -> family/emails/; a topic room
        # ("Family E-Mails", "Hobby") routes to its bucket so a mailbox's mail
        # is filed by the scope of its room, co-located with that room's
        # attachments. The topic tag rides along as a seed.
        binding = await self._rooms.topic_binding(room, sender)
        if binding:
            if binding.seed_topics:
                seed_topics.extend(binding.seed_topics)
            return binding.bucket
        # No topic: scope by room membership (a DM/private room → its
        # human; the shared family room → the shared bucket).
        return self._rooms.scope_bucket(room)


    async def _confirm_email_filing(self, room_id: str, event, outcome) -> None:
        """Show the outcome of an email's filing on the email itself.

        The email gets ✅ when it is in the memory and ❌ when it is not;
        an `empty` email did nothing and gets neither. The words go into
        the email's thread, where the mail bot already put its full text:
        the filed card (`render_capture_reply`) carrying the filing's
        envelope, or the reason it was not filed. The room's timeline
        gets no message of ours, because one confirmation per email would
        fill the mail room.

        The envelope must stay on that message: the correction path finds
        the filing through the thread hanging off the email
        (`ArchivistCorrections.anchor`), and reprocess reads it from there.

        A later email of the same conversation is itself posted inside the
        first email's thread, and Matrix has no nested threads, so the
        confirmation joins that thread and quotes the email it is about.
        """
        if outcome.status == "empty":
            return
        thread = self._bot.get_thread_root(event) or event.event_id
        if outcome.status not in EMAIL_FILED_STATUSES:
            await self._bot._react(room_id, event.event_id, CROSS)
            await self._bot._send(
                room_id, self._not_filed_words(outcome),
                reply_to=event.event_id, thread_root_event_id=thread,
            )
            return
        await self._bot._react(room_id, event.event_id, CHECK)
        if outcome.envelope:
            reply = render_capture_reply(
                self._bot.t, source_title_hint=None,
                classification=shown_in_chat(outcome.classification),
                link=self._captures.capture_link(outcome),
                card_type=card_type(outcome.envelope),
                captured_at=outcome.captured_at, language=self._bot.language,
            )
            await self._bot._send(
                room_id, reply,
                reply_to=event.event_id,
                thread_root_event_id=thread,
                metadata={self._bot.FAMSTACK_EVENT_KEY: outcome.envelope, **NO_MENTIONS},
            )

    def _not_filed_words(self, outcome) -> str:
        """Why an email is not in the memory, in the stack's language."""
        if outcome.status == "not_filed":
            return self._bot.t("capture_not_filed", reason=why(self._bot.language, outcome.failure_reason))
        if outcome.status == "no_mirror":
            return self._bot.t("capture_no_mirror")
        return self._bot.t("email_not_filed")


def _provenance_tags(source: dict) -> list[str]:
    """The email's provenance tags: sender, mailbox and folder.

    The vault can then filter "all work mail" / "everything in Schule".
    Same "Prefix: Value" shape the vault already uses for "Person: X".

        _provenance_tags({"from": "ned@flanders.example", "folder": "Schule"})
        # ["Sender: ned@flanders.example", "Folder: Schule"]
    """
    seed_topics: list[str] = []
    if source.get("from"):
        seed_topics.append(f"{EMAIL_SENDER_TAG}{source['from']}")
    if source.get("account"):
        seed_topics.append(f"{EMAIL_MAILBOX_TAG}{source['account']}")
    if source.get("folder"):
        seed_topics.append(f"{EMAIL_FOLDER_TAG}{source['folder']}")
    return seed_topics
