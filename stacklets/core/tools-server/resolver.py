"""Logical link resolution — stable entity paths to live URLs.

The resolver is the indirection that keeps links posted into append-only
Matrix history from rotting. A chat message carries `home.<domain>/go/topic/camping`;
the resolver maps that logical path to wherever the camping topic's page lives
*right now*, in whichever hosting mode the stack runs (pretty domain or raw
IP:port). Rename a topic, flip from port mode to domain mode, move Paperless —
the frozen chat link still lands, because it re-resolves at click time.

Entities are first-class and explicit — `docs`, `topic`, `person` — so the noun
in the URL says which kind it is. That mirrors the CLI (`stack memory topic
camping todo`) and, crucially, means the resolver never has to *guess* whether
"camping" is a topic or a person by inspecting the household roster: the path
already told it.

This module is the *pure* half: a logical path → a wiki-relative target path.
The HTTP layer (`server.py`) turns that target into a full URL using the
mode-correct public base and issues the 302. Keeping the mapping pure and
stdlib-only is what makes it unit-testable without standing up FastAPI.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Callable
from urllib.parse import quote

from stack.cards.lookup import Found, Removal
from stack.cards import CARD_ID_FIELDS
from stack.people import person_id

# A trailing `todo`/`todos` segment points at the entity's task list instead of
# its overview page. Both spellings accepted — people type either.
_TODO_LEAVES = {"todo", "todos"}


def _split_leaf(segments: list[str]) -> tuple[list[str], str]:
    """Peel a `todo`/`todos` leaf off the path: returns (scope, page)."""
    if segments and segments[-1].lower() in _TODO_LEAVES:
        return segments[:-1], "todos"
    return segments, "about"


def resolve_topic_target(segments: list[str], *, shared_bucket: str) -> str | None:
    """Map `/topic/<segments>` to a wiki-relative target path.

    A lone segment is a shared topic under `shared_bucket`
    (`/topic/camping` → `family/camping/about`); a `<member>/<slug>` pair is a
    personal topic, taken as a literal vault path (`/topic/homer/gravel` →
    `homer/gravel/about`). A `todo`/`todos` leaf selects the topic's list,
    `family/lists/camping`.
    Deeper than two scope segments is not a valid topic path — None, so the
    HTTP layer 404s rather than pointing at a page that 404s anyway.
    """
    scope, page = _split_leaf(segments)
    if not scope or len(scope) > 2:
        return None
    bucket = shared_bucket if len(scope) == 1 else scope[0]
    if page == "todos":
        # A topic's todos moved to the list named after it; links posted to
        # chat before the move still open it.
        return f"{bucket}/lists/{scope[-1]}"
    return f"{bucket}/{scope[-1]}/{page}"


def resolve_list_target(segments: list[str], *, shared_bucket: str) -> str | None:
    """Map `/list/<slug>` to the family's list of that name (`family/lists/<slug>`)."""
    if len(segments) != 1 or not segments[0]:
        return None
    return f"{shared_bucket}/lists/{segments[0]}"


def resolve_person_target(segments: list[str]) -> str | None:
    """Map `/person/<member>` to a wiki-relative target path.

    A person is a top-level bucket, so exactly one scope segment is valid
    (`/person/homer` → `homer/about`, `/person/homer/todo` → `homer/todos`).
    """
    scope, page = _split_leaf(segments)
    if len(scope) != 1:
        return None
    return f"{scope[0]}/{page}"


def card_page_target(kind: str, path: str, card_id: str) -> str | None:
    """Wiki target for a card at vault `path`, or None.

    A note or a bookmark is a page of its own: the wiki serves it at its
    vault path without the suffix. A diary card is not published on its
    own; the family reads the month, and the diary renders each card
    there under an anchor named by its entry id. Its folder under
    `entries/` is that month (`<bucket>/diary/entries/2026/08/...` is on
    `<bucket>/diary/2026/08`).

    >>> card_page_target("capture", "family/camping/notes/2026/08/tent-3a338e.md", "$e")
    'family/camping/notes/2026/08/tent-3a338e'
    >>> card_page_target("diary", "family/diary/entries/2026/08/2026-08-14-3f9a0c1b2d.md",
    ...                  "3f9a0c1b2d")
    'family/diary/2026/08#3f9a0c1b2d'
    """
    if kind != "diary":
        return path.removesuffix(".md")
    parts = path.split("/")
    if len(parts) < 5 or parts[-4] != "entries":
        return None
    month = "/".join(parts[:-4] + parts[-3:-1])
    return f"{month}#{quote(card_id, safe='')}"


