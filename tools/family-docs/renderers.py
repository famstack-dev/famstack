"""renderers.py — turn one document spec into a file (PDF or PNG bytes).

Specs are plain data (see specs/*.yaml). This module is the only place
that touches reportlab and Pillow, so adding a new output format means
adding a function here, not changing every spec.

Three rendering paths, picked by the spec's top-level ``render`` field:

    pdf       A native-text PDF via reportlab. One block-based layout
              expresses letters, invoices, pay slips, statements, report
              cards, contracts, prescriptions and recipes — by composing
              a letterhead, an optional recipient + meta header, a list
              of content blocks (paragraph / heading / table / bullets /
              signature) and a footer. The text layer is real, so the
              archivist's classifier reads actual text rather than OCR
              guesses.

    receipt   A narrow thermal-receipt PNG via Pillow (monospaced, a
              little sensor noise, a slight skew). Lands on the scanned
              image / OCR path the way a phone photo of a receipt would.

    drawing   A child's crayon drawing PNG via Pillow. Exercises the
              vision path and the "Memory" tag — there's barely any text
              to OCR, so classification leans on the image itself.

Every renderer returns ``(filename, data: bytes)``. The ``expected``
block some specs carry is documentation (and future eval ground truth);
it is ignored here.
"""

from __future__ import annotations

from io import BytesIO

# ── Fonts ────────────────────────────────────────────────────────────────
#
# Pillow needs a font file on disk; it ships none. We probe a short list
# of faces that exist on a stock macOS install and fall back to Pillow's
# bundled bitmap font so a missing face degrades to "plain" rather than
# crashing. reportlab brings its own Type-1 fonts, so the PDF path needs
# none of this.

from PIL import Image, ImageDraw, ImageFont

_MONO_FACES = [
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Supplemental/Courier New.ttf",
]
_CRAYON_FACES = [
    "/System/Library/Fonts/Supplemental/Chalkduster.ttf",
    "/System/Library/Fonts/MarkerFelt.ttc",
    "/System/Library/Fonts/Supplemental/Comic Sans MS.ttf",
]
_SANS_FACES = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]
_SERIF_FACES = [
    "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
    "/System/Library/Fonts/Times.ttc",
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
]
_DECO_FACES = [
    "/System/Library/Fonts/Supplemental/Didot.ttc",
    "/System/Library/Fonts/Supplemental/Bodoni 72.ttc",
    "/System/Library/Fonts/Supplemental/Baskerville.ttc",
]
_SCRIPT_FACES = [
    "/System/Library/Fonts/SnellRoundhand.ttc",
    "/System/Library/Fonts/Supplemental/Zapfino.ttf",
]


def _font(candidates: list[str], size: int) -> ImageFont.FreeTypeFont:
    """First loadable face from ``candidates`` at ``size``, else default."""
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


# ── Dispatch ───────────────────────────────────────────────────────────────


def render(spec: dict) -> tuple[str, bytes]:
    """Render ``spec`` to ``(filename, bytes)``; raise on unknown kind."""
    kind = spec.get("render")
    renderer = {
        "pdf": render_pdf,
        "receipt": render_receipt,
        "drawing": render_drawing,
        "certificate": render_certificate,
    }.get(kind)
    if renderer is None:
        raise ValueError(f"unknown render kind {kind!r} in {spec.get('filename', '?')}")
    return spec["filename"], renderer(spec)


# ── PDF (reportlab) ──────────────────────────────────────────────────────


