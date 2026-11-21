"""Which files a compile writes to the diary tree, and which it leaves alone.

    plan(existing, cards, bucket="family", complete=True)   # Plan(writes, deletes, kept)
    same_sources(before, card)   # was the card made from the entry as it is?
    cards_on_file(existing)      # entry id -> (path, Card)

A compile writes a card again only when its entry changed or `--rebuild`
asks for it, removes a card whose entry is gone only after a complete
read of the room, and never touches a card a person edited: it lists it
in `kept`. A card whose header does not parse is not on file, and the
compile never writes over its path: a person broke it, and it stays
as they left it.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field, replace

from stack.cards import new_card_id
from stack.cards.sources import NOTE, sidecar_path
from stack.digest import DIGEST
from stack.frontmatter import parse as frontmatter_parse

from .card import DIARY_TYPE, Card, card_path, edited_by_hand, files, header_on_file, parse

# ── Which files a compile writes ──────────────────────────────────────


@dataclass
class Plan:
    """What a compile does to the vault's diary tree.

    `writes` maps a path to its new content (a card and its sidecar
    always together), `deletes` lists files to remove, and `kept` lists
    cards a person edited that the compile would otherwise have changed
    or removed. Those are reported, never touched.
    """

    writes: dict[str, str] = field(default_factory=dict)
    deletes: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)


def _plain(text: str) -> str:
    return " ".join(text.split())


def same_sources(before: Card, card: Card) -> bool:
    """Whether `before` was made from the entry `card` is made from now:
    the same messages, the same words and the same replies.

    Only what the room says counts: the ids of the messages and replies,
    the entry's words and the replies' words. Whitespace does not count
    (an editor or an older release may have wrapped them), and neither
    does what a migrated card could not record: a reply's time, or which
    of a recording's two halves held which words.
    """
    def ids(c: Card) -> list[str]:
        return [s.id for s in c.sources if s.role != NOTE]
    return (list(dict.fromkeys(ids(before))) == list(dict.fromkeys(ids(card)))
            and _plain(before.body) == _plain(card.body)
            and [(w, _plain(t)) for w, t in before.replies]
            == [(w, _plain(t)) for w, t in card.replies])


def cards_on_file(existing: "dict[str, str]") -> "dict[str, tuple[str, Card]]":
    """The diary cards among `existing` (path to text, cards and their
    sidecars), by entry id: (path, card as read). A card whose header does
    not parse is left out and named on stderr (`header_on_file`)."""
    on_disk: dict[str, tuple[str, Card]] = {}
    for path, text in existing.items():
        if not path.endswith(".md"):
            continue
        header = header_on_file(path, text)
        if header is None or header.get("type") != DIARY_TYPE:
            continue
        card = parse(text, existing.get(sidecar_path(path), ""))
        if card.entry_id:
            on_disk[card.entry_id] = (path, card)
    return on_disk


def plan(existing: "dict[str, str]", cards, *, bucket: str,
         complete: bool, rebuild: bool = False) -> Plan:
    """Compare the vault's diary files with a compile's cards.

    `existing` maps each diary file in the vault, card or sidecar, to its
    content. Cards are matched by `entry_id`, not by path, so a card whose
    day changed is moved rather than duplicated, and keeps its card id. A
    card new to the vault gets its id here. `complete` says the compile
    read the whole room; only then does a card with no entry mean its
    messages are gone.

    A card on file is rewritten only when its entry changed (a message
    joined it, its words were edited, a reply came: `same_sources`), or
    with `rebuild`. Everything else on a card is the model's reading, and
    a reading under new prompts or a new vocabulary is no reason to
    rewrite what a family has been reading. A card a person changed is
    never rewritten; it is listed in `kept` when its entry changed and
    the compiler had signed it, so the family hears the room moved on.
    A card a person corrected carries no signature and is kept silently.
    """
    cards = list(cards)  # read twice below: once to plan, once to find what is gone
    on_disk = cards_on_file(existing)
    out = Plan()
    for card in cards:
        _plan_card(out, card, on_disk.get(card.entry_id, ("", None)), existing,
                   bucket=bucket, rebuild=rebuild)
    if complete:
        seen = {card.entry_id for card in cards}
        _plan_gone(out, {e: pair for e, pair in on_disk.items() if e not in seen}, existing)
    return out


def _plan_card(out: Plan, card: Card, on_file: "tuple[str, Card | None]",
               existing: "dict[str, str]", *, bucket: str, rebuild: bool) -> None:
    """Add to `out` what one compiled card does to the files: written when
    it is new, its entry moved on or `rebuild` asks; moved when its day
    changed; kept when a person edited it."""
    path = card_path(card, bucket=bucket)
    old_path, before = on_file
    old_text = existing.get(old_path, "")
    card = replace(card, card_id=(before.card_id if before else "") or card.card_id
                   or new_card_id(DIARY_TYPE, path))
    written = files(card, bucket=bucket)
    if before is None:
        # Any file at the new card's path that was not read as a diary card
        # (a header a person broke, a type changed by hand) is a person's.
        # Writing would replace it, so the card is not written and the path
        # is named, as `header_on_file` names a header that does not parse.
        if path in existing:
            print(f"[diary] {path}: not read as a diary card, left as it is",
                  file=sys.stderr)
        else:
            out.writes.update(written)
        return
    moved_on = not same_sources(before, card)
    if edited_by_hand(old_text):
        if moved_on and frontmatter_parse(old_text).get(DIGEST):
            out.kept.append(old_path)
        return
    if not (moved_on or rebuild):
        return
    if old_path != path:
        out.deletes += _pair_on_file(old_path, existing)
        out.writes.update(written)
    else:
        out.writes.update({p: t for p, t in written.items() if existing.get(p) != t})


def _plan_gone(out: Plan, gone: "dict[str, tuple[str, Card]]",
               existing: "dict[str, str]") -> None:
    """Add to `out` the cards on file whose entry is gone: removed, or kept
    when a person edited them."""
    for path, _ in gone.values():
        if edited_by_hand(existing[path]):
            out.kept.append(path)
        else:
            out.deletes += _pair_on_file(path, existing)


def _pair_on_file(path: str, existing: "dict[str, str]") -> list[str]:
    """The card at `path` and its sidecar, as far as they are on file."""
    return [p for p in (path, sidecar_path(path)) if p in existing]
