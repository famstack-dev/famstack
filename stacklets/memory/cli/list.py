"""stack memory list: show and change the family's lists.

Every list is a file, `<bucket>/lists/<slug>.md` (the format is
`stack.lists`). Groceries, the market, things to order, the todos for
a trip: one kind of list, one set of verbs.

The verbs are in `USAGE`, which a bare `stack memory list` prints.

The caller names the item, never the line: the item is matched and the
rest of the file is left as it is, so a tick cannot lose other state.
A name that matches several items, a section the list does not have,
or a person the family has not declared is refused with the valid
choices, so the caller can pick one instead of guessing.

Reads come off the vault on disk; writes commit to Forgejo as the
person named by `--as`, through `update_memory`, against the current
file there.
"""

from __future__ import annotations

HELP = "Show and change the family's lists"

import argparse
import re
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import (  # noqa: E402
    DEFAULT_SHARED_BUCKET,
    refresh_vault_if_stale,
    update_memory,
    vault_path_for,
)

from stack.links import go_page, public  # noqa: E402
from stack.lists import (  # noqa: E402
    ARCHIVE_DIR,
    CALENDAR,
    KINDS,
    TODO,
    LISTS_DIR,
    calendar_description,
    calendar_slug,
    calendar_title,
    FamilyList,
    Item,
    ListError,
    find,
    insert_item,
    known_items,
    list_path,
    new_list,
    person_link,
    read_list,
    remove_item,
    set_details,
    set_done,
)
from stack.people import load_people, resolve  # noqa: E402
from stack.vault import slug as vault_slug  # noqa: E402

USAGE = """\
    stack memory list show                          every list, with its open items counted
    stack memory list show einkaufsliste [--all]    the open items (--all: the ticked ones too)
    stack memory list show calendar --days 7        the family calendar's next 7 days, each
                                                    entry with its weekday (2: today, tomorrow)
    stack memory list create einkaufsliste --title Einkaufsliste \\
        --description "Was wir im Supermarkt brauchen" --topic einkaeufe-besorgungen \\
        --section "Gemüse & Obst" --section "Milchprodukte & Eier" --as marge
    stack memory list add einkaufsliste Joghurt Eier --section "Milchprodukte & Eier" --as marge
    stack memory list add Joghurt Eier --topic einkaeufe-besorgungen --as marge
                                                    each where it was last bought
    stack memory list add schule "Sign the field trip form" --for homer --for marge \\
        --due 2026-10-20 --as marge
    stack memory list add calendar "10:30 Elternabend" --due 2026-10-20 --as marge
                                                    `calendar` is the family calendar, whatever
                                                    its name; add starts it when there is none
    stack memory list tick|untick|remove einkaufsliste Joghurt --as homer
    stack memory list tick "Erlaubniszettel" --as marge   on whichever list it is
    stack memory list move einkaufsliste Eier --to markt --as marge
    stack memory list create packliste --title Packliste --kind checklist --as marge
                                                    ticked items stay until a reset
    stack memory list create termine --title Termine --kind calendar --as marge
                                                    in date order, each entry gone
                                                    the day after its date
    stack memory list reset packliste --as marge     reopen every ticked item
    stack memory list set einkaufsliste --topic einkauf --as marge
                                                    the list's title, description, topics,
                                                    kind or walking order (--section, in order)
    stack memory list delete baumarkt [--yes] --as marge
                                                    --yes: even with items still open
"""

VERBS = ("show", "create", "add", "tick", "untick", "remove", "move", "reset", "set", "delete")

# A commit subject's length, as git and Forgejo show it without wrapping.
_SUBJECT_MAX = 72

# The name every caller can use for the family calendar, whatever its file
# is called in this household ("termine", "dates", or one the family started),
# so the agent never has to look it up or guess it.
CALENDAR_NAME = "calendar"

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class Refusal(ValueError):
    """An answer that tells the caller what to do instead."""


