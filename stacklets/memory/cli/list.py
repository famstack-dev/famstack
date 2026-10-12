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
    LISTS_DIR,
    FamilyList,
    Item,
    ListError,
    find,
    insert_item,
    list_path,
    new_list,
    person_link,
    read_list,
    remove_item,
    set_done,
)
from stack.people import load_people, resolve  # noqa: E402

USAGE = """\
    stack memory list show                          every list, with its open items counted
    stack memory list show einkaufsliste [--all]    the open items (--all: the ticked ones too)
    stack memory list create einkaufsliste --title Einkaufsliste \\
        --description "Was wir im Supermarkt brauchen" --topic einkaeufe-besorgungen \\
        --section "Gemüse & Obst" --section "Milchprodukte & Eier" --as marge
    stack memory list add einkaufsliste Joghurt Eier --section "Milchprodukte & Eier" --as marge
    stack memory list add schule "Sign the field trip form" --for homer --for marge \\
        --due 2026-10-20 --as marge
    stack memory list tick|untick|remove einkaufsliste Joghurt --as homer
    stack memory list move einkaufsliste Eier --to markt --as marge
    stack memory list create packliste --title Packliste --recurring --as marge
                                                    ticked items stay until a reset
    stack memory list reset packliste --as marge     reopen every ticked item
"""

VERBS = ("show", "create", "add", "tick", "untick", "remove", "move", "reset")

# A commit subject's length, as git and Forgejo show it without wrapping.
_SUBJECT_MAX = 72

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

    create = verbs.add_parser("create", add_help=False)
    create.add_argument("slug")
    create.add_argument("--title", required=True)
    create.add_argument("--description", default="")
    create.add_argument("--topic", action="append", default=[])
    create.add_argument("--section", action="append", default=[])
    create.add_argument("--recurring", action="store_true")

    add = verbs.add_parser("add", add_help=False)
    add.add_argument("slug")
    add.add_argument("items", nargs="+")
    add.add_argument("--section", default="")
    add.add_argument("--for", dest="people", action="append", default=[])
    add.add_argument("--due", default="")

    for verb in ("tick", "untick", "remove"):
        one = verbs.add_parser(verb, add_help=False)
        one.add_argument("slug")
        one.add_argument("items", nargs="+")

    move = verbs.add_parser("move", add_help=False)
    move.add_argument("slug")
    move.add_argument("items", nargs="+")
    move.add_argument("--to", required=True)

    reset = verbs.add_parser("reset", add_help=False)
    reset.add_argument("slug")

    for sub in (create, add, move, reset, *[verbs.choices[v] for v in ("tick", "untick", "remove")]):
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
            return show(vault, ns.slug, show_all=ns.all)
        if not ns.actor.strip():
            # Every change is a commit with a person's name on it; one
            # without a name cannot be told apart from anybody else's.
            raise Refusal(f"say who {ns.verb}s: add --as <person>, the family member it is for")
        actor = _actor(ns.actor)
        if ns.verb == "create":
            return create(vault, config, ns, actor)
        if ns.verb == "move":
            return move(vault, config, ns.slug, ns.items, ns.to, actor)
        return change(vault, config, ns, actor)
    except Refusal as e:
        return {"error": str(e)}


# ── Reading ──────────────────────────────────────────────────────────────

def show(vault: Path, slug: str | None, *, show_all: bool) -> dict:
    """Print every list, or one list's items under their sections."""
    refresh_vault_if_stale(vault)
    if not slug:
        lists = _all_lists(vault)
        if not lists:
            print("No lists yet. Start one with `stack memory list create`.")
        for lst in lists:
            about = f"  ({lst.description})" if lst.description else ""
            print(f"{lst.slug}: {lst.title}, {len(lst.open_items)} open{about}")
        return {"lists": [{"slug": x.slug, "title": x.title, "description": x.description,
                           "topics": x.topics, "open": len(x.open_items)} for x in lists]}

    path, lst = _find_list(vault, slug)
    items = lst.items if show_all else lst.open_items
    done = len(lst.items) - len(lst.open_items)
    print(f"{lst.title} ({path}): {len(lst.open_items)} open, {done} done")
    section = ""
    for item in items:
        if item.section != section:
            section = item.section
            print(f"## {section}")
        print(_describe(item))
    return {"slug": lst.slug, "path": path, "title": lst.title,
            "items": [_as_dict(i) for i in items]}


def _describe(item: Item) -> str:
    """An item as a person reads it: the box, the text, who and when."""
    extra = [link.label for link in item.links if link.person]
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
    found = []
    for file in sorted(vault.glob(f"*/{LISTS_DIR}/*.md")):
        try:
            found.append(read_list(file.read_text(encoding="utf-8"), path=_rel(vault, file)))
        except (ListError, OSError, UnicodeDecodeError):
            continue
    return found


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

