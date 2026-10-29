"""A fabricated family memory of any size, the same every time it is made.

The wiki compile's prompts grow with what a family has filed, so measuring
them needs a memory of a realistic size and shape: the Simpsons with 50,
500 or 2,000 filings, made from a seed so two runs see the same cards.

    family = fabricated_family(500)
    bundle = family.history(tmp_path / "500.bundle")
    with running_memory(tmp_path, {}, history=bundle) as memory:
        ...

Every card has the shape its writer gives it (the archivist's documents,
notes and bookmarks, the diary compile's records, a person file, a topic
card, a list) and carries a summary, which is what the compile reads. The
history is one commit per filing, by the person who filed it, dated the day
it was filed, in filing order, ending the day before `TODAY`.

About 25 facts are planted in the first filings (`Family.facts`), each with
the home page section it belongs in: the address, the mortgage lender, the
car insurer, relatives, the doctor, the school, events ahead. The rest is
filler (receipts, bookmarks, notes, diary entries). The prompt measurement
(`test_memory_compile_prompts.py`) only counts on the sizes; the facts are
for the quality eval with a real model, which asks whether each one reaches
its section.
"""

from __future__ import annotations

import random
import subprocess
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from stack.cards.links import correspondent_card, topic_card
from stack.lists import new_list
from stack.people import Person, render_person
from stack.slug import path_slug
from stacklets.memory.bot import diary_card

# The day the family's history ends: the last filing is the day before.
TODAY = date(2026, 10, 1)
# The one seed: the same size gives the same memory.
SEED = 742
SHARED = "family"
ROOM = "!memories:simpson"

PEOPLE = {
    "homer": "Homer Simpson",
    "marge": "Marge Simpson",
    "bart": "Bart Simpson",
    "lisa": "Lisa Simpson",
    "maggie": "Maggie Simpson",
}
# The people who file: Maggie has no chat account.
FILERS = ["homer", "marge", "bart", "lisa"]
# Topic folders, as `(bucket, slug)`; a topic card in each.
TOPICS = [(SHARED, "camping"), (SHARED, "school"), ("lisa", "saxophone"), ("homer", "bowling")]
GROCERIES = f"{SHARED}/lists/groceries.md"
GROCERY_ITEMS = ["Milk", "Duff", "Pork chops", "Coffee"]


@dataclass(frozen=True)
class Filing:
    """One card as it is filed: where, its text, by whom, on which day.

    `declared` are the cards its writer files with it in the same commit,
    as `(path, text)`: the archivist declares a correspondent's card with
    the first document from them.
    """

    path: str
    text: str
    by: str
    on: date
    kind: str
    declared: tuple[tuple[str, str], ...] = ()

    @property
    def files(self) -> dict[str, str]:
        """Every file the filing's commit writes, by path."""
        return {self.path: self.text, **dict(self.declared)}


@dataclass(frozen=True)
class Fact:
    """A fact planted in the memory, and where the home page should show it.

    `section` is the home page answer's field (`address`, `glance`,
    `insurance`, `relatives`, `schedule`, ...); `value` what it should say;
    `card` the path of the card that states it. A fact corrected later
    names the card that corrects it (`corrected_by`) and the value it
    replaces (`was`).
    """

    section: str
    name: str
    value: str
    card: str
    corrected_by: str = ""
    was: str = ""


