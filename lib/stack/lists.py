"""The family's lists: one Markdown file each, one checkbox per item.

A list lives at `<bucket>/lists/<slug>.md`. The file name is the slug;
the frontmatter says what the list is and, for a store, the order of
its sections, which is the order the family walks through the store:

    ---
    type: list
    title: Einkaufsliste
    description: Was wir im Supermarkt brauchen
    topics:
      - einkaeufe-besorgungen
    sections:
      - Gemüse & Obst
      - Milchprodukte & Eier
    ---

    ## Gemüse & Obst
    - [ ] Äpfel

    ## Milchprodukte & Eier
    - [ ] Joghurt [Marge](../people/marge.md) 📅 2026-10-14

Every item, on a list or in a record's summary, is the same line: a
checkbox, what the family wrote, then the fields at the end. A link to
someone in `<bucket>/people/` assigns the item to them, any other link
into the vault names the record the item came from, `📅` is the day it
is due and `✅` the day it was ticked. Those two are the Obsidian Tasks
plugin's fields, so its queries work on these files as they are. Links
are relative Markdown links, the one syntax Obsidian, Forgejo and the
wiki all follow (vault-format.md §1).

Only the trailing run of links and dates are fields. A link inside the
sentence ("Call [Homer](...) about the car") stays part of the text,
and an unknown field (another Tasks emoji) ends the run, so nothing the
family wrote is reinterpreted or dropped.

Edits are line edits. A family member may write anything else into a
list file in Obsidian or Forgejo, a note under a heading or a sub-item,
and inserting an item touches only the lines it adds.

Pure functions, no I/O: the memory stacklet reads and commits the
files. Lives in the framework because the readers span stacklets: the
archivist files action items, the memory commands change lists, the
wiki renders them.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field, replace

from .frontmatter import FrontmatterError, dump, parse, validate
from .people import person_path

LISTS_DIR = "lists"
ARCHIVE_DIR = "archive"

DUE = "📅"
DONE = "✅"

_ISO_DAY = r"\d{4}-\d{2}-\d{2}"

# A checkbox line, open or done, at any indentation: Obsidian keeps
# sub-items as indented lines. Group 3 is the status character, " " for
# open and "x" for done; Obsidian Tasks has more (`-` cancelled, `/` in
# progress), which are kept as they are.
_ITEM = re.compile(r"^(\s*)([-*+]) \[(.)\] (.*?)\s*$")

# The trailing fields, matched one at a time from the end of the line.
_TRAILING_LINK = re.compile(r"\s*\[([^\]]+)\]\(([^)\s]+)\)$")
_TRAILING_DUE = re.compile(rf"\s*{DUE}\s*({_ISO_DAY})$")
_TRAILING_DONE = re.compile(rf"\s*{DONE}\s*({_ISO_DAY})$")

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")

# `<bucket>/people/<id>.md`: a declared person (stack.people).
_PERSON_PATH = re.compile(r"^[^/]+/people/([^/]+)\.md$")


class ListError(ValueError):
    """A file that is not a list, or whose frontmatter does not parse."""


# ── Items ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Link:
    """A link in an item's fields: what it reads as, and the vault path it names.

    `target` is relative to the vault root (`family/people/homer.md`),
    so an item keeps its links when it moves to another file.
    """

    label: str
    target: str

    @property
    def person(self) -> str:
        """The id of the person the link names, or "" for a record."""
        m = _PERSON_PATH.match(self.target)
        return m[1] if m else ""


@dataclass(frozen=True)
class Item:
    """One checkbox line.

        >>> item = parse_item("- [ ] Joghurt [Marge](../people/marge.md) 📅 2026-10-14",
        ...                   path="family/lists/einkaufsliste.md")
        >>> item.text, item.people, item.due
        ('Joghurt', ('marge',), '2026-10-14')

    `section` is the heading the item sits under ("" above the first
    one) and `line` its line number in the body it was read from; both
    are set by `read_list`, not by `parse_item`.
    """

    text: str
    done: bool = False
    links: tuple[Link, ...] = ()
    due: str = ""
    done_on: str = ""
    section: str = ""
    status: str = " "
    indent: str = ""
    line: int = -1

    @property
    def people(self) -> tuple[str, ...]:
        """The ids of the people the item is assigned to, in the order written."""
        return tuple(link.person for link in self.links if link.person)

    @property
    def sources(self) -> tuple[str, ...]:
        """The vault paths of the records the item came from."""
        return tuple(link.target for link in self.links if not link.person)


def person_link(person_id: str, label: str, *, bucket: str) -> Link:
    """The link that assigns an item to a declared person."""
    return Link(label, person_path(person_id, bucket))


def action_item(ai, *, source: Link | None = None) -> Item | None:
    """An action item the classifier extracted, as a list item; None for an empty one.

    The classifier answers `{"action": ..., "due": ...}` or a plain
    string. A due date that is not a day ("next week") stays in the text,
    because `📅` takes only a day. `source` links the record it came from.
    """
    if isinstance(ai, str):
        action, due = ai.strip(), ""
    elif isinstance(ai, dict):
        action = str(ai.get("action") or "").strip()
        due = str(ai.get("due") or "").strip()
    else:
        return None
    if not action:
        return None
    if due.lower() in ("null", "none", "n/a"):
        due = ""
    if due and not re.fullmatch(_ISO_DAY, due):
        action, due = f"{action} ({due})", ""
    return Item(" ".join(action.split()), due=due, links=(source,) if source else ())


def parse_item(line: str, *, path: str) -> Item | None:
    """The item on a checkbox line, or None for any other line.

    `path` is the vault path of the file the line is in, so a relative
    link resolves to the vault path it names.
    """
    m = _ITEM.match(line)
    if not m:
        return None
    indent, status, rest = m[1], m[3], m[4]
    links: list[Link] = []
    due = done_on = ""
    while True:
        if (f := _TRAILING_DUE.search(rest)) and not due:
            due = f[1]
        elif (f := _TRAILING_DONE.search(rest)) and not done_on:
            done_on = f[1]
        elif (f := _TRAILING_LINK.search(rest)) and (target := _vault_target(f[2], path)):
            links.insert(0, Link(f[1], target))
        else:
            break
        rest = rest[:f.start()]
    return Item(text=rest.strip(), done=status in "xX", links=tuple(links),
                due=due, done_on=done_on, status=status, indent=indent)


def format_item(item: Item, *, path: str) -> str:
    """The checkbox line for an item in the file at vault path `path`."""
    status = item.status if item.status not in " xX" else ("x" if item.done else " ")
    parts = [f"{item.indent}- [{status}] {item.text}"]
    parts += [f"[{link.label}]({_relative(link.target, path)})" for link in item.links]
    if item.due:
        parts.append(f"{DUE} {item.due}")
    if item.done_on:
        parts.append(f"{DONE} {item.done_on}")
    return " ".join(parts)


def _vault_target(href: str, path: str) -> str:
    """The vault path a link names, or "" for a link out of the vault.

    A link to a web page (an online shop) stays part of the item's text.
    `/family/...` is the wiki's way of writing a vault path and is read
    the same as a relative one.
    """
    if "://" in href or href.startswith(("mailto:", "#")) or not href.endswith(".md"):
        return ""
    joined = href.lstrip("/") if href.startswith("/") \
        else posixpath.join(posixpath.dirname(path), href)
    target = posixpath.normpath(joined)
    return "" if target.startswith("..") else target


def _relative(target: str, path: str) -> str:
    return posixpath.relpath(target, posixpath.dirname(path) or ".")


# ── Lists ────────────────────────────────────────────────────────────────

@dataclass
class FamilyList:
    """What a list file says: its frontmatter and its items, in file order."""

    slug: str
    title: str
    description: str = ""
    topics: list[str] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)
    recurring: bool = False

    @property
    def open_items(self) -> list[Item]:
        return [i for i in self.items if not i.done]


_TOPIC_LIST_DESCRIPTION = {
    "de": "Was für {title} noch zu tun ist",
    "en": "What is still to do for {title}",
}


def topic_list_description(title: str, language: str) -> str:
    """The description of a list started for a topic, in the household language.

    A list the archivist or the move from `todos.md` starts has no
    description a person wrote, and a list picked by its name alone
    gets items that belong elsewhere.
    """
    code = (language or "").strip().lower()[:2]
    return _TOPIC_LIST_DESCRIPTION.get(code, _TOPIC_LIST_DESCRIPTION["en"]).format(title=title)


def list_path(slug: str, *, bucket: str) -> str:
    """The vault path of the list `slug` in `bucket`."""
    return f"{bucket}/{LISTS_DIR}/{slug}.md"


def read_list(text: str, *, path: str) -> FamilyList:
    """The list in a file's text, or `ListError` when it is not one."""
    try:
        fm = parse(text)
    except FrontmatterError as e:
        raise ListError(f"{path}: frontmatter does not parse: {e}") from e
    if fm.get("type") != "list":
        raise ListError(f"{path}: not a list (type: {fm.get('type') or 'missing'})")
    errors = validate(fm)
    if errors:
        raise ListError(f"{path}: {'; '.join(errors)}")

    items: list[Item] = []
    section = ""
    for n, line in enumerate(_body(text).splitlines()):
        if h := _HEADING.match(line):
            section = h[2] if len(h[1]) > 1 else ""
        elif item := parse_item(line, path=path):
            items.append(replace(item, section=section, line=n))
    return FamilyList(
        slug=posixpath.splitext(posixpath.basename(path))[0],
        title=str(fm.get("title") or ""),
        description=str(fm.get("description") or ""),
        topics=list(fm.get("topics") or []),
        sections=list(fm.get("sections") or []),
        items=items,
        recurring=fm.get("recurring") is True,
    )


