"""BDD-style logger for integration tests.

Tests narrate Given / When / Then as they execute. Each step prints a
timestamped line, and assertion failures include the surrounding steps
so the protocol tells the story:

    [12:04:01.123] SCENARIO  Homer uploads an invoice to #documents
    [12:04:01.123] GIVEN     archivist-bot is online
    [12:04:01.345] GIVEN     OpenAI mock stubbed for classify + reformat
    [12:04:01.580] WHEN      Homer uploads invoice.pdf (1.2 KB)
    [12:04:02.120]   .       room event sent: $abc123
    [12:04:12.345] THEN      Paperless has a document titled 'ADAC - Kfz…'
    [12:04:12.500]   ✓       id=17, tags=['Insurance', 'Person: Homer']

Use `-s` with pytest to stream this live. On failure, the last N lines
give the full context — no reading between log entries and source.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field


@dataclass
class BDDLog:
    """Narrate a test as it runs. Each method emits one timestamped line.

    Verbs: scenario (title), given (precondition), when (action),
    then (expected), detail (substep, printed indented).
    """
    steps: list[str] = field(default_factory=list)
    _t0: float = field(default_factory=time.monotonic)

    def _emit(self, verb: str, msg: str, indent: bool = False) -> None:
        ts = time.strftime("%H:%M:%S") + f".{int((time.time() % 1) * 1000):03d}"
        prefix = f"  {verb:<6}" if indent else f"{verb:<10}"
        line = f"[{ts}] {prefix} {msg}"
        self.steps.append(line)
        print(line, file=sys.stderr, flush=True)

    def scenario(self, msg: str) -> None: self._emit("SCENARIO", msg)
    def given(self, msg: str) -> None:    self._emit("GIVEN", msg)
    def when(self, msg: str) -> None:     self._emit("WHEN", msg)
    def then(self, msg: str) -> None:     self._emit("THEN", msg)
    def and_(self, msg: str) -> None:     self._emit("AND", msg)
    def detail(self, msg: str) -> None:   self._emit(".", msg, indent=True)
    def ok(self, msg: str) -> None:       self._emit("✓", msg, indent=True)