def create(vault: Path, config, ns, actor: str) -> dict:
    """Start a new, empty list in the shared bucket."""
    slug = ns.slug.strip()
    if not _SLUG.match(slug):
        raise Refusal(f"{slug!r} is not a slug: lowercase letters, digits and dashes")
    if list(vault.glob(f"*/{LISTS_DIR}/{slug}.md")):
        raise Refusal(f"the list {slug!r} already exists")
    path = list_path(slug, bucket=_shared_bucket(config))
    text = new_list(ns.title.strip(), description=ns.description.strip(),
                    topics=ns.topic, sections=ns.section, recurring=ns.recurring)

    def _create(prior: str) -> str:
        if (prior or "").strip():
            raise ValueError(f"the list {slug!r} already exists")
        return text
    return _commit(config, path, _create, actor, f"started the list {ns.title.strip()}")


def change(vault: Path, config, ns, actor: str) -> dict:
    """add, tick, untick, remove or reset on one list, in one commit."""
    path, lst = _find_list(vault, ns.slug)
    people = _people(vault, config, getattr(ns, "people", []))
    section = _section(lst, getattr(ns, "section", ""))
    due = getattr(ns, "due", "")
    if due and not _ISO_DAY.match(due):
        raise Refusal(f"--due {due!r} is not a date like 2026-10-14")
    today = date.today().isoformat()
    bucket = path.split("/", 1)[0]
    outcome: dict = {}

    def _apply(prior: str) -> str:
        current = read_list(prior, path=path)
        if ns.verb == "add":
            links = tuple(person_link(p.id, p.first_name, bucket=bucket) for p in people)
            new, said = _add(prior, current, ns.items, section, links, due, path)
        elif ns.verb == "reset":
            new, said = _reset(prior, current, today, path)
        else:
            new, said = _each(prior, current.title, ns.verb, ns.items, today, path)
        outcome["said"] = said
        return new

    return _commit(config, path, _apply, actor, lambda: outcome.get("said", "changed the list"))


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
            if not find(read_list(prior, path=target_path), item.text):
                prior = insert_item(prior, item, path=target_path)
        return prior
    said = f"moved {'; '.join(i.text for i in items)} from {lst.title} to {target.title}"
    added = _commit(config, target_path, _add_all, actor, said, quiet=True)
    if "error" in added:
        return added

    def _remove_all(prior: str) -> str:
        for item in items:
            current = read_list(prior, path=path)
            match = [i for i in current.items if i.text == item.text]
            if match:
                prior = remove_item(prior, match[0])
        return prior
    return _commit(config, path, _remove_all, actor, said)


def _add(text: str, lst: FamilyList, names: list[str], section: str,
         links: tuple, due: str, path: str) -> tuple[str, str]:
    added, already = [], []
    for name in names:
        name = " ".join(name.split())
        if any(i.text.casefold() == name.casefold() and not i.done for i in lst.items):
            already.append(name)
            continue
        text = insert_item(text, Item(name, links=links, due=due, section=section), path=path)
        lst = read_list(text, path=path)
        added.append(name)
    said = [f"added {'; '.join(added)}"] if added else []
    said += [f"{name!r} is already on the list" for name in already]
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


def _one(lst: FamilyList, name: str, *, verb: str = "") -> Item:
    """The single item `name` means, or a refusal that names the candidates.

    The same item can be on a list twice, ticked once and open again
    ("we need yoghurt again"). Its name cannot tell the two apart, so a
    tick means the open one, an untick the ticked one, and anything
    else the open one.
    """
    matches = find(lst, name)
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
    result = update_memory(config, path, transform, actor=actor,
                           message=lambda before, after: _commit_message(actor, path, _said()))
    if "error" in result or quiet:
        return result
    if not result.get("committed"):
        print(_said() or "no change")
        return {"ok": True, "committed": False, "path": path, "summary": _said()}
    lag = "" if result.get("mirrored") else "\n  The wiki and the vault mount catch up shortly."
    home_url = (config or {}).get("home_url", "")
    url = public(go_page(path), f"{home_url}/go" if home_url else "")
    link = f"\n  {url}" if url else ""
    print(f"{_said()} (by {actor}){link}{lag}")
    return {"ok": True, "committed": True, "path": path, "by": actor, "summary": _said()}


def _commit_message(actor: str, path: str, said: str) -> str:
    """`chore(lists): marge added Joghurt to Einkaufsliste`; a long one moves to the body."""
    line = f"chore(lists): {actor} {said}"
    if len(line) <= _SUBJECT_MAX:
        return line
    return f"chore(lists): {actor} changed {path}\n\n{said}"


# ── Small things ─────────────────────────────────────────────────────────

def _vault(config) -> Path | None:
    data_dir = (config or {}).get("data_dir")
    vault = vault_path_for(Path(data_dir)) if data_dir else None
    return vault if vault and vault.exists() else None


def _shared_bucket(config) -> str:
    stack_cfg = (config or {}).get("stack") or {}
    return (stack_cfg.get("core") or {}).get("shared_bucket") or DEFAULT_SHARED_BUCKET


def _actor(value: str) -> str:
    """`@homer:example.org` or `homer` as the name a commit is authored by."""
    return (value or "").strip().split(":")[0].lstrip("@") or "someone"


def _rel(vault: Path, file: Path) -> str:
    return file.relative_to(vault).as_posix()