def new_list(title: str, *, description: str = "", topics: list[str] | None = None,
             sections: list[str] | None = None, recurring: bool = False) -> str:
    """The text of a new, empty list file.

    A recurring list (a packing list, the weekly chores) keeps its ticked
    items, so it can be reopened whole; every other list hands them to
    its archive the night after.
    """
    fm = {"type": "list", "title": title, "description": description,
          "topics": list(topics or []), "sections": list(sections or [])}
    if recurring:
        fm["recurring"] = True
    return f"---\n{dump(fm)}\n---\n"


# ── Changing a list ──────────────────────────────────────────────────────

def set_details(text: str, *, title: str | None = None, description: str | None = None,
                topics: list[str] | None = None, sections: list[str] | None = None) -> str:
    """The list text with its frontmatter changed; `None` leaves a field as it is.

    New `sections` are the new walking order: the body's section blocks
    are put in that order, a block the order does not name after them.
    Everything inside a block stays as it is.
    """
    fm = parse(text)
    for key, value in (("title", title), ("description", description),
                       ("topics", topics), ("sections", sections)):
        if value is not None:
            fm[key] = value
    lines = _body(text).splitlines()
    if sections is not None:
        lines = _in_walking_order(lines, sections)
    return _join(f"---\n{dump(fm)}\n---\n", lines)


