"""The base a page folds new cards into, and the budget each model call keeps to.

The home page and a person page read every card that concerns them. Sent
whole, that prompt grows with every filing: about 95,000 tokens for one
family's home page after a few months (2026-10), past what a small local
model holds and minutes per call. A plain cut to what fits drops the
oldest cards first, and those carry the facts the home page exists for:
the address, the relatives, the insurance.

So a page is folded instead. Its **base** is the model's answer from the
last nightly compile, with the cards that answer cites and the cards it
had read. A call reads the base, one title line per card it cites, then
only the cards that are new or changed since, in the order they were
filed, as many as the budget holds:

    answer = fold(base, cards since the base)

- **Daytime**, the page shows the answer and the base stays as it was.
  Each compile of the day therefore repeats the previous prompt up to the
  newly filed cards, and the model server reuses what it read.
- **At night**, the answer becomes the new base. More cards than one call
  holds are folded chunk by chunk, each answer the base of the next.
- **Without a base** (the first compile, a changed prompt file, a page
  from before bases), the page is built from an empty one, chunk by chunk.
- **A corrected or removed card** takes with it every item of the base
  that cites nothing else, before the fold; a changed card is sent again.
  A wrong fact never survives on its own.

The base lives in the view, after the model's answer (`encode`), so it is
versioned with the brain and rebuilt from the cards when it is lost.

Pure functions: no I/O and no model calls. The wiki compiler
(`cli/wiki.py`) reads and writes the base, builds the prompts and calls
the model.

    base = Base.empty("3f2a")
    chunk = base.since(entries)[:10]
    base = base.folded(raw, chunk, wiki._reference)
    base.cites[0].title   # the second card of the chunk
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import zlib
from dataclasses import dataclass, field

# ── The budget ───────────────────────────────────────────────────────────

# The most a call reads, in estimated tokens. About 37 seconds of prompt
# reading for the 9B model on an M-series Mac without a cache hit; a base
# and a day's filings fit well inside it (workspace design memory-compile,
# D4).
TARGET_TOKENS = 8_192

# The context window when stack.toml names none (`[ai] context_window`):
# what the agent's preset assumes and what the rig's oMLX is set to.
DEFAULT_WINDOW = 32_768

# The night folds until its cards are done or the model has worked this
# long; the rest waits for the next night. An import of a few hundred
# filings would otherwise hold the AI server into the morning.
NIGHT_MODEL_SECONDS = 60 * 60


def budget(window: int, answer_cap: int) -> int:
    """The estimated tokens a call may read: the target, or less when a small
    window leaves room for less after the answer and a tenth of slack (the
    estimate is a guess, so a tenth of the window stays free)."""
    return max(0, min(TARGET_TOKENS, window - answer_cap - window // 10))


def window_of(value: str | None) -> int:
    """The context window `AI_CONTEXT_WINDOW` names, `DEFAULT_WINDOW` without a usable one."""
    try:
        window = int(str(value or "").strip())
    except ValueError:
        return DEFAULT_WINDOW
    return window if window > 0 else DEFAULT_WINDOW


# ── Citations ────────────────────────────────────────────────────────────
#
# An answer cites its sources by number in three places: `[N]` inside a
# text, a `cite` list, and the keys of a person's `lately` lines. Moving an
# answer from one numbering to another goes through `renumbered`, so all
# three move together.

_CITATION = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


def citations_in(text: str) -> list[int]:
    """The numbers `text` cites (`[N]`, `[N, M]`, `[N][M]`), each once, in first-seen order."""
    seen: list[int] = []
    for match in _CITATION.finditer(text or ""):
        for part in match.group(1).split(","):
            n = int(part.strip())
            if n not in seen:
                seen.append(n)
    return seen


def renumber_citations(text: str, remap: dict[int, int], *, drop_unknown: bool = False) -> str:
    """Rewrite every ``[N]``/``[N, M]`` citation through ``remap``.

    Numbers without a mapping pass through unchanged, or with
    ``drop_unknown`` are removed, a citation left empty with them.
    Single-pass over citation groups, so chained renumbering (1→2 while
    2→3) cannot double-apply.
    """
    def _sub(m: re.Match) -> str:
        nums = [int(x) for x in re.split(r",\s*", m.group(2))]
        kept = [remap[n] for n in nums if n in remap] if drop_unknown \
            else [remap.get(n, n) for n in nums]
        return m.group(1) + "[" + ", ".join(map(str, kept)) + "]" if kept else ""

    return re.sub(r"(\s?)\[(\d+(?:,\s*\d+)*)\]", _sub, text)


def renumbered(value, remap: dict[int, int], key: str = ""):
    """A model's answer with every citation number moved through `remap`.

    A number `remap` lacks is dropped.
    """
    if isinstance(value, str):
        return renumber_citations(value, remap, drop_unknown=True)
    if isinstance(value, list):
        if key == "cite":
            return [remap[n] for n in value if isinstance(n, int) and n in remap]
        return [renumbered(v, remap, key) for v in value]
    if isinstance(value, dict):
        if key == "lately":
            return {str(remap[int(k)]): renumbered(v, remap)
                    for k, v in value.items() if k.isdigit() and int(k) in remap}
        return {k: renumbered(v, remap, k) for k, v in value.items()}
    return value


def cited(value, key: str = "") -> set[int]:
    """Every number an answer cites, wherever it cites it."""
    if isinstance(value, str):
        return set(citations_in(value))
    if isinstance(value, list):
        if key == "cite":
            return {n for n in value if isinstance(n, int) and not isinstance(n, bool)}
        return set().union(*(cited(v, key) for v in value)) if value else set()
    if isinstance(value, dict):
        found = {int(k) for k in value if k.isdigit()} if key == "lately" else set()
        return found.union(*(cited(v, k) for k, v in value.items())) if value else found
    return set()


_GONE = object()


def without_items_citing_only(value, stale: set[int], key: str = ""):
    """`value` without the items whose every citation is in `stale`.

    An item is an entry of a list (a relative, an insurance) or a text;
    one that cites nothing, such as a person's line on the home page, is
    kept. Citations of `stale` left in the items that stay are `renumbered`'s
    to drop.
    """
    def only_stale(found: set[int]) -> bool:
        return bool(found) and found <= stale

    if isinstance(value, str):
        return _GONE if only_stale(set(citations_in(value))) else value
    if isinstance(value, list) and key != "cite":
        kept = [without_items_citing_only(v, stale, key) for v in value]
        return [v for v in kept if v is not _GONE]
    if isinstance(value, dict):
        if "cite" in value and only_stale(cited(value.get("cite"), "cite")):
            return _GONE
        if key == "lately":
            return {k: v for k, v in value.items() if not (k.isdigit() and int(k) in stale)}
        kept = {k: without_items_citing_only(v, stale, k) for k, v in value.items()}
        return {k: v for k, v in kept.items() if v is not _GONE}
    return value


# ── The base ─────────────────────────────────────────────────────────────

def key(entry: dict) -> str:
    """What a base calls a card: its id, or its path for a file without one."""
    return entry.get("card_id") or entry.get("rel") or ""


@dataclass(frozen=True)
class Cited:
    """A card the base cites, as the prompt's title line and the page's References show it."""

    card: str
    version: str
    title: str
    link: str
    date: str = ""
    type: str = ""

    def entry(self) -> dict:
        """The card as an index entry, for the page module's choice of lately sources."""
        return {"card_id": self.card, "version": self.version, "title": self.title,
                "date": self.date, "type": self.type}

    def ref(self) -> tuple[str, str]:
        """The (title, link) pair the page's References row shows."""
        return self.title, self.link


