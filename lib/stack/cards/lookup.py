"""Finding a card by its id, in a copy of the family memory.

`find_card` finds a card by the id of its kind (`stack.cards.CARD_ID_FIELDS`:
a capture's event id, a diary card's entry id), `find_document` a document
card by its Paperless number, `find_list` a list by its name, `find_by_card_id` by its
own `card_id` or one it had before, `card_id_at` reads the id of the card
at a path the others answered, `renamed_topic` a topic by an id it
had before a rename, `paths_with_card_id` every file that
carries one `card_id` (a copy carries its original's), and `removal_of`
says when the card with an id no file carries any more was removed.
Every reader runs them over the copy of the family memory it reads from:
core's tools server over the wiki's copy, so a `/go/` link lands on a
page the wiki serves; the archivist over its own mount, before it
corrects a capture; the curator's card check over its clone. One
implementation, and no reader waits on another's copy to catch up.

Stdlib only, like `stack.cards`: the host CLI, the bot-runner and core's
tools server all import it.

WHY A SCAN AND NOT AN INDEX

An index the curator maintains would be faster, and it would be one more
thing that can be wrong: a second copy of the truth that can go stale,
disagree with the tree or be missing on a fresh clone, and every one of
those failures looks like a dead link. A scan reads the files themselves.
A family vault is hundreds to low thousands of small markdown files and a
lookup happens when a person clicks a link or replies to a filing, so the
cost lands in the right place. An index can be added behind `find_card`
later without touching its callers or the link format.

Only frontmatter and a record's sidecar count: they are the machine-
readable half of a card, and an id someone quoted in prose must not make
that page answer to it. It
is parsed with `stack.frontmatter`, the parser every writer of these
files uses, so a file whose frontmatter it rejects (a hand edit gone
wrong) answers to no id rather than to a guess.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from . import CARD_ID_FIELDS, FORMER_TOPIC_IDS, card_id_from, former_card_ids, is_card_id
from ..frontmatter import FrontmatterError, parse
from ..lists import LISTS_DIR
from .tree import holds_a_file
from ..vault import TOPIC_CARD

# A record's sidecar, beside its card (ADR-018 §3).
SIDECAR_SUFFIX = ".json"


# ── The lookup ────────────────────────────────────────────────────────

_DELIMITER = "---"


def find_card(kind: str, card_id: str, *, root: Path | str) -> str | None:
    """Path of the card of `kind` with `card_id`, relative to `root`, or None.

    `root` is a copy of the family memory: the vault or the wiki's
    projection of it, whose paths are the same. The answer keeps the
    `.md` suffix.

    None rather than a guess for an unknown kind, an empty id, a missing
    `root`, or an id no card carries: a 404 tells the person the card is
    gone, while a redirect to something else would quietly show them the
    wrong note.
    """
    field = CARD_ID_FIELDS.get(kind)
    wanted = (card_id or "").strip()
    if field is None or not wanted:
        return None
    for rel, fields in _cards_in(Path(root)):
        if str(fields.get(field) or "").strip() == wanted:
            return rel
    return find_by_source_id(wanted, root=root) if kind == "capture" else None


def find_document(paperless_id: str, *, root: Path | str) -> str | None:
    """Path of the document card filed from Paperless document `paperless_id`, or None.

    A document's link in the chat names it by its Paperless number
    (`/go/docs/72`); the card filed from it carries that number in
    `paperless_id`. The answer keeps the `.md` suffix, like `find_card`'s.
    None for an empty id, a missing `root`, or a number no document card
    carries, and the link then opens the document in Paperless.

        find_document("72", root=vault)     # 'family/documents/2026/10/duff-insurance-policy-p72.md'
    """
    wanted = str(paperless_id or "").strip()
    if not wanted:
        return None
    for rel, fields in _cards_in(Path(root)):
        if fields.get("type") == "document" and str(fields.get("paperless_id") or "").strip() == wanted:
            return rel
    return None


def find_list(slug: str, *, root: Path | str, shared_bucket: str) -> str | None:
    """The vault path of the list named `slug` (`<bucket>/lists/<slug>.md`),
    None when no bucket has one.

    A list is named by its file, in the family's bucket or in a person's
    own. When two buckets have a list of that name, the family's
    (`shared_bucket`) wins, then the buckets in name order, so a link
    always opens the same list. A name with a path or a wildcard in it
    names no list: the name is matched as a glob pattern below, and `*`
    would open whichever list it matched first.

        find_list("garage", root=vault, shared_bucket="family")   # 'homer/lists/garage.md'
        find_list("*", root=vault, shared_bucket="family")        # None
    """
    if not slug or slug.startswith(".") or any(c in _NOT_IN_A_LIST_NAME for c in slug):
        return None
    base = Path(root)
    if not base.is_dir():
        return None
    candidates = [base / shared_bucket / LISTS_DIR / f"{slug}.md"]
    candidates += sorted(base.glob(f"*/{LISTS_DIR}/{slug}.md"))
    for path in candidates:
        if path.is_file() and _frontmatter_of(path).get("type") == "list":
            return path.relative_to(base).as_posix()
    return None


# A path separator leaves the lists folder; `*`, `?` and `[` are glob
# syntax for `Path.glob`, which `find_list` matches the name with.
_NOT_IN_A_LIST_NAME = "/\\*?["


def card_id_at(path: str, *, root: Path | str) -> str:
    """The card id of the card at `path` (relative to `root`), "" when the
    file has none or is not there.

    The finders above answer with a path; a link lands on the card's own
    address, its id, so it needs no second redirect from path to id.

        card_id_at("family/lists/einkaufsliste.md", root=vault)   # 'list-einkaufsliste'
    """
    return card_id_from(_frontmatter_of(Path(root) / path)) if path else ""


def find_by_source_id(source_id: str, *, root: Path | str) -> str | None:
    """Path of the card one of whose sources has `source_id`, or None.

    A record keeps where it came from in a sidecar, `<stem>.json` beside
    the card (ADR-018 §3): `sources`, each with the channel's `id` for it,
    a Matrix event id for a chat message. So the message a note was filed
    from, or a reply to it, finds the note. None for an empty or unknown
    id, or a sidecar whose card is gone.
    """
    wanted = (source_id or "").strip()
    base = Path(root)
    if not wanted or not base.is_dir():
        return None
    for sidecar in sorted(base.rglob(f"*{SIDECAR_SUFFIX}")):
        card = sidecar.with_suffix(".md")
        if ".git" in sidecar.relative_to(base).parts or not card.is_file():
            continue
        if wanted in _source_ids(sidecar):
            return card.relative_to(base).as_posix()
    return None


def _source_ids(sidecar: Path) -> set[str]:
    try:
        loaded = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return set()
    sources = loaded.get("sources") if isinstance(loaded, dict) else None
    return {str(s.get("id")) for s in sources or [] if isinstance(s, dict) and s.get("id")}


@dataclass(frozen=True)
class Found:
    """A card found by a card id: where it is, its frontmatter, and the id
    it was asked for, which is a former id of the card when it is not its
    current one.

        found = find_by_card_id("bookmark-7f3a9c21e4", root=vault)
        found.card_id, found.former      # ('note-0b6e2d91aa', True)
    """

    path: str
    fields: dict
    asked: str

    @property
    def card_id(self) -> str:
        return card_id_from(self.fields)

    @property
    def former(self) -> bool:
        return self.asked != self.card_id


def find_by_card_id(card_id: str, *, root: Path | str) -> Found | None:
    """The card in `root` that carries `card_id`, now or as a former id.

    A card that carries it as its id wins over one that lists it among
    its former ids. None for a text that is no card id, a missing `root`,
    or an id no card carries: `removal_of` says whether one did.
    """
    wanted = (card_id or "").strip()
    if not is_card_id(wanted):
        return None
    former: Found | None = None
    for rel, fields in _cards_in(Path(root)):
        if card_id_from(fields) == wanted:
            return Found(rel, fields, wanted)
        if former is None and wanted in former_card_ids(fields):
            former = Found(rel, fields, wanted)
    return former


def renamed_topic(bucket: str, topic: str, *, root: Path | str) -> tuple[str, str] | None:
    """(id, title) the topic `topic` of `bucket` has now, when a rename gave it another.

    The answer comes from the topic card in `bucket` that lists `topic`
    among its `former_topic_ids` (`stack memory topic rename` adds it).
    None while a file is under `<bucket>/<topic>/`, since a folder of that
    name is the topic itself, and when no topic card lists the id. An
    empty folder counts as gone: git has no folders. Only
    the topic cards of `bucket` are read: topic ids are unique within a
    bucket.

        renamed_topic("family", "nahprojekte", root=vault)   # ("naehprojekte", "Nähprojekte")
    """
    base = Path(root) / bucket
    if not topic or holds_a_file(base / topic) or not base.is_dir():
        return None
    for card in sorted(base.glob(f"*/{TOPIC_CARD}")):
        fields = _frontmatter_of(card)
        former = fields.get(FORMER_TOPIC_IDS) or []
        if topic in (former if isinstance(former, list) else [former]):
            return card.parent.name, str(fields.get("title") or card.parent.name)
    return None


def paths_with_card_id(card_id: str, *, root: Path | str) -> list[str]:
    """Every file in `root` that carries `card_id` as its id, by path.

    One id names one card, so a second path is a file copied with its
    frontmatter (ADR-016 §2). [] for a text that is no card id.
    """
    wanted = (card_id or "").strip()
    if not is_card_id(wanted):
        return []
    return [rel for rel, fields in _cards_in(Path(root)) if card_id_from(fields) == wanted]


@dataclass(frozen=True)
class Removal:
    """The commit that removed the last card carrying an id, and the file
    that card was, so a reader can tell who may hear of it."""

    sha: str
    day: str
    who: str
    subject: str
    path: str


# git can wait on a large history; a person is waiting on the answer.
_HISTORY_TIMEOUT_SECS = 20


def removal_of(card_id: str, *, repo: Path | str) -> Removal | None:
    """When the card with `card_id` was removed from the git repository `repo`.

    The newest commit that deleted a file whose frontmatter carried the
    id. Ask it only for an id `find_by_card_id` did not find: a card that
    was removed and written again is found, not gone. None for a text
    that is no card id, a `repo` git cannot read, or an id no file ever
    carried. The repository may belong to another user (a bind mount in
    a container), so git is told to trust it for this one command.
    """
    wanted = (card_id or "").strip()
    if not is_card_id(wanted) or not Path(repo).is_dir():
        return None
    line = "^card_id: " + wanted.replace(".", "\\.") + "$"
    try:
        out = subprocess.run(
            ["git", "-c", "safe.directory=*", "-C", str(repo), "log", "-1",
             "--diff-filter=D", f"-G{line}", "--name-only", "-z",
             "--format=%h%x1f%cs%x1f%an%x1f%s"],
            capture_output=True, text=True, timeout=_HISTORY_TIMEOUT_SECS,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    # `-z` ends the commit line and each file name with a NUL.
    head, *paths = [field.strip("\n") for field in out.split("\0") if field.strip("\n")] or [""]
    parts = head.split("\x1f")
    if len(parts) != 4 or not paths:
        return None
    sha, day, who, subject = parts
    return Removal(sha, day, who, subject, paths[0])


def _cards_in(root: Path) -> Iterator[tuple[str, dict]]:
    """Each markdown file under `root` with its frontmatter, by path."""
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(root)
        if ".git" not in rel.parts:
            yield rel.as_posix(), _frontmatter_of(path)


def _frontmatter_of(path: Path) -> dict:
    """The parsed frontmatter of a file, or {} when it has none that parses.

    Reads line by line up to the closing delimiter, so the cost of a scan
    is the size of the headers, not of the pages.
    """
    try:
        with path.open("r", encoding="utf-8") as fh:
            if fh.readline().rstrip("\r\n") != _DELIMITER:
                return {}
            block = [f"{_DELIMITER}\n"]
            for line in fh:
                if line.rstrip("\r\n") == _DELIMITER:
                    block.append(f"{_DELIMITER}\n")
                    return parse("".join(block))
                block.append(line)
    except (OSError, UnicodeDecodeError, FrontmatterError):
        return {}
    return {}