def _in_walking_order(lines: list[str], order: list[str]) -> list[str]:
    """Body lines with their `##` blocks sorted by `order`, the preamble first."""
    starts = [n for n, ln in enumerate(lines) if (h := _HEADING.match(ln)) and len(h[1]) > 1]
    if not starts:
        return lines
    preamble = lines[:starts[0]]
    blocks = [lines[a:b] for a, b in zip(starts, [*starts[1:], len(lines)])]
    rank = {name.casefold(): n for n, name in enumerate(order)}

    def position(block: list[str]) -> int:
        return rank.get(_HEADING.match(block[0])[2].casefold(), len(rank))
    out = _trimmed(preamble)
    for block in sorted(blocks, key=position):
        out += ([""] if out else []) + _trimmed(block)
    return out


def _trimmed(lines: list[str]) -> list[str]:
    """`lines` without the blank lines at their end."""
    return lines[:_after_last_content(lines, 0, len(lines))]


# ── Adding an item ───────────────────────────────────────────────────────

def insert_item(text: str, item: Item, *, path: str) -> str:
    """The list text with `item` added at the end of its section.

    A section that has no heading yet gets one, placed in the order the
    list's `sections` give; a section the list does not name goes after
    all the others. An item without a section goes above the first
    heading, where nothing has to be named to find it. Every other line
    stays as it is.
    """
    head, body = _split(text)
    lines = body.splitlines()
    line = format_item(item, path=path)
    sections = list(parse(text).get("sections") or [])

    if not item.section:
        first = next((n for n, ln in enumerate(lines) if _HEADING.match(ln)), len(lines))
        at = _after_last_content(lines, 0, first)
        return _join(head, lines[:at] + [line] + _gap_before(lines, at))

    start = _heading_line(lines, item.section)
    if start is not None:
        end = _section_end(lines, start)
        at = _after_last_content(lines, start + 1, end)
        return _join(head, lines[:at] + [line] + lines[at:])

    at = _new_heading_position(lines, item.section, sections)
    block = [f"## {item.section}", line]
    before = [""] if at > 0 and lines[at - 1].strip() else []
    after = [""] if at < len(lines) else []
    return _join(head, lines[:at] + before + block + after + lines[at:])