# ── Arguments ────────────────────────────────────────────────────────────

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stack memory list", add_help=False)
    verbs = parser.add_subparsers(dest="verb")

    show = verbs.add_parser("show", add_help=False)
    show.add_argument("slug", nargs="?")
    show.add_argument("--all", action="store_true")
    show.add_argument("--days", type=int, default=None)

    create = verbs.add_parser("create", add_help=False)
    # The slug is optional: a family names a list, and `Baumarkt` makes
    # `baumarkt` the way a topic's name makes its folder.
    create.add_argument("slug", nargs="?", default="")
    create.add_argument("--title", required=True)
    create.add_argument("--description", default="")
    create.add_argument("--topic", action="append", default=[])
    create.add_argument("--section", action="append", default=[])
    create.add_argument("--kind", choices=KINDS, default=KINDS[0])

    add = verbs.add_parser("add", add_help=False)
    # The list is optional: `add Joghurt` puts a known item where it was
    # last bought. A first word naming a list is the list.
    add.add_argument("items", nargs="+")
    add.add_argument("--topic", default="")
    add.add_argument("--section", default="")
    add.add_argument("--for", dest="people", action="append", default=[])
    add.add_argument("--due", default="")

    # As for `add`, the list is optional: a first word naming a list is
    # the list, and without one the item is looked up on every list.
    for verb in ("tick", "untick", "remove"):
        one = verbs.add_parser(verb, add_help=False)
        one.add_argument("items", nargs="+")

    move = verbs.add_parser("move", add_help=False)
    move.add_argument("slug")
    move.add_argument("items", nargs="+")
    move.add_argument("--to", required=True)

    # `set` changes what is given and keeps the rest. `--topic` and
    # `--section` replace the whole list of them: sections are a walking
    # order, which only means something complete.
    details = verbs.add_parser("set", add_help=False)
    details.add_argument("slug")
    details.add_argument("--title")
    details.add_argument("--description")
    details.add_argument("--topic", action="append")
    details.add_argument("--section", action="append")
    details.add_argument("--kind", choices=KINDS)

    reset = verbs.add_parser("reset", add_help=False)
    reset.add_argument("slug")

    delete = verbs.add_parser("delete", add_help=False)
    delete.add_argument("slug")
    delete.add_argument("--yes", action="store_true")

    for sub in (create, add, move, reset, details, delete,
                *[verbs.choices[v] for v in ("tick", "untick", "remove")]):
        sub.add_argument("--as", dest="actor", default="")
    return parser


def run(args, stacklet, config):
    argv = list(args or [])
    if not argv or argv[0] not in VERBS:
        print(USAGE)
        return {"verbs": list(VERBS)} if not argv else \
            {"error": f"unknown verb {argv[0]!r}; use one of: {', '.join(VERBS)}"}
    try:
        ns = _parser().parse_args(argv)
    except SystemExit:
        return {"error": f"usage: see `stack memory list` for the {argv[0]} arguments"}

    vault = _vault(config)
    if vault is None:
        return {"error": "no vault found; is the memory stacklet installed?"}
    try:
        if ns.verb == "show":
            _name_the_calendar(ns, _all_lists_at(vault), config)
            _name_by_title(ns, _all_lists_at(vault))
            return show(vault, ns.slug, show_all=ns.all, days=ns.days,
                        language=_language(config))
        if not ns.actor.strip():
            # Every change is a commit with a person's name on it; one
            # without a name cannot be told apart from anybody else's.
            raise Refusal(f"say who {ns.verb}s: add --as <person>, the family member it is for")
        actor = _actor(ns.actor)
        if ns.verb != "create":
            _name_the_calendar(ns, _all_lists_at(vault), config)
            _name_by_title(ns, _all_lists_at(vault))
        if ns.verb == "create":
            return create(vault, config, ns, actor)
        if ns.verb == "set":
            return change_details(vault, config, ns, actor)
        if ns.verb == "move":
            return move(vault, config, ns.slug, ns.items, ns.to, actor)
        if ns.verb == "delete":
            return delete(vault, config, ns.slug, actor, even_open=ns.yes)
        if ns.verb == "add":
            return add(vault, config, ns, actor)
        return change(vault, config, ns, actor)
    except Refusal as e:
        return {"error": str(e)}


# ── Reading ──────────────────────────────────────────────────────────────

