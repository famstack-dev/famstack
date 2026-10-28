"""How good the wiki pages a real model wrote are, scored against planted facts.

The quality eval (`wiki_compile.py`) runs the wiki compile on a fabricated
family (`tests/memory/unit/fabricated_family.py`) whose first filings plant
about 25 facts, each with the home page section it belongs in. This module
reads the pages the compile wrote and answers two questions, in code:

- **Is each planted fact on the home page, in its section?** A fact is
  found when one line of its section holds every word of what it should
  say; it is `elsewhere` when that line stands in another section, and
  `missing` when no line has it. A fact that was corrected later is
  `stale` when its section still states the old value. The line's
  citations are checked too: at least one must lead to a card that holds
  the fact's words.
- **Does every citation resolve?** Every `[N]` in a page's text must have
  a row in its References block, and that row's link must lead to a card
  or page that exists.

And it puts two runs side by side (`compare`): tokens, seconds and calls
per scenario and size from each run's `calls.jsonl`, facts found and
citations resolved from its `score.json`.

Pure functions over page text: the caller says how a link is read
(`read(link) -> text | None`). The unit tests on fixed pages are in
`tests/memory/unit/test_memory_wiki_score.py`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from statistics import mean

from stacklets.memory.bot.page_template import format_span, template
from tests.memory.unit.fabricated_family import Fact

# ── The home page's sections ─────────────────────────────────────────────

# The sections where a fact is an item named by the fact's name ("Abe
# Simpson", "Stonecutters"); in the others it is the fact's value ("Duff
# Insurance", "every Thursday").
NAMED = frozenset({"relatives", "friends", "helpers", "pets", "subscriptions", "memberships"})

# The model's answer fields that the template shows under another slot's
# heading: pets are listed with the family, recurring dates after the schedule.
_SHOWN_UNDER = {"pets": "people", "recurring": "schedule"}

# The address is the blockquote under the family's name, before any heading.
ADDRESS = "address"
_PREAMBLE = ""

# Words a fact's text and the page's line may differ in without a fact
# being lost: articles, "is", the honorifics a model drops or adds.
_STOP = frozenset({"a", "an", "the", "is", "of", "and", "s", "dr", "mr", "mrs", "miss", "every"})

_WORD = re.compile(r"[0-9a-z]+")
_CITE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\](?!\()")
_REF_ROW = re.compile(r"^- \[(\d+)\] (?:\[([^\]]*)\]\(([^)]+)\)|\*\*(.*?)\*\*)", re.M)
_REFERENCES = re.compile(r"^## (?:References|Quellen)\s*$", re.M)


def headings(lang: str = "en") -> dict[str, str]:
    """The heading each answer field is shown under on the home page, read
    from the page's template so a renamed heading moves the scoring with it."""
    text = template("home", lang)
    slots = {m[2]: m[1] for m in re.finditer(r"^## (.+)\n\{\{(\w+)\}\}", text, re.M)}
    shown = {field: slots[slot] for field, slot in _SHOWN_UNDER.items()}
    return {**slots, **shown, ADDRESS: _PREAMBLE}


def sections(page: str) -> dict[str, list[str]]:
    """The page's lines by the `## ` heading they stand under, without the
    References block; the lines before the first heading under ""."""
    body = page[:m.start()] if (m := _REFERENCES.search(page)) else page
    found: dict[str, list[str]] = {_PREAMBLE: []}
    current = _PREAMBLE
    for line in body.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            found.setdefault(current, [])
        elif line.strip():
            found[current].append(line)
    return found


def words(text: str) -> set[str]:
    return set(_WORD.findall(text.casefold())) - _STOP


def holds(line: str, needle: str) -> bool:
    """Whether `line` states every word of `needle`."""
    wanted = words(needle)
    return bool(wanted) and wanted <= words(line)


def cited(line: str) -> list[int]:
    """The citation numbers on `line`: `[3]` and `[3, 5]`, not a `[link](…)`."""
    return [int(n) for m in _CITE.finditer(line) for n in m[1].split(",")]


def references(page: str) -> dict[int, str]:
    """The References block's rows: number to link, "" for a row without one."""
    m = _REFERENCES.search(page)
    if not m:
        return {}
    return {int(r[1]): r[3] or "" for r in _REF_ROW.finditer(page[m.end():])}


