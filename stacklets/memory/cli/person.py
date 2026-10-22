"""stack memory person: show a person's page, or add a person the family knows.

Every person the family knows has a file, `<bucket>/people/<id>.md`
(`stack.people`): the full name, the other names they go by, and the
chat account of a member. `stack up memory` writes one for each account;
everyone else, a baby, a grandparent, the family doctor, is added here:

    stack memory person show homer
    stack memory person add "Maggie Simpson" --alias Margaret --as marge
    stack memory person add "Homer Thompson" --id homer-thompson --as marge

`show` reads the person's wiki page (`<slug>/about.md`), the exact read
surface for identity questions, parallel to `stack memory topic <name>`.

`add` names the file after the first name unless `--id` gives another,
and commits it to Forgejo as the person named by `--as`. A name that
already belongs to someone is refused with whose it is: every reader
finds a person by name, so a second file would split one person in two.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import (  # noqa: E402
    DEFAULT_SHARED_BUCKET,
    brain_path_for,
    load_persons_from_vault,
    refresh_vault_if_stale,
    update_memory,
    vault_path_for,
)

from stack.cards.commits import commit_subject  # noqa: E402
from stack.people import (  # noqa: E402
    Person,
    load_people,
    person_id,
    person_path,
    render_person,
    resolve,
)
from stack.vault import slug  # noqa: E402


HELP = "Show the family's people, or add one"

VERBS = ("show", "add")

USAGE = """\
stack memory person show <name>                     a person's page, by any name
stack memory person add "<full name>" [--alias <name>]... [--id <id>] --as <person>
                                                    a person the family knows, without an account"""

_FRONTMATTER = re.compile(r"^---\n.*?\n---\n", re.DOTALL)
_GEN_MARKER = re.compile(r"<!--\s*(?:begin|end):\s*generated\s*-->\n?")
_CITE = re.compile(r"\[\d+(?:,\s*\d+)*\]")


class Refusal(ValueError):
    """An answer that tells the caller what to do instead."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stack memory person", add_help=False)
    verbs = parser.add_subparsers(dest="verb")

    show = verbs.add_parser("show", add_help=False)
    show.add_argument("name", nargs="+", help="person slug, canonical name, or synonym")
    show.add_argument("--vault", default=None, metavar="PATH",
                      help="vault path override (defaults to <data_dir>/memory/vault/)")
    show.add_argument("--no-refresh", action="store_true",
                      help="skip the upstream-HEAD check before reading")

    add = verbs.add_parser("add", add_help=False)
    add.add_argument("name", nargs="+")
    add.add_argument("--alias", action="append", default=[])
    add.add_argument("--id", dest="pid", default="")
    add.add_argument("--as", dest="actor", default="")
    return parser


def run(args, stacklet, config):
    argv = list(args or [])
    if not argv:
        print(USAGE)
        return {"verbs": list(VERBS)}
    if argv[0] not in VERBS:
        return {"error": f"to read a person: stack memory person show {' '.join(argv)}"}
    try:
        ns = _parser().parse_args(argv)
    except SystemExit:
        return {"error": f"usage:\n{USAGE}"}
    try:
        if ns.verb == "add":
            return add(config, ns)
        return show(config, ns)
    except Refusal as e:
        return {"error": str(e)}


# ── Adding ───────────────────────────────────────────────────────────────

def add(config, ns) -> dict:
    """Write a person's file and commit it as the person who added them."""
    if not ns.actor.strip():
        raise Refusal("say who adds them: add --as <person>, the family member it is for")
    vault = _vault(config)
    if vault is None:
        return {"error": "no vault found; is the memory stacklet installed?"}
    refresh_vault_if_stale(vault)
    bucket = _shared_bucket(config)
    known = load_people(vault, bucket) or []
    person = _new_person(" ".join(ns.name), ns.alias, ns.pid, known)
    actor = _actor(ns.actor)
    path = person_path(person.id, bucket)

    def _create(prior: str) -> str:
        if (prior or "").strip():
            raise ValueError(f"{path} already exists")
        return render_person(person)
    result = update_memory(config, path, _create, actor=actor,
                           message=lambda before, after: commit_subject("people", f"{actor} added {person.name}"))
    if "error" in result:
        return result
    print(f"added {person.name} as {path}")
    return {"ok": True, "committed": result.get("committed", False),
            "person": person.id, "path": path}


def _new_person(name: str, aliases: list[str], pid: str, known: list[Person]) -> Person:
    """The person to add, or a `Refusal` naming the clash."""
    name = name.strip()
    aliases = [a.strip() for a in aliases if a.strip()]
    for candidate in (name, *aliases):
        if (owner := resolve(candidate, known)) is not None:
            raise Refusal(f"{candidate!r} is already a name of {owner.name} ({owner.id})")
    new_id = pid.strip() or slug(name.split()[0])
    if person_id(new_id) != new_id:
        raise Refusal(f"{new_id!r} is not an id: lowercase letters, digits and dashes")
    if (owner := next((p for p in known if p.id == new_id), None)) is not None:
        raise Refusal(f"{new_id!r} is taken by {owner.name}; give this person another with --id")
    return Person(new_id, name, tuple(aliases))


# ── Showing ──────────────────────────────────────────────────────────────

def _roots(config, override: str | None) -> list[Path]:
    """Where a person page can live, most authoritative first.

    Two trees hold `<slug>/about.md` and they mean different things.
    memory is source: what the household actually wrote. The brain is a
    projection the wiki pass generates and the installer purges out of
    source again ("purged 1 generated source page(s)"), so a generated
    profile exists *only* there.

    Reading source alone therefore answered "no profile" for every
    member who had one, which is the state this command shipped in.
    Source is still checked first: a hand-curated page beats rebuildable
    output.
    """
    if override:
        return [Path(override)]
    data_dir = config.get("data_dir") if config else None
    if not data_dir:
        return []
    base = Path(data_dir)
    return [vault_path_for(base), brain_path_for(base)]


def _clean_profile(text: str) -> str:
    text = _FRONTMATTER.sub("", text)
    text = _GEN_MARKER.sub("", text)
    text = _CITE.sub("", text)
    return text.strip()


def _resolve_person(vault: Path, query: str):
    q = query.strip().lower()
    for person in load_persons_from_vault(vault):
        names = [person.slug, person.canonical, *person.synonyms]
        if any(str(name).strip().lower() == q for name in names):
            return person
    return None


def _known_people(vault: Path) -> list[str]:
    return [p.slug for p in load_persons_from_vault(vault)]


def show(config, ns) -> dict:
    """Print a person's page, found by any name they go by."""
    name = " ".join(ns.name).strip()

    roots = [r for r in _roots(config, ns.vault) if r.exists()]
    if not roots:
        return {"error": "no vault found - is the memory stacklet installed?"}
    if not ns.no_refresh:
        refresh_vault_if_stale(roots[0])

    fallback = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    path = None
    for root in roots:
        person = _resolve_person(root, name)
        candidate = root / (person.slug if person else fallback) / "about.md"
        if candidate.exists():
            path = candidate
            break
    if path is None:
        # Names from every root, so the hint lists everyone the household
        # could have asked for, not just those in the first tree checked.
        known = sorted({slug for root in roots for slug in _known_people(root)})
        hint = ("  people: " + ", ".join(known)) if known else ""
        return {"error": f"no profile for person {name!r}\n{hint}".rstrip()}

    text = _clean_profile(path.read_text(encoding="utf-8"))
    rel = f"{path.parent.name}/about.md"
    print(f"Source: vault/{rel}\n")
    print(text)
    return {"ok": True, "person": path.parent.name, "path": rel}


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