def show(vault: Path, slug: str | None, *, show_all: bool, days: int | None = None,
         language: str = "") -> dict:
    """Print every list, or one list's items under their sections.

    A calendar's entries carry their weekday and how far away they are,
    worked out here: a model asked for the week counts weekdays instead,
    and gets some wrong. `days` keeps the entries from today on that fall
    within that many days.
    """
    refresh_vault_if_stale(vault)
    if not slug:
        lists = _all_lists(vault)
        if not lists:
            print("No lists yet. Start one with `stack memory list create`.")
        for lst in lists:
            about = f"  ({lst.description})" if lst.description else ""
            kind = f" ({lst.kind})" if lst.kind != TODO else ""
            print(f"{lst.slug}: {lst.title}{kind}, {len(lst.open_items)} open{about}")
        return {"lists": [{"slug": x.slug, "title": x.title, "description": x.description,
                           "kind": x.kind, "topics": x.topics, "open": len(x.open_items)}
                          for x in lists]}

    path, lst = _find_list(vault, slug)
    items = lst.items if show_all else lst.open_items
    when = {}
    if lst.kind == CALENDAR:
        today = date.today()
        when = {id(i): _when(i.due, today, language) for i in items if i.due}
        if days is not None:
            items = [i for i in items if id(i) in when and 0 <= when[id(i)]["in_days"] < days]
    done = len(lst.items) - len(lst.open_items)
    print(f"{lst.title} ({path}): {len(lst.open_items)} open, {done} done")
    section = ""
    for item in items:
        if item.section != section:
            section = item.section
            print(f"## {section}")
        print(_describe(item, when.get(id(item))))
    return {"slug": lst.slug, "path": path, "title": lst.title,
            "items": [{**_as_dict(i), **when.get(id(i), {})} for i in items]}


# A calendar entry's day as the household says it: "Fr 25.12. (in 3 Tagen)".
_DAY_WORDS = {
    "de": {"weekdays": ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"), "date": "{d.day:02}.{d.month:02}.",
           "today": "heute", "tomorrow": "morgen", "yesterday": "gestern",
           "ahead": "in {n} Tagen", "ago": "vor {n} Tagen"},
    "en": {"weekdays": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"), "date": "{d.day} {month}",
           "today": "today", "tomorrow": "tomorrow", "yesterday": "yesterday",
           "ahead": "in {n} days", "ago": "{n} days ago"},
}
_MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _when(due: str, today: date, language: str) -> dict:
    """The weekday, the day as written, and the distance from `today` of `due`."""
    words = _DAY_WORDS.get((language or "").strip().lower()[:2], _DAY_WORDS["en"])
    day = date.fromisoformat(due)
    n = (day - today).days
    relative = {0: words["today"], 1: words["tomorrow"], -1: words["yesterday"]}.get(n) or \
        (words["ahead"] if n > 0 else words["ago"]).format(n=abs(n))
    written = words["date"].format(d=day, month=_MONTHS_EN[day.month - 1])
    weekday = words["weekdays"][day.weekday()]
    return {"weekday": weekday, "day": f"{weekday} {written} ({relative})", "in_days": n}


def _describe(item: Item, when: dict | None = None) -> str:
    """An item as a person reads it: the box, the text, who and when.

    A calendar entry leads with its day (`when`), the way a calendar reads.
    """
    extra = [link.label for link in item.links if link.person]
    if when:
        tail = f"  ({', '.join(extra)})" if extra else ""
        return f"- [{'x' if item.done else ' '}] {when['day']} {item.text}{tail}"
    if item.due:
        extra.append(f"due {item.due}")
    if item.done_on:
        extra.append(f"ticked {item.done_on}")
    tail = f"  ({', '.join(extra)})" if extra else ""
    return f"- [{'x' if item.done else ' '}] {item.text}{tail}"


def _as_dict(item: Item) -> dict:
    return {"text": item.text, "done": item.done, "section": item.section,
            "people": list(item.people), "sources": list(item.sources),
            "due": item.due, "done_on": item.done_on}


def _all_lists(vault: Path) -> list[FamilyList]:
    return [lst for _, lst in _all_lists_at(vault)]


def _all_lists_at(vault: Path) -> list[tuple[str, FamilyList]]:
    """Every list with its vault path; a file that is not a list is skipped."""
    found = []
    for file in sorted(vault.glob(f"*/{LISTS_DIR}/*.md")):
        path = _rel(vault, file)
        try:
            found.append((path, read_list(file.read_text(encoding="utf-8"), path=path)))
        except (ListError, OSError, UnicodeDecodeError):
            continue
    return found


def _history(vault: Path, lists: list[tuple[str, FamilyList]]) -> list[tuple[str, FamilyList]]:
    """These lists and every archived month of them, each paired with its list's slug."""
    pairs = [(lst.slug, lst) for _, lst in lists]
    for path, lst in lists:
        bucket = path.split("/", 1)[0]
        folder = vault / bucket / LISTS_DIR / ARCHIVE_DIR / lst.slug
        for file in sorted(folder.glob("*.md")):
            try:
                pairs.append((lst.slug, read_list(file.read_text(encoding="utf-8"),
                                                  path=_rel(vault, file))))
            except (ListError, OSError, UnicodeDecodeError):
                continue
    return pairs


