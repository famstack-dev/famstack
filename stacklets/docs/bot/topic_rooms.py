"""Topic-room name parsing, slug derivation, and state helpers.

A Matrix room whose name begins with `Thema:` (German) or `Topic:`
(English) is a topic room. The full design lives at
`docs/design/brain/topic-rooms.md`.

This module is the parser + state-shape layer: pure functions, no
Matrix, no filesystem. Everything I/O-shaped — reading and writing
`dev.famstack.capture` room state, creating the `about.md` scaffold,
watching for joins — lives in the archivist. The split keeps the
rules testable without standing up a fake Matrix client.

Public surface:

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
  - `make_room_state(...)`            build the `dev.famstack.capture`
                                      state content for a new topic
  - `binding_from_state(state)`       extract a `TopicBinding` (the
                                      routing input for the capture
                                      pipeline) from existing state

The parser does not know about Matrix users, the vault directory, or
the room state event transport. It returns dataclasses; the archivist
composes them with its own world model.
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


# ── State shape and routing binding ──────────────────────────────────────
#
# Everything below is shape-only. The archivist owns the I/O: reading
# room state through nio, writing it back on bootstrap, passing the
# binding into the capture pipeline. These helpers exist so the rules
# stay unit-testable.

@dataclass(frozen=True)
class TopicBinding:
    """Routing input the archivist passes into the capture pipeline.

    Carries everything a single capture in a topic room needs: the
    bucket it files under (shared topic = the slug; personal topic =
    `<localpart>/<slug>`), the seed tags applied before classification,
    and the display name for any operator-facing surfaces. Scope is
    informational (drives the promotion handler in Step 5; the routing
    itself doesn't branch on it).
    """

    bucket: str
    seed_topics: list[str]
    display_name: str
    scope: Scope
    slug: str


def bucket_for_scope(
    slug: str, scope: Scope, *,
    sender_localpart: str, shared_bucket: str,
) -> str:
    """Compose a bucket path for a topic + scope + sender + shared bucket.

    Topics always nest inside the bucket that owns them: personal
    topics under `<localpart>/`, shared topics under the configured
    `<shared_bucket>/`. The top level of the vault stays purely
    access-scope (one folder per privacy boundary); topics never
    appear there. Promotion (Step 5) is therefore a clean
    bucket-to-bucket `git mv <localpart>/<slug>/ <shared_bucket>/<slug>/`.

    The shared-bucket nesting has a second property: a default
    sender-scoped search (`["<shared_bucket>/", "<localpart>/"]`)
    naturally finds shared-topic content -- the family member asking
    a question in #documents discovers their camping notes without
    knowing the topic exists.
    """

    if scope == "shared":
        return f"{shared_bucket}/{slug}"
    return f"{sender_localpart}/{slug}"


def make_room_state(
    *,
    parsed: ParsedTopicName,
    scope: Scope,
    bootstrapped_by: str,
    sender_localpart: str,
    shared_bucket: str,
    bootstrapped_at: str,
) -> dict:
    """Build the `dev.famstack.capture` state content for a new topic.

    The shape mirrors the schema pinned in
    docs/design/brain/topic-rooms.md §Room state schema. ``kind=topic``
    is the discriminator that downstream consumers (capture routing,
    query scoping, promotion handler) match on.

    ``bootstrapped_by`` is the Matrix user id of whoever the archivist
    is recording as the originator — the capture sender for lazy
    bootstrap, the inviter for the future eager (on_invite) path.
    Same answer regardless of trigger: who put this topic on the map.

    ``shared_bucket`` is the household's configured shared-bucket slug
    (`family` by default; deskstack uses `office`). The bucket field
    becomes ``<shared_bucket>/<slug>`` for shared topics and
    ``<localpart>/<slug>`` for personal ones.
    """

    bucket = bucket_for_scope(
        parsed.slug, scope,
        sender_localpart=sender_localpart, shared_bucket=shared_bucket,
    )
    return {
        "kind": "topic",
        "bucket": bucket,
        "slug": parsed.slug,
        "display_name": parsed.display_name,
        "default_topics": [parsed.slug],
        "scope": scope,
        "extract_knowledge": True,
        "bootstrapped_at": bootstrapped_at,
        "bootstrapped_by": bootstrapped_by,
    }


def binding_from_state(state: dict | None) -> TopicBinding | None:
    """Extract a routing binding from `dev.famstack.capture` state.

    Returns None when the state is absent or carries a different
    ``kind`` than ``"topic"`` (the documents-room state and future
    capture-room kinds ride the same event type). Malformed state
    (missing required fields) also returns None — the archivist
    falls back to sender-based routing and the next capture will
    re-bootstrap if needed.
    """

    if not state or state.get("kind") != "topic":
        return None
    bucket = state.get("bucket")
    slug = state.get("slug")
    display = state.get("display_name")
    scope = state.get("scope")
    if not (
        isinstance(bucket, str) and bucket
        and isinstance(slug, str) and slug
        and isinstance(display, str) and display
        and scope in ("personal", "shared")
    ):
        return None
    seeds_raw = state.get("default_topics") or []
    seeds = [s for s in seeds_raw if isinstance(s, str) and s]
    return TopicBinding(
        bucket=bucket,
        seed_topics=seeds,
        display_name=display,
        scope=scope,
        slug=slug,
    )