def render_pdf(spec: dict) -> bytes:
    """Compose a one-page business document from the spec's blocks."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        HRFlowable,
        ListFlowable,
        ListItem,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    accent = colors.HexColor(spec.get("accent", "#2b3a4a"))
    muted = colors.HexColor("#6b7280")

    base = getSampleStyleSheet()["Normal"]
    body = ParagraphStyle("body", parent=base, fontName="Helvetica",
                          fontSize=10, leading=14)
    small = ParagraphStyle("small", parent=body, fontSize=8, textColor=muted, leading=11)
    small_r = ParagraphStyle("small_r", parent=small, alignment=TA_RIGHT)
    head_name = ParagraphStyle("head_name", parent=body, fontName="Helvetica-Bold",
                               fontSize=17, textColor=accent, leading=20)
    title = ParagraphStyle("title", parent=body, fontName="Helvetica-Bold",
                           fontSize=14, spaceBefore=14, spaceAfter=8)
    heading = ParagraphStyle("heading", parent=body, fontName="Helvetica-Bold",
                             fontSize=11, textColor=accent, spaceBefore=10, spaceAfter=4)

    def esc(text) -> str:
        return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    story: list = []

    # Letterhead: issuer name + address, then an accent rule.
    lh = spec.get("letterhead", {})
    if lh:
        story.append(Paragraph(esc(lh.get("name", "")), head_name))
        for line in lh.get("lines", []):
            story.append(Paragraph(esc(line), small))
        story.append(Spacer(1, 4))
        story.append(HRFlowable(width="100%", thickness=1.4, color=accent,
                                spaceBefore=2, spaceAfter=10))

    # Header row: recipient on the left, the meta key/value box on the right.
    recipient = spec.get("recipient")
    meta = spec.get("meta")
    if recipient or meta:
        left_cell = []
        if recipient:
            left_cell.append(Paragraph("<b>To</b>", small))
            left_cell.append(Paragraph(esc(recipient.get("name", "")), body))
            for line in recipient.get("lines", []):
                left_cell.append(Paragraph(esc(line), small))
        right_cell = []
        for key, value in (meta or {}).items():
            right_cell.append(Paragraph(f"<b>{esc(key)}</b>  {esc(value)}", small_r))
        header = Table([[left_cell, right_cell]], colWidths=[3.4 * inch, 3.1 * inch])
        header.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]))
        story.append(header)

    if spec.get("title"):
        story.append(Paragraph(esc(spec["title"]), title))

    for block in spec.get("blocks", []):
        if "p" in block:
            story.append(Paragraph(esc(block["p"]), body))
            story.append(Spacer(1, 6))
        elif "heading" in block:
            story.append(Paragraph(esc(block["heading"]), heading))
        elif "bullets" in block:
            items = [ListItem(Paragraph(esc(b), body), leftIndent=10)
                     for b in block["bullets"]]
            story.append(ListFlowable(items, bulletType="bullet", start="•",
                                      leftIndent=12))
            story.append(Spacer(1, 6))
        elif "table" in block:
            story.append(_pdf_table(block["table"], body, accent, muted, esc))
            story.append(Spacer(1, 6))
        elif "signature" in block:
            sig = block["signature"]
            story.append(Spacer(1, 18))
            story.append(Paragraph(f"_______________________", small))
            story.append(Paragraph(f"<b>{esc(sig.get('name', ''))}</b>", body))
            if sig.get("role"):
                story.append(Paragraph(esc(sig["role"]), small))
        elif "spacer" in block:
            story.append(Spacer(1, float(block["spacer"])))

    if spec.get("footer"):
        story.append(Spacer(1, 16))
        story.append(HRFlowable(width="100%", thickness=0.5, color=muted,
                                spaceBefore=2, spaceAfter=4))
        story.append(Paragraph(esc(spec["footer"]), small))

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER,
        leftMargin=0.9 * inch, rightMargin=0.9 * inch,
        topMargin=0.8 * inch, bottomMargin=0.8 * inch,
        title=spec.get("title", ""), author=lh.get("name", "famstack demo"),
    )
    doc.build(story)
    return buf.getvalue()


def _pdf_table(t: dict, body, accent, muted, esc):
    """A reportlab Table with a header row, optional total row, and
    sensible alignment (first column left, the rest right unless the
    spec overrides per-column with ``align``)."""
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    columns = t["columns"]
    rows = t.get("rows", [])
    total = t.get("total")
    n = len(columns)
    align = t.get("align") or (["L"] + ["R"] * (n - 1))

    data = [[esc(c) for c in columns]]
    data += [[esc(c) for c in row] for row in rows]
    if total:
        data.append([esc(c) for c in total])

    style = [
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 0), (-1, 0), accent),
        ("ROWBACKGROUNDS", (0, 1), (-1, len(rows)), [colors.white, colors.HexColor("#f3f5f7")]),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, accent),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]
    for col, a in enumerate(align):
        style.append(("ALIGN", (col, 0), (col, -1),
                      {"L": "LEFT", "R": "RIGHT", "C": "CENTER"}[a]))
    if total:
        last = len(data) - 1
        style += [
            ("FONTNAME", (0, last), (-1, last), "Helvetica-Bold"),
            ("LINEABOVE", (0, last), (-1, last), 0.8, muted),
            ("BACKGROUND", (0, last), (-1, last), colors.HexColor("#eef1f4")),
        ]

    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle(style))
    table.hAlign = "LEFT"  # sit against the left margin like a real document
    return table


# ── Receipt (Pillow) ─────────────────────────────────────────────────────


def render_receipt(spec: dict) -> bytes:
    """A narrow monospaced thermal receipt with light scan artifacts."""
    import random

    W = 520
    pad = 28
    mono = _font(_MONO_FACES, 19)
    mono_b = _font(_MONO_FACES, 22)
    small = _font(_MONO_FACES, 15)

    # Lay the text out as lines first so we can size the canvas to fit.
    store = spec.get("store", "")
    addr = spec.get("lines", [])
    items = spec.get("items", [])
    total = spec.get("total")
    paid = spec.get("paid")
    date = spec.get("date", "")
    footer = spec.get("footer", "")

    def money(value) -> str:
        # Prefix "$" only on numeric amounts — "Cash" stays "Cash".
        s = str(value)
        if s.startswith("$"):
            return s
        try:
            float(s.replace(",", "").lstrip("-"))
        except ValueError:
            return s
        return f"${s}"

    def money_line(label: str, amount) -> str:
        amount = money(amount)
        gap = max(1, 30 - len(label) - len(amount))
        return f"{label}{' ' * gap}{amount}"

    # Compose body lines (centered header handled separately).
    body_lines: list[tuple[str, ImageFont.FreeTypeFont]] = []
    for line in addr:
        body_lines.append((line, small))
    body_lines.append(("", small))
    if date:
        body_lines.append((f"{date}", small))
    body_lines.append(("-" * 30, mono))
    for name, price in items:
        body_lines.append((money_line(name, price), mono))
    body_lines.append(("-" * 30, mono))
    if total is not None:
        body_lines.append((money_line("TOTAL", total), mono_b))
    if paid:
        body_lines.append((money_line("PAID", paid), mono))
    body_lines.append(("", small))

    line_h = 26
    height = pad * 2 + 40 + line_h * (len(body_lines) + 2)
    img = Image.new("RGB", (W, height), "#fdfdfb")
    draw = ImageDraw.Draw(img)

    y = pad
    # Store name, centered and bold.
    w = draw.textlength(store, font=mono_b)
    draw.text(((W - w) / 2, y), store, font=mono_b, fill="#111111")
    y += 36
    for text, font in body_lines:
        if set(text) <= {"-", " "} or text == "":
            draw.text((pad, y), text, font=font, fill="#444444")
        else:
            draw.text((pad, y), text, font=font, fill="#111111")
        y += line_h
    if footer:
        w = draw.textlength(footer, font=small)
        draw.text(((W - w) / 2, y), footer, font=small, fill="#333333")

    # Scan artifacts: faint speckle noise + a tiny rotation so it reads
    # like a phone photo, not a clean render.
    rng = random.Random(spec.get("filename", store))
    px = img.load()
    for _ in range(int(W * height * 0.015)):
        x, yy = rng.randrange(W), rng.randrange(height)
        v = rng.randint(190, 240)
        px[x, yy] = (v, v, v)
    img = img.rotate(rng.uniform(-1.2, 1.2), expand=True, fillcolor="#fdfdfb")

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── Certificate (Pillow) ─────────────────────────────────────────────────


def render_certificate(spec: dict) -> bytes:
    """An aged, scanned-looking official certificate.

    Cream paper, a serif/decorative title, typewriter form fields with
    underlines, an inked circular seal and a cursive registrar
    signature, finished with speckle and a slight skew. This is the look
    for the older generation's documents — the ones that predate clean
    digital PDFs and would have been photographed or scanned.
    """
    import math
    import random

    W, H = 920, 1260
    paper = (243, 231, 201)
    ink = (58, 42, 24)
    faded = (122, 59, 46)
    img = Image.new("RGB", (W, H), paper)
    draw = ImageDraw.Draw(img)
    rng = random.Random(spec.get("filename", "cert"))

    deco = _font(_DECO_FACES, 56)
    serif_sm = _font(_SERIF_FACES, 18)
    label = _font(_SERIF_FACES, 19)
    typewriter = _font(_MONO_FACES, 23)
    script = _font(_SCRIPT_FACES, 40)

    # Double-rule border inset from the edge.
    for inset, width in ((28, 4), (40, 2)):
        draw.rectangle([inset, inset, W - inset, H - inset], outline=ink, width=width)

    cx = W // 2
    y = 86

    def centered(text, font, fill, yy):
        w = draw.textlength(text, font=font)
        draw.text((cx - w / 2, yy), text, font=font, fill=fill)

    centered(spec.get("authority", ""), serif_sm, ink, y)
    y += 36
    centered(spec.get("title", "Certificate of Birth"), deco, ink, y)
    y += 88
    draw.line([(120, y), (W - 120, y)], fill=ink, width=2)
    y += 36

    if spec.get("register_no"):
        draw.text((W - 300, 72), f"No. {spec['register_no']}", font=serif_sm, fill=faded)

    # Form fields: serif label, typewriter value, an underline beneath.
    left = 130
    for key, value in spec.get("fields", {}).items():
        draw.text((left, y), f"{key}:", font=label, fill=ink)
        vx = left + 250
        draw.text((vx, y + 1), str(value), font=typewriter, fill=(35, 48, 58))
        draw.line([(vx, y + 32), (W - 150, y + 32)], fill=(185, 168, 127), width=1)
        y += 60

    # Inked seal — a faded stamp on its own layer, rotated and composited.
    seal = Image.new("RGBA", (250, 250), (0, 0, 0, 0))
    sd = ImageDraw.Draw(seal)
    ink_a = faded + (160,)
    sd.ellipse([8, 8, 242, 242], outline=ink_a, width=5)
    sd.ellipse([30, 30, 220, 220], outline=ink_a, width=2)
    star = []
    for k in range(10):
        ang = -math.pi / 2 + k * math.pi / 5
        r = 34 if k % 2 == 0 else 15
        star.append((125 + math.cos(ang) * r, 125 + math.sin(ang) * r))
    sd.polygon(star, fill=ink_a)
    sf = _font(_SERIF_FACES, 16)
    for text, yy in ((spec.get("seal_text", "OFFICIAL SEAL").upper(), 60),
                     ("SPRINGFIELD COUNTY", 178)):
        w = sd.textlength(text, font=sf)
        sd.text((125 - w / 2, yy), text, font=sf, fill=ink_a)
    seal = seal.rotate(-13, expand=True, resample=Image.BICUBIC)
    img.paste(seal, (150, H - 360), seal)

    # Cursive registrar signature over a ruled line.
    if spec.get("registrar"):
        sy = H - 290
        draw.text((W - 440, sy), spec["registrar"], font=script, fill=(29, 43, 82))
        draw.line([(W - 450, sy + 64), (W - 140, sy + 64)], fill=ink, width=1)
        draw.text((W - 450, sy + 70), "Registrar of Vital Records", font=serif_sm, fill=ink)

    # Aging: warm speckle, then a slight skew so it reads as scanned.
    px = img.load()
    for _ in range(int(W * H * 0.012)):
        x, yy = rng.randrange(W), rng.randrange(H)
        v = rng.randint(150, 205)
        px[x, yy] = (v, int(v * 0.92), int(v * 0.78))
    img = img.rotate(rng.uniform(-1.1, 1.1), expand=True, fillcolor=paper)

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── Drawing (Pillow) ─────────────────────────────────────────────────────


def render_drawing(spec: dict) -> bytes:
    """A child's crayon drawing: a wobbly family scene + a caption."""
    import math
    import random

    W, H = 1000, 760
    img = Image.new("RGB", (W, H), "#fffef7")
    draw = ImageDraw.Draw(img)
    rng = random.Random(spec.get("filename", "drawing"))

    crayon = _font(_CRAYON_FACES, 64)
    crayon_s = _font(_CRAYON_FACES, 34)

    def wobble_line(x1, y1, x2, y2, fill, width=7):
        # A hand-drawn stroke: subdivide and jitter the midpoints.
        pts = [(x1, y1)]
        steps = 6
        for i in range(1, steps):
            t = i / steps
            x = x1 + (x2 - x1) * t + rng.uniform(-4, 4)
            y = y1 + (y2 - y1) * t + rng.uniform(-4, 4)
            pts.append((x, y))
        pts.append((x2, y2))
        draw.line(pts, fill=fill, width=width, joint="curve")

    def person(cx, cy, scale, body_color, skin="#ffd9a0"):
        # Head
        r = int(26 * scale)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline="#3a3a3a",
                     width=6, fill=skin)
        # Eyes + smile
        draw.ellipse([cx - r // 2, cy - 6, cx - r // 2 + 6, cy], fill="#3a3a3a")
        draw.ellipse([cx + r // 2 - 6, cy - 6, cx + r // 2, cy], fill="#3a3a3a")
        draw.arc([cx - r // 2, cy, cx + r // 2, cy + r // 2], 20, 160,
                 fill="#3a3a3a", width=4)
        # Body
        wobble_line(cx, cy + r, cx, cy + r + int(90 * scale), body_color, 14)
        # Arms + legs
        wobble_line(cx, cy + r + 24, cx - int(46 * scale), cy + r + int(60 * scale), body_color)
        wobble_line(cx, cy + r + 24, cx + int(46 * scale), cy + r + int(60 * scale), body_color)
        wobble_line(cx, cy + r + int(90 * scale), cx - int(34 * scale), cy + r + int(150 * scale), body_color)
        wobble_line(cx, cy + r + int(90 * scale), cx + int(34 * scale), cy + r + int(150 * scale), body_color)

    # Sky strip + green ground, the universal child-drawing background.
    draw.rectangle([0, 0, W, 130], fill="#cdeafe")
    draw.rectangle([0, H - 110, W, H], fill="#bfe6a6")
    # Sun
    sun = (W - 130, 100)
    draw.ellipse([sun[0] - 50, sun[1] - 50, sun[0] + 50, sun[1] + 50], fill="#ffe14d")
    for k in range(12):
        a = k * math.pi / 6
        wobble_line(sun[0] + math.cos(a) * 55, sun[1] + math.sin(a) * 55,
                    sun[0] + math.cos(a) * 85, sun[1] + math.sin(a) * 85, "#ffcf2d", 5)

    # House
    draw.rectangle([120, 380, 360, 600], outline="#8a5a2b", width=7, fill="#f6c89a")
    draw.polygon([(110, 380), (240, 290), (370, 380)], outline="#a33", width=7, fill="#e26d6d")
    draw.rectangle([210, 500, 270, 600], outline="#8a5a2b", width=6, fill="#b9763e")

    # The family, left to right, big to small (very Simpsons).
    person(520, 360, 1.5, "#2e7d32")   # tall one
    person(640, 380, 1.4, "#1565c0")
    person(740, 410, 1.0, "#e65100")
    person(820, 420, 0.9, "#6a1b9a")
    person(890, 440, 0.6, "#c2185b")   # baby

    title = spec.get("title", "")
    caption = spec.get("caption", "")
    if title:
        w = draw.textlength(title, font=crayon)
        draw.text(((W - w) / 2, 30), title, font=crayon, fill="#d84315")
    if caption:
        # Shrink the caption until it fits the width, so a long line
        # doesn't run off the right edge.
        size = 34
        cap_font = crayon_s
        while size > 18 and draw.textlength(caption, font=cap_font) > W - 80:
            size -= 2
            cap_font = _font(_CRAYON_FACES, size)
        draw.text((40, H - 64), caption, font=cap_font, fill="#3949ab")

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
