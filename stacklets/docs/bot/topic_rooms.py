"""Topic-room name parsing and slug derivation.

A Matrix room whose name begins with `Thema:` (German) or `Topic:`
(English) is a topic room. The full design lives at
`docs/design/brain/topic-rooms.md`.

This module is the parser layer: pure functions, no Matrix, no
filesystem. Everything I/O-shaped — bootstrapping the room state,
writing the `about.md` scaffold, watching for joins — lives in the
archivist. The split keeps the rules testable without standing up
a fake Matrix client.

The four entry points cover the four parser concerns:

  - `parse_topic_name(room_name)`     recognize the prefix, build a
                                      `ParsedTopicName` carrying
                                      display name + slug + language
  - `derive_slug(display)`            NFD-fold + lowercase + hyphenate
                                      (exposed for the rename CLI,
                                      which slugs a string the user
                                      typed)
  - `is_reserved(slug, reserved)`     refuse collisions with vault
                                      built-ins and the configured
                                      personal-bucket localparts
  - `scope_from_members(human_count)` "personal" if one human in the
                                      room, "shared" otherwise

The parser does not know about Matrix users, the vault directory, or
the room state event. It returns dataclasses; the archivist composes
them with its own world model.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Literal, Optional


# ── Public types ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ParsedTopicName:
    """Result of `parse_topic_name`. Carries everything the bootstrap
    handler needs to decide whether to create a topic, what folder
    to make, and what tag to seed.

    `display_name` preserves the casing the user typed — it shows up
    in `about.md` frontmatter, the bootstrap confirmation message,
    and the topic listing CLI. `slug` is normalized for filesystem
    and `topics:` use. `prefix_lang` is informational only; routing
    never branches on language.
    """

    display_name: str
    slug: str
    prefix_lang: Literal["de", "en"]


Scope = Literal["personal", "shared"]


# ── Prefix recognition ───────────────────────────────────────────────────
#
# Both `Thema:` and `Topic:` are accepted in any household, regardless
# of `[core] language`. The colon may have arbitrary whitespace around
# it; leading whitespace before the prefix is tolerated too. A trailing
# empty body fails the parser — a topic room with no display name has
# nothing to bootstrap from.

_PREFIX_RE = re.compile(
    r"""
    ^\s*                       # leading whitespace tolerated
    (?P<prefix>thema|topic)    # the two recognized languages
    \s*:\s*                    # colon with arbitrary surrounding space
    (?P<body>\S.*?)            # display name must start with a non-space char
    \s*$                       # and trailing whitespace gets stripped
    """,
    re.IGNORECASE | re.VERBOSE,
)


def parse_topic_name(room_name: str) -> Optional[ParsedTopicName]:
    """Recognize `Thema: …` / `Topic: …` and return a parsed result.

    Returns `None` when the name doesn't match the prefix pattern or
    when the body after the colon is empty. The caller treats `None`
    as "this is a normal room, not a topic."
    """

    if not room_name:
        return None
    match = _PREFIX_RE.match(room_name)
    if match is None:
        return None
    display = match.group("body").strip()
    if not display:
        return None
    slug = derive_slug(display)
    if not slug:
        # Display name was all punctuation / diacritics that fold to
        # nothing. Refuse rather than create a slugless folder.
        return None
    lang: Literal["de", "en"] = (
        "de" if match.group("prefix").lower() == "thema" else "en"
    )
    return ParsedTopicName(display_name=display, slug=slug, prefix_lang=lang)


# ── Slug derivation ──────────────────────────────────────────────────────
#
# The slug is the bucket path on disk and the value of `topics:` in
# every file beneath that bucket. It must be filesystem-safe, grep-able
# without quoting, and stable across reformatting tools.
#
# The recipe:
#   1. NFD-normalize and strip combining marks (Café -> Cafe)
#   2. Lowercase
#   3. Replace runs of non-alphanumerics with a single hyphen
#   4. Strip leading + trailing hyphens
#   5. Truncate to 40 chars, snapping back to the last hyphen boundary
#      so we never end mid-word

_MAX_SLUG_CHARS = 40


def derive_slug(display: str) -> str:
    """Slugify a display name for filesystem and tag use.

    Empty / whitespace-only / punctuation-only input returns an empty
    string. The caller is responsible for refusing empty slugs at
    bootstrap (see `is_reserved`).
    """

    folded = unicodedata.normalize("NFKD", display)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    folded = folded.lower()
    # Anything not [a-z0-9] becomes a hyphen, runs collapse.
    folded = re.sub(r"[^a-z0-9]+", "-", folded)
    folded = folded.strip("-")
    if len(folded) > _MAX_SLUG_CHARS:
        folded = folded[:_MAX_SLUG_CHARS]
        # Snap back to the last hyphen so we don't end mid-word.
        last_hyphen = folded.rfind("-")
        if last_hyphen > 0:
            folded = folded[:last_hyphen]
        folded = folded.strip("-")
    return folded


# ── Reserved-name check ──────────────────────────────────────────────────
#
# The caller assembles the reserved set. At minimum it carries the
# vault built-ins (`meta`, `wiki`, `archive`, `_unfiled`), the
# configured `[core] shared_bucket`, and every known household-member
# localpart. Empty slugs are always refused as a defensive guard.

def is_reserved(slug: str, reserved: Iterable[str]) -> bool:
    """True when the slug would collide with a vault built-in or with
    an existing top-level bucket (shared or personal)."""

    if not slug:
        return True
    return slug in set(reserved)


# ── Scope detection ──────────────────────────────────────────────────────
#
# The archivist passes the count of *human* members in the room, i.e.
# excluding itself and any other known bot accounts. The caller decides
# what counts as a bot; this function only does the threshold check.

def scope_from_members(human_count: int) -> Scope:
    """Decide topic scope from member count at bootstrap time.

    One human (or zero, as a defensive fallback) means the topic is
    personal — its folder nests under the sender's personal bucket.
    Two or more humans means shared — its folder lives at the vault
    root.
    """

    if human_count <= 1:
        return "personal"
    return "shared"
