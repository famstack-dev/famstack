"""archivist_corrections: a correction to a filing, in the filing's thread.

    corrections = ArchivistCorrections(bot, rooms=rooms, captures=captures,
                                       services=lambda: services)
    anchor = await corrections.anchor(room_id, event)     # (event_id, envelope) or None
    if anchor and await corrections.handle(room_id, event, anchor, reply_to, ctx=ctx):
        return                                            # it was a correction

A person corrects a filing with a reply in the filing's thread, or with
a quote of the filing. For example: "this is from Marge, not Homer". The
archivist finds the filing that the reply is about (`anchor`). It
collects every correction of that item since the filing. Then it has the
item corrected:

    reply ──> anchor: quoted filing | filing in the thread | thread off the upload
          ──> chain: every correction back to the `*.filed` envelope, and its data
          ──> document: DocumentPipeline.reprocess  ──> reclassified summary
          ──> capture:  CapturePipeline.reprocess   ──> what changed, then the card

Every answer goes into the same thread with a new `*.reclassified`
envelope. The next correction finds its anchor through the newest
classification in the thread. A message without words of its own goes
back to the normal routing. A message about no filing goes back too.
"""

from __future__ import annotations

from typing import Callable

from loguru import logger

from stack.bot import CHECK, CROSS, EYES, RoomContext
from stack.cards.events import Correction
from stack.cards.sources import time_of_ms

from stack.filing.correction.chain import CorrectionChain
from .reply_presenter import render_reprocessed_reply, with_unknown_people
from .text_utils import attachment_caption, strip_reply_fallback

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .archivist_services import ArchivistServices

__all__ = ["ArchivistCorrections"]