def _find_list(vault: Path, slug: str) -> tuple[str, FamilyList]:
    """The vault path and contents of the list `slug`, or a refusal naming the lists."""
    files = sorted(vault.glob(f"*/{LISTS_DIR}/{slug}.md"))
    if not files:
        known = ", ".join(x.slug for x in _all_lists(vault)) or "(none yet)"
        raise Refusal(f"no list {slug!r}. Lists: {known}")
    if len(files) > 1:
        raise Refusal(f"{slug!r} is a list in several buckets: "
                      + ", ".join(_rel(vault, f) for f in files))
    path = _rel(vault, files[0])
    try:
        return path, read_list(files[0].read_text(encoding="utf-8"), path=path)
    except ListError as e:
        raise Refusal(str(e)) from e


# ── Writing ──────────────────────────────────────────────────────────────

def change_details(vault: Path, config, ns, actor: str) -> dict:
    """Change a list's title, description, topics, kind or walking order."""
    path, lst = _find_list(vault, ns.slug)
    given = {"title": ns.title, "description": ns.description,
             "topics": _named(ns.topic), "sections": _named(ns.section), "kind": ns.kind}
    changed = [name for name, value in given.items() if value is not None]
    if not changed:
        raise Refusal("name what to change: --title, --description, --topic, --section or --kind")

    def _apply(prior: str) -> str:
        return set_details(prior, **given)
    return _commit(config, path, _apply, actor,
                   f"changed the {', '.join(changed)} of {lst.title}")


def _named(values: list[str] | None) -> list[str] | None:
    """The non-empty values given, so `--topic ""` leaves a list in no topic."""
    return None if values is None else [v.strip() for v in values if v.strip()]


def create(vault: Path, config, ns, actor: str) -> dict:
    """Start a new, empty list in the shared bucket."""
    slug = ns.slug.strip() or vault_slug(ns.title)
    if not _SLUG.match(slug):
        raise Refusal(f"{slug!r} is not a slug: lowercase letters, digits and dashes")
    if list(vault.glob(f"*/{LISTS_DIR}/{slug}.md")):
        raise Refusal(f"the list {slug!r} already exists")
    path = list_path(slug, bucket=_shared_bucket(config))
    text = new_list(ns.title.strip(), description=ns.description.strip(),
                    topics=ns.topic, sections=ns.section, kind=ns.kind)

    def _create(prior: str) -> str:
        if (prior or "").strip():
            raise ValueError(f"the list {slug!r} already exists")
        return text
    return _commit(config, path, _create, actor, f"started the list {ns.title.strip()}")


def add(vault: Path, config, ns, actor: str) -> dict:
    """Add items, each to its list: the one named, or where it was last bought.

    An item the household had before keeps its spelling and its section.
    `calendar` names the family calendar, which is started with the first
    entry when there is none. One commit per list.
    """
    lists = _all_lists_at(vault)
    slug, names = _list_and_items(lists, ns.items)
    start = ""
    if slug == CALENDAR_NAME and not any(lst.slug == slug for _, lst in lists):
        path, calendar, start = _calendar(lists, config)
        lists, slug = [*lists, (path, calendar)], calendar.slug
    if slug:
        candidates = [(p, lst) for p, lst in lists if lst.slug == slug]
    else:
        candidates = [(p, lst) for p, lst in lists if not ns.topic or ns.topic in lst.topics]
    known = known_items(_history(vault, candidates))
    by_slug = {lst.slug: (p, lst) for p, lst in candidates}

    plan: dict[str, list[tuple[str, str]]] = {}
    for name in names:
        seen = known.get(name.casefold())
        target = slug or (seen.slug if seen else "")
        if target not in by_slug:
            raise Refusal(_which_list(name, candidates, ns.topic))
        lst = by_slug[target][1]
        here = seen if seen and seen.slug == target else None
        section = _section(lst, ns.section) if ns.section else \
            (here.section if here and (not lst.sections or here.section in lst.sections) else "")
        plan.setdefault(target, []).append((here.text if here else name, section))

    due = ns.due
    if due and not _ISO_DAY.match(due):
        raise Refusal(f"--due {due!r} is not a date like 2026-10-14")
    undated = [by_slug[t][1].title for t in plan if by_slug[t][1].kind == CALENDAR and not due]
    if undated:
        raise Refusal(f"an entry on {undated[0]} needs its date: --due YYYY-MM-DD")
    people = _people(vault, config, ns.people)
    return _combined([_add_to(config, *by_slug[target], entries, people, due, actor,
                              start=start if target == slug else "")
                      for target, entries in plan.items()])


