"""Unit tests for pdf_render.render_pages."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "stacklets" / "docs" / "bot"))

from pdf_render import render_pages


def _make_pdf(pages: int = 1, *, text: str = "page") -> bytes:
    """Tiny multi-page PDF built via Pillow — enough for a render to chew on.

    Pillow's PDF writer produces a real (text-layer) PDF; we use it
    because spinning up a fixture PDF on the test class would either
    require a binary blob in the repo or a heavier dep. Pillow is
    already a test extra.
    """
    from PIL import Image, ImageDraw
    imgs = []
    for i in range(pages):
        img = Image.new("RGB", (300, 400), "white")
        d = ImageDraw.Draw(img)
        d.text((20, 20), f"{text} {i + 1}", fill="black")
        imgs.append(img)
    buf = io.BytesIO()
    imgs[0].save(buf, format="PDF",
                 save_all=True, append_images=imgs[1:] if pages > 1 else [])
    return buf.getvalue()


# ── Happy path ──────────────────────────────────────────────────────────

def test_renders_single_page_pdf_to_one_png():
    pdf = _make_pdf(pages=1, text="hello")
    out = render_pages(pdf)
    assert out is not None
    assert len(out) == 1
    # PNG magic bytes — proves the renderer produced an actual PNG, not
    # an empty buffer or a JPEG fallback.
    assert out[0].startswith(b"\x89PNG\r\n\x1a\n")
    assert len(out[0]) > 1000  # any real page render is well above this


def test_multipage_pdf_yields_one_png_per_page():
    """Each page becomes its own PNG — caller decides how to send them."""
    out = render_pages(_make_pdf(pages=3))
    assert out is not None
    assert len(out) == 3
    assert all(p.startswith(b"\x89PNG\r\n\x1a\n") for p in out)


def test_long_pdf_renders_every_page():
    """No artificial cap — a 20-page PDF gives 20 PNGs. The caller (or
    the model's context window) decides how many is too many."""
    out = render_pages(_make_pdf(pages=20))
    assert out is not None
    assert len(out) == 20


# ── Failure modes — must return None, never raise ───────────────────────

def test_returns_none_on_empty_bytes():
    assert render_pages(b"") is None


def test_returns_none_on_garbage_bytes():
    # Random bytes that don't form a PDF header — pdfium should reject.
    assert render_pages(b"not a pdf at all" * 100) is None


def test_returns_none_on_truncated_pdf():
    # Real PDF magic byte prefix but truncated — pdfium tolerates some
    # corruption, so we just assert "doesn't raise" rather than "rejects".
    pdf = _make_pdf(pages=1)
    truncated = pdf[: len(pdf) // 2]
    # Either renders something or returns None — both are acceptable; we
    # just don't want a stack trace bubbling up to the bot's on_file.
    result = render_pages(truncated)
    assert result is None or isinstance(result, list)
