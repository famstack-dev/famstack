"""The compile's record of what each model call cost.

Every model call the compile makes (a wiki page, a diary month's
paragraph) hands the chat client a recorder for its page (`recorder`).
The client gives it a `CallCost` once per call, for an answer and for a
call that failed. With `WIKI_CALLS_LOG` set to a path, the record is
appended there as one JSON object per line:

    {"page": "index.md", "at": "2026-10-28T13:58:54+00:00", "ok": true,
     "error": null, "estimated_tokens": 1245, "prompt_tokens": 777,
     "cached_tokens": 0, "completion_tokens": 210, "seconds": 2.9}

Work on prompts and budgets reads these numbers back, so the record is
data, not a sentence. The curator sets `WIKI_CALLS_LOG` for the compile
it runs (`curator.calls_log`), so an instance keeps the record without
configuration. A person sees nothing of it: the readable line goes to
DEBUG, and the compile's output stays what it was.

Writing the record never fails the call: the client catches anything a
recorder raises and logs it (`stack.ai.client._report`).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timezone

from loguru import logger

from stack.ai.client import CallCost

# The file the record is appended to; unset, the record is only logged.
CALLS_LOG = "WIKI_CALLS_LOG"


def recorder(page: str) -> Callable[[CallCost], None]:
    """What the client calls with the cost of the model call for `page`."""

    def record(cost: CallCost) -> None:
        logger.debug("[llm] {}: {}", page, _readable(cost))
        path = os.environ.get(CALLS_LOG)
        if not path:
            return
        entry = {"page": page, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "ok": cost.ok, **asdict(cost)}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return record


def _readable(cost: CallCost) -> str:
    """The record as one line for a person reading the debug log."""
    estimate = f"~{cost.estimated_tokens} prompt tokens estimated"
    if not cost.ok:
        return f"{estimate}, failed after {cost.seconds:.1f}s: {cost.error}"
    if cost.prompt_tokens is None:
        return f"{estimate}, the server sent no counts, {cost.seconds:.1f}s"
    return (f"{estimate}, {cost.prompt_tokens} prompt ({cost.cached_tokens} cached), "
            f"{cost.completion_tokens} completion, {cost.seconds:.1f}s")