def _name_the_calendar(ns, lists: list[tuple[str, FamilyList]], config) -> None:
    """Replace `calendar` wherever a list is named with the family calendar's slug.

    A list the family really called `calendar` is itself. Without any
    calendar the name is left as it is: `add` starts one, every other verb
    says there is no such list.
    """
    if any(lst.slug == CALENDAR_NAME for _, lst in lists):
        return
    found = next((lst.slug for path, lst in lists
                  if lst.kind == CALENDAR and path.startswith(f"{_shared_bucket(config)}/")), "")
    if not found:
        return
    if getattr(ns, "slug", None) == CALENDAR_NAME:
        ns.slug = found
    if getattr(ns, "to", None) == CALENDAR_NAME:
        ns.to = found
    items = getattr(ns, "items", None)
    if items and items[0].strip().casefold() == CALENDAR_NAME:
        ns.items = [found, *items[1:]]


# What people put after a list's name when they say it.
_LIST_WORDS = re.compile(r"[\s-]*(liste|list)$", re.IGNORECASE)


def _name_by_title(ns, lists: list[tuple[str, FamilyList]]) -> None:
    """Replace a list named by its title ("Wochenmarkt", "Wochenmarkt-Liste")
    with its slug (`markt`), wherever a list is named. A slug always wins."""
    slugs = {lst.slug for _, lst in lists}

    def slug_for(name: str | None) -> str | None:
        if not name or name in slugs:
            return None
        wanted = _LIST_WORDS.sub("", name.strip()).casefold()
        found = [lst.slug for _, lst in lists
                 if _LIST_WORDS.sub("", lst.title.strip()).casefold() == wanted]
        return found[0] if len(found) == 1 else None

    for attr in ("slug", "to"):
        found = slug_for(getattr(ns, attr, None))
        if found:
            setattr(ns, attr, found)
    items = getattr(ns, "items", None)
    found = slug_for(items[0]) if items else None
    if found and len(items) > 1:
        ns.items = [found, *items[1:]]


def _calendar(lists: list[tuple[str, FamilyList]], config) -> tuple[str, FamilyList, str]:
    """The family calendar as (path, list, start): the text to start it with, or "".

    The first list of `kind: calendar` in the shared bucket, by name, the
    one the archivist puts a letter's dates on. Without one, the calendar
    the archivist would start, in the household language, with the text
    that starts it.
    """
    bucket = _shared_bucket(config)
    for path, lst in lists:
        if lst.kind == CALENDAR and path.startswith(f"{bucket}/"):
            return path, lst, ""
    language = _language(config)
    path = list_path(calendar_slug(language), bucket=bucket)
    start = new_list(calendar_title(language), description=calendar_description(language),
                     kind=CALENDAR)
    return path, read_list(start, path=path), start


def _add_to(config, path: str, lst: FamilyList, entries: list[tuple[str, str]],
            people: list, due: str, actor: str, *, start: str = "") -> dict:
    bucket = path.split("/", 1)[0]
    links = tuple(person_link(p.id, p.first_name, bucket=bucket) for p in people)
    outcome: dict = {}

    def _apply(prior: str) -> str:
        text = prior or start
        new, outcome["said"] = _add(text, read_list(text, path=path), entries, links, due, path)
        return new
    return _commit(config, path, _apply, actor, lambda: outcome.get("said", "added"))


def _which_list(name: str, candidates: list[tuple[str, FamilyList]], topic: str) -> str:
    """The refusal for an item no list has had: every list it could go on."""
    if not candidates:
        return f"no list names the topic {topic!r}. Start one with `stack memory list create`."
    lists = "; ".join(f"{lst.slug} ({lst.description})" if lst.description else lst.slug
                      for _, lst in candidates)
    return f"no list has had {name!r} before; name the list. Lists: {lists}"