def card_target(path: str, fields: dict) -> str | None:
    """Wiki target for the card at vault `path`, found by its `card_id`.

    Where the older link of its kind lands: a diary card on its month at
    its anchor (`card_page_target`), any other card on its own page, the
    vault path without the suffix. None for a diary card outside the
    diary's folders, which no page shows.

    A person, a topic and a correspondent are not pages on the wiki: each
    is read through the view compiled from its card, and the link lands on
    that view, where the wiki's own links to the card land too
    (`memory/quartz/plugins/vaultLinks.ts`). A person's is the page
    `/person/<id>` opens, a topic's is `about` in the topic's folder, and a
    correspondent's is `about` in a folder named after its card.

    >>> card_target("marge/notes/2026/10/art-supplies-5f9740.md", {"type": "note"})
    'marge/notes/2026/10/art-supplies-5f9740'
    >>> card_target("family/people/homer.md", {"type": "person"})
    'homer/about'
    >>> card_target("family/camping/topic.md", {"type": "topic"})
    'family/camping/about'
    >>> card_target("family/correspondents/kwik-e-mart.md", {"type": "correspondent"})
    'family/correspondents/kwik-e-mart/about'
    >>> card_target("family/diary/entries/2026/08/2026-08-14-3f9a0c1b2d.md",
    ...             {"type": "diary", "entry_id": "3f9a0c1b2d"})
    'family/diary/2026/08#3f9a0c1b2d'
    """
    kind = str(fields.get("type") or "")
    card = PurePosixPath(path)
    if kind == "diary":
        return card_page_target("diary", path, str(fields.get("entry_id") or ""))
    if kind == "person":
        return resolve_person_target([person_id(card.stem)])
    if kind == "topic":
        return f"{card.parent}/about"
    if kind == "correspondent":
        return f"{card.with_suffix('')}/about"
    return path.removesuffix(".md")


def gone_text(card_id: str, removal: Removal) -> str:
    """What a link to a removed card answers: that it is gone, and when.

    A link in a chat's history keeps saying what became of the card. The
    commit's subject is left out: it can carry the card's title, and the
    page answers anyone who has the link.

    >>> gone_text("note-7f3a9c21e4", Removal("0322e4c", "2026-10-19", "marge", "remove: x",
    ...                                     "marge/notes/x.md"))
    'The card note-7f3a9c21e4 is gone: marge removed it on 2026-10-19 (commit 0322e4c).'
    """
    return (f"The card {card_id} is gone: {removal.who} removed it on {removal.day} "
            f"(commit {removal.sha}).")


def card_link_answer(
    card_id: str, *, wiki_base: str,
    find: "Callable[[str], Found | None]",
    removal: "Callable[[str], Removal | None]",
) -> tuple[int, str] | None:
    """What `/<prefix>/<card_id>` answers: `(302, url)`, `(410, text)` or None.

    `find` is `stack.cards.lookup.find_by_card_id` over the tree the wiki serves,
    so a current id and a former one land on the same page. `removal` is
    `stack.cards.lookup.removal_of` over the memory repo, asked only when no card
    carries the id: one did, and the answer says when it went. None (a
    404) for an id no card ever carried. Both are injected by the HTTP
    layer, like `build_redirect`'s `find_card`, to keep the I/O out of here.
    """
    found = find(card_id)
    if found is not None:
        target = card_target(found.path, found.fields)
        return (302, f"{wiki_base.rstrip('/')}/{target}") if target else None
    gone = removal(card_id)
    return (410, gone_text(card_id, gone)) if gone else None


def build_redirect(
    kind: str, rest: list[str], *,
    docs_base: str, wiki_base: str, shared_bucket: str,
    find_card: "Callable[[str, str], str | None] | None" = None,
) -> str | None:
    """Full redirect URL for a `/<prefix>/<kind>/<rest>` request, or None.

    `kind` is `docs`, `topic`, `person`, `list`, or a card kind from
    `stack.cards` (`capture`, `diary`); `rest` is the remaining path
    segments. `docs_base`/`wiki_base` are the public base URLs of Paperless
    and the wiki — already mode-correct, computed once by the HTTP layer from
    env, so this stays a pure string join. None (→ 404) for an unknown kind, a
    non-numeric doc id, or an entity shape the resolver rejects.

    A card kind is the one whose target cannot be computed from the path:
    the card id says *which* card, never where it is now. `find_card(kind,
    card_id)` is injected by the HTTP layer for exactly that lookup and
    answers with the card's vault path, which keeps the I/O out of here and
    this module unit-testable without a vault on disk. Left unset, a card
    link 404s rather than guessing.
    """
    if kind == "docs":
        if len(rest) != 1 or not rest[0].isdigit():
            return None
        return f"{docs_base.rstrip('/')}/documents/{rest[0]}/details"
    if kind in CARD_ID_FIELDS:
        if len(rest) != 1 or find_card is None:
            return None
        path = find_card(kind, rest[0])
        target = card_page_target(kind, path, rest[0]) if path else None
    elif kind == "topic":
        target = resolve_topic_target(rest, shared_bucket=shared_bucket)
    elif kind == "person":
        target = resolve_person_target(rest)
    elif kind == "list":
        target = resolve_list_target(rest, shared_bucket=shared_bucket)
    else:
        return None
    if target is None:
        return None
    return f"{wiki_base.rstrip('/')}/{target}"
