"""A QR code as inline SVG, for an address a phone opens with its camera.

The setup page's step "Chat on your phone" shows one: the phone scans it
and opens famstack's own page on the Mac (`phone_page.py`). The code is
drawn on the server, into the page's HTML, so it shows without
JavaScript and nothing is loaded from elsewhere.

    svg("http://192.168.1.20:42000/go/app", label="QR code: http://192.168.1.20:42000/go/app")
    # '<svg class="setup-qr-code" viewBox="0 0 37 37" role="img" aria-label="QR code: ...">'
    # '<rect .../><path d="M4 4h7v1h-7z..." .../></svg>'

Why segno: a QR encoder is a few hundred lines of error correction and
masking that nobody here should maintain. segno is pure Python with no
dependencies of its own, and the copy deck's mock drew its code with it.
The code uses error correction M and the four-module quiet zone a
scanner needs around it. It is black on white in every theme, because a
phone's camera reads dark modules on a light ground.

Pure: no web framework, no I/O.
"""

from __future__ import annotations

from html import escape

import segno

__all__ = ["svg"]

# The quiet zone, in modules: the white border a scanner needs to find the code.
QUIET_ZONE = 4


def svg(data: str, *, label: str) -> str:
    """`data` as a QR code: one `<svg>` element, sized by CSS.

    Returns the element with `role="img"` and `label` as its `aria-label`,
    so a screen reader says what the code holds. The modules are one path
    on a white square that includes the quiet zone.

        svg("http://192.168.1.20:42000/go/app", label="QR code")
        # '<svg class="setup-qr-code" viewBox="0 0 37 37" role="img" aria-label="QR code" ...'
    """
    code = segno.make(data, error="m", micro=False)
    side = code.symbol_size(border=QUIET_ZONE)[0]
    return (f'<svg class="setup-qr-code" viewBox="0 0 {side} {side}" role="img" '
            f'aria-label="{escape(label)}" shape-rendering="crispEdges">'
            f'<rect width="{side}" height="{side}" fill="#fff"/>'
            f'<path d="{_path(code)}" fill="#000"/></svg>')


# ── Drawing ───────────────────────────────────────────────────────────────

def _path(code: segno.QRCode) -> str:
    """The dark modules as one SVG path: a run of dark modules in a row is
    one rectangle, so the path stays short."""
    parts = []
    for y, row in enumerate(code.matrix_iter(border=QUIET_ZONE)):
        x = 0
        for dark, run in _runs(row):
            if dark:
                parts.append(f"M{x} {y}h{run}v1h-{run}z")
            x += run
    return "".join(parts)


def _runs(row) -> list[tuple[bool, int]]:
    """A row of modules as (dark, length) runs: `[1, 1, 0]` is `[(True, 2), (False, 1)]`."""
    runs: list[tuple[bool, int]] = []
    for module in row:
        dark = bool(module)
        if runs and runs[-1][0] == dark:
            runs[-1] = (dark, runs[-1][1] + 1)
        else:
            runs.append((dark, 1))
    return runs
