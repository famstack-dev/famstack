"""Rename one topic of a bucket: `topic rename <old> <new> [<title>] [--bucket b] [--dry-run]`.

`stack memory topic rename` on the host sends the rename here, to memory's
side of the bot runner, which reaches the curator on the stack network
(ADR-019: the command is a client, the work is the stacklet's). It reads
the memory through the curator, plans the rename, commits it as one commit
by the memory bot, and prints a JSON report on stdout for the host to show.
`--dry-run` plans and reports, and writes nothing.

`plan` is the pure half: given the memory's files as the curator has them,
it answers the changes of the one commit (`memory_api.put` and `remove`),
what moves where, and the links it leaves alone.

    planned = plan(files, bucket="homer", shared="family", old="nahprojekte",
                   new="naehprojekte", title="Nähprojekte")
    planned.changes     # every put and remove, made from the versions read
    planned.moved       # [(old path, new path), ...]
    planned.left        # [(card, link)]: links into the old folder it did not rewrite
    planned.kept        # a list left with another bucket's topic of the same id, or ""

What a topic of a bucket is:

- its folder `<bucket>/<id>/`, with its card `topic.md` and what was filed
  into it; every file moves, and the card keeps its `card_id`;
- its list and the list's archive, which live in the shared bucket for
  every topic, a personal one too (the archivist starts them there):
  `<shared>/lists/<id>.md` and `<shared>/lists/archive/<id>/`. They move,
  the list's `topics` carry the new id, and every list item that links a
  moved card links its new path (`stack.lists.relink`);
- its id in the `tags` of the bucket's cards and the `topics` of its lists.

A topic whose id another bucket's topic also has shares its list with
that topic. The list stays then, with the other topic, and `kept` names
it. Any list of the shared bucket whose items link a moved card, a kept
one too, links the card's new path.

The topic card gets the new title, when one is given, and lists the old id
in `former_topic_ids`. That is how a room bound to the old id, or named the
way the old id was derived, finds the topic after the rename
(`stack.cards.lookup.renamed_topic`). Nothing else changes outside the
bucket: topic ids are unique within a bucket only. A link someone wrote into the
old folder by hand is named in `left`, not rewritten, because it can be
anywhere in a text a person wrote.

It is the admin's decision, made on purpose: nothing renames a topic on
its own, and a new topic is still named the way the archivist names it.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import sys
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field

from stack.cards import FORMER_TOPIC_IDS
from stack.cards.links import topic_card
from stack.frontmatter import FrontmatterError, parse, with_field
from stack.lists import ARCHIVE_DIR, LISTS_DIR, ListError, list_path, read_list, relink
from stack.ontology import Ontology
from stack.people import PEOPLE_DIR
from stack.vault import CAPTURE_FOLDERS, RESERVED_TOPIC_IDS, TOPIC_CARD, topic_card_path

from ...lib import load_seed_ontology
from ...memory_api import MEMORY_BOT, CuratorDown, File, MemoryAPI, Refused, put, remove
from ..diary import DIARY_DIR

# A topic id is a folder name and a tag: lowercase ASCII letters and digits
# joined by hyphens, with at least one letter, as the archivist derives them.
TOPIC_ID = re.compile(r"^(?=[a-z0-9-]*[a-z])[a-z0-9]+(?:-[a-z0-9]+)*$")

# Names a topic is never renamed from or to: the bucket's own folders, the
# archivist's reserved names and the folders a bucket files into, the
# people and the diary among them.
NOT_TOPIC_IDS = RESERVED_TOPIC_IDS | set(CAPTURE_FOLDERS) | {PEOPLE_DIR, DIARY_DIR}

# A markdown link's target: `[label](target)`.
_LINK = re.compile(r"\]\(([^)\s]+)\)")
# The first heading of a card's body, which shows its title.
_HEADING = re.compile(r"^# .*$", re.M)


USAGE = ("usage: stack memory topic rename <old id> <new id> [<title>] "
         "[--bucket <bucket>] [--dry-run]")


class Refusal(ValueError):
    """A rename that cannot be made as asked; nothing was written."""


@dataclass
class Planned:
    """The one commit a rename is, and what it leaves for a person."""

    changes: list[dict] = field(default_factory=list)
    moved: list[tuple[str, str]] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    left: list[tuple[str, str]] = field(default_factory=list)
    kept: str = ""


# ── The command ──────────────────────────────────────────────────────────

async def run(llm, argv: list[str]) -> int:
    """`topic rename ...`: plan, commit unless `--dry-run`, print the report."""
    if argv[:1] != ["rename"]:
        print(USAGE, file=sys.stderr)
        return 2
    try:
        old, new, title, bucket, dry_run = _arguments(argv[1:])
    except Refusal as e:
        return _report({"error": str(e)})
    memory = MemoryAPI.from_container(os.environ)
    try:
        everything = {f.path: f for f in memory.files("")}
        planned = plan(everything, bucket=bucket, shared=_shared_bucket(), old=old, new=new,
                       title=title,
                       ontology_topics=_ontology_topics(everything.get("ontology.toml")))
        report = {"ok": True, "committed": False, "bucket": bucket, "old": old, "new": new,
                  "moved": [list(m) for m in planned.moved], "changed": planned.changed,
                  "links_left": [{"path": p, "link": link} for p, link in planned.left],
                  "list_kept": planned.kept}
        if dry_run:
            return _report(report)
        written = memory.commit(planned.changes, by=MEMORY_BOT, kind="rename",
                                title=f"{bucket}/{old} → {bucket}/{new}", body=_body(planned))
    except (Refusal, Refused, CuratorDown, ValueError) as e:
        return _report({"error": str(e)})
    return _report({**report, "committed": True, "commit": written.commit,
                    "pending": written.pending, "mirrored": written.mirrored})


def _arguments(argv: list[str]) -> tuple[str, str, str, str, bool]:
    """(old, new, title, bucket, dry run) from the words after `rename`."""
    dry_run = "--dry-run" in argv
    argv = [a for a in argv if a != "--dry-run"]
    bucket = _shared_bucket()
    if "--bucket" in argv:
        at = argv.index("--bucket")
        if at + 1 >= len(argv):
            raise Refusal(USAGE)
        bucket = argv[at + 1].strip()
        del argv[at:at + 2]
    if len(argv) < 2 or any(a.startswith("--") for a in argv):
        raise Refusal(USAGE)
    return argv[0].strip(), argv[1].strip(), " ".join(argv[2:]).strip(), bucket, dry_run


def _shared_bucket() -> str:
    return os.environ.get("SHARED_BUCKET") or "family"


def _ontology_topics(ontology: File | None) -> set[str]:
    """The topic ids of the memory's ontology, the shipped seed's while it has none."""
    return set((Ontology.loads(ontology.text) if ontology else load_seed_ontology()).topics)


def _report(report: dict) -> int:
    print(json.dumps(report, ensure_ascii=False))
    return 0


def _body(planned: Planned) -> str:
    lines = [f"- {before} -> {after}" for before, after in planned.moved]
    lines += [f"- {path}: the topic's new id" for path in planned.changed]
    return "\n".join(lines)


# ── The plan ─────────────────────────────────────────────────────────────

def plan(memory: Mapping[str, File], *, bucket: str, old: str, new: str, title: str = "",
         shared: str = "", ontology_topics: Collection[str] = ()) -> Planned:
    """The rename of topic `old` in `bucket` to `new`, from the memory's files by path.

    `title` is the topic's new title, "" to keep it. `shared` is the shared
    bucket, where every topic's list lives; `bucket` when not given. The
    files outside the rename are read only to name the links into the old
    folder that stay as they are. `ontology_topics` are the ontology's
    topic ids, which the classifier tags documents with and a rename leaves
    alone. `Refusal` when `old` is no topic of the bucket or an ontology
    topic, or `new` is no free, valid topic id.
    """
    shared = shared or bucket
    _check(memory, bucket=bucket, shared=shared, old=old, new=new,
           ontology_topics=ontology_topics)
    moves = _Moves(bucket, shared, old, new)
    planned = Planned()
    files = {p: f for p, f in memory.items() if p.startswith(f"{bucket}/")}
    if shared != bucket:
        topic_list = {p: f for p, f in memory.items() if moves.list_of(p)}
        others = {p.split("/", 1)[0] for p in memory if "/" in p} - {bucket}
        if topic_list and any(_has_folder(memory, other, old) for other in others):
            planned.kept = list_path(old, bucket=shared)
        else:
            files.update(topic_list)
    texts: dict[str, str] = {}
    targets = {o: n for o, n in ((p, moves.of(p)) for p in files) if n and o.endswith(".md")}
    for path, file in sorted(files.items()):
        to = moves.of(path) or path
        text = _changed_text(file.text, path=to, old=old, new=new, targets=targets)
        texts[to] = text
        if to != path:
            planned.moved.append((path, to))
            planned.changes += [put(to, text, base=""), remove(path, base=file.version)]
        elif text != file.text:
            planned.changed.append(path)
            planned.changes.append(put(path, text, base=file.version))
    # The shared bucket's other lists, a kept one among them: their items
    # follow the moved cards, and nothing else in them changes.
    for path, file in sorted(memory.items()):
        if path in files or not path.startswith(f"{shared}/{LISTS_DIR}/"):
            continue
        if (text := _relinked(file.text, path=path, targets=targets)) != file.text:
            files[path] = file
            texts[path] = text
            planned.changed.append(path)
            planned.changes.append(put(path, text, base=file.version))
    card = topic_card_path(bucket, new)
    made = _topic_card(texts.get(card) or topic_card(bucket, new)[1], old=old, new=new,
                       title=title)
    if card in texts:
        planned.changes = [put(card, made, base="") if c["path"] == card else c
                           for c in planned.changes]
    else:
        planned.changes.append(put(card, made, base=""))
    texts[card] = made
    elsewhere = {p: f.text for p, f in memory.items() if p not in files}
    planned.left = _links_left(texts, elsewhere, folder=f"{bucket}/{old}/",
                               own=f"{bucket}/{new}/")
    return planned


# ── What may be renamed ──────────────────────────────────────────────────

def _check(memory: Mapping[str, File], *, bucket: str, shared: str, old: str, new: str,
           ontology_topics: Collection[str]) -> None:
    """`Refusal` unless `old` is a topic of `bucket` and `new` a free topic id."""
    for topic in (old, new):
        if not TOPIC_ID.match(topic):
            raise Refusal(f"{topic!r} is not a topic id: lowercase letters, digits and hyphens, "
                          "with at least one letter")
        if topic in NOT_TOPIC_IDS:
            raise Refusal(f"{topic!r} is a folder a bucket uses for itself, never a topic: "
                          + ", ".join(sorted(NOT_TOPIC_IDS)))
    for topic in (old, new):
        if topic in ontology_topics:
            # The id is on every document the classifier filed under it, in
            # every bucket: renaming from it is a change to the ontology, and
            # renaming onto it would merge the topic into the ontology's.
            raise Refusal(f"{topic!r} is a topic of the ontology (ontology.toml), not one of "
                          "the household's own; a rename moves only the household's own topics, "
                          "under an id of their own")
    # A list in the shared bucket alone is a topic of the shared bucket:
    # nothing says which person's it would be.
    if not (_has_folder(memory, bucket, old)
            or bucket == shared and list_path(old, bucket=shared) in memory):
        raise Refusal(f"{bucket} has no topic {old!r}: no topic card {bucket}/{old}/{TOPIC_CARD}, "
                      f"nothing filed in {bucket}/{old}/"
                      + (f" and no list {list_path(old, bucket=shared)}" if bucket == shared else ""))
    if _held(memory, bucket, shared, new):
        raise Refusal(f"{new!r} is taken: a folder {bucket}/{new}/, or the list "
                      f"{list_path(new, bucket=shared)} or its archive, is there already")


def _has_folder(memory: Mapping[str, File], bucket: str, topic: str) -> bool:
    """Whether `topic` has its folder in `bucket`: its card, or a record filed into it."""
    folder = f"{bucket}/{topic}/"
    return topic_card_path(bucket, topic) in memory \
        or any(p.startswith(f"{folder}{kind}/") for p in memory for kind in CAPTURE_FOLDERS)


def _held(memory: Mapping[str, File], bucket: str, shared: str, topic: str) -> bool:
    """Whether a rename onto `topic` would meet a file: in its folder, its list or the archive."""
    return any(p.startswith(f"{bucket}/{topic}/") or p.startswith(_archive(shared, topic))
               for p in memory) or list_path(topic, bucket=shared) in memory


def _archive(bucket: str, topic: str) -> str:
    return f"{bucket}/{LISTS_DIR}/{ARCHIVE_DIR}/{topic}/"


class _Moves:
    """Where each file of the topic goes: its folder in `bucket`, its list
    and the list's archive in the shared bucket."""

    def __init__(self, bucket: str, shared: str, old: str, new: str):
        self._folder = (f"{bucket}/{old}/", f"{bucket}/{new}/")
        self._archive = (_archive(shared, old), _archive(shared, new))
        self._list = (list_path(old, bucket=shared), list_path(new, bucket=shared))

    def list_of(self, path: str) -> str:
        """The new path of `path` when it is the topic's list or of its archive; else ""."""
        if path == self._list[0]:
            return self._list[1]
        before, after = self._archive
        return after + path[len(before):] if path.startswith(before) else ""

    def of(self, path: str) -> str:
        """The new path of `path`, "" when it stays where it is."""
        before, after = self._folder
        return after + path[len(before):] if path.startswith(before) else self.list_of(path)