def _heading_line(lines: list[str], section: str) -> int | None:
    want = section.casefold()
    for n, ln in enumerate(lines):
        h = _HEADING.match(ln)
        if h and len(h[1]) > 1 and h[2].casefold() == want:
            return n
    return None


def _section_end(lines: list[str], start: int) -> int:
    """The line after the section that starts at heading `start`."""
    for n in range(start + 1, len(lines)):
        if _HEADING.match(lines[n]):
            return n
    return len(lines)


def _after_last_content(lines: list[str], start: int, end: int) -> int:
    """The position after the last non-blank line in `lines[start:end]`."""
    at = end
    while at > start and not lines[at - 1].strip():
        at -= 1
    return at


def _gap_before(lines: list[str], at: int) -> list[str]:
    """The lines from `at` on, with one blank line before a heading that follows."""
    rest = lines[at:]
    if rest and _HEADING.match(rest[0]):
        return [""] + rest
    return rest


def _new_heading_position(lines: list[str], section: str, order: list[str]) -> int:
    """Where a new heading for `section` goes: before the next section in `order`."""
    later = [s.casefold() for s in _after(order, section)]
    for n, ln in enumerate(lines):
        h = _HEADING.match(ln)
        if h and len(h[1]) > 1 and h[2].casefold() in later:
            return n
    return len(lines)


def _after(order: list[str], section: str) -> list[str]:
    """The sections that come after `section` in `order`; [] if it is not there."""
    folded = [s.casefold() for s in order]
    if section.casefold() not in folded:
        return []
    return order[folded.index(section.casefold()) + 1:]


# ── Changing an item ─────────────────────────────────────────────────────

def find(lst: FamilyList, name: str) -> list[Item]:
    """The items `name` means: the one with exactly that text, else every one containing it.

    Case does not count. Several matches are the caller's to resolve,
    by asking for more of the item's words; a guess would tick the
    wrong thing.
    """
    wanted = name.strip().casefold()
    exact = [i for i in lst.items if i.text.casefold() == wanted]
    return exact or [i for i in lst.items if wanted and wanted in i.text.casefold()]


def set_done(text: str, item: Item, *, done: bool, today: str, path: str) -> str:
    """The list text with `item` ticked on `today`, or opened again."""
    changed = replace(item, done=done, status="x" if done else " ",
                      done_on=today if done else "")

    def edit(lines: list[str]) -> None:
        lines[item.line] = format_item(changed, path=path)
    return _edit_body(text, edit)


def remove_item(text: str, item: Item) -> str:
    """The list text without `item`'s line, and without a heading it leaves empty."""
    def edit(lines: list[str]) -> None:
        del lines[item.line]
        start = next((n for n in range(item.line - 1, -1, -1) if _HEADING.match(lines[n])), None)
        if start is None:
            return
        end = _section_end(lines, start)
        if not any(ln.strip() for ln in lines[start + 1:end]):
            del lines[start:end]
    return _edit_body(text, edit)