def change(vault: Path, config, ns, actor: str) -> dict:
    """tick, untick, remove or reset; one commit per list it touches.

    Without a list, each item is looked up on every list, so "I signed the
    permission slip" ticks it on whichever list the archivist put it.
    """
    if ns.verb == "reset":
        return _change_list(config, *_find_list(vault, ns.slug), "reset", [], actor)
    lists = _all_lists_at(vault)
    slug, names = _list_and_items(lists, ns.items)
    if slug:
        return _change_list(config, *_find_list(vault, slug), ns.verb, names, actor)
    plan: dict[str, tuple[FamilyList, list[str]]] = {}
    for name in names:
        path, lst = _locate(lists, name, ns.verb)
        plan.setdefault(path, (lst, []))[1].append(name)
    return _combined([_change_list(config, path, lst, ns.verb, found, actor)
                      for path, (lst, found) in plan.items()])


def _change_list(config, path: str, lst: FamilyList, verb: str, names: list[str],
                 actor: str) -> dict:
    """One verb on one list, in one commit."""
    today = date.today().isoformat()
    outcome: dict = {}

    def _apply(prior: str) -> str:
        current = read_list(prior, path=path)
        if verb == "reset":
            new, said = _reset(prior, current, today, path)
        else:
            new, said = _each(prior, current.title, verb, names, today, path)
        outcome["said"] = said
        return new

    return _commit(config, path, _apply, actor, lambda: outcome.get("said", "changed the list"))


def _locate(lists: list[tuple[str, FamilyList]], name: str, verb: str) -> tuple[str, FamilyList]:
    """The list holding the item `name` means, when the caller named none.

    An item open on one list and ticked on another is the one the verb
    would change; anything still on several lists is refused with each
    list and its matches, so the caller names the list.
    """
    found = [(path, lst) for path, lst in lists if find(lst, name)]
    if len(found) > 1:
        wanted_done = verb == "untick"
        changeable = [(path, lst) for path, lst in found
                      if any(i.done == wanted_done for i in find(lst, name))]
        found = changeable if len(changeable) == 1 else found
    if not found:
        raise Refusal(f"no item matching {name!r} on any list")
    if len(found) > 1:
        where = "; ".join(f"{lst.slug}: {', '.join(i.text for i in find(lst, name))}"
                          for _, lst in found)
        raise Refusal(f"{name!r} is on several lists ({where}). Name the list.")
    return found[0]


def _list_and_items(lists: list[tuple[str, FamilyList]], words: list[str]) -> tuple[str, list[str]]:
    """The list a command names first, if any, and the items after it."""
    names = [" ".join(w.split()) for w in words if w.strip()]
    first = names[0] if names else ""
    if first.casefold() == CALENDAR_NAME:
        slug = names.pop(0).casefold()
    elif any(lst.slug == first for _, lst in lists):
        slug = names.pop(0)
    else:
        slug = ""
    if not names:
        raise Refusal("name at least one item")
    return slug, names


def _combined(results: list[dict]) -> dict:
    """One answer for changes to several lists: each list's, or every error."""
    if len(results) == 1:
        return results[0]
    errors = [r["error"] for r in results if "error" in r]
    return {"error": "; ".join(errors)} if errors else {"ok": True, "lists": results}


def delete(vault: Path, config, slug: str, actor: str, *, even_open: bool) -> dict:
    """Delete a list, so a list nobody needs is gone instead of left empty.

    Open items are what someone still meant to do, so a list with any is
    deleted only with `--yes`; the refusal names them, for the caller to
    ask whoever wanted the list gone. The archive stays: it is the record
    of what was bought, and a list started again under the same name
    finds its known items there.
    """
    path, lst = _find_list(vault, slug)

    def _delete(prior: str) -> None:
        current = read_list(prior, path=path)
        if current.open_items and not even_open:
            open_items = current.open_items
            count = "1 open item" if len(open_items) == 1 else f"{len(open_items)} open items"
            raise ValueError(f"{current.title} still has {count}: "
                             f"{'; '.join(i.text for i in open_items)}. "
                             "Delete it with them: add --yes")
        return None
    return _commit(config, path, _delete, actor, f"deleted the list {lst.title}")


def move(vault: Path, config, slug: str, names: list[str], to: str, actor: str) -> dict:
    """Move items to another list: added there first, so a failure never loses one."""
    path, lst = _find_list(vault, slug)
    target_path, target = _find_list(vault, to)
    # A section the other list does not have would be refused there, so the
    # item goes above its first heading instead.
    kept = {s.casefold() for s in target.sections}
    items = [i if not target.sections or i.section.casefold() in kept else replace(i, section="")
             for i in (_one(lst, name) for name in names)]

    def _add_all(prior: str) -> str:
        for item in items:
            if not _same_on(read_list(prior, path=target_path), item):
                prior = insert_item(prior, item, path=target_path)
        return prior
    said = f"moved {'; '.join(i.text for i in items)} from {lst.title} to {target.title}"
    added = _commit(config, target_path, _add_all, actor, said, quiet=True)
    if "error" in added:
        return added

    def _remove_all(prior: str) -> str:
        for item in items:
            if match := _same_on(read_list(prior, path=path), item):
                prior = remove_item(prior, match)
        return prior
    return _commit(config, path, _remove_all, actor, said)