# ── Facts ────────────────────────────────────────────────────────────────

@dataclass
class FactScore:
    """One planted fact on one page.

    `status` is `found`, `elsewhere` or `missing`; `where` the heading of the
    line that states it; `cited` whether a citation on that line leads to a
    card holding the fact (None where the page shows no citation, as for
    the address); `stale` whether its section still states the old value
    of a corrected fact.
    """

    name: str
    section: str
    status: str
    where: str = ""
    line: str = ""
    cited: bool | None = None
    stale: bool = False


def needle(fact: Fact, lang: str = "en") -> str:
    """What the page's line must say for `fact`: the name of a named item,
    else the value; a date as the schedule prints it."""
    if fact.section in NAMED:
        return fact.name
    if fact.section == "schedule":
        return format_span(fact.value, "", lang)
    return fact.value


def score_fact(fact: Fact, page: str, read: Callable[[str], str | None], *,
               lang: str = "en") -> FactScore:
    """Where `page` states `fact`, and whether its citation leads to it."""
    by_heading = sections(page)
    home = headings(lang).get(fact.section, fact.section)
    text = needle(fact, lang)
    lines = [(home, ln) for ln in by_heading.get(home, [])]
    lines += [(h, ln) for h, lns in by_heading.items() if h != home for ln in lns]
    where, line = next(((h, ln) for h, ln in lines if holds(ln, text)), ("", ""))
    status = "missing" if not line else "found" if where == home else "elsewhere"
    stale = bool(fact.was) and any(holds(ln, fact.was) and words(ln) & words(fact.name)
                                   for ln in by_heading.get(home, []))
    return FactScore(fact.name, fact.section, status, where, line.strip(),
                     _cited(line, (text, fact.value), references(page), read), stale)


def _cited(line: str, says: tuple[str, ...], rows: dict[int, str],
           read: Callable[[str], str | None]) -> bool | None:
    """Whether a citation on `line` leads to a card that holds the fact in
    any of its forms (`says`: as the page prints it, as the card states
    it); None for no line, or a line the page shows no citation on."""
    numbers = cited(line)
    if not line or not numbers:
        return None if not line or line.startswith(">") else False
    cards = [read(rows[n]) or "" for n in numbers if rows.get(n)]
    return any(holds(card, text) for card in cards for text in says)


# ── Citations ────────────────────────────────────────────────────────────

@dataclass
class Citations:
    """A page's citations: how many there are, how many resolve, and the
    numbers of those that do not."""

    total: int = 0
    resolved: int = 0
    unresolved: list[str] = field(default_factory=list)

    def add(self, other: "Citations") -> None:
        self.total += other.total
        self.resolved += other.resolved
        self.unresolved += other.unresolved


def score_citations(page: str, read: Callable[[str], str | None], *, name: str = "") -> Citations:
    """Every `[N]` in `page`'s text, each resolved when the References block
    has its row and the row's link leads to something `read` finds."""
    body = page[:m.start()] if (m := _REFERENCES.search(page)) else page
    rows = references(page)
    result = Citations()
    for n in (n for line in body.splitlines() for n in cited(line)):
        result.total += 1
        if rows.get(n) and read(rows[n]) is not None:
            result.resolved += 1
        else:
            result.unresolved.append(f"{name}[{n}]" if name else f"[{n}]")
    return result


# ── One scenario ─────────────────────────────────────────────────────────

def score(home: str, pages: dict[str, str], facts: Iterable[Fact],
          read: Callable[[str], str | None], *, lang: str = "en") -> dict:
    """The score of one scenario: every fact on the `home` page, and the
    citations of every page in `pages` (by path), as `score.json` holds it."""
    found = [score_fact(f, home, read, lang=lang) for f in facts]
    citations = Citations()
    for path, text in sorted(pages.items()):
        citations.add(score_citations(text, read, name=path))
    return {
        "facts_found": sum(f.status == "found" for f in found),
        "facts_total": len(found),
        "facts_cited": sum(bool(f.cited) for f in found if f.status == "found"),
        "facts_stale": sum(f.stale for f in found),
        "citations_total": citations.total,
        "citations_resolved": citations.resolved,
        "unresolved": citations.unresolved,
        "facts": [asdict(f) for f in found],
    }


