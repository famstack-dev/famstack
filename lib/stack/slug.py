"""The one rule that turns a name into a path segment or an id.

A topic room's folder, a document's or a correspondent's file name, a
list's file and a card id are all spelled from a name the family typed,
and every one of them goes through `path_slug`. One rule matters
because a name is spelled again whenever the code looks a file up by
it: two rules that disagree on "ß" put one correspondent in two files.

German letters are spelled out, as a German reader writes them without
umlauts: "Nähprojekte" is `naehprojekte`, not `nahprojekte` ("near
projects"). Other accents are dropped ("Café" is `cafe`), anything that
is not a letter or a digit becomes a hyphen.

The module is stdlib-only and imports nothing from the framework: the
framework uses it as `stack.slug`, and the agent container mounts this
one file as `slug`, so both spell a topic room's folder the same way.
Two JavaScript copies follow the same rule (the wiki's `fold` and the
dashboard's `username`); `tests/fixtures/path-slugs.json` holds the
examples all of them are tested against.
"""

from __future__ import annotations

import re
import unicodedata

# German spellings before the general rule: NFKD alone makes Jürgen "jurgen".
_SPELLED_OUT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})

# Anything a path segment does not hold: one hyphen stands for a run of them.
_NOT_A_TO_Z = re.compile(r"[^a-z0-9]+")

# A defensive ceiling for file names; document titles are short anyway.
DEFAULT_LIMIT = 60


def in_a_to_z(text: str) -> str:
    """`text` lowercased and in ASCII: German letters spelled out (ü is ue,
    ß is ss), other accents dropped (é is e), anything else left out.

    >>> in_a_to_z("Jürgen Groß")
    'juergen gross'
    """
    spelled = unicodedata.normalize("NFKD", text.lower().translate(_SPELLED_OUT))
    return "".join(c for c in spelled if c.isascii())


def path_slug(name: str, *, limit: int = DEFAULT_LIMIT) -> str:
    """`name` as a path segment: lowercase a to z and digits, hyphens between.

    Longer than `limit`, it is cut back to the last hyphen that fits, so a
    word is never cut in half. Empty when nothing of the name is left; the
    caller decides what that means.

    >>> path_slug("Nähprojekte")
    'naehprojekte'
    >>> path_slug("Müller & Söhne")
    'mueller-soehne'
    >>> path_slug("Fußball")
    'fussball'
    >>> path_slug("Moe's Tavern")
    'moe-s-tavern'
    >>> path_slug("Camping im Sommer am See", limit=12)
    'camping-im'
    >>> path_slug("!!!")
    ''
    """
    slug = _NOT_A_TO_Z.sub("-", in_a_to_z(name)).strip("-")
    if len(slug) > limit:
        slug = slug[:limit]
        if (cut := slug.rfind("-")) > 0:
            slug = slug[:cut]
        slug = slug.strip("-")
    return slug
