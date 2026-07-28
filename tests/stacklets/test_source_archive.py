"""Unit tests for the email → single-PDF assembler (`email_archive`).

These are pure and offline: they build fixture images and PDFs in-process,
run them through `build_source_pdf`, and count the resulting pages with
pypdf. No Matrix, no Paperless, no running stack.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

from PIL import Image
from pypdf import PdfReader

# The bot modules live outside the package tree; add the archivist bot dir
# to the path exactly as the container does.
sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "stacklets" / "docs" / "bot")
)

from source_archive import (  # noqa: E402
    SourceAttachment,
    build_source_pdf,
    render_text_pages,
)


def _png_bytes(color: tuple[int, int, int], size=(240, 160)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def _pdf_bytes(n_pages: int) -> bytes:
    """A minimal n-page PDF built from solid-colour images."""
    imgs = [Image.new("RGB", (240, 160), (40 * i % 255, 10, 10)) for i in range(n_pages)]
    buf = io.BytesIO()
    imgs[0].save(buf, "PDF", save_all=True, append_images=imgs[1:])
    return buf.getvalue()


def _page_count(pdf_bytes: bytes) -> int:
    return len(PdfReader(io.BytesIO(pdf_bytes)).pages)


HEADER = "From: service@osiander.de\nSubject: Bestellung 1434978587\nDate: 2026-07-18"


class TestRenderTextPages:
    def test_returns_at_least_one_openable_png(self):
        pages = render_text_pages("Hello world")
        assert len(pages) >= 1
        img = Image.open(io.BytesIO(pages[0]))
        assert img.width > 0 and img.height > 0

    def test_long_text_paginates(self):
        pages = render_text_pages("lorem ipsum " * 3000)
        assert len(pages) > 1, "a very long body should span multiple pages"


class TestBuildEmailPdf:
    def test_body_only_is_at_least_one_page(self):
        pdf = build_source_pdf(HEADER, "A short body.", [])
        assert _page_count(pdf) >= 1

    def test_image_attachment_adds_one_page(self):
        base = _page_count(build_source_pdf(HEADER, "body", []))
        pdf = build_source_pdf(
            HEADER, "body",
            [SourceAttachment("scan.png", "image/png", _png_bytes((0, 200, 0)))],
        )
        assert _page_count(pdf) == base + 1

    def test_pdf_attachment_contributes_all_its_pages(self):
        base = _page_count(build_source_pdf(HEADER, "body", []))
        pdf = build_source_pdf(
            HEADER, "body",
            [SourceAttachment("invoice.pdf", "application/pdf", _pdf_bytes(3))],
        )
        assert _page_count(pdf) == base + 3

    def test_multiple_attachments_accumulate(self):
        base = _page_count(build_source_pdf(HEADER, "body", []))
        pdf = build_source_pdf(
            HEADER, "body",
            [
                SourceAttachment("a.png", "image/png", _png_bytes((0, 0, 200))),
                SourceAttachment("b.pdf", "application/pdf", _pdf_bytes(2)),
            ],
        )
        assert _page_count(pdf) == base + 1 + 2

    def test_unsupported_attachment_becomes_a_placeholder_page(self):
        base = _page_count(build_source_pdf(HEADER, "body", []))
        pdf = build_source_pdf(
            HEADER, "body",
            [SourceAttachment("contract.docx", "application/msword", b"not a real doc")],
        )
        # Nothing is dropped: the unreadable attachment still yields a page.
        assert _page_count(pdf) == base + 1

    def test_undecodable_image_does_not_crash_and_still_files(self):
        base = _page_count(build_source_pdf(HEADER, "body", []))
        pdf = build_source_pdf(
            HEADER, "body",
            [SourceAttachment("broken.png", "image/png", b"\x89PNG not really")],
        )
        assert _page_count(pdf) == base + 1
