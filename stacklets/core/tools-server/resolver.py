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

Where each link lands:

    /go/capture/<event id>      the card's page        /card/<card id>
    /go/diary/<entry id>        the card's page        /card/<card id>
    /go/docs/<n>                the document's card,   /card/<card id>
                                else Paperless         <docs>/documents/<n>/details
    /go/topic/camping[/todo]    the wiki               <wiki>/family/camping/about
    /go/topic/homer/gravel      the topic's memory     /memory/topic/<card id>
                                page
    /go/topic/homer/gravel/todo the list's page        /card/<card id>
    /go/person/homer[/todo]     the person's memory    /memory/person/homer
                                page
    /go/list/<slug>             the list's page,       /card/<card id>
                                the family's first

`/go/card/<card id>` is not here: a card id is already the card's
address, so the server sends it straight to `/card/<card id>`, and the
page decides which card the id names (a former id, a removed card, no
card; `memory_ui.pages.open_card`). One place answers "which card is this", so
a removed card says the same at its link and at its own address.

The wiki is one site for every paired device, so it holds only what the
family shares. A person's page and a person's own topic are not in it:
their links land on the memory pages, which check who asks.

A link by another id than the card's own (an event id, an entry id, a
Paperless number, a list's name) needs a lookup to find the card. The
lookup answers the card's id, so the link lands on the card's address in
one redirect (`memory_ui.addresses.card_address`); a card without an id
lands on its file path, which the page sends on. The pages compiled from
many cards of the family (a shared topic) stay in the wiki, which compiles
them. A list is one card, so its link lands on its page too, the family's list
first and else a person's own of that name.

This module is the *pure* half: a logical path → a target. The HTTP layer
(`server.py`) passes in the lookups (`stack.cards.lookup`) and the public
bases of Paperless and the wiki, mode-correct, and issues the 302.
Keeping the mapping pure and stdlib-only is what makes it unit-testable
without standing up FastAPI.
"""

from __future__ import annotations

from typing import Callable

from memory_ui.addresses import card_address as address, memory_page, paperless_page
from stack.cards import CARD_ID_FIELDS
from stack.lists import LISTS_DIR
from stack.vault import TOPIC_CARD

__all__ = ["resolve_topic_target", "build_redirect"]


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


def build_redirect(
    kind: str, rest: list[str], *,
    docs_base: str, wiki_base: str, shared_bucket: str,
    find_card: "Callable[[str, str], str | None] | None" = None,
    find_document: "Callable[[str], str | None] | None" = None,
    find_list: "Callable[[str], str | None] | None" = None,
    find_card_id: "Callable[[str], str | None] | None" = None,
) -> str | None:
    """Full redirect URL for a `/<prefix>/<kind>/<rest>` request, or None.

    `kind` is `docs`, `topic`, `person`, `list`, or a card kind from
    `stack.cards` (`capture`, `diary`); `rest` is the remaining path
    segments. `docs_base`/`wiki_base` are the public base URLs of Paperless
    and the wiki — already mode-correct, computed once by the HTTP layer from
    env, so this stays a pure string join. None (→ 404) for an unknown kind, a
    non-numeric doc id, or an entity shape the resolver rejects.

    Each lookup answers where the card is: its card id, or its file path
    when it has none; None when no card answers. The link lands on that
    address. The HTTP layer injects them (`stack.cards.lookup`, then
    `card_id_at`), to keep the I/O out of here.

    A card kind is the one whose target cannot be computed from the path:
    the kind's id says *which* card, never where it is now.
    `find_card(kind, id)` is that lookup. Left unset, a card link 404s
    rather than guessing.

    `find_document(n)` finds the document card filed from Paperless
    document `n`. With no such card, or no lookup, the link lands on the
    document in Paperless, and is a 404 while core has no Paperless
    address.

    `find_list(slug)` finds the list of that name, the family's or a
    person's own (`stack.cards.lookup.find_list`). None when no list has
    the name.

    `find_card_id(path)` answers the card id of the card at a brain path,
    None when there is no card there or the device may not read it. A
    person's own topic is found by it (`homer/gravel/topic.md`), and so is
    its list. A person's page needs no lookup: its address is the slug.

        >>> build_redirect("docs", ["72"], docs_base="http://mac.lan:42020", wiki_base="",
        ...                shared_bucket="family", find_document=lambda n: "document-3f9a0c1b2d")
        '/card/document-3f9a0c1b2d'
        >>> build_redirect("docs", ["72"], docs_base="http://mac.lan:42020", wiki_base="",
        ...                shared_bucket="family", find_document=lambda n: None)
        'http://mac.lan:42020/documents/72/details'
    """
    if kind == "docs":
        if len(rest) != 1 or not rest[0].isdigit():
            return None
        card = find_document(rest[0]) if find_document else None
        return address(card) if card else paperless_page(docs_base, rest[0]) or None
    if kind == "list":
        path = find_list(rest[0]) if find_list and len(rest) == 1 and rest[0] else None
        return address(path) if path else None
    if kind in CARD_ID_FIELDS:
        if len(rest) != 1 or find_card is None:
            return None
        path = find_card(kind, rest[0])
        return address(path) if path else None
    if kind == "person":
        return _person_page(rest)
    if kind != "topic":
        return None
    scope, _ = _split_leaf(rest)
    if len(scope) == 2 and scope[0] != shared_bucket:
        return _own_topic_page(rest, find_card_id)
    target = resolve_topic_target(rest, shared_bucket=shared_bucket)
    return f"{wiki_base.rstrip('/')}/{target}" if target else None


# ── A person's own part: the memory pages ───────────────────────────

def _person_page(segments: list[str]) -> str | None:
    """`/person/<member>[/todo]` → the person's memory page.

    A person is a top-level bucket, so exactly one scope segment is valid.
    Their todos have no page of their own there; the link opens the
    person's page, which lists what is theirs.
    """
    scope, _ = _split_leaf(segments)
    if len(scope) != 1 or not scope[0]:
        return None
    return memory_page("person", scope[0])


def _own_topic_page(segments: list[str],
                    find_card_id: "Callable[[str], str | None] | None") -> str | None:
    """`/topic/<member>/<slug>[/todo]` → the topic's memory page, or its list's card page.

    The memory page takes the topic's card id, so the topic card
    (`<member>/<slug>/topic.md`) is looked up; the todo leaf opens the list
    named after the topic (`<member>/lists/<slug>.md`). None when there is
    no card or the device may not read it.
    """
    (member, slug), page = _split_leaf(segments)
    if find_card_id is None:
        return None
    if page == "todos":
        card_id = find_card_id(f"{member}/{LISTS_DIR}/{slug}.md")
        return address(card_id) if card_id else None
    card_id = find_card_id(f"{member}/{slug}/{TOPIC_CARD}")
    return memory_page("topic", card_id) if card_id else None


# ── Details ──────────────────────────────────────────────────────────

# A trailing `todo`/`todos` segment points at the entity's task list instead of
# its overview page. Both spellings accepted — people type either.
_TODO_LEAVES = {"todo", "todos"}


def _split_leaf(segments: list[str]) -> tuple[list[str], str]:
    """Peel a `todo`/`todos` leaf off the path: returns (scope, page)."""
    if segments and segments[-1].lower() in _TODO_LEAVES:
        return segments[:-1], "todos"
    return segments, "about"
