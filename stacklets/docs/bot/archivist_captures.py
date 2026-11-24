"""archivist_captures: a capture (a link, a text, a file) and its reply.

    captures = ArchivistCaptures(bot, rooms=rooms, services=lambda: services)
    await captures.capture_url(room_id, "https://example.org/zelt", "@marge:simpson",
                               reply_to, ctx=ctx, capture_id=event_id)
    await captures.capture_text(room_id, "Bart braucht neue Schuhe ...", ...)
    await captures.capture_binary(ctx=ctx, room_id=room_id, file_data=pdf, ...)

Every room but the documents room is a capture room. A link becomes a
bookmark card, a pasted text or a voice memo a note, a photo a note and
a PDF a bookmark, filed in the room's topic or the sender's own bucket.
The capture pipeline (`capture_pipeline.py`) does the work; this module
picks the bucket from the room, keeps an upload's bytes in the media
archive, and turns the pipeline's `CaptureOutcome` into the reply.

    room ──> bucket (topic binding or scope) ──> CapturePipeline ──> CaptureOutcome
                                                                       │
             ✅/❌ on the message, the card as a reply with its envelope <┘

The reply carries the `capture.filed` or `capture.reclassified` envelope,
so a reply in its thread is a correction (`archivist_corrections.py`).
A card names people, so every reply that shows one mentions nobody
(`NO_MENTIONS`), and only the tags the room may all read (`RoomView`).

No Paperless write, no entity reconciliation: a capture shares the
memory's vocabulary with the documents but not their pipeline. The
sender is the person a capture belongs to ("you pasted it, it's yours").
"""

from __future__ import annotations

import asyncio
from typing import Callable
from urllib.parse import urlsplit

from stack import media
from stack.bot import CHECK, CROSS, EYES, RoomContext
from stack.cards.sources import time_of_ms
from stack.filing.vocabulary import RoomView
from stack.links import go_card, public
from stack.matrix.html import matrix_html

from .capture_pipeline import CaptureOutcome
from .email_correction import EMAIL_PROVENANCE_SLUGS
from .notifier import MatrixNotifier
from .reasons import why
from .reply_presenter import render_capture_correction_reply, render_capture_reply, with_unknown_people

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .archivist_services import ArchivistServices

__all__ = ["NO_MENTIONS", "ArchivistCaptures", "card_type", "guess_mime", "shown_in_chat"]


# Content key that says a message mentions nobody (`m.mentions`, Matrix
# 1.7). Without it Element notifies each person whose display name the
# text contains, and a capture's card names the people it is about.
NO_MENTIONS = {"m.mentions": {}}

# Capture statuses that mean the memory wrote nothing: the answer says
# so and why, and never shows the card as the classifier made it,
# because that card does not exist in the memory.
NOT_WRITTEN = ("not_filed", "not_changed", "capture_missing")


