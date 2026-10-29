"""The compile's record of what each model call cost.

Every model call the compile makes hands the chat client a recorder
(`recorder`): a wiki page (`view`), a diary month's paragraph
(`diary-month`), a slice of the memories room read for its entries
(`diary-room`), an entry read for its card (`diary-entry`), and a
recording's transcript polished (`transcript-polish`) or its names
repaired (`transcript-names`). The client gives it a `CallCost` once per
call, for an answer and for a call that failed. With `WIKI_CALLS_LOG` set
to a path, the record is appended there as one JSON object per line:

    {"kind": "view", "page": "index.md", "at": "2026-10-28T13:58:54+00:00",
     "ok": true, "estimated_tokens": 1245, "seconds": 2.9,
     "prompt_tokens": 777, "cached_tokens": 0, "completion_tokens": 210,
     "finish_reason": "stop", "max_tokens": 8192, "error": null,
     "cards": 12, "deferred": 0}

`page` is the path of the page the call is for, None for a call that
serves no one page. A home or person page's call also says how many
cards it carried (`cards`) and how many it left for a later call
(`deferred`, `fold.py`). Work on prompts and budgets reads these numbers back,
so the record is data, not a sentence. Core renders `WIKI_CALLS_LOG` for
the bot runner and the curator sets it for the compile it runs; both name
one file in the curator's state dir, so an instance keeps the record
without configuration. A person sees nothing of it: the readable line
goes to DEBUG, and the compile's output stays what it was.

Writing the record never fails the call: the client catches anything a
recorder raises and logs it at DEBUG (`stack.ai.client._report`).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from stack.ai.client import CallCost

# The file the record is appended to; unset, the record is only logged.
CALLS_LOG = "WIKI_CALLS_LOG"

# Past this size the record moves to `<file>.1` once, replacing the one
# there, and a new file starts. A record is about 300 bytes and a nightly
# sweep makes a few dozen calls, so 5 MB holds well over a year: enough to
# compare prompts across releases, and the data dir never grows with it.
ROTATE_BYTES = 5 * 1024 * 1024


def recorder(kind: str, page: str | None = None,
             detail: dict | None = None) -> Callable[[CallCost], None]:
    """What the client calls with the cost of one model call of `kind`, for `page`.

    `detail` is what the caller knows of the call besides its cost, added
    to the record as it is: a folded page's `cards` (how many the call
    carried) and `deferred` (how many it left for a later call).
    """

    def record(cost: CallCost) -> None:
        logger.debug("[llm] {} {}: {}", kind, page or "", _readable(cost))
        path = os.environ.get(CALLS_LOG)
        if not path:
            return
        entry = {"kind": kind, "page": page,
                 "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "ok": cost.ok, **asdict(cost), **(detail or {})}
        _append(Path(path), json.dumps(entry, ensure_ascii=False) + "\n")

    return record


def _append(path: Path, line: str) -> None:
    """Append `line` to `path`, rotating it once past `ROTATE_BYTES`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > ROTATE_BYTES:
        path.replace(path.with_name(path.name + ".1"))
    with path.open("a", encoding="utf-8") as f:
        f.write(line)


def _readable(cost: CallCost) -> str:
    """The record as one line for a person reading the debug log."""
    parts = [f"~{cost.estimated_tokens} prompt tokens estimated"]
    if cost.prompt_tokens is not None:
        cached = f" ({cost.cached_tokens} cached)" if cost.cached_tokens is not None else ""
        parts.append(f"{cost.prompt_tokens} prompt{cached}")
    if cost.completion_tokens is not None:
        parts.append(f"{cost.completion_tokens} completion")
    if cost.prompt_tokens is None and cost.completion_tokens is None and cost.error is None:
        parts.append("the server sent no counts")
    if cost.finish_reason == "length":
        parts.append(f"cut off at {cost.max_tokens} tokens")
    parts.append(f"{cost.seconds:.1f}s")
    if cost.error:
        parts.append(f"failed: {cost.error}")
    return ", ".join(parts)
