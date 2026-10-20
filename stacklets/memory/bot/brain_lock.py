"""One writer at a time in the brain working copy.

The curator compiles views without the model while a compile with the
model runs in another process, an admin can compile a page by hand in the
bot-runner, and the curator commits the brain. Each of them takes this
lock around what it reads and then writes, so no view is written by two
of them at once and no commit picks up a page half written.

    with brain_lock.held(brain):
        ...read the cards, write the view...

An exclusive `flock` on a file in the brain's `.git` directory (beside it
when the brain is no git working copy), so it holds across processes and
across containers that mount the same brain. Within one process it can be
taken again: `_publish` takes it for each write, and a whole compile
without the model holds it around its writes. Stdlib only.
"""

from __future__ import annotations

import fcntl
from contextlib import contextmanager
from pathlib import Path

LOCK_NAME = "views.lock"

# How deep this process already holds the lock: a nested `held` neither
# locks nor unlocks again.
_depth = 0


def _lock_file(brain: Path) -> Path:
    git = brain / ".git"
    return git / LOCK_NAME if git.is_dir() else brain / f".{LOCK_NAME}"


@contextmanager
def held(brain: Path):
    """Hold the brain's writer lock for the block; waits while another holds it."""
    global _depth
    if _depth:
        _depth += 1
        try:
            yield
        finally:
            _depth -= 1
        return
    brain = Path(brain)
    brain.mkdir(parents=True, exist_ok=True)
    with open(_lock_file(brain), "a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _depth = 1
        try:
            yield
        finally:
            _depth = 0
            fcntl.flock(lock, fcntl.LOCK_UN)
