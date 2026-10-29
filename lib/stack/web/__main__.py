"""`python -m stack.web`: read one web page with the fetch ladder, as JSON.

`stack core web fetch <url>` (`stacklets/core/cli/web.py`) runs this in
the bot runner (`stack.bot_runner.run_module`), which has the extractor
(trafilatura) and keeps a hostile page's HTML off the host. It reads
pages with the code every reader of a link uses (`fetch_url`, the ladder
the archivist files a pasted link with), so its answer is what the stack
gets from that page.

The request is one JSON object on stdin, `{"url": ..., "timeout": 30}`.
The answer is one JSON line on stdout:

    {"url", "verdict", "detail", "tier", "profile", "title", "text"}

`verdict` is `ok` or the refusal that stopped the read (`challenge`,
`login`, `empty`, ...); `title` and `text` are null when nothing was read.
"""

from __future__ import annotations

import asyncio
import json
import sys

from stack.web.fetch import fetch_url, urllib_transport


async def read(url: str, timeout: int) -> dict:
    """The fetch ladder's outcome for `url`, as the answer's fields."""
    outcome = await fetch_url(url, transport=urllib_transport(timeout=timeout))
    content = outcome.content
    return {"url": outcome.url, "verdict": outcome.verdict.name,
            "detail": outcome.verdict.detail, "tier": outcome.tier,
            "profile": outcome.profile,
            "title": content.title_hint if content else None,
            "text": content.text if content else None}


def main() -> int:
    request = json.loads(sys.stdin.read())
    answer = asyncio.run(read(request["url"], int(request.get("timeout", 30))))
    print(json.dumps(answer, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