def _same_on(lst: FamilyList, item: Item) -> Item | None:
    """The item on `lst` that is `item` again: the same name, open or ticked alike.

    Exact and tick-aware, unlike `find`: "Eier" is not "Bio-Eier", and a
    ticked "Eier" is not the open one a move or an add brings.
    """
    return next((i for i in lst.items
                 if i.text.casefold() == item.text.casefold() and i.done == item.done), None)


def _add(text: str, lst: FamilyList, entries: list[tuple[str, str]],
         links: tuple, due: str, path: str) -> tuple[str, str]:
    added, already = [], []
    for name, section in entries:
        if _same_on(lst, Item(name)):
            already.append(name)
            continue
        text = insert_item(text, Item(name, links=links, due=due, section=section), path=path)
        lst = read_list(text, path=path)
        added.append(name)
    said = [f"added {'; '.join(added)} to {lst.title}"] if added else []
    said += [f"{name!r} is already on {lst.title}" for name in already]
    return text, "; ".join(said)


def _each(text: str, title: str, verb: str, names: list[str], today: str,
          path: str) -> tuple[str, str]:
    done, unchanged = [], []
    for name in names:
        item = _one(read_list(text, path=path), name, verb=verb)
        if verb == "remove":
            text = remove_item(text, item)
        elif item.done == (verb == "tick"):
            unchanged.append(f"{item.text!r} is already {'ticked' if item.done else 'open'}")
            continue
        else:
            text = set_done(text, item, done=verb == "tick", today=today, path=path)
        done.append(item.text)
    verb_said = {"tick": "ticked off {} on", "untick": "reopened {} on",
                 "remove": "removed {} from"}[verb]
    said = [f"{verb_said.format('; '.join(done))} {title}"] if done else []
    return text, "; ".join(said + unchanged)


def _reset(text: str, lst: FamilyList, today: str, path: str) -> tuple[str, str]:
    ticked = [i for i in lst.items if i.done]
    for item in ticked:
        text = set_done(text, item, done=False, today=today, path=path)
    if not ticked:
        return text, f"nothing is ticked on {lst.title}; the list is already open"
    return text, f"reopened {'; '.join(i.text for i in ticked)} on {lst.title}"


# The day `show` prints in front of a calendar entry: a weekday, the date,
# and how far away it is in brackets.
_PRINTED_DAY = re.compile(r"^\S+ \S+(?: \S+)? \([^)]*\)\s+")


def _one(lst: FamilyList, name: str, *, verb: str = "") -> Item:
    """The single item `name` means, or a refusal that names the candidates.

    The same item can be on a list twice, ticked once and open again
    ("we need yoghurt again"). Its name cannot tell the two apart, so a
    tick means the open one, an untick the ticked one, and anything
    else the open one.
    """
    matches = find(lst, name)
    if not matches and lst.kind == CALENDAR:
        # Named the way `show` prints it, the day in front:
        # "Mon 19 Oct (in 2 days) 17:00 Turnen", "Mo 19.10. (in 2 Tagen) 17:00 Turnen".
        bare = _PRINTED_DAY.sub("", name, count=1)
        matches = find(lst, bare) if bare != name else matches
    if len(matches) > 1 and len({i.text.casefold() for i in matches}) == 1:
        wanted_done = verb == "untick"
        matches = [i for i in matches if i.done == wanted_done][:1] or matches[:1]
    if not matches:
        open_items = "; ".join(i.text for i in lst.open_items) or "(none)"
        raise Refusal(f"no item matching {name!r} on {lst.title}. Open items: {open_items}")
    if len(matches) > 1:
        raise Refusal(f"{name!r} matches several items: "
                         f"{'; '.join(i.text for i in matches)}. Name the item more exactly.")
    return matches[0]


