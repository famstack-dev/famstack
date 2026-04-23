"""Per-model capability cache — vision support, etc.

Probing a new model costs one HTTP round-trip; caching the answer to
disk means the bot doesn't re-probe on every restart and doesn't pay
the cost on every classify call. Keyed by model name so swapping or
upgrading a model is a clean slate, not a stale-cache footgun.

File format: `{"<model>": {"vision": true, "probed_at": "..."}}`
A missing key means "not yet probed"; the caller re-probes and writes.

Atomic writes via temp-file rename — surviving a crash mid-write is
worth the small ceremony given the file lives in the bot's data dir
that survives container restarts.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ModelCapabilities:
    """JSON-backed capability cache.

    `path=None` makes the cache in-memory only — useful for tests and
    one-shot CLI invocations that don't want to leak state to disk.
    """
    path: Path | None = None
    _cache: dict[str, dict] = field(default_factory=dict, init=False)
    _loaded: bool = field(default=False, init=False)

    # ── Persistence ──────────────────────────────────────────────────

    def _load(self) -> None:
        """Lazy-load on first read. Bad JSON → start empty, log nothing
        — the cache is rebuilt on next probe, the user doesn't need to
        know."""
        if self._loaded:
            return
        if self.path and self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                if isinstance(data, dict):
                    self._cache = data
            except (json.JSONDecodeError, OSError):
                self._cache = {}
        self._loaded = True

    def _save(self) -> None:
        """Atomic write so a crash mid-flush doesn't leave a half-file."""
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._cache, indent=2, sort_keys=True))
        tmp.replace(self.path)

    # ── Vision capability ────────────────────────────────────────────

    def supports_vision(self, model: str) -> bool | None:
        """`None` when not yet probed; `True` / `False` when cached.

        The tri-state matters — callers branch on "we don't know" vs
        "we know it doesn't" differently (probe vs skip).
        """
        self._load()
        entry = self._cache.get(model)
        if not entry or "vision" not in entry:
            return None
        return bool(entry["vision"])

    def record_vision(self, model: str, supported: bool) -> None:
        """Record a probe outcome and persist immediately.

        Persisting on every write trades a tiny disk hit for crash
        safety — without it, a crash between probe and persist would
        force a re-probe on next start, and probes that hit a model
        that isn't loaded can stall for seconds.
        """
        self._load()
        entry = self._cache.setdefault(model, {})
        entry["vision"] = bool(supported)
        entry["probed_at"] = (
            dt.datetime.now(dt.timezone.utc)
            .replace(microsecond=0).isoformat().replace("+00:00", "Z")
        )
        self._save()
