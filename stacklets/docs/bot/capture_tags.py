"""Capture-tag cache — the system's growing capture vocabulary.

When the user captures a Reddit thread and the LLM tags it `["LLMs",
"Local Inference"]`, those tags become candidates for the next
capture's prompt. The LLM sees "LLMs" already exists and reuses it
instead of producing "llm" or "Large Language Models" as a parallel
variant.

The cache is a flat JSON file in the bot's data dir, loaded once at
startup, updated after each `publish_capture`. Rendering for the
prompt is "top N by frequency" — recency tracked but not used for
ranking yet; the dream-cycle wiki rebuild is the place to add time-
decay if tag drift becomes a real problem.

Casing is preserved as the classifier emitted it. Normalization
("llm" → "LLMs", "AI/ML" → "AI") is explicitly out of scope here —
that's a rebuild-time concern where you can see the whole corpus
and make principled decisions.
"""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger


class CaptureTagCache:
    """Persistent dictionary of tag → (count, last_used).

    Single-writer: only the archivist bot updates this file. No
    locking. Failure modes (missing file, corrupt JSON, unwritable
    directory) degrade to "empty cache" so a capture never fails
    because of cache trouble — degraded prompt, working capture.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._tags: dict[str, dict] = {}

    # ── Load / save ──────────────────────────────────────────────────

    def load(self) -> None:
        """Read the JSON file into memory. Missing or corrupt → empty."""
        if not self.path.exists():
            self._tags = {}
            return
        try:
            data = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(
                "[capture-tags] Bad cache at {} ({}), starting empty",
                self.path, e,
            )
            self._tags = {}
            return

        raw = data.get("tags", {}) if isinstance(data, dict) else {}
        if not isinstance(raw, dict):
            self._tags = {}
            return

        clean: dict[str, dict] = {}
        for name, info in raw.items():
            if not isinstance(name, str) or not name.strip():
                continue
            if isinstance(info, dict):
                count = int(info.get("count", 0))
                last_used = info.get("last_used") or ""
                clean[name] = {"count": count, "last_used": last_used}
        self._tags = clean

    def save(self) -> None:
        """Atomic-ish write: tmp file + rename. Best-effort — log on
        failure but don't raise; a capture shouldn't fail because the
        cache can't be persisted."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"tags": self._tags}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
            tmp.replace(self.path)
        except OSError as e:
            logger.warning("[capture-tags] Save failed: {}", e)

    # ── Record + query ───────────────────────────────────────────────

    def record(self, tags, when: str) -> None:
        """Bump counts for a capture's tags.

        Within a single record() call, duplicates are deduplicated
        before incrementing — the LLM occasionally emits the same tag
        twice in one response, and that should count as one capture's
        worth of usage, not two.

        Blank / non-string entries are silently dropped (the LLM
        sometimes returns nulls or numbers when the JSON shape drifts).
        """
        seen: set[str] = set()
        for raw in tags or []:
            if not isinstance(raw, str):
                continue
            name = raw.strip()
            if not name or name in seen:
                continue
            seen.add(name)
            entry = self._tags.setdefault(name, {"count": 0, "last_used": ""})
            entry["count"] += 1
            if when and when > entry["last_used"]:
                entry["last_used"] = when

    def top(self, n: int) -> list[str]:
        """Return up to N tags, most frequent first."""
        return [name for name, _ in self._sorted_tags()[:n]]

    def _sorted_tags(self) -> list[tuple[str, int]]:
        """All tags as `(name, count)`, sorted by count desc, then name."""
        return sorted(
            ((name, info.get("count", 0)) for name, info in self._tags.items()),
            key=lambda kv: (-kv[1], kv[0]),
        )
