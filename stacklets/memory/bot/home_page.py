"""The wiki's home page: a Markdown template, the model reads the sources.

The home page is the family's front door: who lives here, who is
around them, and the household facts worth
finding at a glance (the home, the cars, the insurance). It is filled
from `templates/home.<lang>.md` (see `page_template`).

- **The compiler** lists everyone the person files declare, each linked
  to their page, in the order the model gives (oldest first) and never
  without someone: nobody is dropped, nobody appears twice.
- **The model** answers in JSON: the family's name and address, a short
  welcome, the key household facts, one line per person, the pets, the
  relatives and the friends around
  them, and the household facts it found, each with the sources it
  cites as numbers. The compiler formats them, so the page has one
  style whatever the model writes.

Pure functions: no I/O beyond reading the template, no model calls.
The wiki compiler (`wiki._generate_home`) owns the evidence, the call
and the publish.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from page_template import LANGUAGE_NAMES, cites, fact, fill, language, sentence, template, text

FACT_SECTIONS = ("insurance", "subscriptions", "memberships")

# What a newcomer takes in before scrolling: the home, the cars, the
# doctor, the school. Past that it is a list, not a glance.
GLANCE_MAX = 6

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
    facts: dict[str, list[Item]] = field(default_factory=dict)


class AnswerError(ValueError):
    """The model's answer is not the JSON object the prompt asked for."""


def read_answer(raw: str, *, ids: list[str]) -> Answer:
    """The model's JSON answer, or `AnswerError`.

    A field of the wrong shape is dropped rather than guessed at, and a
    person the roster does not know is ignored.
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
        facts={s: _items(data.get(s)) for s in FACT_SECTIONS},
    )


def _items(value, *, name: str = "name", detail: str = "detail") -> list[Item]:
    if not isinstance(value, list):
        return []
    items = [Item(text(v.get(name)), text(v.get(detail)), cites(v.get("cite")))
             for v in value if isinstance(v, dict)]
    return [i for i in items if i.name]


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
        "relatives": "\n".join(i.render() for i in answer.relatives),
        "friends": "\n".join(i.render() for i in answer.friends),
        "helpers": "\n".join(i.render() for i in answer.helpers),
        **{s: "\n".join(i.render() for i in answer.facts.get(s, [])) for s in FACT_SECTIONS},
    }
    return fill(template("home", lang), slots)


# ── The prompt ───────────────────────────────────────────────────────────

def prompt(*, evidence: str, ids: list[str], names: dict[str, str], lang: str,
           aliases: dict[str, list[str]] | None = None) -> str:
    """The instructions for the home page, asking for a JSON object.

    `aliases` are the other names the family uses for a person. Without
    them a model reads "Margaret" on a birth certificate as a stranger,
    and lists Maggie's twin sister among the relatives.
    """
    aliases = aliases or {}

    def known_as(pid: str) -> str:
        other = aliases.get(pid) or []
        return f" (also called {', '.join(other)})" if other else ""

    roster = "\n".join(f"- {pid}: {names.get(pid) or '(full name from the sources)'}{known_as(pid)}"
                       for pid in ids)
    return f"""You write the home page of a family's private wiki. It is the first page the family sees when they open it: who they are, who is around them, and the household facts they want to find at a glance. The wiki is private and runs on the family's own server, so full names and addresses are fine.

The family's people, by id. Each has their own page; write one line for each:
{roster}

What the family has filed. Each source is numbered; cite sources by their numbers:

{evidence}

Write in {LANGUAGE_NAMES[language(lang)]}. The welcome and the lines about people are warm and personal, the way a family member would introduce them to a visitor. The household facts are plain and exact.

The welcome is about the family, never about the wiki, the pages or the sources.

Answer with one JSON object and nothing else:
{{
  "family": "the family's name as a heading: \\"The Simpsons\\" in English, \\"Familie Simpson\\" in German. Use the surname most of the people above share.",
  "address": "where the family lives now: the street address if a source gives one, else the town",
  "welcome": "two or three warm sentences about the family as it is now, with citations like [3]",
  "glance": [{{"label": "one or two words in {LANGUAGE_NAMES[language(lang)]}: Home, Car, Doctor, School", "value": "the fact, short and exact: owned or rented and the lender, make and model, name", "cite": [2]}}],
  "order": ["every id above, oldest first: parents before their children, older children before younger ones"],
  "people": {{"<id>": {{"name": "the full name, only for a person listed without one", "line": "who they are, warmly and specifically, in under fifteen words: their place in the family and what they are like or busy with. No citation."}}}},
  "pets": [{{"name": "the pet's name", "detail": "what animal, and whose", "cite": [4]}}],
  "relatives": [{{"name": "full name", "detail": "how they are related to ONE of the family's people, in a few words, like \"Homer's father\"", "cite": [1]}}],
  "friends": [{{"name": "full name", "detail": "who they are to the family, in a few words", "cite": [5]}}],
  "helpers": [{{"name": "name", "detail": "what they do for the family: family doctor, Bart's teacher, the school, the corner shop", "cite": [9]}}],
  "insurance": [{{"name": "the kind of insurance and the insurer, like \"Car insurance, Springfield Mutual\"", "detail": "what it covers, for whom, cost if known", "cite": [7]}}],
  "subscriptions": [{{"name": "service", "detail": "cadence and amount if known, for whom", "cite": [7]}}],
  "memberships": [{{"name": "club or association", "detail": "what for, who is a member", "cite": [7]}}]
}}

Rules:
- Use only what the sources say. Never invent. Leave out a field, or give an empty list, when the sources have nothing for it. No placeholder text.
- Never write about the sources themselves: no "the sources do not say", "not specified", "no records". What is unknown is simply not on the page.
- "relatives" are family by blood or marriage who are not among the people above: parents, grandparents, siblings and half-siblings, aunts, uncles, cousins, in-laws. Friends, neighbours, classmates and colleagues go in "friends", at most {FRIENDS_MAX}, the closest first. Doctors, teachers, schools, shops, tradespeople and others who serve the family go in "helpers".
- The family's people above go by every name listed for them. Nobody from that list appears in "relatives", "friends" or "helpers", under any of their names.
- "glance" holds the household facts someone new to the family wants first, at most {GLANCE_MAX}: the home (owned or rented, the mortgage lender), each car, the family doctor, the children's school. Pets go in "pets", not here.
- Each person or place appears in one section only: a shop owner who is a friend is a friend, not also a helper.
- No account numbers, policy numbers or IDs; the documents carry those.
"""
