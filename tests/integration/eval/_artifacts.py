"""Per-run artifact writer for the pipeline eval.

Every eval run gets its own timestamped subdirectory under `runs/`,
with one folder per case carrying the OCR text the classifier saw,
the resolved classification, the expected ground truth, and the
scorecard. Diffing two runs is a `diff -ru runs/<a> runs/<b>` away —
useful when iterating on prompts or comparing models.

Lives at module level (not under tests/) so an `import _artifacts`
from a different consumer (a CLI report generator, future analysis
notebook) doesn't drag pytest's `conftest.py` along.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_RUNS_DIR = Path(__file__).parent / "runs"


def _slug(s: str) -> str:
    """Filesystem-safe model name for the run dir."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-") or "model"


@dataclass
class RunArtifacts:
    """One run = one directory. Cases hang off it.

    Fields are populated as the eval progresses; `finalize()` writes
    the aggregate summary at session end.
    """
    root: Path
    model: str
    started_at: str
    cases: list[dict] = field(default_factory=list)

    @classmethod
    def open(cls, *, model: str) -> "RunArtifacts":
        now = dt.datetime.now()
        stamp = now.strftime("%Y-%m-%d_%H-%M-%S")
        root = _RUNS_DIR / f"{stamp}_{_slug(model)}"
        root.mkdir(parents=True, exist_ok=True)
        return cls(root=root, model=model, started_at=now.isoformat(timespec="seconds"))

    # ── Per-case writes ────────────────────────────────────────────────

    def write_case(self, *,
                   name: str,
                   doc_id: int,
                   ocr_text: str,
                   actual: dict[str, Any],
                   expected: dict[str, Any],
                   scorecard_text: str,
                   passed: int, total: int,
                   raw_classification: dict | None = None,
                   ) -> Path:
        """Dump everything the eval saw + produced for one case.

        Returns the case directory so the caller can print it for the
        user. Writes are atomic-ish (write_text is non-atomic but each
        file is independently overwritable on rerun).
        """
        case_dir = self.root / name
        case_dir.mkdir(exist_ok=True)

        (case_dir / "ocr.txt").write_text(ocr_text or "")
        (case_dir / "actual.json").write_text(_json(actual))
        (case_dir / "expected.json").write_text(_json(expected))
        (case_dir / "scorecard.txt").write_text(scorecard_text + "\n")
        if raw_classification is not None:
            (case_dir / "classification.json").write_text(_json(raw_classification))

        self.cases.append({
            "name": name,
            "doc_id": doc_id,
            "passed": passed,
            "total": total,
            "score_pct": round(100.0 * passed / total, 1) if total else 0.0,
        })
        return case_dir

    # ── Session-end aggregate ──────────────────────────────────────────

    def finalize(self) -> Path:
        """Write `summary.json` + `summary.txt`. Returns the run root."""
        total_passed = sum(c["passed"] for c in self.cases)
        total_assertions = sum(c["total"] for c in self.cases)
        overall_pct = (
            round(100.0 * total_passed / total_assertions, 1)
            if total_assertions else 0.0
        )
        summary = {
            "model": self.model,
            "started_at": self.started_at,
            "finished_at": dt.datetime.now().isoformat(timespec="seconds"),
            "cases_scored": len(self.cases),
            "field_assertions_passed": total_passed,
            "field_assertions_total": total_assertions,
            "overall_score_pct": overall_pct,
            "cases": self.cases,
        }
        (self.root / "summary.json").write_text(_json(summary))

        lines = [
            f"Eval run: {self.root.name}",
            f"  Model:   {self.model}",
            f"  Started: {self.started_at}",
            f"  Score:   {total_passed}/{total_assertions} ({overall_pct:.0f}%)  "
            f"across {len(self.cases)} case(s)",
            "",
            "  Cases:",
        ]
        for c in self.cases:
            lines.append(
                f"    {c['name']:<30} {c['passed']}/{c['total']} "
                f"({c['score_pct']:.0f}%)  doc#{c['doc_id']}"
            )
        (self.root / "summary.txt").write_text("\n".join(lines) + "\n")
        return self.root


def _json(obj: Any) -> str:
    """Pretty JSON with deterministic key order — friendly to `diff`."""
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True, default=str)
