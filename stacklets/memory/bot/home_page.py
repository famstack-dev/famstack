"""The wiki's home page: a Markdown template, the model reads the sources.

The home page is the family's front door: who lives here, what they
have been up to, who is around them, and the household facts worth
finding at a glance (the home, the cars, the insurance). It is filled
from `templates/home.<lang>.md` (see `page_template`).

- **The compiler** lists everyone the person files declare, each linked
  to their page, in the order the model gives (oldest first) and never
  without someone: nobody is dropped, nobody appears twice. It also
  picks the filings of the last month the "lately" paragraph is written
  from, so the model cannot retell an old story as news.
- **The model** answers in JSON: the family's name and address, a short
  welcome, the key household facts, one line per person, the pets, a
  paragraph on the last month, the relatives and the friends around
  them, and the household facts it found, each with the sources it
  cites as numbers. The compiler formats them, so the page has one
  style whatever the model writes.

Pure functions: no I/O beyond reading the template and the prompt
(`templates/prompts/`), no model calls.
The wiki compiler (`wiki._generate_home`) owns the evidence, the call
and the publish.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import prompt_template
from .page_template import (
    LANGUAGE_NAMES, cites, fact, fill, format_span, language, sentence, template, text,
)

FACT_SECTIONS = ("insurance", "subscriptions", "memberships")

# What a newcomer takes in before scrolling: the home, the cars, the
# doctor, the school. Past that it is a list, not a glance.
GLANCE_MAX = 6

# "Lately" is the last month, and only what the family did or wrote
# down: a diary entry or a note, not a bill that arrived.
LATELY_DAYS = 31
LATELY_MAX = 8

# The schedule looks one season ahead: far enough to plan, near enough
# that every line still matters this week.
SCHEDULE_DAYS = 90
SCHEDULE_MAX = 8
RECURRING_MAX = 6

_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# The friends a reader takes in at a glance. Past that, the list stops
# being the people closest to the family.
FRIENDS_MAX = 8

_TRAILING_CITES = re.compile(r"\s*\[\d+(?:\s*,\s*\d+)*\]\s*\.?\s*$")


@dataclass
class Item:
    """One line of a list section: a name, what it is, the sources."""

    name: str
    detail: str = ""
    cites: str = ""

    def __post_init__(self) -> None:
        # The model often cites inside the text as well as in `cite`.
        # The page shows the citation once, at the end.
        if found := _TRAILING_CITES.search(self.detail):
            self.cites = self.cites or " " + found.group().strip().rstrip(".")
            self.detail = self.detail[:found.start()].rstrip()

    def render(self) -> str:
        detail = f" · {sentence(self.detail)}" if self.detail else ""
        return f"- **{self.name}**{detail}{self.cites}"


@dataclass
class Event:
    """One date on the schedule: a day, or a span of days."""

    start: str
    end: str
    what: str
    cites: str = ""

    def render(self, lang: str) -> str:
        return fact(format_span(self.start, self.end, lang), self.what, self.cites)


@dataclass
class Answer:
    """What the model read from the sources; every field may be empty."""

    family: str = ""
    address: str = ""
    welcome: str = ""
    people: dict[str, Item] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    relatives: list[Item] = field(default_factory=list)
    friends: list[Item] = field(default_factory=list)
    helpers: list[Item] = field(default_factory=list)
    glance: list[Item] = field(default_factory=list)
    pets: list[Item] = field(default_factory=list)
    lately: str = ""
    schedule: list[Event] = field(default_factory=list)
    recurring: list[Item] = field(default_factory=list)
    facts: dict[str, list[Item]] = field(default_factory=dict)


class AnswerError(ValueError):
    """The model's answer is not the JSON object the prompt asked for."""


