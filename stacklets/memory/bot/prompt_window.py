"""prompt_window: keep a prompt's sources inside the model's context window.

A page prompt numbers every source it reads, oldest filing first. As a
family files, the home page's prompt grows with every summary the family
reads, and one day it no longer fits the context window the AI server
reports for the model. The server then refuses the call, and the page
never changes again. This module leaves out the oldest sources until
the prompt fits, with room for the answer:

    fit = prompt_window.fit(index, home_prompt, window=32_768, answer_tokens=2_048)
    fit.kept        # the newest sources, still in filed order
    fit.left_out    # how many of the oldest it dropped: 0 when all fit

Worked example. 500 summaries make a home prompt of about 40,000 tokens.
The window is 32,768 tokens and the answer needs about 2,048, so the
prompt may take 30,720. `fit` finds the smallest number of oldest
summaries to leave out (say 120) and returns the other 380. Under the limit it returns
the list it was given, so the prompt is the same, byte for byte.

Why the oldest go first: the newest filings are what the family asks
about, and the home page tells what happened lately. Why a ceiling and not a
budget: the window is the server's hard limit
(`stack.ai.context_window`). A smaller budget would cut facts from every
home page long before the server refuses one, which is a design decision
of its own (#341). For the same reason the caller reserves the answer's
usual size, not its cap.

It does not count tokens. It estimates them from characters with
`stack.ai.context_window.window_estimate`, and it asks no server: the
caller passes the window it asked for once per job, or None when the
server does not say.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence, TypeVar

from stack.ai.context_window import window_estimate

__all__ = ["Fit", "fit"]

_Entry = TypeVar("_Entry")


@dataclass(frozen=True)
class Fit:
    """The sources a prompt keeps, and how many of the oldest it left out.

        Fit(kept=[deed, camping], left_out=0)    # everything fits
    """

    kept: list
    left_out: int


def fit(entries: Sequence[_Entry], prompt_for: Callable[[Sequence[_Entry]], str], *,
        window: int | None, answer_tokens: int) -> Fit:
    """The newest `entries` whose prompt fits `window` with `answer_tokens` to spare.

    `entries` are in filed order, oldest first; `prompt_for(entries)` is
    the whole prompt made from them. With `window` None (the server does
    not say) or a prompt that fits, every entry is kept, the same list.
    Otherwise the fewest oldest entries are left out; when none fits,
    `kept` is empty. `prompt_for` is called about log2(len) times.

    >>> sources = ["a" * 30, "b" * 30, "c" * 30]
    >>> fit(sources, "".join, window=25, answer_tokens=5)
    Fit(kept=['bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', 'cccccccccccccccccccccccccccccc'], left_out=1)
    >>> fit(sources, "".join, window=None, answer_tokens=5).left_out
    0
    """
    budget = None if window is None else window - answer_tokens
    if budget is None or window_estimate(prompt_for(entries)) <= budget:
        return Fit(list(entries), 0)
    # The prompt shrinks as entries are left out, so the fewest to leave
    # out is found by bisection: `low` never fits, `high` always does.
    low, high = 0, len(entries)
    while high - low > 1:
        middle = (low + high) // 2
        if window_estimate(prompt_for(entries[middle:])) <= budget:
            high = middle
        else:
            low = middle
    return Fit(list(entries[high:]), high)