class ArchivistCaptures:
    """Files a capture through the capture pipeline and answers in the room.

        captures = ArchivistCaptures(bot, rooms=rooms, services=lambda: services)
        await captures.capture_text(room_id, text, "@lisa:simpson", reply_to, ctx=ctx)
        # 👀, then ✅ and the note's card as a reply in the room
    """

    def __init__(self, bot, *, rooms, services: Callable[[], "ArchivistServices"]):
        self._bot = bot
        self._rooms = rooms
        self._services = services

    # ── URL capture (knowledge rooms, DMs, per-person notes rooms) ───────
    #
    # Capture mode is what runs when the bot sees a URL in any room that
    # isn't the documents room. The flow is intentionally lean compared
    # to `ArchivistDocuments.file`:
    #
    #   UrlExtractor → SourceContent → classifier → mirror.publish_capture
    #
    # No Paperless write, no entity reconciliation, no event emission.
    # The classifier still uses the memory ontology + correspondents wiki
    # so captures share a vocabulary with Paperless mirrors and can be
    # cross-referenced in Obsidian.
    #
    # Person attribution: the sender's first name is the default. The
    # classifier can return a wider `persons` list if the article body
    # mentions multiple family members, but for v1 the sender is whose
    # capture this is — the rule is "you pasted it, it's yours."

    def notifier(self, room_id: str, reply_to: str | None) -> MatrixNotifier:
        """A Notifier bound to this room + reply thread for mid-flow status.

        Carries a 👀 react thunk so the capture pipeline can acknowledge
        the source message instead of posting a "Reading ..." status line."""
        async def react(rid: str, eid: str) -> None:
            await self._bot._react(rid, eid, EYES)

        return MatrixNotifier(
            room_id=room_id, reply_to=reply_to, send=self._bot._send, t=self._bot.t,
            react=react,
        )


    async def capture_url(
        self, room_id: str, url: str, sender_mxid: str,
        reply_to: str | None = None, *, ctx: RoomContext,
        capture_id: str | None = None,
        user_hint: str | None = None,
    ) -> None:
        room = self._rooms.room_by_id(room_id)
        binding = await self._rooms.topic_binding(room, sender_mxid)
        outcome = await self._services().capture.capture_url(
            url=url, sender_mxid=sender_mxid,
            notifier=self.notifier(room_id, reply_to),
            capture_id=capture_id, channel="chat",
            seed_topics=binding.seed_topics if binding else None,
            bucket=binding.bucket if binding else None,
            user_hint=user_hint,
            write_in=self._rooms.write_in(ctx), read_in=self._rooms.scope_bucket(room),
        )
        await self.reply(room_id, outcome, reply_to)


    async def capture_text(
        self, room_id: str, text: str, sender_mxid: str,
        reply_to: str | None = None, *, ctx: RoomContext,
        capture_id: str | None = None,
        transcribed: bool = False, caption: str = "", sent_at: str = "",
    ) -> None:
        # 👀 before the work, like every other capture shape: a link gets
        # it from the pipeline's notifier, an upload from the handler. A
        # note was the one path that filed in silence, and classification
        # on a local model is seconds of it. That silence matters more
        # now than it did -- in a room with other people 📌 is the only
        # way to keep something, so this is the gesture with no fallback.
        if reply_to:
            await self._bot._react(room_id, reply_to, EYES)
        room = self._rooms.room_by_id(room_id)
        binding = await self._rooms.topic_binding(room, sender_mxid)
        outcome = await self._services().capture.capture_text(
            text=text, sender_mxid=sender_mxid,
            capture_id=capture_id, channel="chat",
            seed_topics=binding.seed_topics if binding else None,
            bucket=binding.bucket if binding else None,
            transcribed=transcribed,
            write_in=self._rooms.write_in(ctx), read_in=self._rooms.scope_bucket(room),
            caption=caption, sent_at=sent_at,
        )
        await self.reply(room_id, outcome, reply_to)


    def _keep_original(
        self, *, file_data: bytes, mime: str, filename: str,
        event_id: str | None, room_id: str, sender_mxid: str, ts_ms: int,
    ) -> dict | None:
        """Archive the uploaded bytes; return how the entry shows them.

        Blocking: it writes megabytes and may run a converter, so the
        caller hands it to a thread. Every bot in the stack shares one
        event loop, and a photograph being resized here would otherwise
        be every other bot standing still.

        ``{"name", "original", "embed"}``, or None when nothing was
        kept. The store is its own directory; the wiki sees it through
        a mount, so the entry addresses the file by a site path that
        resolves at any page depth and in either deployment mode.

        Best-effort by construction. No archive configured (memory not
        installed), no event id, no writable disk: the capture files
        exactly as it did before, pointing at the mxc URL alone.

        Documents are not archived here. They go to Paperless, which
        holds the bytes durably and is named on the entry; a second
        copy would be two places to keep in step and two places to
        delete from.
        """
        root = media.open_archive()
        if root is None or not event_id:
            return None
        import datetime as _dt
        when = (_dt.datetime.fromtimestamp(ts_ms / 1000, tz=_dt.timezone.utc)
                if ts_ms else _dt.datetime.now(_dt.timezone.utc))
        kind = media.kind_for(mime)
        ext = media.extension_for(filename, mime)
        link = media.keep(
            root, event_id, file_data, ext=ext, when=when, kind=kind,
            source="capture", mime=mime, filename=filename,
            room_id=room_id, sender=sender_mxid,
        )
        if not link:
            return None
        # A screenshot is the entry and goes on the page; the bounded
        # copy when there is one, because a phone photograph embedded
        # at full size is a page nobody waits for.
        shown = media.derive(root, event_id, ext=ext, when=when, kind=kind)
        return {
            "name": filename,
            "original": link,
            "embed": shown or (link if kind == "image" else ""),
        }


    async def capture_upload(
        self, *, ctx: RoomContext, room_id: str, event, content: dict,
        file_data: bytes, filename: str, caption: str,
    ) -> None:
        """Capture an upload in a capture room: a PDF becomes a bookmark and
        a photo a note, in the room's topic or the sender's bucket, linked
        to the mxc URL and with a copy of the bytes in the media archive.

        A bot-posted email attachment (`dev.famstack.attachment`) is filed
        on behalf of the source: the bot is not the person, and the card
        carries the email's provenance tags. The bucket still comes from
        the room's topic binding (e.g. a "Family Email" topic).
        """
        attach = content.get(self._bot.ATTACHMENT_KEY)
        bot_attachment = isinstance(attach, dict)
        extra_seed_topics = None
        if bot_attachment:
            extra_seed_topics = ["email"]
            frm = (attach.get("from") or "").strip()
            if frm:
                extra_seed_topics.append(f"Sender: {frm}")
        await self.capture_binary(
            ctx=ctx,
            room_id=room_id,
            file_data=file_data,
            mime=content.get("info", {}).get("mimetype")
                 or guess_mime(filename, content.get("msgtype", "")),
            filename=filename,
            source_uri=content["url"],
            sender_mxid=event.sender,
            capture_id=event.event_id,
            reply_to=event.event_id,
            default_person=not bot_attachment,
            extra_seed_topics=extra_seed_topics,
            captured_ts=getattr(event, "server_timestamp", 0) or 0,
            caption=caption,
        )

    async def capture_binary(
        self, *, ctx: RoomContext, room_id: str, file_data: bytes, mime: str,
        filename: str, source_uri: str, sender_mxid: str,
        capture_id: str | None = None,
        reply_to: str | None = None,
        default_person: bool = True,
        extra_seed_topics: list[str] | None = None,
        captured_ts: int = 0,
        caption: str = "",
    ) -> None:
        """Capture a PDF as a bookmark, or a photo as a note.

        Vision-driven by default (the classifier sees the rendered
        page(s) + any extractable text layer). The entry points back at
        the Matrix mxc URL and carries a copy of the bytes in the media
        archive, so it still has the file when the homeserver's media
        store does not. ``capture_id`` is the Matrix event_id of the
        upload, stored on the entry as a stable correlation key so a
        deriver (or the reply-to-correct path) can find this capture
        later without depending on the title-derived path. It is also
        what the archived file is named after.

        ``default_person`` is False for bot-posted content (an email
        attachment): the sender is a bot, not the owner, so don't fall
        it in as the person. ``extra_seed_topics`` prepend provenance
        tags (Sender, ``email``) on top of any topic-room seeds.
        ``caption`` is what the sender wrote with the upload.
        """
        room = self._rooms.room_by_id(room_id)
        binding = await self._rooms.topic_binding(room, sender_mxid)
        seed_topics = list(extra_seed_topics or [])
        if binding:
            bucket = binding.bucket
            if binding.seed_topics:
                seed_topics.extend(binding.seed_topics)
        elif not default_person:
            # Bot-posted (email attachment) with no topic: scope by membership
            # so a DM/private room files under its human, not @mail-bot. Human
            # uploads keep bucket=None -> the sender-bucket fallback.
            bucket = self._rooms.scope_bucket(room)
        else:
            bucket = None
        outcome = await self._services().capture.capture_binary(
            file_data=file_data, mime=mime, filename=filename,
            source_uri=source_uri, sender_mxid=sender_mxid,
            capture_id=capture_id, channel="chat",
            seed_topics=seed_topics or None,
            bucket=bucket,
            default_person=default_person,
            kept_media=await asyncio.to_thread(
                self._keep_original,
                file_data=file_data, mime=mime, filename=filename,
                event_id=capture_id, room_id=room_id,
                sender_mxid=sender_mxid, ts_ms=captured_ts,
            ),
            write_in=self._rooms.write_in(ctx), read_in=self._rooms.scope_bucket(room),
            caption=caption, sent_at=time_of_ms(captured_ts),
        )
        await self.reply(room_id, outcome, reply_to)


    async def reply(
        self, room_id: str, o: CaptureOutcome, reply_to: str | None,
        *, corrected_from: dict | None = None,
    ) -> None:
        """Map a CaptureOutcome to a chat reply.

        `empty` (whitespace-only paste) is a silent drop; terminal
        statuses get a one-line message; a capture or a reclassified
        capture renders via the shared presenter and carries the
        `capture.*` envelope as metadata so the user can reply-to-
        correct the same way they do with document filings.

        `corrected_from` is the classification a correction started from
        (the anchor envelope's `data`): a corrected capture answers with
        what changed, then its card. Every reply that shows a card
        mentions nobody (`NO_MENTIONS`), because the card names people.
        """
        # Terminal glyph alongside the 👀: ❌ on a genuine failure, ✅
        # otherwise. `empty` did nothing, so it gets no glyph.
        if o.status in ("extract_failed", "no_mirror", *NOT_WRITTEN):
            await self._bot._react(room_id, reply_to, CROSS)
        elif o.status != "empty":
            await self._bot._react(room_id, reply_to, CHECK)

        if o.status == "empty":
            return
        failure = self._failure_reply(o)
        if failure is not None:
            await self._bot._answer(room_id, failure, reply_to)
            return
        vault = self._services().vault
        view = RoomView.between(vault, o.vault_path or "", language=vault.language,
                                room=self._rooms.scope_bucket(self._rooms.room_by_id(room_id)))
        reply, html = self._card_reply(o, corrected_from, view)
        metadata = {**NO_MENTIONS}
        if o.envelope:
            metadata["dev.famstack.event"] = view.event(o.envelope)
        await self._bot._answer(room_id, reply, reply_to, metadata=metadata, html=html)

    def _failure_reply(self, o: CaptureOutcome) -> str | None:
        """The one line for a capture that wrote no card, or None when it did."""
        if o.status == "extract_failed":
            # The reason qualifies what failed so the family doesn't hear
            # "couldn't read that link" after sending a voice memo. Falls
            # back to the original URL-shaped message for legacy callers.
            failure_keys = {
                "url": "capture_failed",
                "binary": "capture_failed_binary",
            }
            return self._bot.t(failure_keys.get(o.failure_reason or "", "capture_failed"))
        if o.status == "no_mirror":
            return self._bot.t("capture_no_mirror")
        if o.status in NOT_WRITTEN:
            return self._not_written(o)
        return None

    def _card_reply(
        self, o: CaptureOutcome, corrected_from: dict | None, view: RoomView,
    ) -> tuple[str, str | None]:
        """The reply that shows the card: its plain body and, for a
        correction, its HTML with the card folded away; tags as `view` shows."""
        kind = card_type(o.envelope)
        # The title links to the card; without a home base, to the page a
        # link capture came from. "(capture)" is a placeholder, not a link.
        link = self.capture_link(o) or (
            o.display_link if o.display_link.startswith(("http://", "https://")) else "")
        html = None
        if o.status == "reclassified":
            # The card after the correction is folded away in the HTML;
            # the plain body shows it open.
            answer = render_capture_correction_reply(
                self._bot.t,
                before=view.of(shown_in_chat(corrected_from)),
                after=view.of(shown_in_chat(o.classification)),
                link=link, card_type=kind, captured_at=o.captured_at,
                language=self._bot.language, changes=view.of_changes(_shown_changes(o.changes)),
                source=o.display_link,
            )
            answer = with_unknown_people(self._bot.t, answer, o.unknown_persons)
            reply, html = answer.body, answer.html
        else:
            reply = render_capture_reply(
                self._bot.t,
                source_title_hint=o.source_title_hint,
                classification=view.of(shown_in_chat(o.classification)),
                link=link,
                transcript=o.transcript,
                card_type=kind,
                captured_at=o.captured_at,
                language=self._bot.language,
                source=o.display_link,
            )
        return self._with_blocked_note(o, reply, html)

    def _with_blocked_note(
        self, o: CaptureOutcome, reply: str, html: str | None,
    ) -> tuple[str, str | None]:
        """The reply with the reason a link card has no summary put first.

        "Reddit wants you signed in" is something a person can act on;
        "couldn't read that link" is not.
        """
        if not o.blocked_reason:
            return reply, html
        host = urlsplit(o.display_link or "").netloc or "the site"
        note = self._bot.t(f"capture_blocked_{o.blocked_reason}", host=host)
        reply = f"{note}\n\n{reply}"
        if html is not None:
            html = f"{matrix_html(note)}\n{html}"
        return reply, html


    def _not_written(self, o: CaptureOutcome) -> str:
        """The answer to a capture or a correction the memory did not write,
        with the reason worded from its code in the stack's language.

            ❌ The card was not changed: the card changed while I was writing it, try again.
        """
        reason = why(self._bot.language, o.failure_reason or "card_missing")
        if o.status == "not_filed":
            return self._bot.t("capture_not_filed", reason=reason)
        return self._bot.t("capture_not_changed", reason=reason)


    def capture_link(self, o: CaptureOutcome) -> str:
        """A `/go/card/<card_id>` link to the page this capture became.

        The footer used to carry the source: the URL that was pasted, or
        a placeholder for typed text. That is the message directly above
        the reply in the room, so it told the sender nothing they did not
        have, while the one new thing -- where it was filed -- was not
        there at all. The page itself carries the source, so nothing is
        lost by pointing at the page instead.

        Keyed by the card's id rather than its vault path because the path
        moves on its own: a capture re-scopes when a second person joins
        the room, a topic gets renamed, a correction rewrites the title.
        The resolver looks the id up in the tree it serves. A filing whose
        card has no id of its own yet is linked by its capture id
        (`/go/capture/<id>`), which keeps resolving.

        "" when there is nothing to link -- no home base yet, or a
        capture that never reached the mirror and so has no page. The
        caller falls back to the source footer.
        """
        return public(go_card((o.envelope or {}).get("data") or {}), self._bot.link_base_url)


