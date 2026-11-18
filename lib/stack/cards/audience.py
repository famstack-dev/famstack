"""Who may read a card: the audience rule of the family memory.

    readable("homer/notes/grill.md", frozenset({"homer"}))      # True: his own folder
    readable("homer/notes/grill.md", frozenset({"marge"}))      # False
    readable("family/lists/camping.md", frozenset({"marge"}))   # True: the shared folder
    audience_of("homer")                                         # frozenset({"homer"})
    audience_of("")                                              # None: the admin's view

An audience is the people who will see an answer, as person slugs. The
first folder of a card's path is its bucket: the shared bucket (`family`)
and the top level are read by everyone, a person's bucket (`homer/`) only
when that person is the whole audience. No audience (None) is the admin's
view, which reads every card; a caller decides that before it asks here.

The memory applies the rule to every read (its graph, its cards API, its
commands), and core's card pages apply it to what the memory's cards API
cannot narrow for them: a removed card's history and a `/go` link's
lookup on disk. One rule in one place, so a page and the memory never
disagree about who may read a card. `audience_of` is the wire format of
the cards API's `audience` parameter, parsed the same way on both sides.

Pure and stdlib only: the host CLI, the curator and core's tools server
all import it.
"""

from __future__ import annotations

__all__ = ["readable", "audience_of"]

# The shared bucket when the instance names none (`SHARED_BUCKET`).
SHARED_BUCKET = "family"


def readable(path: str, audience: frozenset[str], *, shared_bucket: str = SHARED_BUCKET) -> bool:
    """Whether everyone in `audience` may read the card at `path`.

    A person's bucket is the first folder of the path, named by their slug;
    it is read only when that person is the whole audience.
    """
    # SAFETY: a person's own folder never reaches anyone else, also not a
    # group they belong to: their notes are written for them alone.
    bucket, _, rest = path.partition("/")
    if not rest or bucket == shared_bucket:
        return True
    return audience == frozenset({bucket})


def audience_of(value: str | None) -> frozenset[str] | None:
    """The audience a comma-separated `value` names ("homer", "homer,marge"),
    lowercased; None for an empty or missing value, which is the admin's view."""
    if not value:
        return None
    return frozenset(p.strip().lower() for p in value.split(",") if p.strip())
