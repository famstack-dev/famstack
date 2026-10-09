"""The family's people: one declared file each.

Every person the family knows has a file in the shared bucket,
`people/<id>.md`, in the vault's frontmatter format:

    ---
    type: person
    title: Maggie Simpson
    aliases:
      - Margaret
    ---

The file name is the id. `title` is the full name, `aliases` the other
names the family uses, and `account` the chat user name of a person who
has one. The schema lives with the others in `stack.frontmatter`
(`DECLARED_SCHEMAS`), and a file that breaks it is reported by path
and left out; nothing here guesses at what a broken file meant.

A member of the household is a person with an account. That is the
first, opinionated guess at membership (docs/design/brain/domain-model.md,
Member); it is derived, never a field the family sets. A person
without an account (a baby) is still known by every name: the
classifier, the transcription vocabulary and the diary read all
people, the wiki's member list reads the members.

The readers return `None` while no person file exists, so a caller can
keep its previous behaviour until `stack up memory` has written them.

Pure functions and file reads. No Forgejo, no git: the memory stacklet
writes the files (`ensure_people` in its library). Lives in the
framework because the readers span stacklets: the wiki, the diary, the
archivist, transcription.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from .frontmatter import FrontmatterError, dump, parse, validate
from .vault import DEFAULT_SHARED_BUCKET, slug

PEOPLE_DIR = "people"

# An id as the vault uses it for buckets and Matrix for localparts. A
# file name that is not one is slugged ("Grampa Simpson.md" is
# `grampa-simpson`), so a file made in Obsidian still names a person.
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

Report = Callable[[str], None]


@dataclass(frozen=True)
class Person:
    """One person the family declared.

        >>> p = Person("maggie", "Maggie Simpson", ("Margaret",))
        >>> p.all_names()
        ['Maggie Simpson', 'Maggie', 'Margaret']
        >>> p.member
        False
    """

    id: str
    name: str
    aliases: tuple[str, ...] = field(default_factory=tuple)
    account: str = ""

    @property
    def member(self) -> bool:
        """Whether the person belongs to the household: they have an account."""
        return bool(self.account)

    @property
    def first_name(self) -> str:
        return self.name.split()[0] if self.name.split() else self.id

    def all_names(self) -> list[str]:
        """Full name, first name, then the aliases; each once, in that order."""
        out: list[str] = []
        for name in (self.name, self.first_name, *self.aliases):
            if name and name.lower() not in {n.lower() for n in out}:
                out.append(name)
        return out


class PersonError(ValueError):
    """A person file that does not follow the format; the message says how."""


# ── Reading ──────────────────────────────────────────────────────────────

def people_dir(root: Path, shared_bucket: str = DEFAULT_SHARED_BUCKET) -> Path:
    """Where the person files live under a vault or brain working copy."""
    return Path(root) / shared_bucket / PEOPLE_DIR


def person_path(person_id: str, shared_bucket: str = DEFAULT_SHARED_BUCKET) -> str:
    """The repo-relative path of one person's file."""
    return f"{shared_bucket}/{PEOPLE_DIR}/{person_id}.md"


def person_id(file_stem: str) -> str:
    """The id a person file's name gives."""
    return file_stem if _ID.match(file_stem) else slug(file_stem)


def read_person(file_stem: str, text: str) -> Person:
    """The person one file declares, or `PersonError` saying what is wrong."""
    try:
        fm = parse(text)
    except FrontmatterError as e:
        raise PersonError(f"frontmatter does not parse: {e}") from e
    if fm.get("type") != "person":
        raise PersonError("not a person file: it needs `type: person`")
    if fm.get("generated") is True:
        raise PersonError("a generated page does not belong in people/")
    if errors := validate(fm):
        raise PersonError("; ".join(errors))
    return Person(
        id=person_id(file_stem),
        name=str(fm["title"]).strip(),
        aliases=tuple(str(a).strip() for a in fm.get("aliases") or [] if str(a).strip()),
        account=str(fm.get("account") or "").strip(),
    )


def load_people(
    root: Path | None,
    shared_bucket: str = DEFAULT_SHARED_BUCKET,
    *,
    report: Report | None = None,
) -> list[Person] | None:
    """Every person declared under `root`, members first, or `None` if none is.

    A file that cannot be read as a person is passed to `report` with
    its path and the reason, and left out. `None` covers no folder, an
    empty folder and a folder of broken files alike: the caller keeps
    its previous behaviour rather than act on nobody.
    """
    if root is None:
        return None
    folder = people_dir(root, shared_bucket)
    people: dict[str, Person] = {}
    for path in sorted(folder.glob("*.md")):
        rel = f"{shared_bucket}/{PEOPLE_DIR}/{path.name}"
        try:
            person = read_person(path.stem, path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as e:
            _report(report, f"{rel}: cannot be read ({e})")
            continue
        except PersonError as e:
            _report(report, f"{rel}: {e}")
            continue
        if person.id in people:
            _report(report, f"{rel}: {person.id!r} is declared twice; the first file counts")
            continue
        people[person.id] = person
    if not people:
        return None
    return sorted(people.values(), key=lambda p: (not p.member, p.name.lower()))


def find_people(
    roots: Iterable,
    shared_bucket: str = DEFAULT_SHARED_BUCKET,
    *,
    report: Report | None = None,
) -> list[Person] | None:
    """The people from the first of `roots` that declares any.

    A process often sees two copies of the vault: the working copy the
    family's edits land in, and the brain the curator mirrors it to.
    Callers list the fresher one first. Empty and None roots are skipped.
    """
    for root in roots:
        if root and (people := load_people(Path(root), shared_bucket, report=report)) is not None:
            return people
    return None


def members(people: list[Person]) -> list[Person]:
    """The people who belong to the household."""
    return [p for p in people if p.member]


def resolve(name: str, people: list[Person]) -> Person | None:
    """The person a name means, by any name they go by, or None.

        >>> resolve("Margaret", [Person("maggie", "Maggie Simpson", ("Margaret",))]).id
        'maggie'
    """
    wanted = name.strip().lower()
    for person in people:
        if wanted == person.id or wanted in (n.lower() for n in person.all_names()):
            return person
    return None


def _report(report: Report | None, message: str) -> None:
    if report is not None:
        report(message)


# ── Writing ──────────────────────────────────────────────────────────────

def people_from_accounts(users: list[dict]) -> list[Person]:
    """A person for everyone with an account, as `users.toml` lists them.

    The id follows `users.user_id` (an explicit `id`, else the first name
    lowercased), so it is the person's Matrix localpart and their bucket.
    Entries without a name are skipped. Bots never appear in `users.toml`.
    """
    people: list[Person] = []
    seen: set[str] = set()
    for user in users:
        name = str(user.get("name") or "").strip()
        if not name:
            continue
        account = str(user.get("id") or name.split()[0].lower())
        if account in seen:
            continue
        seen.add(account)
        people.append(Person(account, name, account=account))
    return people


def render_person(person: Person) -> str:
    """A person's file as famstack first writes it."""
    fm = dump({
        "type": "person",
        "title": person.name,
        "aliases": list(person.aliases),
        "account": person.account,
    })
    return f"---\n{fm}\n---\n\n# {person.name}\n"