# ── What a reply shows ──────────────────────────────────────────────────────


def card_type(envelope: dict | None) -> str:
    """The card type a capture was filed as, from its filing envelope.

    The envelope's `kind` is the card's type (`note`, `bookmark`,
    `email`); a capture without one was filed as a note.

        >>> card_type({"data": {"kind": "email"}})
        'email'
    """
    return str(((envelope or {}).get("data") or {}).get("kind") or "note")


def shown_in_chat(classification: dict | None) -> dict:
    """A capture's classification with only the tags a reply shows.

        >>> shown_in_chat({"title": "Elternabend",
        ...                "tags": ["Mailbox: family", "folder-inbox", "school"]})
        {'title': 'Elternabend', 'tags': ['school']}
    """
    view = dict(classification or {})
    view["tags"] = [t for t in view.get("tags") or []
                    if isinstance(t, str) and not _is_provenance_tag(t)]
    return view


def _shown_changes(changes):
    """A correction's changes as the memory wrote them, without provenance tags;
    None (the writer did not say) stays None."""
    if changes is None:
        return None
    return [c for c in changes if not (c.field == "tag" and _is_provenance_tag(c.value))]


def _is_provenance_tag(tag: str) -> bool:
    """Whether `tag` is an email's sender, mailbox or folder, as filed or slugged."""
    return tag.lower().replace(": ", "-", 1).startswith(EMAIL_PROVENANCE_SLUGS)


