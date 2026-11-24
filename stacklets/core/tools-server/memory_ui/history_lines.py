"""The quieter line under a filing and a correction in a card's history.

A card's history (`history.py`) says who did what in one line per change.
For two kinds of change it can say more, from the source the change
brought into the card's sidecar (ADR-018):

    Homer corrected it · today
        "The title shall be Fuel receipt"      the correction's words, cut to about 120 characters
    Homer filed it · yesterday
        a photo, in Homer's chat               what was sent, and where
        a PDF, by email

    said = lines(versions, card, names={"homer": "Homer"}, language="en")
    # ["a photo, in Homer's chat", "", "“The title shall be Fuel receipt”"], one per version

Which source a change brought: each version carries the sidecar it left
(the memory's `/cards/<id>/versions`), so a version brought the sources
its sidecar has and the version before it did not.

Why the words come from the card and not from the old sidecar: the card,
as the memory answers it for the device's audience, holds the sources
its page shows (`entry.sections`). A version's source is quoted only when
the card still holds it, so the history never shows what the card page
does not: a source taken out later, or a card from before the sidecars,
keeps its one line.

Where a message was sent is not recorded for a chat room, only the
channel. A card filed into the sender's own folder came from their own
chat (the archivist files a private chat there), so it says "in Homer's
chat"; any other chat message says "in a chat".

Pure: no I/O.
"""

from __future__ import annotations

from collections.abc import Mapping

from memory_ui.entry.sections import medium_and_words, sources_of_card
from memory_ui.words import words
from stack.cards.sources import read_sidecar

__all__ = ["lines", "QUOTE_CHARS"]

# A correction is quoted up to about this many characters: the history
# is a list of lines, and the card shows the whole text.
QUOTE_CHARS = 120

# The kinds of change that get a second line; every other kind keeps its one.
_FILED, _CORRECTED = "filed", "corrected"


def lines(versions: list[Mapping], card: Mapping | None, *, kinds: list[str], names: Mapping[str, str],
          language: str) -> list[str]:
    """The quieter line of each version (oldest first, as the memory sends
    them), "" for a version that gets none. `kinds` are the versions'
    kinds of change (`stack.cards.commits.kind_of`), in the same order;
    `card` is the card as the memory answered it for the device, None
    when it did not, which leaves every line "".

        lines([filed, corrected], card, kinds=["filed", "corrected"], names=..., language="en")
        # ["a photo, in Homer's chat", "“The title shall be Fuel receipt”"]
    """
    if card is None:
        return [""] * len(versions)
    shown = {_key(s): s for s in sources_of_card(card)}
    bucket = str(card.get("path") or "").partition("/")[0]
    w = words(language)["history_said"]
    found, before = [], set()
    for version, kind in zip(versions, kinds, strict=True):
        now = {_key(s) for s in _sidecar_sources(version)}
        brought = [shown[k] for k in shown if k in now - before]
        before = now
        if kind == _CORRECTED:
            found.append(_quoted(brought, w))
        elif kind == _FILED:
            found.append(_sent(brought, w, names=names, bucket=bucket))
        else:
            found.append("")
    return found


# ── The two lines ─────────────────────────────────────────────────────────

def _quoted(brought: list[Mapping], w: dict) -> str:
    """The words of the first source a correction brought, cut to about
    `QUOTE_CHARS` at a word, in quotation marks; "" for none."""
    said = next((text for s in brought if (text := medium_and_words(s)[1])), "")
    if not said:
        return ""
    said = " ".join(said.split())
    if len(said) > QUOTE_CHARS:
        said = said[:QUOTE_CHARS].rsplit(" ", 1)[0].rstrip(",.;:") + "…"
    return w["quote"].format(said=said)


def _sent(brought: list[Mapping], w: dict, *, names: Mapping[str, str], bucket: str) -> str:
    """What a filing brought and where it came from ("a photo, in Homer's
    chat", "3 photos, in a chat", "a PDF, by email"), from the originals
    it brought; "" for none. Several of one kind are counted; a mix is
    named by its first."""
    originals = [s for s in brought if s.get("role") == "original"]
    if not originals:
        return ""
    what = _what(originals[0])
    same = sum(1 for s in originals if _what(s) == what)
    singular, plural = w["what"].get(what, w["what"]["file"])
    where = _where(originals[0], w, names=names, bucket=bucket)
    named = singular if same == 1 else plural.format(n=same)
    return w["sent"].format(what=named, where=where) if where else named


def _what(source: Mapping) -> str:
    """The kind of thing a source is, as the line names it: the sidecar's
    kind, with a file told apart as a PDF by its name in the media store."""
    kind = str(source.get("kind") or "")
    if kind == "file":
        medium, _ = medium_and_words(source)
        return "pdf" if medium and medium.path.lower().endswith(".pdf") else "file"
    return kind


def _where(source: Mapping, w: dict, *, names: Mapping[str, str], bucket: str) -> str:
    """Where a source arrived, by its channel; "" for a channel the line
    has no words for."""
    channel = str(source.get("channel") or "")
    person = str(source.get("person") or "")
    if channel == "chat" and person and person == bucket:
        return w["where"]["own_chat"].format(name=names.get(person) or person.capitalize())
    return w["where"].get(channel, "")


# ── Reading a version ─────────────────────────────────────────────────────

def _sidecar_sources(version: Mapping) -> list[Mapping]:
    """The sources a version's sidecar lists; [] for a version without a
    sidecar, or one that does not parse (`read_sidecar`)."""
    found = read_sidecar(str(version.get("sidecar") or "")).get("sources")
    return [s for s in found or [] if isinstance(s, Mapping)]


def _key(source: Mapping) -> tuple[str, ...]:
    """What tells one source from another: its channel's id, or who sent
    it and when for a source nobody recorded an id of."""
    role, ident = str(source.get("role") or ""), str(source.get("id") or "")
    if ident and ident != "unknown":
        return role, ident
    return role, str(source.get("person") or ""), str(source.get("time") or "")
