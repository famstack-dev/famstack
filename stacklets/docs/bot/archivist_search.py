"""archivist_search: a question to the archive, and showing one document.

    search = ArchivistSearch(bot, rooms=rooms, documents=documents,
                             captures=captures, services=lambda: services)
    await search.search(room_id, "Duff Versicherung", reply_to, sender="@homer:simpson")
    await search.show(room_id, 42, reply_to)          # "show 42": the document's text

Free text in the documents room, or any message that @-mentions the
archivist, is a search. The search service (`search_service.py`) asks
Paperless and the memory and writes the answer; this module scopes it
to the room's topic when the room has one, and posts the answer. A
search in a topic room looks only in that topic's folder; outside it,
the whole archive.

`show <n>` posts the text Paperless read from document n, cut at 4000
characters, because Matrix limits the size of one message.
"""

from __future__ import annotations

from typing import Callable

__all__ = ["ArchivistSearch"]

# Matrix limits the size of one message; a longer document is cut here.
SHOW_MAX_CHARS = 4000


class ArchivistSearch:
    """Answers a search or a `show <n>` in the room it was asked in.

        search = ArchivistSearch(bot, rooms=rooms, documents=documents,
                                 captures=captures, services=lambda: services)
        await search.search(room_id, "Zeugnis Lisa", reply_to, sender="@marge:simpson")
    """

    def __init__(self, bot, *, rooms, documents, captures, services: Callable[[], object]):
        self._bot = bot
        self._rooms = rooms
        self._documents = documents
        self._captures = captures
        self._services = services

    # ── Search ───────────────────────────────────────────────────────────

    async def search(
        self, room_id: str, query: str, reply_to: str | None = None,
        *, sender: str | None = None,
    ):
        """Search Paperless + the memory vault and reply.

        Delegates to SearchService for the work (query resolution, dual
        search, synthesis + bounded deep-dive); it returns the reply
        text. Search posts no confirmation before the work, so typing
        starts immediately and the framework's wrap clears it on exit.
        The one mid-flow status (the deep-dive "looking deeper" note) is
        sent through the `announce` callback.

        A search asked in a topic room auto-scopes to that topic's
        bucket -- the room context narrows the haystack. The user can
        still reach a wider search by asking outside the topic room.
        """
        await self._bot._set_typing(room_id, on=True)

        topic_bucket: str | None = None
        if sender:
            binding = await self._rooms.topic_binding(
                self._rooms.room_by_id(room_id), sender,
            )
            if binding is not None:
                topic_bucket = binding.bucket

        reply = await self._services().search.run(
            query=query, sender=sender,
            notifier=self._captures.notifier(room_id, reply_to),
            topic_bucket=topic_bucket,
        )
        await self._bot._send(room_id, reply, reply_to)


    # ── Show document content ─────────────────────────────────────────

    async def show(self, room_id: str, doc_id: int, reply_to: str | None = None):
        """Fetch a document's content from Paperless and return it as Markdown."""
        doc = await self._services().paperless.get_doc(doc_id)
        if not doc:
            await self._bot._send(room_id, self._bot.t("show_not_found", doc_id=doc_id), reply_to)
            return

        title = doc.get("title", self._bot.t("show_untitled"))
        content = doc.get("content", "").strip()
        link = await self._documents.document_link(doc_id)

        if not content:
            await self._bot._send(room_id, self._bot.t("show_no_text", title=title, link=link), reply_to)
            return

        if len(content) > SHOW_MAX_CHARS:
            content = content[:SHOW_MAX_CHARS] + "\n\n" + self._bot.t("show_truncated")

        lines = [f"**{title}**", "", content]
        if link:
            lines.extend(["", link])

        await self._bot._send(room_id, "\n".join(lines), reply_to)