@dataclass(frozen=True)
class Base:
    """What a page's model answer stands on between nights.

    `answer` is the model's JSON answer citing `cites` as 1..k ("" for the
    empty base); `read` every card it had read, by `key`, at its version;
    `prompt` the fingerprint of the prompt file it was written under.
    """

    answer: str
    cites: tuple[Cited, ...]
    read: dict[str, str] = field(default_factory=dict)
    prompt: str = ""

    @classmethod
    def empty(cls, prompt: str) -> "Base":
        return cls("", (), {}, prompt)

    def stale(self, entries: list[dict]) -> set[str]:
        """The cards read at a version they no longer have, or no longer there."""
        now = {key(e): e.get("version") or "" for e in entries}
        return {k for k, version in self.read.items() if now.get(k) != version}

    def without(self, stale: set[str]) -> "Base":
        """The base with no item that cites only `stale` cards, and no word of them.

        The answer drops those items, the citations of `stale` cards and the
        cards themselves from `cites` and `read`, so the fold sends a changed
        card again and never sees what it said before.
        """
        if not stale:
            return self
        gone = {n for n, c in enumerate(self.cites, start=1) if c.card in stale}
        read = {k: v for k, v in self.read.items() if k not in stale}
        if not gone:
            return Base(self.answer, self.cites, read, self.prompt)
        data = without_items_citing_only(json.loads(self.answer), gone)
        return _compacted(data, list(self.cites), read, self.prompt)

    def since(self, entries: list[dict]) -> list[dict]:
        """The `entries` the base has not read at their version, in their order."""
        return [e for e in entries if self.read.get(key(e)) != (e.get("version") or "")]

    def sources(self, chunk: list[dict]) -> list[dict]:
        """A prompt's numbered sources: the cited cards, then `chunk` numbered on."""
        return [c.entry() for c in self.cites] + chunk

    def refs(self, chunk: list[dict], ref) -> list[tuple[str, str]]:
        """The (title, link) of each of `sources(chunk)`; `ref` reads an index entry's."""
        return [c.ref() for c in self.cites] + [ref(e) for e in chunk]

    def folded(self, raw: str, chunk: list[dict], ref) -> "Base":
        """The base a model's answer `raw` over `sources(chunk)` makes.

        Only the cards the answer cites stay in `cites`, in the order the
        prompt numbered them, and the answer is renumbered to match; every
        card of `chunk` joins `read`. `ref` reads an index entry's (title,
        link). `ValueError` when `raw` is no JSON.
        """
        sources = list(self.cites) + [_cited(e, ref) for e in chunk]
        read = {**self.read, **{key(e): e.get("version") or "" for e in chunk}}
        return _compacted(json.loads(raw), sources, read, self.prompt)


