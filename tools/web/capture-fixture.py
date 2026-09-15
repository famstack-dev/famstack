#!/usr/bin/env python3
"""Capture a live page as a test fixture for the web quality gate.

Adding a site profile should cost one fixture and one test. This is the
fixture half: it fetches a URL the way the archivist does, strips the
bulk that no detector reads, and writes the result into
`tests/fixtures/web/`.

Why a tool rather than "save the page": fixtures have to stay real. The
gate's whole claim is that it was tested against what sites actually
serve, and a hand-written fixture agrees with the detector that reads
it by construction. So this keeps every tag, attribute, `<meta>` and
`ld+json` block byte-for-byte, and drops only inline script and style
*bodies* -- which are most of the megabyte and none of the meaning.

Usage:

    uv run python tools/web/capture-fixture.py <url> <fixture-name>

(`uv run` because the framework needs Python 3.11+ for tomllib, and
the system python on macOS is usually older.)

Then add a row to `tests/fixtures/web/README.md` saying where it came
from and what the site answered, and a test asserting the verdict.
"""

from __future__ import annotations

import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

from stack.web.fetch import BROWSER_HEADERS  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "web"


def shrink(html: str) -> str:
    """Drop the bytes no detector reads, keep everything one might."""
    def _script(match: re.Match) -> str:
        attrs = match.group(1)
        # ld+json is data, not code — it is the whole point of tier 1.
        if "ld+json" in attrs.lower():
            return match.group(0)
        return f"<script{attrs}></script>"

    html = re.sub(r"<script([^>]*)>.*?</script>", _script, html, flags=re.S | re.I)
    html = re.sub(r"<style([^>]*)>.*?</style>", r"<style\1></style>", html, flags=re.S | re.I)
    html = re.sub(r"data:[a-z/+-]+;base64,[A-Za-z0-9+/=]+", "data:stripped", html)
    html = re.sub(r'(\ssrcset=")[^"]{200,}(")', r"\1stripped\2", html)
    html = re.sub(r'(\sd=")[^"]{200,}(")', r"\1stripped\2", html)
    return html


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__.strip().split("Usage:")[1].strip(), file=sys.stderr)
        return 2
    url, name = argv[1], argv[2]

    request = urllib.request.Request(url, headers=dict(BROWSER_HEADERS))
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status, landed = response.status, response.url
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as err:
        # A 403 challenge page *is* the fixture we usually want.
        status, landed = err.code, err.url
        body = err.read().decode("utf-8", errors="replace")
    except OSError as err:
        print(f"could not fetch {url}: {err}", file=sys.stderr)
        return 1

    shrunk = shrink(body)
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / f"{name}.html").write_text(shrunk, encoding="utf-8")

    title = re.search(r"<title[^>]*>([^<]*)</title>", shrunk, re.I | re.S)
    print(f"wrote tests/fixtures/web/{name}.html  ({len(shrunk)} bytes, from {len(body)})")
    print(f"  HTTP {status}")
    print(f"  landed on {landed}")
    print(f"  title {title.group(1).strip()!r}" if title else "  no <title>")
    if landed != url:
        print("  note: the redirect target is the gate's signal — record it in the README")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
