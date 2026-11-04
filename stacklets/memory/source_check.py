"""Sources are only appended: the card store's rule for a card's sources (ADR-018 §4).

A card's sources hold what people sent: one quote per source under the
card's `## Sources`, and one entry per source in the sidecar beside it
(`stack.cards.sources`). A rebuild reads them again, so a writer that
changes one rewrites what a person sent, and every later rebuild reads
the rewritten words as theirs. This module is the rule the curator's card
store holds every write to (`card_check.CardCheck` calls it):

    problems = changed_sources(puts, read, history, removes=removes)
    # [{"path": "family/notes/2026/10/camping-7903f2.md", "field": "sources[0]",
    #   "message": "the quote of sources[0] changed from 'we leave fri at 4' to ...",
    #   "refusal": "nothing was written, ... edit the card in Forgejo, or give a correction
    #               with `stack memory reprocess --msg`"}]

How a write is judged, card by card, against the card as it was:

    old quote n  ──same text──────────────────────────▶ passes
    old entry n  ──same as a source (`record()`)──────▶ passes
      ├─ old entry `reconstructed`, new one same role ▶ passes  (the bridge, ADR-018 §8)
      ├─ only the quote differs, and the new quote is
      │  the words as sent before a person's edit ────▶ passes  (the put-back, D1a)
      └─ anything else ───────────────────────────────▶ refused, field `sources[n]`
    new sources after the last old one ───────────────▶ appended, pass

A worked case: Homer's camping note holds his message and Bart's reply.
Marge edits Homer's quote in Forgejo ("bring 4 sleeping bags"). The next
rebuild writes the quote back as he sent it ("bring 3 sleeping bags") and
appends her words as her `note`: quote 0 differs from the card as it was,
equals the words as sent, and passes. A writer that writes "we leave at 5"
into quote 0 instead is refused, and nothing is written.

Why this way:

- **Compared by position, as sources.** Old entry n meets new entry n. Two
  entries are the same when `source_from(entry, quote).record()` is equal,
  because a rebuild drops keys left empty (`"reconstructed": false`). Two
  quotes are the same when the whole quote is equal, its media line
  included: `Source.words` leaves the media line out, so a changed embed
  would pass.
- **The words as sent come from the card's history** (`history_of`), read
  through the store's own reader, at most once per card and only when a
  quote differs. The check never runs git itself: the store's git carries
  the environment that lets git trust the curator's mounted clone.
- **A moved card is judged against the card it was,** found among the
  write's removals by its `card_id` or a `former_card_ids`; its history is
  read at the removed path, because the new path has none of it yet.
- **A previous card whose quotes and entries do not pair up** is no reason
  to raise: the quotes and the entries are compared as two lists.
- **Not a hash per source in the sidecar:** it would change the format, a
  person can edit the sidecar too, and the bridge changes those quotes
  anyway.

What it does not judge: a card that kept no sidecar before the write (a
document, a diary card) and a new card have no existing sources, so any
pass. A migration's commit is not held to it (`card_check` leaves it out
for `formats=False`). A person's push to Forgejo never passes the store.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from stack.cards import card_id_from, former_card_ids
from stack.cards.history import Version, history_of
from stack.cards.sources import quotes_of, read_sidecar, sidecar_path, source_from
from stack.frontmatter import FrontmatterError, parse

from .note_card import card_sources

__all__ = ["HistoryReader", "REFUSAL", "changed_sources"]

# What a check reads the memory as it stands with: a path's text, or None.
Reader = Callable[[str], "str | None"]

# The store's reader of a card's history: `(path, card_id)` to its versions,
# oldest first (`CardStore.history`).
HistoryReader = Callable[[str, str], Sequence[Version]]

# What the store says when it refuses a write under this rule, the same for
# every writer, so a person always learns the two ways to correct a source.
REFUSAL = ("nothing was written, the change would rewrite what a person sent: a card's "
           "sources are only appended. To correct one, edit the card in Forgejo, or give "
           "a correction with `stack memory reprocess --msg`")


def changed_sources(puts: Sequence[tuple[str, str]], read: Reader, history: HistoryReader | None,
                    *, removes: Sequence[str] = ()) -> list[dict]:
    """What the write changes of the sources its cards already have; [] when it only appends.

    `puts` are the files written, `(path, text)`; `removes` the paths taken
    out; `read` gives a path's text as the memory stands before the write;
    `history` the store's history reader (None: no put-back can be proven,
    so a changed quote is refused). Each problem is `{"path", "field",
    "message", "refusal"}`, `field` being `sources[n]`, `path` the card's or,
    when only the sidecar is written, the sidecar's. Reads, never writes.

        changed_sources([(CARD, card_with_quote_0_changed)], read, store.history)
        # [{"path": CARD, "field": "sources[0]", "message": "the quote of ...", ...}]
    """
    written = dict(puts)
    gone = set(removes)
    problems = []
    for card in _cards_touched(puts, removes):
        if card in gone or (now := _now(card, written, gone, read)) is None:
            continue
        before = _before(card, now.card, read, gone)
        if before is None:
            continue
        where = card if card in written else sidecar_path(card)
        problems += [{"path": where, **p, "refusal": REFUSAL}
                     for p in _compare(before, now, history)]
    return problems


# ── The card before the write and after it ──────────────────────────────

@dataclass(frozen=True)
class _Kept:
    """A card's text and its sidecar's, and where its history is read."""

    path: str
    card: str
    sidecar: str | None


def _cards_touched(puts: Sequence[tuple[str, str]], removes: Sequence[str]) -> list[str]:
    """The card paths whose card or sidecar the write puts or removes, in order."""
    paths = [path for path, _ in puts] + list(removes)
    cards = [p if p.endswith(".md") else str(PurePosixPath(p).with_suffix(".md")) for p in paths
             if p.endswith(".md") or (p.endswith(".json")
                                      and sidecar_path(str(PurePosixPath(p).with_suffix(".md"))) == p)]
    return list(dict.fromkeys(cards))


def _now(card: str, written: dict[str, str], gone: set[str], read: Reader) -> _Kept | None:
    """The card at `card` as the write leaves it; None when it has none there."""
    text = written[card] if card in written else read(card)
    if text is None:
        return None
    beside = sidecar_path(card)
    sidecar = written[beside] if beside in written else (None if beside in gone else read(beside))
    return _Kept(card, text, sidecar)


def _before(card: str, text: str, read: Reader, gone: set[str]) -> _Kept | None:
    """The card as it was before the write, with a sidecar; None when it kept no sources.

    At the same path, or, for a card the write moves there, the card the
    write removes that carries its id or one of its former ids."""
    old = read(card)
    path = card
    if old is None:
        path = _moved_from(text, read, gone)
        old = read(path) if path else None
    sidecar = read(sidecar_path(path)) if old is not None else None
    return _Kept(path, old, sidecar) if old is not None and sidecar is not None else None


def _moved_from(text: str, read: Reader, gone: set[str]) -> str:
    """The removed card the card `text` was, by its id; "" for none."""
    fields = _fields(text)
    ids = {card_id_from(fields), *former_card_ids(fields)} - {""}
    return next((path for path in sorted(gone) if path.endswith(".md")
                 and (old := read(path)) is not None and card_id_from(_fields(old)) in ids), "")


def _fields(text: str) -> dict:
    try:
        return parse(text) or {}
    except FrontmatterError:
        return {}


# ── Old source n against new source n ──────────────────────────────────

def _compare(before: _Kept, now: _Kept, history: HistoryReader | None) -> list[dict]:
    """Each old source the new card does not keep, as `{"field", "message"}`."""
    old_quotes, new_quotes = quotes_of(before.card), quotes_of(now.card)
    old_entries, new_entries = _entries(before.sidecar), _entries(now.sidecar)
    as_sent = _AsSent(before, history)
    problems = []
    for n in range(max(len(old_quotes), len(old_entries))):
        old_q, new_q = _at(old_quotes, n), _at(new_quotes, n)
        old_e, new_e = _at(old_entries, n), _at(new_entries, n)
        if old_e is not None and old_e.get("reconstructed") and new_e is not None \
                and new_e.get("role") == old_e.get("role"):
            continue                           # the bridge for notes filed before sidecars
        entry_kept = old_e is None or (new_e is not None and _same_entry(old_e, new_e))
        quote_kept = old_q is None or old_q == new_q
        if entry_kept and quote_kept:
            continue
        if entry_kept and new_q is not None and old_e is not None \
                and new_q == as_sent.quote(str(old_e.get("id") or "")):
            continue                           # a person's hand edit put back (D1a)
        problems.append({"field": f"sources[{n}]",
                         "message": _what_changed(n, old_q, new_q, old_e, new_e)})
    return problems


def _entries(sidecar: str | None) -> list[dict]:
    return [e for e in read_sidecar(sidecar).get("sources") or [] if isinstance(e, dict)]


def _at(items: list, n: int):
    return items[n] if n < len(items) else None


def _same_entry(old: dict, new: dict) -> bool:
    """Whether two entries are one source: compared as `record()` gives
    them, so key order and keys left empty do not count."""
    return source_from(old, "").record() == source_from(new, "").record()


def _what_changed(n: int, old_q, new_q, old_e, new_e) -> str:
    if (old_q is not None and new_q is None) or (old_e is not None and new_e is None):
        return f"sources[{n}] is removed"
    if old_e is not None and new_e is not None and not _same_entry(old_e, new_e):
        return f"the entry of sources[{n}] changed from {old_e} to {new_e}"
    return f"the quote of sources[{n}] changed from {old_q!r} to {new_q!r}"


class _AsSent:
    """The words as sent of a card's sources, read from its history once, when first asked.

    `quote(source_id)` is the quote a source held before a person first
    edited it by hand (`history_of(...).quotes_as_sent`), "" for a source
    nobody edited, or when no history reader is given."""

    def __init__(self, before: _Kept, history: HistoryReader | None):
        self._before, self._history = before, history
        self._quotes: dict[str, str] | None = None

    def quote(self, source_id: str) -> str:
        if self._quotes is None:
            self._quotes = self._read()
        return self._quotes.get(source_id, "") if source_id else ""

    def _read(self) -> dict[str, str]:
        card_id = card_id_from(_fields(self._before.card))
        if self._history is None or not card_id:
            return {}
        versions = self._history(self._before.path, card_id)
        return dict(history_of(versions, read_sources=card_sources).quotes_as_sent)