# ── The texts ────────────────────────────────────────────────────────────

def _changed_text(text: str, *, path: str, old: str, new: str, targets: Mapping[str, str]) -> str:
    """The card at `path` with the topic's new id in its fields, and a list's
    links to moved cards pointing at their new paths. Anything but a card
    with frontmatter comes back as it is."""
    if not path.endswith(".md"):
        return text
    try:
        fields = parse(text)
    except FrontmatterError:
        return text
    # A list's `topics` hold the household's topic ids; a correspondent's
    # hold the ontology's, which a rename never names, so they stay.
    for key in ("tags", "topics") if fields.get("type") == "list" else ("tags",):
        values = fields.get(key)
        if isinstance(values, list) and old in values:
            text = with_field(text, key, list(dict.fromkeys(new if v == old else v for v in values)))
    if fields.get("type") == "list":
        text = _relinked(text, path=path, targets=targets)
    return text


def _relinked(text: str, *, path: str, targets: Mapping[str, str]) -> str:
    """A list's text with each link to a moved card pointing at its new path."""
    try:
        linked = {link.target for item in read_list(text, path=path).items for link in item.links}
    except ListError:
        return text
    for target in sorted(linked & targets.keys()):
        text = relink(text, target, targets[target], path=path)
    return text


def _topic_card(text: str, *, old: str, new: str, title: str) -> str:
    """The topic card with `old` among its former ids and, when given, the new title."""
    fields = parse(text)
    former = fields.get(FORMER_TOPIC_IDS) or []
    former = former if isinstance(former, list) else [former]
    # A rename back takes the id it returns to off the list: it is the
    # topic's own again.
    text = with_field(text, FORMER_TOPIC_IDS,
                      [f for f in dict.fromkeys([*former, old]) if f != new])
    if title.strip():
        before = str(fields.get("title") or "")
        text = with_field(text, "title", title.strip())
        head, sep, body = text.partition("\n---\n")
        if before and (heading := _HEADING.search(body)) and heading[0] == f"# {before}":
            body = body[:heading.start()] + f"# {title.strip()}" + body[heading.end():]
        text = head + sep + body
    return text


def _links_left(texts: Mapping[str, str], elsewhere: Mapping[str, str], *,
                folder: str, own: str) -> list[tuple[str, str]]:
    """(card, link) of every link that still resolves into `folder`, the old one.

    The cards in the topic's new folder `own` are left out: a link among
    them is relative and moved with them.
    """
    left = []
    for path, text in sorted({**elsewhere, **texts}.items()):
        if not path.endswith(".md") or path.startswith(own):
            continue
        for target in _LINK.findall(text):
            if "://" in target:
                continue
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
            if resolved.startswith(folder):
                left.append((path, target))
    return left
