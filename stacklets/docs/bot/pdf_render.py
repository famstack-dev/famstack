"""Render PDF pages to PNG bytes for vision-capable classifiers.

Used by the archivist when a scanned PDF (no embedded text layer)
arrives and the classifier model has vision capability — turning each
page into an image is what lets vision contribute on those uploads.

Returns a list of PNG payloads, one per page, so the caller can hand
each one to the classifier as a separate `image_url` part. The model
processes each image at native resolution rather than us pre-stacking
into one tall image — quality dominates over wire-format simplicity.

Pure helper, no I/O outside the bytes the caller hands in. Returns
None on any rendering error so the caller can degrade gracefully to
text-only classification — vision is enrichment, never a gate.

Why pypdfium2: pure-Python wheel, no system deps (no poppler /
ImageMagick), permissively licensed, Linux ARM wheels available. The
bot runs inside Docker on Apple Silicon, so a pip-installable wheel
keeps the container build trivial.
"""

from __future__ import annotations

import io

from loguru import logger


# Render scale — pdfium2 measures in DPI relative to the PDF's own
# coordinate system. 150 DPI gives roughly 1240x1750 pixels for an A4
# page: enough resolution for the model's vision tower to read small
# print, well below memory limits.
_RENDER_DPI = 150


def render_pages(pdf_data: bytes) -> list[bytes] | None:
    """Render every page of `pdf_data` to PNG. Returns None on failure.

    No page cap — the caller decides what to do with very long PDFs
    (a 50-page contract becomes 50 image_url parts; the model's
    context window is the natural limit). Per-page render failures
    are logged and skipped, not fatal: a 30-page PDF where page 17
    is corrupted still returns 29 pages.

    Defensive about every external call: pypdfium2 raises on encrypted,
    corrupted, or unsupported PDFs; the caller can't always know in
    advance which it has, so any failure → None → text-only
    classification is the right fallback.
    """
    try:
        import pypdfium2 as pdfium
    except ImportError:
        logger.warning(
            "[pdf_render] pypdfium2 not installed — cannot render scanned "
            "PDFs to images. Install in the bot container."
        )
        return None

    try:
        doc = pdfium.PdfDocument(pdf_data)
    except Exception as e:
        logger.warning("[pdf_render] could not open PDF: {}", e)
        return None

    try:
        if len(doc) == 0:
            logger.warning("[pdf_render] PDF has zero pages")
            return None

        # pdfium uses scale = pixels per PDF point; 72 points per inch.
        scale = _RENDER_DPI / 72.0
        pages: list[bytes] = []
        for i in range(len(doc)):
            try:
                pil_image = doc[i].render(scale=scale).to_pil()
                buf = io.BytesIO()
                pil_image.save(buf, format="PNG")
                pages.append(buf.getvalue())
            except Exception as e:
                logger.warning("[pdf_render] page {} render failed: {} — "
                               "skipping", i, e)

        if not pages:
            return None
        return pages
    except Exception as e:
        logger.warning("[pdf_render] render failed: {}", e)
        return None
    finally:
        try:
            doc.close()
        except Exception:
            pass