def read_answer(raw: str, *, ids: list[str], recent: list[int], today: date) -> Answer:
    """The model's JSON answer, or `AnswerError`.

    A field of the wrong shape is dropped rather than guessed at, and a
    person the roster does not know is ignored. Without `recent` sources
    there is no "lately", whatever the model wrote. The schedule keeps
    only real dates from `today` to `SCHEDULE_DAYS` ahead, soonest first.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise AnswerError(f"not JSON: {e}") from e
    if not isinstance(data, dict):
        raise AnswerError("not a JSON object")

    people: dict[str, Item] = {}
    raw_people = data.get("people")
    for pid, value in (raw_people.items() if isinstance(raw_people, dict) else []):
        if pid in ids and isinstance(value, dict):
            # No citation: the line links to the person's own page, where
            # every fact is cited. A model citing the whole family to one
            # source was the most common wrong citation on this page.
            people[pid] = Item(text(value.get("name")), text(value.get("line")))
    order = data.get("order")
    return Answer(
        family=text(data.get("family")),
        address=text(data.get("address")),
        welcome=text(data.get("welcome")),
        people=people,
        order=[p for p in order if p in ids] if isinstance(order, list) else [],
        relatives=_items(data.get("relatives")),
        friends=_items(data.get("friends"))[:FRIENDS_MAX],
        helpers=_items(data.get("helpers")),
        glance=_items(data.get("glance"), name="label", detail="value")[:GLANCE_MAX],
        pets=_items(data.get("pets")),
        lately=text(data.get("lately")) if recent else "",
        schedule=_schedule(data.get("schedule"), today),
        recurring=_items(data.get("recurring"), name="when", detail="what")[:RECURRING_MAX],
        facts={s: _items(data.get(s)) for s in FACT_SECTIONS},
    )


def _items(value, *, name: str = "name", detail: str = "detail") -> list[Item]:
    if not isinstance(value, list):
        return []
    items = [Item(text(v.get(name)), text(v.get(detail)), cites(v.get("cite")))
             for v in value if isinstance(v, dict)]
    return [i for i in items if i.name]


def _schedule(value, today: date) -> list[Event]:
    first, last = today.isoformat(), (today + timedelta(days=SCHEDULE_DAYS)).isoformat()
    events = []
    for v in value if isinstance(value, list) else []:
        if not isinstance(v, dict):
            continue
        start, end, what = text(v.get("date")), text(v.get("end")), text(v.get("what"))
        end = end if _ISO_DAY.match(end) and end > start else ""
        if _ISO_DAY.match(start) and what and first <= (end or start) and start <= last:
            events.append(Event(start, end, what, cites(v.get("cite"))))
    return sorted(events, key=lambda e: e.start)[:SCHEDULE_MAX]


def recent(entries: list[dict], today: date) -> list[int]:
    """The evidence numbers of the last month's diary entries and notes, newest first."""
    since = (today - timedelta(days=LATELY_DAYS)).isoformat()
    found = [(e.get("date") or "", n) for n, e in enumerate(entries, start=1)
             if e.get("type") != "document" and since <= (e.get("date") or "") <= today.isoformat()]
    return [n for _, n in sorted(found, reverse=True)][:LATELY_MAX]


# ── The page ─────────────────────────────────────────────────────────────

def render(answer: Answer, *, ids: list[str], names: dict[str, str], lang: str) -> str:
    """The home page body. `names` holds the declared full names by id."""
    order = answer.order + [p for p in ids if p not in answer.order]
    people = []
    for pid in order:
        item = answer.people.get(pid, Item(""))
        name = names.get(pid) or item.name or pid.capitalize()
        line = f" · {sentence(item.detail)}" if item.detail else ""
        people.append(f"- **[{name}]({pid}/about)**{line}")
    people += [pet.render() for pet in answer.pets]
    slots = {
        "family": answer.family,
        "address": answer.address,
        "welcome": answer.welcome,
        "glance": "\n".join(fact(i.name, i.detail, i.cites) for i in answer.glance),
        "people": "\n".join(people),
        "lately": answer.lately,
        "schedule": "\n".join([e.render(lang) for e in answer.schedule]
                              + [fact(i.name, i.detail, i.cites) for i in answer.recurring]),
        "relatives": "\n".join(i.render() for i in answer.relatives),
        "friends": "\n".join(i.render() for i in answer.friends),
        "helpers": "\n".join(i.render() for i in answer.helpers),
        **{s: "\n".join(i.render() for i in answer.facts.get(s, [])) for s in FACT_SECTIONS},
    }
    return fill(template("home", lang), slots)


# ── The prompt ───────────────────────────────────────────────────────────

def prompt(*, evidence: str, ids: list[str], names: dict[str, str], lang: str,
           recent: list[int], today: date, aliases: dict[str, list[str]] | None = None) -> str:
    """The instructions for the home page, asking for a JSON object (`templates/prompts/home.md`).

    `today` lets the model tell what is still ahead for the schedule.
    `recent` are the sources of the last month (see `recent`); without
    them the model is not asked for a "lately" paragraph. `aliases` are
    the other names the family uses for a person. Without them a model
    reads "Margaret" on a birth certificate as a stranger, and lists
    Maggie's twin sister among the relatives.
    """
    aliases = aliases or {}

    def known_as(pid: str) -> str:
        other = aliases.get(pid) or []
        return f" (also called {', '.join(other)})" if other else ""

    roster = "\n".join(f"- {pid}: {names.get(pid) or '(full name from the sources)'}{known_as(pid)}"
                       for pid in ids)
    return prompt_template.prompt("home", {
        "today": today.isoformat(),
        "roster": roster,
        "evidence": evidence,
        "language": LANGUAGE_NAMES[language(lang)],
        "lately": ", ".join(map(str, recent)),
        "glance_max": str(GLANCE_MAX),
        "friends_max": str(FRIENDS_MAX),
        "recurring_max": str(RECURRING_MAX),
    })