# ── Two runs side by side ────────────────────────────────────────────────

@dataclass
class Cell:
    """One measure of one scenario at one size, over a run's repeats."""

    values: list[float]

    def text(self) -> str:
        if not self.values:
            return "-"
        lo, hi, avg = min(self.values), max(self.values), mean(self.values)
        return _num(avg) if lo == hi else f"{_num(avg)} ({_num(lo)}–{_num(hi)})"

    @property
    def mean(self) -> float | None:
        return mean(self.values) if self.values else None


MEASURES = ("prompt tokens", "completion tokens", "seconds", "calls", "failed calls",
            "facts found", "facts cited", "citations resolved")


def measures(calls: list[dict], scores: dict) -> dict[tuple[int, str], dict[str, Cell]]:
    """Each measure per `(size, scenario)` of one run directory, a value per repeat.

    `calls` are the lines of `calls.jsonl`; `scores` is `score.json`'s
    `sizes`: size to repeat to scenario to its score. Prompt tokens are the
    server's count, the estimate where the server sent none.
    """
    table: dict[tuple[int, str], dict[str, Cell]] = {}
    for size, repeats in scores.items():
        for repeat, scenarios in repeats.items():
            for scenario, s in scenarios.items():
                mine = [c for c in calls if str(c["size"]) == str(size)
                        and str(c["repeat"]) == str(repeat) and c["scenario"] == scenario]
                row = table.setdefault((int(size), scenario), {m: Cell([]) for m in MEASURES})
                values = {
                    "prompt tokens": sum(_prompt_tokens(c) for c in mine),
                    "completion tokens": sum(c.get("completion_tokens") or 0 for c in mine),
                    "seconds": round(sum(c.get("seconds") or 0 for c in mine), 1),
                    "calls": len(mine),
                    "failed calls": sum(not c.get("ok", True) for c in mine),
                    "facts found": s.get("facts_found", 0),
                    "facts cited": s.get("facts_cited", 0),
                    "citations resolved": _share(s.get("citations_resolved", 0),
                                                 s.get("citations_total", 0)),
                }
                for m, v in values.items():
                    if v is not None:
                        row[m].values.append(v)
    return table


def compare(a: dict[tuple[int, str], dict[str, Cell]], b: dict[tuple[int, str], dict[str, Cell]],
            *, names: tuple[str, str] = ("a", "b"), order: Iterable[str] = ()) -> str:
    """One Markdown table per scenario and size: each measure in run `a`,
    in run `b`, and the difference of their means (b minus a). A cell with
    repeats that disagree shows the mean and the spread: `22 (21–23)`."""
    rank = {s: i for i, s in enumerate(order)}
    keys = sorted(a.keys() | b.keys(), key=lambda k: (k[0], rank.get(k[1], len(rank)), k[1]))
    empty = {m: Cell([]) for m in MEASURES}
    out = []
    for size, scenario in keys:
        left, right = a.get((size, scenario), empty), b.get((size, scenario), empty)
        out += ["", f"#### {size} filings, {scenario}", "",
                f"| measure | {names[0]} | {names[1]} | difference |", "|---|---:|---:|---:|"]
        for m in MEASURES:
            x, y = left[m].mean, right[m].mean
            diff = "-" if x is None or y is None else _signed(y - x)
            out.append(f"| {m} | {left[m].text()} | {right[m].text()} | {diff} |")
    return "\n".join(out).lstrip("\n")


def _prompt_tokens(call: dict) -> int:
    found = call.get("prompt_tokens")
    return found if isinstance(found, int) else call.get("estimated_tokens") or 0


def _share(resolved: int, total: int) -> float | None:
    """Citations resolved as a percentage; None for a scenario with none."""
    return round(100 * resolved / total, 1) if total else None


def _num(x: float) -> str:
    return f"{x:,.0f}" if abs(x) >= 100 or float(x).is_integer() else f"{x:,.1f}"


def _signed(x: float) -> str:
    return "0" if x == 0 else ("+" if x > 0 else "-") + _num(abs(x))
