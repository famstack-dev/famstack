"""The protocol of a driver run, in the e2e tests' Given / When / Then form.

Each action and what came of it is one timestamped line on stderr, so a run
reads as a story while stdout stays the command's answer for scripts:

    [20:10:13.402] WHEN       marge says in picnic: 'Archivist, where do we meet?'
    [20:10:14.017]   .        sent $5O23YtaZHKj8...
    [20:10:14.018] THEN       archivist answers in picnic
    [20:10:22.431]   ✓        after 8.4s: 💡 Antwort: Wir treffen uns am Nordeingang ...

With DRIVER_LOG set, the lines are also appended to that file, so the driver
calls of one scenario leave one protocol behind.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "e2e"))

from bdd import BDDLog  # noqa: E402


class Narrator(BDDLog):
    """The e2e tests' BDD log, also written to DRIVER_LOG when that is set.

    One narrator per driver command; each command tells what it does and what
    came back, and nothing else prints to stderr.

        log = Narrator()
        log.when("marge says in picnic: 'hi'")
        log.detail("sent $abc")
    """

    def _emit(self, verb: str, msg: str, indent: bool = False) -> None:
        super()._emit(verb, msg, indent)
        target = os.environ.get("DRIVER_LOG")
        if target:
            with open(target, "a", encoding="utf-8") as protocol:
                protocol.write(self.steps[-1] + "\n")

    def failed(self, msg: str) -> None:
        self._emit("\u2717", msg, indent=True)


def first_line(text: str, width: int = 100, lines: int = 2) -> str:
    """A message on one protocol line: its first non-empty lines, shortened."""
    kept = [line.strip() for line in text.splitlines() if line.strip()][:lines]
    return " | ".join(kept)[:width]
