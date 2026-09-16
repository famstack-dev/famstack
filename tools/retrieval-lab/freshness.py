#!/usr/bin/env python3
"""What it costs to find out whether the index is still current.

    uv run --extra test python tools/retrieval-lab/freshness.py

The index is a cache over a git checkout, so every search has to answer
"has anything changed" before it answers the question. There are two
ways to ask, and they scale differently:

    scan    stat every *.md and compare against the stored mtime
    head    read the checkout's git HEAD and compare against the one
            stored in the index

The scan is O(pages). The head read is O(1) and is sound only because
nothing in famstack writes into the vault working copy outside git --
`update_memory` commits to Forgejo and fast-forwards the clone, so a
file cannot change without HEAD moving.

Both columns below are real `build_index` calls on a vault where
nothing changed: one on the checkout, one on the same tree with its
`.git` hidden so the short-circuit cannot fire. The third column is
what a cold rebuild costs, for the case where the index is deleted.
Read the curve rather than the 177-page corpus, where everything is
fast enough to hide the difference.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "lib"))
sys.path.insert(0, str(REPO / "stacklets" / "memory"))

import fts_index  # noqa: E402

SIZES = (177, 1000, 3000, 8000)
REPEATS = 5


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, check=True).stdout.strip()


def build_vault(pages: int, root: Path) -> Path:
    """Render a vault of roughly `pages` files and commit it."""
    vault = root / f"vault-{pages}"
    subprocess.run(
        [sys.executable, str(HERE / "generate.py"), "--out", str(vault),
         "--noise", str(max(pages - 27, 0))],
        check=True, capture_output=True)
    git("init", "-q", cwd=vault)
    git("add", "-A", cwd=vault)
    git("-c", "user.email=lab@famstack.dev", "-c", "user.name=lab",
        "commit", "-qm", "corpus", cwd=vault)
    return vault


def best_of(fn, repeats: int = REPEATS) -> float:
    """Milliseconds for the fastest of `repeats` runs.

    Fastest rather than mean: this is measuring a floor cost that a
    search pays, and the slow runs are the machine doing something
    else, not the code doing more work.
    """
    timings = []
    for _ in range(repeats):
        started = time.perf_counter()
        fn()
        timings.append((time.perf_counter() - started) * 1000)
    return min(timings)


def main() -> None:
    root = HERE / "out" / "freshness"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)

    print(f"{'pages':>6}  {'head gate (ms)':>15}  {'scan (ms)':>10}  "
          f"{'cold rebuild (ms)':>18}")
    for pages in SIZES:
        vault = build_vault(pages, root)
        db = root / f"index-{pages}.sqlite3"

        cold_ms = best_of(lambda: fts_index.build_index(vault, db), repeats=1)
        gated_ms = best_of(lambda: fts_index.build_index(vault, db))

        # Same tree, no checkout to ask: the fallback path, and what
        # every search would cost without the gate.
        dot_git = vault / ".git"
        dot_git.rename(vault.parent / f"git-{pages}")
        try:
            scan_ms = best_of(lambda: fts_index.build_index(vault, db))
        finally:
            (vault.parent / f"git-{pages}").rename(dot_git)

        total = fts_index.build_index(vault, db).total
        print(f"{total:>6}  {gated_ms:>15.1f}  {scan_ms:>10.1f}  "
              f"{cold_ms:>18.0f}")


if __name__ == "__main__":
    main()