def _section(lst: FamilyList, section: str) -> str:
    """The list's own spelling of `section`, or a refusal naming its sections."""
    section = " ".join((section or "").split())
    if not section or not lst.sections:
        return section
    for known in lst.sections:
        if known.casefold() == section.casefold():
            return known
    raise Refusal(f"{lst.title} has no section {section!r}. Sections: {'; '.join(lst.sections)}")


def _people(vault: Path, config, names: list[str]) -> list:
    if not names:
        return []
    known = load_people(vault, _shared_bucket(config)) or []
    found = []
    for name in names:
        person = resolve(name, known)
        if person is None:
            names_known = ", ".join(p.first_name for p in known) or "(none declared)"
            raise Refusal(f"nobody called {name!r} in the family. People: {names_known}")
        found.append(person)
    return found


def _commit(config, path: str, transform, actor: str,
            said: str | Callable[[], str], *, quiet: bool = False) -> dict:
    """Commit `transform` of `path` as `actor` and print what happened.

    `said` may be a function, for an edit that only knows what it did once
    the transform has met the current file. `quiet` commits without
    printing, for the first of the two commits a move makes.
    """
    def _said() -> str:
        return said() if callable(said) else said

    written: dict = {}

    def _recorded(prior: str) -> str | None:
        written["after"] = transform(prior)
        return written["after"]
    result = update_memory(config, path, _recorded, actor=actor,
                           message=lambda before, after: _commit_message(actor, path, _said()))
    if "error" in result or quiet:
        return result
    if not result.get("committed"):
        print(_said() or "no change")
        return {"ok": True, "committed": False, "path": path, "summary": _said()}
    lag = "" if result.get("mirrored") else "\n  The wiki and the vault mount catch up shortly."
    home_url = (config or {}).get("home_url", "")
    # A transform answering None deleted the list: there is no page to link.
    deleted = written.get("after", "") is None
    url = "" if deleted else public(go_page(path), f"{home_url}/go" if home_url else "")
    link = f"\n  {url}" if url else ""
    print(f"{_said()} (by {actor}){link}{lag}")
    return {"ok": True, "committed": True, "path": path, "by": actor, "summary": _said()}


def _commit_message(actor: str, path: str, said: str) -> str:
    """`chore(lists): marge added Joghurt to Einkaufsliste`, short enough for a subject.

    When the sentence is too long, the subject counts the items instead of
    naming them and the whole sentence moves to the body, so the history
    still says what changed and to which list.
    """
    line = f"chore(lists): {actor} {said}"
    if len(line) <= _SUBJECT_MAX:
        return line
    return f"chore(lists): {actor} {_counted(said, path)}\n\n{said}"


# "moved A; B from Einkaufsliste to Wochenmarkt", "ticked off A; B on Einkaufsliste"
_MOVED = re.compile(r"^moved (?P<items>.+) from (?P<a>.+) to (?P<b>[^;]+)$")
_CHANGED = re.compile(r"^(?P<verb>added|ticked off|reopened|removed) (?P<items>.+) "
                      r"(?P<prep>to|on|from) (?P<list>[^;]+)$")


def _counted(said: str, path: str) -> str:
    """The first clause of an answer with its items counted: `ticked off 3 items on Einkaufsliste`."""
    clause = said.split("; '")[0]
    if m := _MOVED.match(clause):
        return f"moved {_items(m['items'])} from {m['a']} to {m['b']}"
    if m := _CHANGED.match(clause):
        return f"{m['verb']} {_items(m['items'])} {m['prep']} {m['list']}"
    return f"changed {path}"


def _items(names: str) -> str:
    count = len(names.split("; "))
    return "1 item" if count == 1 else f"{count} items"


# ── Small things ─────────────────────────────────────────────────────────

def _vault(config) -> Path | None:
    data_dir = (config or {}).get("data_dir")
    vault = vault_path_for(Path(data_dir)) if data_dir else None
    return vault if vault and vault.exists() else None


def _shared_bucket(config) -> str:
    stack_cfg = (config or {}).get("stack") or {}
    return (stack_cfg.get("core") or {}).get("shared_bucket") or DEFAULT_SHARED_BUCKET


def _language(config) -> str:
    stack_cfg = (config or {}).get("stack") or {}
    return (stack_cfg.get("core") or {}).get("language") or ""


def _actor(value: str) -> str:
    """`@homer:example.org` or `homer` as the name a commit is authored by."""
    return (value or "").strip().split(":")[0].lstrip("@") or "someone"


def _rel(vault: Path, file: Path) -> str:
    return file.relative_to(vault).as_posix()