def relink(text: str, old: str, new: str, *, path: str) -> str:
    """The list text with every link to vault path `old` pointing at `new`.

    A record is renamed when a refile gives it a better title; the items
    it put on a list keep linking to it.
    """
    for item in read_list(text, path=path).items:
        if old in (link.target for link in item.links):
            moved = replace(item, links=tuple(replace(link, target=new) if link.target == old
                                              else link for link in item.links))

            def edit(lines: list[str], at: int = item.line, line: str = format_item(moved, path=path)) -> None:
                lines[at] = line
            text = _edit_body(text, edit)
    return text


# ── The archive ──────────────────────────────────────────────────────────
#
# A ticked item stays on its list for the rest of the day ("did we get
# eggs?") and moves to the list's archive the night after, one file per
# month: `<bucket>/lists/archive/<slug>/<YYYY-MM>.md`. The list then holds
# only what is still open. The archive keeps the line as it was, section
# included, and is where the household's known items come from.

def archive_path(slug: str, month: str, *, bucket: str) -> str:
    """The archive file for the items of list `slug` ticked in `month` (`YYYY-MM`)."""
    return f"{bucket}/{LISTS_DIR}/{ARCHIVE_DIR}/{slug}/{month}.md"


def stamp_ticks(text: str, *, path: str, today: str) -> str:
    """The list text with today's date on every ticked item that has none.

    An item ticked by hand in Obsidian or Forgejo carries no `✅` date;
    stamping it on the first night gives it the day after on the list,
    like any other tick.
    """
    for item in read_list(text, path=path).items:
        if item.done and not item.done_on:
            text = set_done(text, item, done=True, today=today, path=path)
    return text


def archive_ticked(text: str, archives: dict[str, str], *, path: str, today: str,
                   bucket: str) -> tuple[str, dict[str, str], list[Item]]:
    """Move the items ticked before `today` from a list into its archive.

    `archives` maps an archive path to its current text ("" or missing
    for a month with no file yet). Returns the list text, the archive
    texts that changed, and the items moved. An item ticked today stays,
    and a recurring list keeps every tick until it is reset.
    """
    lst = read_list(text, path=path)
    if lst.recurring:
        return text, {}, []
    moving = [i for i in lst.items if i.done and i.done_on and i.done_on < today]
    changed: dict[str, str] = {}
    for item in moving:
        target = archive_path(lst.slug, item.done_on[:7], bucket=bucket)
        current = changed.get(target) or archives.get(target) or \
            new_list(f"{lst.title}, {item.done_on[:7]}", sections=lst.sections)
        changed[target] = insert_item(current, item, path=target)
    # Last line first, so removing one item never moves the lines of the next.
    for item in sorted(moving, key=lambda i: i.line, reverse=True):
        text = remove_item(text, item)
    return text, changed, moving


# ── Known items ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Known:
    """Where an item was last seen: the list, its section and its spelling."""

    slug: str
    section: str
    text: str


def known_items(lists: list[tuple[str, FamilyList]]) -> dict[str, Known]:
    """Every item the household has had on these lists, by its text in any case.

    `lists` pairs a list's slug with a list or one of its archive files.
    An item open on a list now is where it lives; otherwise the most
    recent tick wins, so an item bought somewhere else lately follows.
    """
    seen: dict[str, tuple[str, Known]] = {}
    for slug, lst in lists:
        for item in lst.items:
            rank = "9999-99-99" if not item.done else (item.done_on or "0000-00-00")
            key = item.text.casefold()
            if key not in seen or rank > seen[key][0]:
                seen[key] = (rank, Known(slug, item.section, item.text))
    return {key: known for key, (_, known) in seen.items()}


# ── File text ────────────────────────────────────────────────────────────

def _split(text: str) -> tuple[str, str]:
    """(frontmatter block with its delimiters and blank line, body)."""
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---\n", 4)
    if end < 0:
        return "", text
    head = text[:end + len("\n---\n")]
    return head, text[len(head):].lstrip("\n")


def _body(text: str) -> str:
    return _split(text)[1]


def _edit_body(text: str, edit) -> str:
    """`text` with its body lines changed in place by `edit`."""
    head, body = _split(text)
    lines = body.splitlines()
    edit(lines)
    return _join(head, lines)


def _join(head: str, lines: list[str]) -> str:
    body = "\n".join(lines).strip("\n")
    if not body:
        return head
    return f"{head}\n{body}\n" if head else f"{body}\n"