def _cited(entry: dict, ref) -> Cited:
    title, link = ref(entry)
    return Cited(key(entry), entry.get("version") or "", title, link,
                 str(entry.get("date") or ""), str(entry.get("type") or ""))


def _compacted(data, sources: list[Cited], read: dict[str, str], prompt: str) -> Base:
    """A base from answer `data` over `sources`, keeping only the cited ones, renumbered."""
    used = sorted(n for n in cited(data) if 1 <= n <= len(sources))
    remap = {old: new for new, old in enumerate(used, start=1)}
    answer = json.dumps(renumbered(data, remap), ensure_ascii=False)
    return Base(answer, tuple(sources[n - 1] for n in used), read, prompt)


# ── In the view ──────────────────────────────────────────────────────────
#
# The base is machine data in a page a person reads: one HTML comment line
# after the answer line, zlib-compressed JSON in base64. `read` names every
# card the page has read, which at a few thousand filings is tens of
# kilobytes of ids and versions uncompressed.

def encode(base: Base) -> str:
    """`base` as the one-line text `decode` reads back."""
    data = {"answer": base.answer, "cites": [list(_fields(c)) for c in base.cites],
            "read": base.read, "prompt": base.prompt}
    packed = zlib.compress(json.dumps(data, ensure_ascii=False).encode("utf-8"), 9)
    return base64.b64encode(packed).decode("ascii")


def decode(text: str) -> Base | None:
    """The base `encode` wrote, or None for anything it did not."""
    try:
        data = json.loads(zlib.decompress(base64.b64decode(text, validate=True)))
        return Base(str(data["answer"]), tuple(Cited(*c) for c in data["cites"]),
                    {str(k): str(v) for k, v in data["read"].items()}, str(data["prompt"]))
    except (binascii.Error, zlib.error, ValueError, KeyError, TypeError, AttributeError):
        return None


def _fields(c: Cited) -> tuple[str, ...]:
    return c.card, c.version, c.title, c.link, c.date, c.type
