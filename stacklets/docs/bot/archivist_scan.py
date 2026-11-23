"""archivist_scan: the scan mode, several photos filed as one document.

    scan = ArchivistScan(bot, documents=documents)
    await scan.open(room_id, "@marge:simpson", "neue Personalausweise", reply_to)
    await scan.add_page(room_id, event, mxc_url, "IMG_0412.jpg")     # 👀 per page
    await scan.close(room_id, "@marge:simpson", "", reply_to, ctx=ctx, date_filed=None)

A letter of three pages is three photos. A person types `(` or `scan`
(with an optional caption), sends the pages, and types `)` or `fertig`.
The archivist keeps the pages of each sender in memory, combines them
into one PDF and files it as one document. One page is filed as it is;
no page at all cancels the scan. Every caption on the way (the opener's,
a page's, the closer's) is joined into the hint the classifier reads.

Only photos and files are pages. A line of text or a voice message sent
during a scan is filed as it is outside the scan, as a note of its own,
and `say_still_open` tells the person that the scan waits for `)`. A
typed line counts as handed over on purpose while the scan is open, so
even a short one is kept (the router asks `is_open` for that).

The sessions live in the process only: a restart in the middle of a
scan loses its pages, and the person sends them again.
"""

from __future__ import annotations

import io

from PIL import Image

from stack.bot import EYES, RoomContext

from .text_utils import clean_filename, join_captions

__all__ = ["ArchivistScan"]


class ArchivistScan:
    """The open scans, one per sender, and filing them as one document.

        scan = ArchivistScan(bot, documents=documents)
        scan.is_open("@marge:simpson")   # False until `(` opens one
    """

    def __init__(self, bot, *, documents):
        self._bot = bot
        self._documents = documents
        # Sender mxid -> {"files": [(filename, bytes)], "room_id", "caption"}.
        self.sessions: dict[str, dict] = {}

    def is_open(self, sender: str) -> bool:
        """Whether `sender` has a scan open; their next upload is a page,
        and their next typed line is a note."""
        return sender in self.sessions

    async def open(self, room_id: str, sender: str, caption: str, reply_to: str) -> None:
        """Open a scan for `sender` with the opener's `caption`, and say so."""
        sender_name = sender.split(":")[0].replace("@", "").capitalize()
        self.sessions[sender] = {"files": [], "room_id": room_id, "caption": caption}
        await self._bot._send(room_id, self._bot.t("scan_started", sender=sender_name), reply_to)

    async def say_still_open(self, room_id: str, reply_to: str) -> None:
        """Say that the scan is still open, in reply to a message that was
        filed beside it, so that the person knows `)` still ends it."""
        await self._bot._send(room_id, self._bot.t("scan_still_open"), reply_to)

    async def close(
        self, room_id: str, sender: str, caption: str, reply_to: str,
        *, ctx: RoomContext, date_filed: str | None,
    ) -> None:
        """Close `sender`'s scan with the closer's `caption` and file it;
        without an open scan, say that none is open."""
        if sender not in self.sessions:
            await self._bot._send(room_id, self._bot.t("no_active_scan"), reply_to)
            return
        if caption:
            session = self.sessions[sender]
            session["caption"] = join_captions(session.get("caption", ""), caption)
        await self.complete(room_id, sender, reply_to, ctx=ctx, date_filed=date_filed)

    # ── Scan mode ────────────────────────────────────────────────────────

    async def add_page(
        self, room_id: str, event, url: str, raw_filename: str,
        caption: str = "",
    ):
        reply_to = event.event_id
        try:
            file_data = await self._bot._download_media(url)
        except Exception as e:
            await self._bot._send(room_id, self._bot.t("scan_page_failed", error=str(e)), reply_to)
            return

        if not file_data:
            await self._bot._send(room_id, self._bot.t("scan_page_failed_matrix"), reply_to)
            return

        session = self.sessions[event.sender]
        session["files"].append((raw_filename, file_data))
        if caption:
            session["caption"] = join_captions(session.get("caption", ""), caption)
        # 👀 on the page instead of a "Page N received." line — the
        # batch can run several pages deep, so a reaction per page keeps
        # the timeline clean; the scan-complete reply is the closure.
        await self._bot._react(room_id, reply_to, EYES)


    async def complete(
        self, room_id: str, sender: str, reply_to: str | None = None,
        *, ctx: RoomContext, date_filed: str | None = None,
    ):
        """Close the sender's scan and file its pages as one document.

        One page is filed as it is; several are combined into one PDF.
        A scan closed with no pages is cancelled, and files nothing.
        """
        session = self.sessions.pop(sender)
        files = session["files"]
        caption = session.get("caption", "").strip()

        # Nothing accumulated -- the user opened a session and closed it
        # without sending anything. Treat as cancelled, same as today.
        if not files:
            await self._bot._send(room_id, self._bot.t("scan_cancelled"), reply_to)
            return

        if len(files) == 1:
            filename, file_data = files[0]
            display_name = clean_filename(filename)
            await self._bot._send(
                room_id, self._bot.t("scan_complete_single"), reply_to,
            )
        else:
            combined = await self._combine(room_id, sender, files, reply_to)
            if combined is None:
                return
            filename, display_name, file_data = combined
        await self._documents.file(
            room_id, filename, display_name, file_data, reply_to,
            ctx=ctx, date_filed=date_filed,
            submitter_mxid=sender,
            user_hint=caption or None,
        )

    async def _combine(
        self, room_id: str, sender: str, files: list[tuple[str, bytes]],
        reply_to: str | None,
    ) -> tuple[str, str, bytes] | None:
        """The pages as one PDF with its file and display name, or None
        after saying in the chat why they could not be combined."""
        page_count = len(files)
        await self._bot._send(
            room_id,
            self._bot.t("scan_complete_multi", count=page_count),
            reply_to,
        )
        try:
            pdf_data = _combine_images_to_pdf(files)
        except Exception as e:
            await self._bot._send(
                room_id,
                self._bot.t("scan_combine_failed", error=str(e)),
                reply_to,
            )
            return None
        sender_name = sender.split(":")[0].replace("@", "").capitalize()
        filename = f"scan-{sender_name.lower()}-{page_count}p.pdf"
        return filename, f"scan ({page_count} pages)", pdf_data


def _combine_images_to_pdf(files: list[tuple[str, bytes]]) -> bytes:
    """Combine multiple image files into a single multi-page PDF."""
    images = []
    for _, data in files:
        img = Image.open(io.BytesIO(data))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        images.append(img)

    pdf_buffer = io.BytesIO()
    images[0].save(pdf_buffer, "PDF", save_all=True, append_images=images[1:])
    return pdf_buffer.getvalue()
