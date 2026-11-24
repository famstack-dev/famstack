"""How a correction is worked into a note's clean version, asked of a real model.

A note typed in a person's own words is filed as a clean version of
them (`clean_words`). When someone corrects it, in a reply or with
`stack memory reprocess --msg`, it is asked as the card conversation
(`stack.filing.correction.conversation`, ADR-018 §7): the words, the card as filed, each
correction, and the model answers what they change. Its clean version
should read as the note would have read had it been written right the
first time: the corrected point changes where it stands, and only
something new adds a point. The failure this measures is the model
keeping the old point and appending the correction as a point of its own.

Each case is a note, one or two corrections, and deterministic checks
on the clean version the model answers:

- every corrected value is in the clean version, in the same list item
  or sentence as a word of the point it corrects (or, for something new,
  in a list item of its own), and only once: a second mention is the
  correction restated;
- every old value is gone;
- a list has as many items as the corrected note should;
- nothing else is lost or added, by the check the pipeline files with
  (`clean_words.rebuild_problems`).

The prompt is built by the real code: `build_conversation` and
`Classifier.correct_card`, the call a refile makes for a corrected note,
from the clean version the capture prompt files the note with
(`clean_words.starting_point`). The field cases correct a card's title,
tags and people, and are checked as the model answered them and after
`stack.filing.correction.changes.named_changes`. `--shape whole` asks for the whole
card instead of the changes, the comparison that chose the changes.
`--list ''` asks each note as one in a topic that keeps a list, as a chat
refile there does, and prints the to-dos the answers add.
Opt-in and report-only, like the rest of `tests/e2e/eval/`: it is
not a pytest module and nothing collects it.

    AI_DEFAULT_MODEL=<model> EVAL_AI_URL=<base url> EVAL_AI_KEY=<key> \\
        uv run --extra test python tests/e2e/eval/correction_fold.py [--runs 3] [-k trip] \\
        [--shape whole] [--list '']

Cost: one model call per case and run, plus one per text case to file
it: 8 text and 4 field cases x 3 runs + 8 = 44 calls,
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
if str(_ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(_ROOT / "lib"))

from stack.stacklet_code import import_stacklets  # noqa: E402

import_stacklets([_ROOT / "stacklets"])

import httpx  # noqa: E402
from openai import AsyncOpenAI  # noqa: E402

from stack.filing import clean_words  # noqa: E402
from stack.filing.correction.conversation import build_conversation  # noqa: E402
from stack.filing.correction.changes import named_changes  # noqa: E402
from stack.people import Person  # noqa: E402
from stacklets.docs.bot.pipeline import LLM, Classifier  # noqa: E402

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
        """The corrections as the model reads them, each after who wrote it."""
        return "\n".join(self.corrections)

    @property
    def said(self) -> str:
        """The corrections as the check reads them, without who wrote them
        (`stack.cards.events.text_of`)."""
        return "\n".join(c.split(": ", 1)[1] for c in self.corrections)


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



@dataclass(frozen=True)
class FieldCase:
    """A card as filed, its corrections, and the fields it should have after them."""

    name: str
    kind: str
    original: str
    filed: dict
    corrections: tuple[str, ...]
    want: dict

    @property
    def said(self) -> str:
        return "\n".join(c.split(": ", 1)[1] for c in self.corrections)


SIMPSONS = [Person("homer", "Homer Simpson"), Person("marge", "Marge Simpson"),
            Person("bart", "Bart Simpson"), Person("lisa", "Lisa Simpson")]

RECEIPT = ("Texxon Tankstelle Springfield\n12.11.2026 14:32\nSuper E10 32,4 l x 1,378 EUR\n"
           "Summe 44,64 EUR\nAutowäsche Premium 8,00 EUR\nGesamt 52,64 EUR\nKarte VISA ****4411")

FIELD_CASES = (
    FieldCase(
        "a receipt: quoted title and one wrong tag", "document", RECEIPT,
        {"title": "Texxon Tankquittung November", "tags": ["Fahrzeug", "Einkauf"],
         "persons": ["Homer"], "date": "2026-11-12"},
        ('Homer: Der Titel soll "Tankquittung Camper van März" heißen, und das Tag Einkauf '
         'ist falsch, das gehört zu Reise.',),
        {"title": "Tankquittung Camper van März", "tags": ["Fahrzeug", "Reise"],
         "persons": ["Homer"], "date": "2026-11-12"},
    ),
    FieldCase(
        "a note: a person missing", "note", "grillen samstag bei flanders, wir bringen salat",
        {"title": "Grillen bei Flanders", "tags": ["Freunde"], "persons": ["Marge"],
         "date": "2026-11-21", "clean": "Grillen am Samstag bei Flanders. Wir bringen Salat."},
        ("Marge: Homer kommt auch mit",),
        {"title": "Grillen bei Flanders", "tags": ["Freunde"], "persons": ["Marge", "Homer"],
         "date": "2026-11-21"},
    ),
    FieldCase(
        "a note: a fact, no field", "note", "dentist for bart tue 3pm, bring insurance card",
        {"title": "Bart's dentist", "tags": ["health"], "persons": ["Bart"],
         "date": "2026-11-17", "clean": "Bart's dentist on Tuesday at 3 pm. Bring the insurance card."},
        ("Marge: and his retainer too",),
        {"title": "Bart's dentist", "tags": ["health"], "persons": ["Bart"], "date": "2026-11-17"},
    ),
    FieldCase(
        "a note: title said plainly, two points", "note",
        "camping list: tent, sleeping bags, lantern",
        {"title": "Camping list", "tags": ["camping"], "persons": ["Homer"],
         "date": "2026-11-20", "clean": "Camping list:\n\n- Tent\n- Sleeping bags\n- Lantern"},
        ("Lisa: the title should be Packing for the lake, and the tag camping is wrong, it is travel",),
        {"title": "Packing for the lake", "tags": ["travel"], "persons": ["Homer"],
         "date": "2026-11-20"},
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
              clean_words.rebuild_problems(case.words, clean, events=case.said)]
    return found


# ── Running ──────────────────────────────────────────────────────────────


@dataclass
class Result:
    case: Case
    cleans: list[str] = field(default_factory=list)
    reasons: list[list[str]] = field(default_factory=list)
    todos: list[list] = field(default_factory=list)

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


# The answer shape measured against the changes-only one: the whole card,
# every field and the text, as a new filing would answer it.
WHOLE_SHAPE = ('{"title": "the title", "tags": ["..."], "persons": ["..."], "date": "YYYY-MM-DD"'
               ', "clean": "the whole text as it reads now"}')
WHOLE_REQUEST = "Answer the whole card as it reads now, every key, as a JSON object."


def whole(messages: list[dict]) -> list[dict]:
    """The conversation asking for the whole card instead of the changes."""
    task = messages[0]["content"]
    head = task[: task.index("Answer with ONLY")]
    head = head.replace("Answer what the events change.", "Answer the card as it reads after them.")
    head = head.replace("Everything an event does not name stays as the card says it: do not answer it.",
                        "Everything an event does not name stays as the card says it.")
    shape = WHOLE_SHAPE if "clean" in messages[2]["content"] else WHOLE_SHAPE.replace(
        ', "clean": "the whole text as it reads now"', "")
    return [{"role": "system", "content": head + "Answer with ONLY a JSON object:\n" + shape},
            *messages[1:-1], {"role": "user", "content": WHOLE_REQUEST}]


async def filed(endpoint: Endpoint, case: Case) -> dict:
    """The note as it was filed, which a correction starts from: its fields and clean version."""
    answer = await endpoint.classifier(case.language).classify_capture(
        text=case.words, person_names=["Homer", "Marge", "Bart", "Lisa"], clean_words=True)
    return {"title": answer.get("title") or "", "tags": answer.get("tags") or [],
            "persons": answer.get("persons") or [], "date": "2026-11-20",
            "clean": clean_words.starting_point(case.words, str(answer.get("clean") or ""))}


async def asked(endpoint: Endpoint, *, kind: str, original: str, card: dict,
                corrections: tuple[str, ...], shape: str, language: str,
                current_list: str | None = None) -> dict:
    """The model's answer to the card conversation, in `shape` (changes or
    whole); with ``current_list``, a note in a topic that keeps a list."""
    messages = build_conversation(writer="Marge", kind=kind, original=original, filed=card,
                                  events=list(corrections), current_list=current_list)
    if shape == "whole":
        messages = whole(messages)
    return await endpoint.classifier(language).correct_card(
        messages, text="clean" in card, todos=current_list is not None)


def raw_fields(card: dict, answer: dict) -> dict:
    """The fields as the model answered them, applied without the check."""
    out = {}
    for key in ("title", "date"):
        out[key] = answer.get(key) or card.get(key)
    for key in ("tags", "persons"):
        value, have = answer.get(key), list(card.get(key) or [])
        if isinstance(value, list):
            have = [str(v) for v in value]
        elif isinstance(value, dict):
            drop = {str(v).casefold() for v in value.get("remove") or []}
            have = [v for v in have if v.casefold() not in drop] + [
                str(v) for v in value.get("add") or [] if str(v).casefold() not in
                {h.casefold() for h in have}]
        out[key] = have
    return out


def field_failures(want: dict, got: dict) -> list[str]:
    found = []
    for key, value in want.items():
        g = got.get(key)
        same = (sorted(x.casefold() for x in g or []) == sorted(x.casefold() for x in value)
                if isinstance(value, list) else str(g or "") == str(value))
        if not same:
            found.append(f"{key} {g!r}, want {value!r}")
    return found


async def measure(endpoint: Endpoint, cases=CASES, runs: int = 3, shape: str = "changes",
                  field_cases=FIELD_CASES, current_list: str | None = None,
                  ) -> tuple[list[Result], list[Result], list[Result]]:
    """Run every case `runs` times and check each answer: the text cases'
    clean versions, the field cases' fields as answered and after the check.
    With ``current_list`` each note is asked as one in a topic that keeps
    that list, and the to-dos each answer adds are kept (`Result.todos`)."""
    import stack.ai.models as models

    models._DEFAULT_MODEL, models._MODELS = endpoint.model, {}
    texts, raws, checked = [], [], []
    for case in cases:
        result = Result(case)
        card = await filed(endpoint, case)  # filed once: not what is measured
        for _ in range(runs):
            try:
                answer = await asked(endpoint, kind="note", original=case.words, card=card,
                                     corrections=case.corrections, shape=shape,
                                     language=case.language, current_list=current_list)
                result.todos.append(list(answer.get("action_items") or []))
            except Exception as e:  # noqa: BLE001 -- a failed call is a failed run, not a crash
                clean, reasons = "", [f"call failed: {e}"]
            else:
                clean = str(answer.get("clean") or "")
                reasons = failures(case, clean)
                fields = named_changes(card, answer, said=case.said, people=SIMPSONS).fields
                reasons += [f"field: {r}" for r in field_failures(
                    {k: card[k] for k in ("title", "tags", "persons")}, fields)]
            result.cleans.append(clean)
            result.reasons.append(reasons)
        texts.append(result)
    for fcase in field_cases:
        raw, check = Result(fcase), Result(fcase)  # type: ignore[arg-type]
        for _ in range(runs):
            try:
                answer = await asked(endpoint, kind=fcase.kind, original=fcase.original,
                                     card=fcase.filed, corrections=fcase.corrections,
                                     shape=shape, language="de")
            except Exception as e:  # noqa: BLE001
                answer = {"_error": str(e)}
            raw.cleans.append(str(answer))
            raw.reasons.append(field_failures(fcase.want, raw_fields(fcase.filed, answer)))
            check.cleans.append(str(answer))
            check.reasons.append(field_failures(fcase.want, named_changes(
                fcase.filed, answer, said=fcase.said, people=SIMPSONS).fields))
        raws.append(raw)
        checked.append(check)
    return texts, raws, checked


def table(results: list[Result], runs: int) -> str:
    width = max(len(r.case.name) for r in results)
    lines = [f"{'case':<{width}}  pass  first failure"]
    for r in results:
        failed = next((x for x in r.reasons if x), [])
        lines.append(f"{r.case.name:<{width}}  {r.passed}/{runs}   {'; '.join(failed)}")
    total = sum(r.passed for r in results)
    lines.append(f"{'total':<{width}}  {total}/{runs * len(results)}")
    # A text case also names the fields that moved; a correction that names
    # a person or a tag moves one on purpose, so the text alone is counted too.
    text = sum(1 for r in results for x in r.reasons
               if not [y for y in x if not y.startswith("field: ")])
    if text != total:
        lines.append(f"{'text alone':<{width}}  {text}/{runs * len(results)}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--runs", type=int, default=3, help="runs per case (default 3)")
    parser.add_argument("-k", default="", help="only cases whose name contains this")
    parser.add_argument("--show", action="store_true", help="print every clean version")
    parser.add_argument("--shape", choices=("changes", "whole"), default="changes",
                        help="ask for the changes only (default) or the whole card")
    parser.add_argument("--list", default=None, metavar="TEXT",
                        help="ask each note as one in a topic that keeps this list ('' for an empty one)")
    args = parser.parse_args()

    model = os.environ.get("AI_DEFAULT_MODEL", "").strip()
    if not model:
        print("AI_DEFAULT_MODEL is not set: name the model to measure.", file=sys.stderr)
        return 2
    endpoint = Endpoint(os.environ.get("EVAL_AI_URL", "http://localhost:42060/v1"),
                        os.environ.get("EVAL_AI_KEY", ""), model)
    cases = [c for c in CASES if args.k.casefold() in c.name.casefold()]
    fcases = [c for c in FIELD_CASES if args.k.casefold() in c.name.casefold()]
    texts, raws, checked = asyncio.run(measure(endpoint, cases, args.runs, args.shape, fcases,
                                               current_list=args.list))
    if args.show:
        for r in [*texts, *raws]:
            for n, (clean, reasons) in enumerate(zip(r.cleans, r.reasons), 1):
                print(f"── {r.case.name}, run {n}: {'; '.join(reasons) or 'pass'}\n{clean}\n")
    for title, results in (("text cases (clean version, fields after the check)", texts),
                           ("field cases, as the model answered", raws),
                           ("field cases, after the check", checked)):
        if results:
            print(f"\n{title}, shape {args.shape}:")
            print(table(results, args.runs))
    if args.list is not None:
        added = [(r.case.name, t) for r in texts for t in r.todos if t]
        print(f"\nto-dos answered in {len(added)} of {sum(len(r.todos) for r in texts)} runs:")
        for name, todos in added:
            print(f"  {name}: {todos}")
    print(f"\n{model}, {endpoint.requests} requests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
