"""The diary's cards in the memory: read from the curator, written through it.

    tree = read_diary_tree(memory, "family")     # path -> (version, text)
    failed = _commit(memory, plan, tree, by_path)  # the paths that did not land
    records = records_on_file(vault, "family")   # path -> text, from the clone

The curator is the memory's one writer. Each write names the version it
was made from, so a card a person changed in the meantime fails its
commit instead of being overwritten.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from stack.cards import card_version
from stack.cards.lookup import SIDECAR_SUFFIX
from stack.cards.sources import sidecar_path
from stack.memory import MEMORY_BOT, CuratorDown, MemoryAPI, Refused, put, remove

from ...diary.card import ENTRIES_DIR, card_path, header_on_file, to_entry
from ...diary.card import parse as parse_card
from ...diary.pages import DIARY_DIR
from .progress import err

# ── The vault ─────────────────────────────────────────────────────────
#
# Cards are records, so they go to the memory, the repository documents
# and captures are filed into, through the curator, its one writer. The
# records are read from the curator with the version each is at, and every
# write names the version it was made from: a card a person corrected in
# Forgejo's web editor in between fails the commit instead of being lost.


# Cards per commit, each with its sidecar. A first compile over years of a
# room writes hundreds of cards; batches keep each request a modest size.
_COMMIT_BATCH = 50

# Where every card of a compile or a correction comes from, as its commit says.
SOURCE = {"Source": "memories room"}


def read_diary_tree(memory: MemoryAPI, bucket: str) -> "dict[str, tuple[str, str]]":
    """Every diary record in the memory, card and sidecar: path -> (version, content)."""
    root = f"{bucket}/{DIARY_DIR}/{ENTRIES_DIR}"
    return {f.path: (f.version, f.text) for f in memory.files(root)
            if f.path.endswith((".md", SIDECAR_SUFFIX))}


def cards_in(records: "dict[str, str]") -> "dict[str, object]":
    """Each diary card in `records` (path to text, cards and sidecars), read
    with its sidecar, by the card's path. A card whose header does not
    parse is left out and named on stderr (`header_on_file`)."""
    return {path: parse_card(text, records.get(sidecar_path(path), ""))
            for path, text in records.items()
            if path.endswith(".md") and header_on_file(path, text) is not None}


def commit_parts(cards, *, new: int, verb: str | None = None,
                  corrections=()) -> tuple[str, str, str]:
    """The kind, title and body of the commit a family reads in the vault's history.

    A correction's own words go in the body: the card shows what it
    changed, and this is where what was said is kept.
    """
    kind = verb or ("learn" if new == len(cards) else "update")
    title = cards[0].title if len(cards) == 1 else f"{len(cards)} diary entries"
    lines: list[str] = []
    for who, text, _ in corrections:
        lines += [f"{who.title()}:"] + [f"> {ln}" for ln in text.splitlines()] + [""]
    lines += [f"- {c.on.isoformat()} {c.title}" for c in cards]
    return kind, title, "\n".join(lines)


def _commit(memory: MemoryAPI, plan, tree, cards_by_path) -> "set[str]":
    """Write a plan through the curator: one commit per sender, whose
    entries they are, and the removals as the memory bot's. A card and
    its sidecar are always in the same commit; a sidecar that changed
    under an unchanged card is written in its card's place. Returns the
    paths that did not land, so the pages can be built from what did."""
    card_of = {**{sidecar_path(p): p for p in cards_by_path}, **{p: p for p in cards_by_path}}
    by_sender: dict[str, list[str]] = {}
    for card in dict.fromkeys(card_of[p] for p in plan.writes if p in card_of):
        by_sender.setdefault(cards_by_path[card].sender, []).append(card)

    batches = []
    for sender, paths in sorted(by_sender.items()):
        for i in range(0, len(paths), _COMMIT_BATCH):
            chunk = paths[i:i + _COMMIT_BATCH]
            written = [p for card in chunk for p in (card, sidecar_path(card)) if p in plan.writes]
            changes = [put(p, plan.writes[p], base=tree[p][0] if p in tree else "")
                       for p in written]
            new = sum(1 for p in chunk if p not in tree)
            batches.append((changes, sender, commit_parts(
                [cards_by_path[p] for p in chunk], new=new), SOURCE))
    if plan.deletes:
        count = sum(1 for p in plan.deletes if p.endswith(".md"))
        batches.append(([remove(p, base=tree[p][0]) for p in plan.deletes], MEMORY_BOT,
                        ("forget", f"{count} diary entr{'y' if count == 1 else 'ies'} "
                                   "deleted in the room", ""), {}))

    failed: set[str] = set()
    for changes, by, (kind, title, body), trailers in batches:
        try:
            memory.commit(changes, by=by, kind=kind, title=title, body=body, trailers=trailers)
        except (Refused, CuratorDown) as e:
            cards = sum(1 for c in changes if c["path"].endswith(".md"))
            err(f"  could not write {cards} card(s): {e}")
            failed.update(c["path"] for c in changes)
    return failed


def entries_on_file(records) -> tuple[list, list]:
    """The cards in `records` (path to text, cards and sidecars) and their
    entries, in date order."""
    cards = cards_in(records)
    on_file = list(cards.values())
    versions = {c.card_id: card_version(records[path].encode("utf-8"))
                for path, c in cards.items()}
    entries = sorted((replace(e, version=versions.get(e.card_id, ""))
                      for e in map(to_entry, on_file)),
                     key=lambda e: (e.on, e.at))
    return on_file, entries


def records_on_file(vault: Path, bucket: str) -> dict[str, str]:
    """Every diary record in the memory repo's working copy, card and
    sidecar: path to text."""
    root = vault / bucket / DIARY_DIR / ENTRIES_DIR
    return {path.relative_to(vault).as_posix(): path.read_text(encoding="utf-8")
            for path in sorted(root.rglob("*")) if path.suffix in (".md", SIDECAR_SUFFIX)}


# ── After the plan ────────────────────────────────────────────────────


def write(memory, plan, tree, cards, *, dry_run: bool, bucket: str) -> "set[str]":
    """Commit the plan through the curator, or with `dry_run` say what it
    would write. Returns the paths that did not land."""
    if dry_run:
        for path in sorted(plan.writes):
            err(f"    would write {path}")
        for path in plan.deletes:
            err(f"    would remove {path}")
        return set()
    if not (plan.writes or plan.deletes):
        return set()
    by_path = {card_path(c, bucket=bucket): c for c in cards}
    return _commit(memory, plan, tree, by_path)


def records_after(tree, plan, failed: "set[str]") -> "dict[str, str]":
    """The diary records as they are after the plan: the tree with what
    landed written and removed."""
    records = {p: t for p, (_, t) in tree.items()}
    for path, text in plan.writes.items():
        if path not in failed:
            records[path] = text
    for path in plan.deletes:
        if path not in failed:
            records.pop(path, None)
    return records
