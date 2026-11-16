"""archivist_reactions: what a person's emoji on a message asks the archivist to do.

    reactions = ArchivistReactions(bot, captures=captures, documents=documents,
                                   sources=sources)
    handler = reactions.handlers().get("📌")
    await handler(room, reaction_event, target_event, target_id)

A reaction is a gesture on one message:

    🔖 📌   keep this message: the capture the room would make, on demand
    📎 📄   file this email (a source card) with its attachments as one document
    🔁 🔄   do the work on this message again, after a failure

The bot's `_on_reaction` ignores bots, normalizes the emoji, fetches the
reacted message once and hands it to the handler here. Each handler
decides for itself whether a target is valid (a bookmark of a bot's
message is not). Reactions replay at least once, so every handler keys
on the target event: a capture dedups on its id, Paperless on the
content, and a repeat does nothing new.

To add a gesture, add one entry to `handlers` and write its method.
"""

from __future__ import annotations

from stack.bot import EYES
from stack.cards.sources import time_of_ms

from .archivist_documents import SUPPORTED_MSGTYPES
from .source_archive import build_source_pdf
from .text_utils import first_url, is_just_url

__all__ = ["ArchivistReactions"]


class ArchivistReactions:
    """The reaction handlers, keyed by emoji.

        reactions = ArchivistReactions(bot, captures=captures, documents=documents,
                                       sources=sources)
        sorted(reactions.handlers())   # ['📄', '📌', '📎', '🔁', '🔄', '🔖']
    """

    def __init__(self, bot, *, captures, documents, sources):
        self._bot = bot
        self._captures = captures
        self._documents = documents
        self._sources = sources

    # ── Reactions: user → bot per-message routing ────────────────────────
    #
    # A small hard-coded registry maps a (normalized) emoji to the handler
    # method that runs when a family member drops it on a message. To add a
    # binding — 🗑 redact, 👎 reclassify — add one entry here and write the
    # `_react_*` method; the dispatcher needs no changes. Handlers receive
    # the already-fetched target event, so they decide for themselves
    # whether a bot-authored target is valid (bookmark says no, redact yes).

    def handlers(self) -> dict:
        return {
            "🔖": self.bookmark,
            "📌": self.bookmark,
            "📎": self.archive_source,
            "📄": self.archive_source,
            "🔁": self.retry,
            "🔄": self.retry,
        }


    async def bookmark(self, room, event, target, target_id) -> None:
        """🔖 / 📌 — bookmark the reacted message into the room: the same
        capture auto-mode would make, but on demand. The only capture path
        in a `!config process react` room.

        Never bookmarks a bot message (a filing, a welcome). Attribution
        is the message author, not the reactor — we're saving their
        content — and the capture is keyed on the target event id so a
        replay or a second reactor dedups downstream.
        """
        if self._bot.is_bot_user(getattr(target, "sender", "")):
            return
        body = (getattr(target, "body", "") or "").strip()
        if not body:
            # v1 bookmarks text/URL messages; file uploads are a follow-up.
            return
        author = target.sender
        ctx = await self._bot._message_context(room)
        if is_just_url(body):
            await self._captures.capture_url(
                room.room_id, body, author, target_id, ctx=ctx,
                capture_id=target_id,
            )
        elif (embedded_url := first_url(body)) is not None:
            hint = body.replace(embedded_url, "", 1).strip(" \t\n\r:.,;!?—-")
            await self._captures.capture_url(
                room.room_id, embedded_url, author, target_id, ctx=ctx,
                capture_id=target_id, user_hint=hint or None,
            )
        else:
            await self._captures.capture_text(
                room.room_id, body, author, target_id, ctx=ctx,
                capture_id=target_id,
                sent_at=time_of_ms(getattr(target, "server_timestamp", 0)),
            )


    async def archive_source(self, room, event, target, target_id) -> None:
        """📎 / 📄 — archive a whole ingest source card as one Paperless doc.

        Generic over `dev.famstack.source` cards (email today, another ingest
        channel tomorrow): page one is the header the producing bot rendered
        (the card body) plus the card's verbatim content, then every
        attachment the producer threaded under this card follows as pages.
        The card's `source` value becomes a flat provenance tag and the header
        rides along as a Paperless note, so the sender/subject are found by
        full-text search. This reads only the generic source contract — it
        does not parse the producer's domain fields (email's From/Subject
        live in the header the mail bot already rendered). 📎 on a non-source
        message is a no-op. Reactions replay at-least-once, so Paperless's own
        duplicate detection makes a repeat 📎 a no-op rather than a copy.
        """
        content = (getattr(target, "source", {}) or {}).get("content", {}) or {}
        source = content.get(self._bot.SOURCE_KEY)
        if not isinstance(source, dict):
            return
        header = (content.get("body") or "").strip()
        body = (source.get("raw_content") or "").strip()
        source_type = (source.get("source") or "").strip() or "source"
        if not header and not body:
            return

        await self._bot._react(room.room_id, target_id, EYES)
        await self._bot._set_typing(room.room_id, on=True)
        attachments = await self._sources.collect_attachments(room.room_id, target_id)
        pdf = build_source_pdf(header, body, attachments)

        title_hint = next(
            (ln.strip("# *_>-\t ") for ln in header.splitlines()
             if ln.strip("# *_>-\t ")),
            source_type,
        )
        safe = "".join(
            ch if ch.isalnum() or ch in " .-_" else "_" for ch in title_hint
        ).strip()
        filename = f"{(safe[:80] or source_type)}.pdf"
        await self._documents.file(
            room.room_id, filename, title_hint[:120] or source_type, pdf,
            ctx=await self._bot._message_context(room),
            reply_to=target_id, date_filed=source.get("captured_at") or None,
            submitter_mxid=event.sender, user_hint=title_hint or None,
            extra_tags=[source_type], note_prefix=header or None,
        )


    async def retry(self, room, event, target, target_id) -> None:
        """🔁 / 🔄 — do the work on this message again.

        Filing fails for reasons that have nothing to do with the message:
        the model was still loading, Paperless was restarting, the handler
        ran past its timeout. Rather than a retry queue somebody has to
        drain, the recovery gesture is the same shape as every other one
        here: react on the message that failed. The ❌ the framework left
        tells you which message; this tells you what to do about it.

        Retry re-dispatches the handler that owns that kind of message, so
        there is nothing to keep in sync as bindings are added. Both paths
        are idempotent (Paperless dedups on content, captures key on the
        target event id), so retrying something that actually succeeded is
        a no-op rather than a second copy.
        """
        content = (getattr(target, "source", {}) or {}).get("content", {}) or {}
        if isinstance(content.get(self._bot.SOURCE_KEY), dict):
            await self.archive_source(room, event, target, target_id)
        elif content.get("msgtype") in SUPPORTED_MSGTYPES:
            await self._bot._on_file(room, target)
