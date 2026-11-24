"""The logical link resolver: first-class entity paths to where they live now.

Pins the pure mapping that keeps Matrix-frozen links from rotting. Entities are
explicit nouns: `/topic/camping` resolves to a shared topic's wiki page, and a
`todo`/`todos` leaf to the entity's task list. A person's page and a person's
own topic are not in the family wiki, which shows only what the family shares:
`/person/homer` and `/topic/homer/gravel` land on the memory pages, which check
who asks. Because the kind is named, there is no member-vs-topic guessing.
Unknown shapes return None so the HTTP layer 404s instead of pointing at a page
that 404s anyway.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "core" / "tools-server"))

from resolver import resolve_topic_target  # noqa: E402

_SHARED = "family"


def _topic(*segments: str) -> str | None:
    return resolve_topic_target(list(segments), shared_bucket=_SHARED)


class TestTopicResolution:

    def test_shared_topic(self):
        assert _topic("camping") == "family/camping/about"

    def test_explicit_shared_bucket_path(self):
        # The literal vault path still works for people who type it out.
        assert _topic("family", "camping") == "family/camping/about"

    def test_personal_topic(self):
        assert _topic("homer", "gravel") == "homer/gravel/about"

    def test_topic_todos(self):
        assert _topic("camping", "todo") == "family/lists/camping"

    def test_topic_todos_plural(self):
        assert _topic("camping", "todos") == "family/lists/camping"

    def test_personal_topic_todos(self):
        assert _topic("homer", "gravel", "todos") == "homer/lists/gravel"

    def test_todo_leaf_is_case_insensitive(self):
        assert _topic("camping", "TODO") == "family/lists/camping"

    def test_empty(self):
        assert _topic() is None

    def test_todo_with_no_scope(self):
        assert _topic("todo") is None

    def test_too_deep(self):
        # A topic path is <bucket>/<slug> at most; deeper is malformed.
        assert _topic("random", "x", "y") is None


# ── build_redirect — the full target URL ────────────────────────────────


from resolver import build_redirect  # noqa: E402


# The cards a device may read, by their path in the brain, and their ids.
_CARD_IDS = {"homer/gravel/topic.md": "topic-gravel", "homer/lists/gravel.md": "list-gravel"}


def _b(kind, *rest, docs="https://docs.home.tld", wiki="https://wiki.home.tld"):
    return build_redirect(
        kind, list(rest), docs_base=docs, wiki_base=wiki, shared_bucket=_SHARED,
        find_card_id=_CARD_IDS.get,
    )


class TestBuildRedirect:

    def test_docs(self):
        assert _b("docs", "247") == "https://docs.home.tld/documents/247/details"

    def test_docs_non_numeric_id(self):
        assert _b("docs", "abc") is None

    def test_docs_extra_segments(self):
        assert _b("docs", "1", "2") is None

    def test_topic_shared(self):
        assert _b("topic", "camping") == "https://wiki.home.tld/family/camping/about"

    def test_topic_shared_by_its_bucket_stays_on_the_wiki(self):
        assert _b("topic", "family", "camping") == "https://wiki.home.tld/family/camping/about"

    def test_topic_todos(self):
        assert _b("topic", "camping", "todo") == "https://wiki.home.tld/family/lists/camping"


class TestAPersonsOwnPartLandsOnTheMemoryPages:
    """The wiki shows only what the family shares. A person's page and their
    own topics are on the memory pages, which check who asks."""

    def test_a_persons_page(self):
        assert _b("person", "homer") == "/memory/person/homer"

    def test_a_persons_todos_open_their_page(self):
        assert _b("person", "homer", "todo") == "/memory/person/homer"

    def test_a_person_link_names_one_person(self):
        assert _b("person", "homer", "gravel") is None

    def test_a_persons_own_topic(self):
        assert _b("topic", "homer", "gravel") == "/memory/topic/topic-gravel"

    def test_a_persons_own_topic_todos_open_its_list(self):
        assert _b("topic", "homer", "gravel", "todo") == "/card/list-gravel"

    def test_a_persons_topic_the_device_may_not_read_is_a_404(self):
        assert _b("topic", "marge", "geschenke") is None

    def test_unknown_kind(self):
        assert _b("photos", "1") is None

    def test_port_mode_base_no_trailing_slash(self):
        # Port-mode bases arrive as http://ip:port — the join must not
        # double the slash or drop the path.
        assert build_redirect(
            "topic", ["camping", "todo"], docs_base="http://10.0.0.5:42000",
            wiki_base="http://10.0.0.5:42070", shared_bucket=_SHARED,
        ) == "http://10.0.0.5:42070/family/lists/camping"


# ── Round trip: what emitters build lands where it used to ──────────────
#
# The bots used to paste `{PAPERLESS_PUBLIC_URL}/documents/<id>/details`
# straight into chat; they now post a logical `/go` link built by
# `stack.links`. Nothing about the destination was supposed to move, so
# these pin the old URLs as literals and walk a freshly built logical
# path through the same two hops a click takes: the HTTP route splits it,
# the resolver maps it. A drift here is a family clicking a link in an
# old message and landing somewhere else.

from stack.links import go_docs, go_person, go_topic, public  # noqa: E402

_DOCS_BASE = "http://localhost:42020"
_WIKI_BASE = "http://localhost:42070"

# Verbatim from the pre-change emitters (archivist.py, document_pipeline.py,
# search_format.py, matching.py) — the contract this refactor must not break.
_LEGACY_DOC_URL = f"{_DOCS_BASE}/documents/247/details"


def _click(logical: str) -> str | None:
    """Resolve a logical path the way a browser hitting `/go/…` does.

    Mirrors the split in `server.py`'s three routes: the first segment
    is the entity kind, the rest is the entity path.
    """
    kind, *rest = [s for s in logical.split("/") if s]
    return build_redirect(
        kind, rest, docs_base=_DOCS_BASE, wiki_base=_WIKI_BASE,
        shared_bucket=_SHARED, find_card_id=_CARD_IDS.get,
    )


class TestEmittedLinksResolveWhereTheyUsedTo:

    def test_document_link_lands_on_the_paperless_detail_page(self):
        assert _click(go_docs(247)) == _LEGACY_DOC_URL

    def test_document_link_survives_a_string_doc_id(self):
        # Paperless ids arrive as ints from the API and as strings from
        # chat commands; both must build the same link.
        assert _click(go_docs("247")) == _LEGACY_DOC_URL

    def test_topic_todo_link_is_unchanged(self):
        # The archivist already emitted this one by hand as
        # f"{base}/topic/{scope}/todo" — same path, same destination.
        assert go_topic("family/camping", "todo") == "/topic/family/camping/todo"
        assert _click(go_topic("family/camping", "todo")) == \
            f"{_WIKI_BASE}/family/lists/camping"

    def test_person_link_resolves_to_the_memory_page(self):
        assert _click(go_person("homer")) == "/memory/person/homer"

    def test_a_persons_topic_link_resolves_to_the_memory_page(self):
        assert _click(go_topic("homer/gravel")) == "/memory/topic/topic-gravel"

    def test_public_form_is_what_goes_into_the_message(self):
        # The base carries the `/go` prefix (core's LINK_BASE_URL), so
        # the emitted link is base + logical path, no separator surprises.
        assert public(go_docs(247), "https://home.example.org/go") == \
            "https://home.example.org/go/docs/247"


class TestListResolution:
    @staticmethod
    def _b(*rest, found="family/lists/einkaufsliste.md"):
        return build_redirect("list", list(rest), docs_base="", wiki_base="https://wiki.home.tld",
                              shared_bucket="family", find_list=lambda slug: found)

    def test_a_list_opens_its_card_page_by_its_name(self):
        assert self._b("einkaufsliste") == "/card/family/lists/einkaufsliste.md"

    def test_a_persons_own_list_opens_its_card_page(self):
        assert self._b("garage", found="homer/lists/garage.md") == "/card/homer/lists/garage.md"

    def test_a_name_no_list_has_is_a_404(self):
        assert self._b("nothing", found=None) is None

    def test_a_list_link_has_one_name(self):
        assert self._b("a", "b") is None