class ArchivistCorrections:
    """Reads a reply as a correction to one of the archivist's filings.

        corrections = ArchivistCorrections(bot, rooms=rooms, captures=captures,
                                           services=lambda: services)
        anchor = await corrections.anchor(room_id, event)
        await corrections.handle(room_id, event, anchor, event.event_id, ctx=ctx)
        # 👀, then ✅ and the corrected card in the thread
    """

    def __init__(self, bot, *, rooms, captures, services: Callable[[], "ArchivistServices"]):
        self._bot = bot
        self._rooms = rooms
        self._captures = captures
        self._services = services

    # ── Document processing pipeline ─────────────────────────────────────

    @staticmethod
    def _is_filing_envelope(envelope: dict) -> bool:
        """Whether an envelope announces a filed item the user can correct.

        Any `*.filed` (the original) or `*.reclassified` (the result of
        an earlier correction round), generic over filing kind so
        documents and captures share one reading. Accepting the
        reclassified form is what lets a user chain corrections: the
        target of the next one is the most recent classification, not
        just the original filing.
        """
        return envelope.get("type", "").endswith((".filed", ".reclassified"))


    async def anchor(
        self, room_id: str, event,
    ) -> tuple[str, dict] | None:
        """The filing `event` is a correction to, as `(event_id, envelope)`.

        A correction is aimed at an *item*, not at a Matrix event, and
        the family member's client decides how to express "about this
        one". Three shapes reach the same filing, tried in the order
        that respects the user's most explicit gesture first:

        1. They quoted one of our classification messages: the reply
           parent carries the envelope, so take it. Deliberate, and the
           only shape available in a room without threads.
        2. They typed inside the filing's thread. The thread relation
           is the deliberate part; the `m.in_reply_to` a client folds
           in alongside it points at the newest event in the thread
           (see `_thread_envelopes`), which may well be a todo line or
           a status update with no envelope on it.
        3. They replied to the message that started it all -- their own
           upload -- from the main timeline. That event carries no
           envelope of ours, but it roots the thread our filing answered
           in, so the thread hanging off it identifies the item.

        Returns None when nothing in reach is a filing of ours, which is
        the common case: most messages are searches.
        """
        parent_id = self._in_reply_to_id(event)
        envelope = await self._bot._reply_parent_envelope(room_id, event)
        if envelope is not None and self._is_filing_envelope(envelope):
            return (parent_id, envelope)

        roots = [r for r in (self._bot.get_thread_root(event), parent_id) if r]
        for root in dict.fromkeys(roots):
            for event_id, candidate in await self._bot._thread_envelopes(room_id, root):
                if self._is_filing_envelope(candidate):
                    return (event_id, candidate)
        return None


    @staticmethod
    def _target_doc_id(envelope: dict) -> int | None:
        """The paperless_id a document filing envelope points at."""
        if envelope.get("type") not in ("document.filed", "document.reclassified"):
            return None
        paperless_id = envelope.get("data", {}).get("paperless_id")
        return paperless_id if isinstance(paperless_id, int) else None


    @staticmethod
    def _target_capture_path(envelope: dict) -> str | None:
        """The vault path a capture filing envelope points at."""
        if envelope.get("type") not in ("capture.filed", "capture.reclassified"):
            return None
        vault_path = envelope.get("data", {}).get("vault_path")
        return vault_path if isinstance(vault_path, str) and vault_path else None


    async def handle(
        self, room_id: str, event, anchor: tuple[str, dict], reply_to: str,
        *, ctx: RoomContext,
    ) -> bool:
        """Re-run classification for the item `anchor` names, using the
        user's message as an authoritative hint. Returns whether the
        message was consumed as a correction.

        One entry point for both filing kinds: the envelope says whether
        the item is a Paperless document or a vault capture, and the
        correction chain is collected the same way for either. A message
        that turns out to carry no correction (a bare quote with no words
        of its own) is handed back to normal routing.
        """
        anchor_id, envelope = anchor
        chain = await self._collect_chain(room_id, event, anchor_id, envelope)
        hint, initial = chain.hint, chain.latest
        if not hint:
            return False

        doc_id = self._target_doc_id(envelope)
        capture_path = self._target_capture_path(envelope)
        if doc_id is None and capture_path is None:
            return False

        await self._acknowledge(room_id, reply_to)
        if doc_id is not None:
            await self.reprocess_document(
                room_id, doc_id, chain, event.sender, reply_to,
            )
            return True

        await self.reprocess_capture(
            room_id, capture_path, hint, event.sender, reply_to, ctx=ctx,
            initial_classification=initial,
            capture_id=envelope.get("data", {}).get("capture_id"),
            reply_text=strip_reply_fallback(event.body or ""),
            reply_id=getattr(event, "event_id", "") or "",
            reply_at=time_of_ms(getattr(event, "server_timestamp", 0)),
        )
        return True


    async def _acknowledge(self, room_id: str, reply_to: str) -> None:
        """Show that the correction was taken: 👀 on it and typing.

        Called only once the message is known to be a correction (it has
        words and points at a filing), so a question or a thank-you in
        the same thread gets no reaction. A filing shows the same pair
        when it starts. The typing indicator ends with the answer's send.
        """
        await self._bot._react(room_id, reply_to, EYES)
        await self._bot._set_typing(room_id, on=True)


    async def _collect_chain(
        self, room_id: str, event, anchor_id: str, anchor_envelope: dict,
    ) -> "CorrectionChain":
        """What the reply chain holds, back to the original filing (`CorrectionChain`).

        The chain holds three things: every correction, the filing as
        first made and the latest classification that the person saw.

        Each round of correction adds two messages to the thread: the
        person's reply and the bot's answer. The walk takes them in
        pairs. It collects every person's message between the current
        event and the bot's `*.filed` message. The words before that
        message are the upload's caption, not a correction.

        The ``data`` of the anchor envelope is the latest state that the
        person saw. A document is corrected from the first filing and
        every correction in time order (ADR-018 §7). A capture reads the
        corrections as one joined hint.
        """
        current = (event.sender, strip_reply_fallback(event.body))
        data = anchor_envelope.get("data")
        latest: dict | None = data if isinstance(data, dict) else None
        prior, filed = await self._prior_corrections(room_id, anchor_id)
        newest_first = [c for c in (current, *prior) if c[1]]
        return CorrectionChain(
            hint=_joined_hint([body for _, body in newest_first]),
            latest=latest,
            filed=filed,
            corrections=tuple(
                Correction(_person_of(sender), at="", words=body)
                for sender, body in reversed(newest_first)),
        )

    async def _prior_corrections(
        self, room_id: str, anchor_id: str,
    ) -> tuple[list[tuple[str, str]], dict | None]:
        """The earlier corrections in the thread, and the first filing.

        The corrections are (sender, words) pairs, newest first, back to
        the bot's `*.filed` message. The first filing is the data of that
        message, or None when the walk does not reach it."""
        found: list[tuple[str, str]] = []
        parent_id = anchor_id
        # Each loop iteration consumes one (bot, prior-user) pair from
        # the chain. Bounded by the framework's in_reply_to depth and a
        # hard cap so a thread that somehow loops doesn't burn API
        # calls forever.
        for _ in range(10):
            if not parent_id:
                break
            parent = await self._fetch_event(room_id, parent_id)
            if parent is None or getattr(parent, "sender", None) != self._bot.user_id:
                break
            envelope = parent.source.get("content", {}).get(self._bot.FAMSTACK_EVENT_KEY)
            if not isinstance(envelope, dict):
                break
            # Generic over filing kind: any `*.filed` is the boundary,
            # any `*.reclassified` is an intermediate hop. So the same
            # walker handles `document.{filed,reclassified}` AND
            # `capture.{filed,reclassified}` without per-kind branches.
            env_type = envelope.get("type", "")
            if env_type.endswith(".filed"):
                data = envelope.get("data")
                return found, data if isinstance(data, dict) else None
            if not env_type.endswith(".reclassified"):
                break
            grandparent_id = self._in_reply_to_id(parent)
            grandparent = await self._fetch_event(room_id, grandparent_id) if grandparent_id else None
            if grandparent is None:
                break
            prior = strip_reply_fallback(getattr(grandparent, "body", "") or "")
            if prior:
                found.append((getattr(grandparent, "sender", "") or "", prior))
            parent_id = self._in_reply_to_id(grandparent)

        return found, None


    @staticmethod
    def _in_reply_to_id(event) -> str | None:
        """The event_id this event replies to, or None if it isn't a reply."""
        return (
            getattr(event, "source", {})
            .get("content", {})
            .get("m.relates_to", {})
            .get("m.in_reply_to", {})
            .get("event_id")
        )


    async def _fetch_event(self, room_id: str, event_id: str | None):
        """Best-effort `room_get_event`; returns the event or None on any failure."""
        if not event_id:
            return None
        try:
            resp = await self._bot.client.room_get_event(room_id, event_id)
        except Exception as e:
            logger.debug("[archivist] event fetch failed for {}: {}", event_id, e)
            return None
        return getattr(resp, "event", None)


    async def reprocess_document(
        self, room_id: str, doc_id: int, chain: "CorrectionChain", sender_mxid: str,
        reply_to: str,
    ) -> None:
        """Correct `doc_id` with the corrections of `chain`.

        The card's commit has the replier (`sender_mxid`) as its author,
        as for a capture. The pipeline returns a ReprocessOutcome, and this
        method turns it into a chat reply. The reply carries a new
        `document.reclassified` envelope. A person can then reply to it
        with the next correction.

        The answers go through `_answer` into the thread of the
        correction. The next correction finds its anchor through the
        newest classification in the thread. An answer outside the thread
        would tie every later correction to the original filing.
        """
        o = await self._services().pipeline.reprocess(
            doc_id=doc_id, corrections=chain.corrections,
            filed=chain.filed or chain.latest, changed_by=sender_mxid,
        )
        # The terminal glyph beside the 👀 `handle` put there.
        await self._bot._react(
            room_id, reply_to,
            CHECK if o.status == "reclassified" else CROSS,
        )
        if o.status == "reclassified":
            answer = self._reprocessed_reply(o, chain.latest)
            await self._bot._answer(
                room_id, answer.body, reply_to,
                metadata={"dev.famstack.event": o.envelope}, html=answer.html,
            )
        else:
            await self._bot._answer(room_id, self._reprocess_failed(o, doc_id), reply_to)

    def _reprocessed_reply(self, o, initial_classification: dict | None):
        """The confirmation of a document classified again.

        `initial_classification` is what the person saw (the anchor
        envelope's data), so the line names what their note changed.
        """
        before = initial_classification or {}
        return with_unknown_people(self._bot.t, render_reprocessed_reply(
            self._bot.t,
            before={**before, "tags": before.get("topics")},
            after={**o.classification, "title": o.title,
                   "persons": o.resolved_persons, "tags": o.resolved_topics},
            link=o.link,
            correspondent=o.resolved_correspondent,
            date=o.date,
            language=self._bot.language,
        ), o.unknown_persons)

    def _reprocess_failed(self, o, doc_id: int) -> str:
        """Why a document could not be classified again."""
        if o.status == "doc_missing":
            return self._bot.t("reprocess_doc_missing", doc_id=doc_id)
        if o.status == "not_changed":
            return self._bot.t("capture_not_changed",
                               reason=self._bot.t(f"why_{o.failure_reason or 'unknown'}"))
        kind, detail = o.llm_error
        return self._bot.t("reprocess_llm_error", doc_id=doc_id, kind=kind, detail=detail)


    async def reprocess_capture(
        self, room_id: str, vault_path: str, user_hint: str,
        sender_mxid: str, reply_to: str,
        *, ctx: RoomContext, initial_classification: dict | None = None,
        capture_id: str | None = None,
        reply_text: str = "",
        reply_id: str = "",
        reply_at: str = "",
    ) -> None:
        """Re-classify a capture with the user's reply as a hint.

        Parallels `reprocess_document` for documents: dispatch to
        the pipeline, render via the shared reprocessed presenter,
        attach the fresh `capture.reclassified` envelope so the user
        can chain another correction by replying to THIS message.

        ``capture_id`` is the event the capture was filed from. Its
        caption is read off that event again, so a correction restores
        a caption an earlier filing dropped. ``reply_text`` is the words
        of this reply, which the entry keeps as written; a note keeps it as
        a source sent as ``reply_id`` at ``reply_at``.
        """
        upload = await self._fetch_event(room_id, capture_id)
        caption = (
            attachment_caption(upload.source.get("content", {}))
            if upload is not None else ""
        )
        outcome = await self._services().capture.reprocess(
            vault_path=vault_path, user_hint=user_hint,
            sender_mxid=sender_mxid,
            initial_classification=initial_classification,
            write_in=self._rooms.write_in(ctx),
            read_in=self._rooms.scope_bucket(self._rooms.room_by_id(room_id)),
            caption=caption,
            reply_text=reply_text,
            capture_id=capture_id,
            reply_id=reply_id, reply_at=reply_at,
        )
        # `ArchivistCaptures.reply` already knows how to render the
        # reclassified branch + attach the envelope, so we just defer.
        await self._captures.reply(
            room_id, outcome, reply_to, corrected_from=initial_classification,
        )


def _joined_hint(bodies: list[str]) -> str:
    """The corrections as one hint: a single one as it is, several as a
    numbered list, most recent first.

        _joined_hint(["It is Marge's"])   # "It is Marge's"
        _joined_hint([])                  # ""
    """
    if not bodies:
        return ""
    if len(bodies) == 1:
        return bodies[0]
    numbered = "\n".join(f"  {i+1}. {b}" for i, b in enumerate(bodies))
    return (
        "Conversation of corrections, most recent first. The most "
        "recent line supersedes earlier ones when they conflict for "
        "the same field; merge non-conflicting fields:\n" + numbered
    )


def _person_of(mxid: str) -> str:
    """The person id a Matrix id stands for: `@marge:simpson` is `marge`."""
    return mxid.split(":")[0].lstrip("@").lower()