@dataclass
class Family:
    """The fabricated memory: the seed, the filings in order, the planted facts."""

    size: int
    seed: list[Filing]
    filings: list[Filing]
    facts: list[Fact]
    _rng: random.Random = field(repr=False)
    # The correspondents a card has been declared for, so far.
    _known: set[str] = field(default_factory=set, repr=False)
    _made: int = 0

    def __post_init__(self) -> None:
        self.filings = [self._declared(f) for f in self.filings]

    def history(self, bundle: Path) -> Path:
        """The memory's history as a git bundle at `bundle`: the seed, then
        one commit per filing. Made with `git fast-import`, because a git
        command per commit takes seconds at 2,000 filings."""
        repo = bundle.with_suffix(".git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(repo)], check=True)
        stream = [_commit(1, "homer", "Seed the Simpsons' memory", self.seed[0].on,
                          {f.path: f.text for f in self.seed})]
        stream += [_commit(n, f.by, f"File {f.path.rsplit('/', 1)[-1]}", f.on, f.files)
                   for n, f in enumerate(self.filings, start=2)]
        subprocess.run(["git", "-C", str(repo), "fast-import", "--quiet"],
                       input=b"".join(stream), check=True)
        subprocess.run(["git", "-C", str(repo), "bundle", "create", "-q", str(bundle), "main"],
                       check=True, capture_output=True)
        return bundle

    # ── Filings after the history ──

    def document(self, on: date) -> Filing:
        """A new document filed `on` that day."""
        return self._next(_document, on)

    def diary_entry(self, on: date) -> Filing:
        """A new diary record of `on` that day."""
        return self._next(_diary, on)

    def burst(self, count: int, on: date) -> list[Filing]:
        """`count` filings of every kind on one day, as after a holiday."""
        return [self._next(self._rng.choice(_FILLER), on) for _ in range(count)]

    def corrected(self, filing: Filing, on: date) -> Filing:
        """`filing` as its filer corrects it `on` that day: the summary says
        something else. A card without a summary has nothing to correct."""
        if _SUMMARY not in filing.text:
            raise ValueError(f"{filing.path} has no summary to correct")
        text = filing.text.replace(_SUMMARY, f"{_SUMMARY}Corrected: ", 1)
        return replace(filing, text=text, on=on, declared=())

    def _next(self, make, on: date) -> Filing:
        self._made += 1
        return self._declared(make(self._rng, self.size + self._made, on))

    def _declared(self, filing: Filing) -> Filing:
        """`filing` with the correspondent card its writer files with it, for
        a correspondent no earlier filing named."""
        name = next((line.split(": ", 1)[1] for line in filing.text.splitlines()
                     if line.startswith("correspondent: ")), "")
        if not name or name in self._known:
            return filing
        self._known.add(name)
        return replace(filing, declared=(correspondent_card(name, bucket=SHARED),))


def fabricated_family(size: int) -> Family:
    """The Simpsons' memory with `size` filings, the same for the same size.

    Every planted fact and the correction of one are in it at any size from
    `smallest()` up; the rest is filler. Filings are numbered once, in the
    order they are made, so no two cards share an id or a path.
    """
    if size < smallest():
        raise ValueError(f"a fabricated family has at least {smallest()} filings")
    rng = random.Random(SEED)
    start = TODAY - timedelta(days=max(60, size // 2))
    planted, facts = _planted(start)
    # The correction of a planted fact comes halfway through the history.
    correction, fact = _correction(facts, len(planted) + 1, start + (TODAY - start) / 2)
    facts = [fact if f.name == fact.name else f for f in facts]
    first = len(planted) + 2
    days = sorted(start + timedelta(days=rng.randrange((TODAY - start).days - 10) + 10)
                  for _ in range(size - first + 1))
    filler = [rng.choice(_FILLER)(rng, n, on) for n, on in enumerate(days, start=first)]
    filings = sorted([*planted, correction, *filler], key=lambda f: f.on)
    return Family(size, _seed(start), filings, facts, rng)


def smallest() -> int:
    """The fewest filings a fabricated family has: the planted facts and the correction."""
    return len(_planted(TODAY)[0]) + 1


# ── The seed: people, topics, a list ──────────────────────────────────────

def _seed(on: date) -> list[Filing]:
    cards = [Filing(f"{SHARED}/people/{pid}.md",
                    render_person(Person(pid, name, account=pid if pid in FILERS else "")),
                    "homer", on, "person")
             for pid, name in PEOPLE.items()]
    cards += [Filing(*topic_card(bucket, topic), "homer", on, "topic")
              for bucket, topic in TOPICS]
    groceries = new_list("Groceries", path=GROCERIES, topics=["camping"])
    groceries += "\n" + "".join(f"- [ ] {item}\n" for item in GROCERY_ITEMS)
    return [*cards, Filing(GROCERIES, groceries, "marge", on, "list")]


# ── Card shapes, as their writers produce them ───────────────────────────

# Where a record's summary starts, as the archivist writes it.
_SUMMARY = "> [!summary]\n> "


def _record(kind: str, path: str, card_id: str, title: str, on: date, summary: str, *,
            by: str, persons: list[str], extra: str = "", body: str = "") -> Filing:
    """A record the archivist files: frontmatter, title, the summary callout."""
    people = "".join(f"\n  - {p}" for p in persons)
    text = (f"---\ntype: {kind}\ncard_id: {card_id}\ntitle: {title}\ndate: \"{on}\"\n"
            + (f"persons:{people}\n" if persons else "") + f"filed_by: {by}\n" + extra
            + f"timestamp: \"{on}T10:00:00Z\"\n---\n\n# {title}\n\n> [!summary]\n> {summary}\n"
            + (f"\n{body}\n" if body else ""))
    return Filing(path, text, by, on, kind)


def _doc(n: int, on: date, title: str, summary: str, *, correspondent: str,
         document_type: str, by: str = "marge", persons: list[str] | None = None) -> Filing:
    path = f"{SHARED}/documents/{on:%Y}/{on:%m}/{path_slug(title)}-p{n}.md"
    extra = (f"correspondent: {correspondent}\ndocument_type: {document_type}\n"
             f"paperless_id: {n}\nsource: paperless\n")
    return _record("document", path, f"document-{n:010x}", title, on, summary,
                   by=by, persons=persons or [by], extra=extra)


def _note(n: int, on: date, title: str, summary: str, *, by: str, bucket: str = SHARED,
          topic: str = "", persons: list[str] | None = None) -> Filing:
    where = f"{bucket}/{topic}/notes" if topic else f"{bucket}/notes"
    path = f"{where}/{on:%Y}/{on:%m}/{path_slug(title)}-{n:04x}.md"
    return _record("note", path, f"note-{n:010x}", title, on, summary, by=by,
                   persons=persons or [by], body=summary)


def _bookmark(n: int, on: date, title: str, summary: str, *, by: str, bucket: str = SHARED,
              topic: str = "") -> Filing:
    where = f"{bucket}/{topic}/bookmarks" if topic else f"{bucket}/bookmarks"
    path = f"{where}/{on:%Y}/{on:%m}/{path_slug(title)}-{n:04x}.md"
    extra = f"resource: \"https://example.org/{path_slug(title)}\"\n"
    return _record("bookmark", path, f"bookmark-{n:010x}", title, on, summary, by=by,
                   persons=[by], extra=extra)


def _diary_card(n: int, on: date, title: str, body: str, *, by: str,
                persons: list[str]) -> Filing:
    """A diary record as the diary compile writes it: `persons` are person
    ids, and the card is signed, so the compile owns it."""
    entry = f"{n:010x}"
    card = diary_card.Card(
        entry_id=entry, on=on, confidence="sent", kind="text", sender=by,
        at=int(datetime(on.year, on.month, on.day, 19, tzinfo=timezone.utc).timestamp() * 1000),
        event_ids=[f"$e{entry}"], resource=f"https://matrix.to/#/{ROOM}/$e{entry}", body=body,
        title=title, persons=persons, tags=[], summary=f"{title}.", card_id=f"diary-{entry}")
    return Filing(diary_card.card_path(card, bucket=SHARED),
                  diary_card.render(card), by, on, "diary")


# ── Filler ────────────────────────────────────────────────────────────────

_SHOPS = ["Kwik-E-Mart", "Springfield Hardware", "Lard Lad Donuts", "Krusty Burger",
          "Springfield Pharmacy", "Costington's", "Try-N-Save"]
_THINGS = ["batteries", "a garden hose", "school shoes", "a birthday cake", "paint",
           "light bulbs", "a raincoat", "printer paper", "dog food", "a frying pan"]
_WHAT = ["recipe", "article", "video", "map", "how-to", "review"]
_ABOUT = ["pancakes", "fixing a bike chain", "the Springfield museum", "stargazing",
          "bird watching", "growing tomatoes", "card games", "the science fair"]
_DAYS = ["a trip to the lake", "a rainy Sunday at home", "a visit to the zoo", "pancakes for dinner",
         "a walk in the park", "a game night", "a bake sale", "a power cut"]


def _receipt(rng: random.Random, n: int, on: date) -> Filing:
    shop, thing = rng.choice(_SHOPS), rng.choice(_THINGS)
    price = f"{rng.randrange(2, 90)}.{rng.randrange(100):02d}"
    by = rng.choice(["homer", "marge"])
    return _doc(n, on, f"Receipt from {shop}", f"{by.title()} bought {thing} at {shop} for "
                f"${price}.", correspondent=shop, document_type="Receipt", by=by)


def _document(rng: random.Random, n: int, on: date) -> Filing:
    """A letter no other card shares a title with, so its evidence can be found."""
    child = rng.choice(["bart", "lisa"])
    return _doc(n, on, f"School letter {n}", f"Springfield Elementary writes about "
                f"{child.title()}'s class trip.", correspondent="Springfield Elementary",
                document_type="Letter", persons=["marge", child])


def _where(rng: random.Random, by: str) -> tuple[str, str]:
    """Where `by` files a capture: the family's or their own folder, a shared
    topic or one of their own, never another person's private topic."""
    return rng.choice([(SHARED, ""), (by, ""),
                       *[(b, t) for b, t in TOPICS if b in (SHARED, by)]])


def _bookmark_filler(rng: random.Random, n: int, on: date) -> Filing:
    by, what, about = rng.choice(FILERS), rng.choice(_WHAT), rng.choice(_ABOUT)
    bucket, topic = _where(rng, by)
    return _bookmark(n, on, f"A {what} about {about}", f"{by.title()} kept a {what} about "
                     f"{about} for later.", by=by, bucket=bucket, topic=topic)


def _note_filler(rng: random.Random, n: int, on: date) -> Filing:
    by, about = rng.choice(FILERS), rng.choice(_ABOUT)
    bucket, topic = _where(rng, by)
    return _note(n, on, f"Trying {about}", f"{by.title()} wants to try {about} soon.",
                 by=by, bucket=bucket, topic=topic)


def _diary(rng: random.Random, n: int, on: date) -> Filing:
    by, day = rng.choice(FILERS), rng.choice(_DAYS)
    who = sorted({by, rng.choice(list(PEOPLE))})
    return _diary_card(n, on, f"{day[0].upper()}{day[1:]}", f"We had {day}.",
                       by=by, persons=who)


_FILLER = [_receipt, _receipt, _bookmark_filler, _note_filler, _note_filler, _diary]


# ── The planted facts, filed first ───────────────────────────────────────

def _planted(start: date) -> tuple[list[Filing], list[Fact]]:
    """The facts the home page should carry, each in the card that states it."""
    ahead1, ahead2 = TODAY + timedelta(days=20), TODAY + timedelta(days=45)
    plan = [
        # (section, name, value, the card stating it)
        ("address", "Home", "742 Evergreen Terrace, Springfield",
         lambda n, d: _note(n, d, "Our address", "The Simpsons live at 742 Evergreen "
                            "Terrace, Springfield.", by="marge")),
        ("glance", "Mortgage", "Springfield Savings & Loan",
         lambda n, d: _doc(n, d, "Mortgage statement", "The mortgage on 742 Evergreen Terrace "
                           "is with Springfield Savings & Loan.",
                           correspondent="Springfield Savings & Loan", document_type="Statement")),
        ("glance", "Car", "Plymouth Junkerolla",
         lambda n, d: _doc(n, d, "Car registration", "Homer's car is a pink Plymouth "
                           "Junkerolla.", correspondent="Springfield DMV",
                           document_type="Registration", by="homer")),
        ("insurance", "Car insurance", "Duff Insurance",
         lambda n, d: _doc(n, d, "Car insurance policy", "The car is insured with Duff "
                           "Insurance for $640 a year.", correspondent="Duff Insurance",
                           document_type="Policy", by="homer")),
        ("insurance", "Home insurance", "Springfield Mutual",
         lambda n, d: _doc(n, d, "Home insurance policy", "The house is insured with "
                           "Springfield Mutual.", correspondent="Springfield Mutual",
                           document_type="Policy")),
        ("subscriptions", "Itchy & Scratchy Channel", "$9.99 a month",
         lambda n, d: _doc(n, d, "Itchy & Scratchy Channel invoice", "The Itchy & Scratchy "
                           "Channel costs $9.99 a month.", correspondent="Itchy & Scratchy "
                           "Channel", document_type="Invoice", by="bart")),
        ("subscriptions", "Springfield Shopper", "$12 a month",
         lambda n, d: _doc(n, d, "Springfield Shopper subscription", "The Springfield "
                           "Shopper is delivered daily for $12 a month.",
                           correspondent="Springfield Shopper", document_type="Invoice")),
        ("memberships", "Stonecutters", "Homer is a member",
         lambda n, d: _doc(n, d, "Stonecutters membership card", "Homer is a member of the "
                           "Stonecutters, number 908.", correspondent="Stonecutters",
                           document_type="Membership", by="homer")),
        ("relatives", "Abe Simpson", "Homer's father",
         lambda n, d: _note(n, d, "Grampa's visit", "Abe Simpson, Homer's father, visited "
                            "from the retirement castle.", by="homer")),
        ("relatives", "Patty Bouvier", "Marge's sister",
         lambda n, d: _note(n, d, "Patty's birthday", "Patty Bouvier, Marge's sister, "
                            "turned 45.", by="marge")),
        ("relatives", "Selma Bouvier", "Marge's sister",
         lambda n, d: _note(n, d, "Selma's iguana", "Selma Bouvier, Marge's other sister, "
                            "has a new iguana.", by="marge")),
        ("friends", "Ned Flanders", "the neighbour",
         lambda n, d: _note(n, d, "Ned's ladder", "Ned Flanders, the neighbour, lent Homer "
                            "his ladder again.", by="homer")),
        ("friends", "Milhouse Van Houten", "Bart's best friend",
         lambda n, d: _note(n, d, "Sleepover", "Milhouse Van Houten, Bart's best friend, "
                            "slept over.", by="bart", persons=["bart"])),
        ("helpers", "Dr. Julius Hibbert", "family doctor",
         lambda n, d: _doc(n, d, "Check-up letter", "Dr. Julius Hibbert, the family doctor, "
                           "confirms Homer's check-up.", correspondent="Dr. Julius Hibbert",
                           document_type="Letter", by="homer")),
        ("helpers", "Springfield Elementary", "Bart's and Lisa's school",
         lambda n, d: _doc(n, d, "School enrolment", "Bart and Lisa attend Springfield "
                           "Elementary.", correspondent="Springfield Elementary",
                           document_type="Letter", persons=["bart", "lisa"])),
        ("helpers", "Miss Hoover", "Lisa's teacher",
         lambda n, d: _note(n, d, "Parents' evening", "Miss Hoover is Lisa's teacher.",
                            by="marge", topic="school", persons=["lisa"])),
        ("helpers", "Dr. Wolfe", "the dentist",
         lambda n, d: _doc(n, d, "Dentist invoice", "Dr. Wolfe, the family dentist, cleaned "
                           "Bart's teeth.", correspondent="Dr. Wolfe", document_type="Invoice",
                           persons=["bart"])),
        ("helpers", "Springfield Animal Hospital", "the vet",
         lambda n, d: _doc(n, d, "Vet bill", "Springfield Animal Hospital vaccinated Santa's "
                           "Little Helper.", correspondent="Springfield Animal Hospital",
                           document_type="Invoice", persons=["bart"])),
        ("helpers", "Kwik-E-Mart", "the corner shop",
         lambda n, d: _note(n, d, "Corner shop", "Apu's Kwik-E-Mart is the corner shop the "
                            "family uses.", by="homer")),
        ("pets", "Santa's Little Helper", "Bart's greyhound",
         lambda n, d: _note(n, d, "Our dog", "Santa's Little Helper is Bart's greyhound.",
                            by="bart")),
        ("schedule", "Lisa's dentist appointment", ahead1.isoformat(),
         lambda n, d: _note(n, d, "Dentist for Lisa", f"Lisa sees Dr. Wolfe on {ahead1}.",
                            by="marge", persons=["lisa"])),
        ("schedule", "The school play", ahead2.isoformat(),
         lambda n, d: _note(n, d, "School play", f"The school play is on {ahead2}; Lisa plays "
                            "the lead.", by="marge", topic="school", persons=["lisa"])),
        ("recurring", "Saxophone lesson", "every Tuesday",
         lambda n, d: _note(n, d, "Saxophone lessons", "Lisa has a saxophone lesson every "
                            "Tuesday.", by="lisa", bucket="lisa", topic="saxophone")),
        ("glance", "Homer's employer", "Springfield Nuclear Power Plant",
         lambda n, d: _doc(n, d, "Payslip", "Homer works at the Springfield Nuclear Power "
                           "Plant.", correspondent="Springfield Nuclear Power Plant",
                           document_type="Payslip", by="homer")),
        ("glance", "Bowling team", "the Pin Pals",
         lambda n, d: _note(n, d, "Bowling team", "Homer bowls with the Pin Pals on "
                            "Thursdays.", by="homer", bucket="homer", topic="bowling")),
    ]
    filings, facts = [], []
    for n, (section, name, value, make) in enumerate(plan, start=1):
        filing = make(n, start + timedelta(days=n // 3))
        filings.append(filing)
        facts.append(Fact(section, name, value, filing.path))
    return filings, facts


def _correction(facts: list[Fact], n: int, on: date) -> tuple[Filing, Fact]:
    """The filing that corrects a planted fact, and that fact as corrected:
    the saxophone lesson moves from Tuesday to Thursday."""
    before = next(f for f in facts if f.name == "Saxophone lesson")
    filing = _note(n, on, "Saxophone lessons moved", "Lisa's saxophone lesson moves from "
                   "Tuesday to every Thursday.", by="lisa", bucket="lisa", topic="saxophone")
    return filing, replace(before, value="every Thursday", was=before.value,
                           corrected_by=filing.path)


# ── git fast-import ──────────────────────────────────────────────────────

def _commit(mark: int, by: str, subject: str, on: date, files: dict[str, str]) -> bytes:
    """One commit of `files` by `by` at ten in the morning `on` that day, on `main`."""
    when = int(datetime(on.year, on.month, on.day, 10, tzinfo=timezone.utc).timestamp())
    message = subject.encode()
    out = [b"commit refs/heads/main\n", f"mark :{mark}\n".encode(),
           f"author {by} <{by}@simpson> {when} +0000\n".encode(),
           f"committer {by} <{by}@simpson> {when} +0000\n".encode(),
           f"data {len(message)}\n".encode(), message, b"\n"]
    if mark > 1:
        out.append(f"from :{mark - 1}\n".encode())
    for path, text in files.items():
        data = text.encode()
        out += [f"M 100644 inline {path}\n".encode(), f"data {len(data)}\n".encode(), data, b"\n"]
    return b"".join(out) + b"\n"
