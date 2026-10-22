"""How a correction is worked into a note's clean version, asked of a real model.

A note typed in a person's own words is filed as a clean version of
them (`clean_words`). When someone corrects it, in a reply or with
`stack memory reprocess --msg`, the note is refiled from its words, and
its clean version is written first by a prompt of its own
(`note_correction`) from the words and the corrections. It should read
as the note would have read had it been written right the first time:
the corrected point changes where it stands, and only something new adds
a point. The failure this measures is the model keeping the old point and
appending the correction as a point of its own.

Each case is a note, one or two corrections, and deterministic checks
on the clean version the model answers:

- every corrected value is in the clean version, in the same list item
  or sentence as a word of the point it corrects (or, for something new,
  in a list item of its own), and only once: a second mention is the
  correction restated;
- every old value is gone;
- a list has as many items as the corrected note should;
- nothing else is lost or added, and every value a correction gives is
  there, by the guard the pipeline files with
  (`clean_words.correction_problems`).

The prompt is built by the real code: `Classifier.correct_note` with the
note's words and the corrections, the call the refile makes
(`CapturePipeline._corrected`). It measures that step alone; the capture
prompt that reads its answer is not asked. Opt-in and report-only, like
the rest of `tests/e2e/eval/`: it is not a pytest module and nothing
collects it.

    AI_DEFAULT_MODEL=<model> EVAL_AI_URL=<base url> EVAL_AI_KEY=<key> \\
        uv run --extra test python tests/e2e/eval/correction_fold.py [--runs 3] [-k trip]

Cost: one model call per case and run, 8 cases x 3 runs = 24 calls,
about 4 seconds each on a 35B-A3B model (a cold model takes a minute
for the first). The classifier pins temperature 0, so the runs of one
case usually agree; more than one run catches a server that does not.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
for _path in (_ROOT / "lib", _ROOT / "stacklets" / "docs" / "bot"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import httpx  # noqa: E402
from openai import AsyncOpenAI  # noqa: E402

import clean_words  # noqa: E402
from pipeline import LLM, Classifier  # noqa: E402

# ── Cases ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Fold:
    """A corrected value and where it belongs: in the same list item or
    sentence as `anchor`, a word of the point it corrects. No anchor means
    something new, which belongs in a list item of its own."""

    value: str
    anchor: str | None = None


@dataclass(frozen=True)
class Case:
    name: str
    words: str
    corrections: tuple[str, ...]
    folds: tuple[Fold, ...]
    gone: tuple[str, ...] = ()
    items: int | None = None
    language: str = "en"

    @property
    def hint(self) -> str:
        """The corrections as the reprocess path passes them (`corrections_of`)."""
        return "\n".join(self.corrections)


CASES = (
    Case(
        "a changed number",
        "lisas bday party sat:\ncake from the bakery\n12 kids invited\nballoons\n"
        "ask Patty if she can help",
        ("Marge: 15 kids now, not 12",),
        folds=(Fold("15", "kids"),), gone=("12",), items=4,
    ),
    Case(
        "a changed name",
        "bowling night thursday: lenny brings the pizza, i bring the beer, "
        "we meet at the lanes at 8",
        ("Homer: carl brings the pizza, not lenny",),
        folds=(Fold("Carl", "pizza"),), gone=("Lenny",),
    ),
    Case(
        "a changed weekday and date",
        "this week: bart dentist tue the 14th 3pm, bring the insurance card. "
        "lisa sax lesson thu. trash out fri",
        ("Marge: bart's dentist is wednesday the 15th, not tuesday the 14th",),
        folds=(Fold("Wednesday", "dentist"), Fold("15", "dentist")),
        gone=("Tuesday", "Tue", "14"),
    ),
    Case(
        "something new added",
        "packing for the lake:\nsunscreen\ntowels\nthe big cooler\nmaggie's floaties",
        ("Homer: and the fishing rods",),
        folds=(Fold("fishing rods"),), gone=("Homer",), items=5,
    ),
    Case(
        "two corrections in a row",
        "garage sale saturday 9 to 1. ned lends us 2 tables. "
        "prices: books 1 dollar, lamps 5",
        ("Marge: it starts at 8, not 9", "Homer: lamps are 10"),
        folds=(Fold("8", "sale"), Fold("10", "lamps")), gone=("9", "5"),
    ),
    Case(
        "one correction contradicts, one adds",
        "school bake sale friday:\nmarge bakes brownies\nlisa makes lemonade\n"
        "we need 30 cups",
        ("Lisa: it's 50 cups", "Marge: bart helps at the stand too"),
        folds=(Fold("50", "cups"), Fold("Bart")), gone=("30",), items=4,
    ),
    # Typed by the family in German, the household's language here. The
    # prompt stays English; only the note is German.
    Case(
        "prose, in German",
        "heute mit dem Klempner telefoniert, er kommt montag früh wegen der "
        "spüle in der küche, kostet ca 120 euro und wir müssen vorher den "
        "schrank darunter ausräumen",
        ("Marge: er kommt erst dienstag, nicht montag",),
        folds=(Fold("Dienstag", "kommt"),), gone=("Montag",), language="de",
    ),
    Case(
        "the trip note",
        "for the trip:\nhomer needs to call the hotel about the late check-in\n"
        "bart forgot his swimsuit again, buy one at the mall before saturday\n"
        "lisa wants the museum visit on sunday, not saturday\nbudget max 200",
        ("Marge: budget is 250 now, and lisa's museum is the science museum",),
        folds=(Fold("250", "budget"), Fold("science museum", "Sunday")),
        gone=("200",), items=4,
    ),
)


# ── Checks (pure) ────────────────────────────────────────────────────────

_ITEM = re.compile(r"^\s*[-*]\s+")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def units(clean: str) -> list[str]:
    """The clean version's list items and sentences, the places a point stands."""
    found: list[str] = []
    for line in clean.splitlines():
        if not line.strip():
            continue
        if _ITEM.match(line):
            found.append(line.strip())
        else:
            found.extend(s for s in _SENTENCE_END.split(line.strip()) if s)
    return found