# ── An upload's type ────────────────────────────────────────────────────────

_MIME_BY_EXT = {
    "pdf": "application/pdf",
    "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "png": "image/png",
    "heic": "image/heic", "heif": "image/heif",
    "webp": "image/webp",
    "tiff": "image/tiff", "tif": "image/tiff",
    "md": "text/markdown", "markdown": "text/markdown",
    "txt": "text/plain",
    # Voice messages: Element and most Matrix clients upload Opus-in-OGG
    # for the in-app voice recorder; mp3 / m4a / wav cover files attached
    # from elsewhere. Element X uses .ogg specifically.
    "ogg": "audio/ogg", "oga": "audio/ogg", "opus": "audio/ogg",
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4", "mp4a": "audio/mp4",
    "wav": "audio/wav",
    "webm": "audio/webm",
}


def guess_mime(filename: str, msgtype: str) -> str:
    """Best-effort MIME from filename extension, with msgtype as a
    fallback ("m.image" → image/jpeg). Used when the upload event
    omits `content.info.mimetype`, which happens on some clients.

        >>> guess_mime("Zeugnis.pdf", "m.file")
        'application/pdf'
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in _MIME_BY_EXT:
        return _MIME_BY_EXT[ext]
    if msgtype == "m.image":
        return "image/jpeg"
    return "application/octet-stream"
