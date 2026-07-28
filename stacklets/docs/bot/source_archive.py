"""Assemble a source card (header + body + attachments) into one PDF.

Reacting 📎 / 📄 on an ingest source card (a ``dev.famstack.source`` message
posted by the mail bot today, another ingest channel tomorrow) files the
whole thing as one document: page one is a text page carrying the header the
producing bot rendered plus the verbatim body, and every attachment follows
as further pages (images kept as-is, PDFs rasterised page by page). The
archivist hands the result to the normal document pipeline, so it is OCR'd,
classified, and filed like any scan.

This module is deliberately source-agnostic: it knows nothing about email
(no ``From``/``Subject`` parsing) — the caller passes an already-rendered
header string and the body. Everything here is pure and offline: Pillow
draws the text pages and writes the combined multi-page PDF, pypdfium2 (via
``pdf_render``) rasterises attachment PDFs. No Matrix, no network, no running
stack, so the assembly is unit-testable on its own.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from loguru import logger
from PIL import Image, ImageDraw, ImageFont

from pdf_render import render_pages


# Text pages are laid out at A4, ~150 DPI. Attachment images and rasterised
# PDF pages keep their own dimensions; only the text pages we draw use these.
_PAGE_W, _PAGE_H = 1240, 1754
_MARGIN = 70
_FONT_SIZE = 28
_LINE_GAP = 10


@dataclass
class SourceAttachment:
    """One source attachment: its filename, MIME type, and raw bytes."""

    filename: str
    mimetype: str
    data: bytes


def _font() -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """A legible, scalable font that exists in the container.

    Pillow's ``load_default(size=...)`` ships a bundled TrueType face since
    10.1, so we never depend on system fonts (the slim bot image carries
    none). Fall back to the fixed bitmap default on any older Pillow.
    """
    try:
        return ImageFont.load_default(size=_FONT_SIZE)
    except TypeError:
        return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    """Word-wrap one logical line to ``max_width`` pixels, keeping blanks."""
    if not text:
        return [""]
    lines: list[str] = []
    words = text.split(" ")
    cur = ""
    for word in words:
        trial = word if not cur else f"{cur} {word}"
        if draw.textlength(trial, font=font) <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    lines.append(cur)
    return lines


def render_text_pages(text: str) -> list[bytes]:
    """Render ``text`` into one or more PNG page images.

    Wraps to the page width and paginates on overflow, so a long body
    becomes several pages rather than one clipped one. Always returns at
    least one page.
    """
    font = _font()
    probe = ImageDraw.Draw(Image.new("RGB", (_PAGE_W, _PAGE_H), "white"))
    line_h = (probe.textbbox((0, 0), "Ag", font=font)[3]) + _LINE_GAP
    usable_w = _PAGE_W - 2 * _MARGIN
    lines_per_page = max(1, (_PAGE_H - 2 * _MARGIN) // line_h)

    # Flatten the text into wrapped display lines, preserving blank lines.
    display: list[str] = []
    for logical in text.split("\n"):
        display.extend(_wrap(probe, logical, font, usable_w))

    pages: list[bytes] = []
    for start in range(0, max(len(display), 1), lines_per_page):
        chunk = display[start:start + lines_per_page]
        img = Image.new("RGB", (_PAGE_W, _PAGE_H), "white")
        draw = ImageDraw.Draw(img)
        y = _MARGIN
        for line in chunk:
            draw.text((_MARGIN, y), line, fill="black", font=font)
            y += line_h
        buf = io.BytesIO()
        img.save(buf, "PNG")
        pages.append(buf.getvalue())
    return pages


def _load_rgb(data: bytes) -> Image.Image | None:
    """Open image bytes as an RGB Pillow image, or ``None`` if undecodable."""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:  # Pillow raises a zoo of errors on bad input.
        logger.debug("[source_archive] undecodable image: {}", e)
        return None
    if img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")
    return img


def _attachment_pages(att: SourceAttachment) -> list[Image.Image]:
    """Render one attachment to page images.

    Images pass through; PDFs are rasterised page by page; anything else
    (or an unreadable image/PDF) becomes a single placeholder page naming
    the file, so nothing is ever silently dropped from the archive.
    """
    mt = (att.mimetype or "").lower()
    is_pdf = mt == "application/pdf" or att.filename.lower().endswith(".pdf")

    if mt.startswith("image/"):
        img = _load_rgb(att.data)
        if img is not None:
            return [img]
    elif is_pdf:
        pngs = render_pages(att.data)
        if pngs:
            return [img for png in pngs if (img := _load_rgb(png)) is not None]

    placeholder = (
        f"Attachment: {att.filename}\n"
        f"Type: {att.mimetype or 'unknown'}\n\n"
        "(not embedded in this archive)"
    )
    return [_load_rgb(png) for png in render_text_pages(placeholder)]


def build_source_pdf(
    header: str, body: str, attachments: list[SourceAttachment],
) -> bytes:
    """Assemble header + body + attachments into one multi-page PDF.

    Page one onward is the ``header`` followed by the ``body`` (as text
    pages); each attachment then contributes its own page(s). Returns the
    PDF bytes, ready for the document pipeline.
    """
    pages: list[Image.Image] = []

    first = f"{header}\n\n{body}".strip() if body else header.strip()
    pages.extend(img for png in render_text_pages(first)
                 if (img := _load_rgb(png)) is not None)

    for att in attachments:
        pages.extend(p for p in _attachment_pages(att) if p is not None)

    if not pages:  # header is always non-empty in practice; belt and braces.
        pages = [Image.new("RGB", (_PAGE_W, _PAGE_H), "white")]

    buf = io.BytesIO()
    pages[0].save(buf, "PDF", save_all=True, append_images=pages[1:])
    return buf.getvalue()
