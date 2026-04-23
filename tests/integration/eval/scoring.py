"""Per-field scoring + scorecard rendering for the pipeline eval.

Tolerance-based on purpose: LLM output is non-deterministic in surface
form (synonyms, casing, ordering) but deterministic in semantic intent.
Exact-match scoring would punish "Esso" vs "ESSO" or
["Vehicle", "Receipt"] vs ["Receipt", "Vehicle"] — neither is a real
quality regression.

Each matcher returns (passed, detail) where detail is what the
scorecard prints. The scorecard is render-only; no test gating happens
here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


# ── Matchers (pure functions over actual + expected) ────────────────────

def match_keywords_in(actual: str, keywords: list[str]) -> tuple[bool, str]:
    """All keywords must appear (case-insensitive substring) in `actual`.

    For fields where we care about content but not phrasing — title,
    summary, free-text. The keyword list comes from the case YAML and
    represents the minimum facts the LLM must surface.
    """
    if not actual:
        return False, "(empty)"
    haystack = actual.casefold()
    missing = [k for k in keywords if k.casefold() not in haystack]
    if missing:
        return False, f"missing: {missing}"
    return True, f"all {len(keywords)} keywords present"


def match_substring_casefold(actual: str | None, expected: str) -> tuple[bool, str]:
    """`expected` must appear as a case-insensitive substring of `actual`.

    Right shape for correspondent: ground truth is "ESSO" but the LLM
    might return "ESSO Station Stefan Sulger" — a substring hit means
    the entity was identified, even if the LLM included extra context.
    """
    if not actual:
        return False, "(none)"
    if expected.casefold() in actual.casefold():
        return True, f"matched in {actual!r}"
    return False, f"expected {expected!r}, got {actual!r}"


def match_set_jaccard(actual: list[str], expected: list[str],
                      *, threshold: float = 0.5) -> tuple[bool, str]:
    """Jaccard similarity ≥ threshold (case-insensitive).

    Good for topics: order doesn't matter, partial overlap is fine
    (LLM might add "Fuel" alongside the expected "Vehicle"). At
    threshold 0.5 a 2-item expected set passes with 1 correct + 1
    extra.
    """
    a = {x.casefold() for x in actual}
    e = {x.casefold() for x in expected}
    if not a and not e:
        return True, "(both empty)"
    if not a:
        return False, f"empty (expected {sorted(e)})"
    union = a | e
    intersect = a & e
    score = len(intersect) / len(union) if union else 0.0
    ok = score >= threshold
    return ok, f"Jaccard {score:.2f} (got {sorted(a)}, expected {sorted(e)})"


def match_set_exact_casefold(actual: list[str], expected: list[str]) -> tuple[bool, str]:
    """Exact set match (case-insensitive). Right for `persons`: if the
    LLM tags Bart on Homer's invoice that's a real bug, not noise."""
    a = {x.casefold() for x in actual}
    e = {x.casefold() for x in expected}
    if a == e:
        return True, f"{sorted(a) or '(empty)'}"
    return False, f"got {sorted(a)}, expected {sorted(e)}"


def match_in_set_casefold(actual: str | None, allowed: list[str]) -> tuple[bool, str]:
    """`actual` must equal one of `allowed` (case-insensitive).

    Document type is a small closed set per case (e.g. ["Receipt",
    "Invoice"]) — being precise about which one matters.
    """
    if not actual:
        return False, f"(none) — expected one of {allowed}"
    a = actual.casefold()
    if any(a == x.casefold() for x in allowed):
        return True, actual
    return False, f"{actual!r} ∉ {allowed}"


def match_date_exact(actual: str | None, expected: str) -> tuple[bool, str]:
    """Date as YYYY-MM-DD. Ground truth is unambiguous so we go strict —
    fuzzy date matching tends to hide real OCR-driven errors."""
    if not actual:
        return False, f"(none) — expected {expected}"
    actual_short = actual[:10]
    if actual_short == expected:
        return True, actual_short
    return False, f"got {actual_short!r}, expected {expected!r}"


# ── Scorecard ───────────────────────────────────────────────────────────

@dataclass
class Row:
    field: str
    passed: bool
    detail: str


@dataclass
class Scorecard:
    case_name: str
    rows: list[Row] = field(default_factory=list)

    def add(self, field_name: str, result: tuple[bool, str]) -> None:
        self.rows.append(Row(field_name, result[0], result[1]))

    @property
    def total(self) -> int:
        return len(self.rows)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.rows if r.passed)

    def render(self) -> str:
        if not self.rows:
            return f"{self.case_name}: no fields scored"
        width = max(len(r.field) for r in self.rows)
        lines = [f"\n─ {self.case_name} " + "─" * max(0, 60 - len(self.case_name))]
        for r in self.rows:
            mark = "✓" if r.passed else "✗"
            lines.append(f"  {mark} {r.field:<{width}}  {r.detail}")
        pct = 100.0 * self.passed / self.total
        lines.append(f"  → {self.passed}/{self.total} ({pct:.0f}%)")
        return "\n".join(lines)


# ── Driver: turn an (actual, expected) pair into a scorecard ────────────

def score_classification(case_name: str,
                         actual: dict, expected: dict) -> Scorecard:
    """Run every applicable matcher. Skips fields the case YAML omits.

    Each `expected.<field>` is independently optional — that way a case
    file can score only the fields it has confidence in. Adding a field
    later doesn't require touching every existing case.
    """
    sc = Scorecard(case_name)

    if "title_keywords" in expected:
        sc.add("title", match_keywords_in(
            actual.get("title", "") or "",
            expected["title_keywords"],
        ))
    if "topics" in expected:
        sc.add("topics", match_set_jaccard(
            actual.get("topics", []) or [],
            expected["topics"],
        ))
    if "persons" in expected:
        sc.add("persons", match_set_exact_casefold(
            actual.get("persons", []) or [],
            expected["persons"],
        ))
    if "correspondent" in expected:
        sc.add("correspondent", match_substring_casefold(
            actual.get("correspondent"),
            expected["correspondent"],
        ))
    if "document_type" in expected:
        allowed = expected["document_type"]
        if isinstance(allowed, str):
            allowed = [allowed]
        sc.add("document_type", match_in_set_casefold(
            actual.get("document_type"), allowed,
        ))
    if "date" in expected:
        sc.add("date", match_date_exact(
            actual.get("date"), expected["date"],
        ))
    if "summary_keywords" in expected:
        sc.add("summary", match_keywords_in(
            actual.get("summary", "") or "",
            expected["summary_keywords"],
        ))
    if "facts_keywords" in expected:
        # Facts come back as a list of strings — join for keyword scan.
        facts = actual.get("facts") or []
        joined = "\n".join(str(f) for f in facts)
        sc.add("facts", match_keywords_in(joined, expected["facts_keywords"]))

    return sc