def mentions(text: str, token: str) -> bool:
    """Whether `token` stands in `text` as a word or phrase of its own, in any case."""
    edge, tail = (r"(?<!\d)", r"(?!\d)") if token.isdigit() else (r"(?<!\w)", r"(?!\w)")
    return re.search(edge + re.escape(token) + tail, text, re.IGNORECASE) is not None


def failures(case: Case, clean: str) -> list[str]:
    """Why `clean` is not the note written right the first time; [] when it is."""
    if not clean.strip():
        return ["no clean version"]
    found: list[str] = []
    parts = units(clean)
    for fold in case.folds:
        holding = [u for u in parts if mentions(u, fold.value)]
        if not holding:
            found.append(f"missing {fold.value!r}")
            continue
        if len(holding) > 1:
            found.append(f"{fold.value!r} said {len(holding)} times")
        if fold.anchor is None:
            if not any(_ITEM.match(u) for u in holding):
                found.append(f"{fold.value!r} is not an item of its own")
        elif not any(mentions(u, fold.anchor) for u in holding):
            found.append(f"{fold.value!r} not where {fold.anchor!r} is")
    found += [f"still says {old!r}" for old in case.gone if mentions(clean, old)]
    if case.items is not None:
        count = sum(1 for line in clean.splitlines() if _ITEM.match(line))
        if count != case.items:
            found.append(f"{count} items, want {case.items}")
    found += [f"guard: {p}" for p in
              clean_words.correction_problems(case.words, clean, corrections=case.hint)]
    return found


# ── Running ──────────────────────────────────────────────────────────────


@dataclass
class Result:
    case: Case
    cleans: list[str] = field(default_factory=list)
    reasons: list[list[str]] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.reasons if not r)


@dataclass
class Endpoint:
    """The model server, and how many requests the run sent it."""

    url: str
    key: str
    model: str
    requests: int = 0

    def classifier(self, language: str) -> Classifier:
        async def count(_request) -> None:
            self.requests += 1

        client = AsyncOpenAI(
            base_url=self.url.rstrip("/"), api_key=self.key or "not-needed",
            max_retries=1, timeout=300.0,
            http_client=httpx.AsyncClient(event_hooks={"request": [count]}, timeout=300.0),
        )
        return Classifier(LLM(client, namespace="archivist-bot"), language=language)


async def clean_version(endpoint: Endpoint, case: Case) -> str:
    """The clean version the refile would file, before the guard."""
    answer = await endpoint.classifier(case.language).correct_note(
        words=case.words, corrections=case.hint)
    return str(answer.get("clean") or "")


async def measure(endpoint: Endpoint, cases=CASES, runs: int = 3) -> list[Result]:
    """Run every case `runs` times, one after the other, and check each answer."""
    import stack.ai.models as models

    models._DEFAULT_MODEL, models._MODELS = endpoint.model, {}
    results = []
    for case in cases:
        result = Result(case)
        for _ in range(runs):
            try:
                clean = await clean_version(endpoint, case)
            except Exception as e:  # noqa: BLE001 -- a failed call is a failed run, not a crash
                clean, reasons = "", [f"call failed: {e}"]
            else:
                reasons = failures(case, clean)
            result.cleans.append(clean)
            result.reasons.append(reasons)
        results.append(result)
    return results


def table(results: list[Result], runs: int) -> str:
    width = max(len(r.case.name) for r in results)
    lines = [f"{'case':<{width}}  pass  first failure"]
    for r in results:
        failed = next((x for x in r.reasons if x), [])
        lines.append(f"{r.case.name:<{width}}  {r.passed}/{runs}   {'; '.join(failed)}")
    total = sum(r.passed for r in results)
    lines.append(f"{'total':<{width}}  {total}/{runs * len(results)}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--runs", type=int, default=3, help="runs per case (default 3)")
    parser.add_argument("-k", default="", help="only cases whose name contains this")
    parser.add_argument("--show", action="store_true", help="print every clean version")
    args = parser.parse_args()

    model = os.environ.get("AI_DEFAULT_MODEL", "").strip()
    if not model:
        print("AI_DEFAULT_MODEL is not set: name the model to measure.", file=sys.stderr)
        return 2
    endpoint = Endpoint(os.environ.get("EVAL_AI_URL", "http://localhost:42060/v1"),
                        os.environ.get("EVAL_AI_KEY", ""), model)
    cases = [c for c in CASES if args.k.casefold() in c.name.casefold()]
    results = asyncio.run(measure(endpoint, cases, args.runs))
    if args.show:
        for r in results:
            for n, (clean, reasons) in enumerate(zip(r.cleans, r.reasons), 1):
                print(f"── {r.case.name}, run {n}: {'; '.join(reasons) or 'pass'}\n{clean}\n")
    print(table(results, args.runs))
    print(f"\n{model}, {endpoint.requests} requests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
