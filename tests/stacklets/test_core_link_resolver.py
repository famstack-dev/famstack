"""The logical link resolver — first-class entity paths to a current wiki path.

Pins the pure mapping that keeps Matrix-frozen links from rotting. Entities are
explicit nouns: `/person/homer` resolves to that member's home, `/topic/camping`
to a shared topic under the bucket, `/topic/homer/gravel` to a personal topic,
and a `todo`/`todos` leaf to the entity's task list. Because the kind is named,
there is no member-vs-topic guessing. Unknown shapes return None so the HTTP
layer 404s instead of pointing at a page that 404s anyway.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "core" / "tools-server"))

from resolver import resolve_person_target, resolve_topic_target  # noqa: E402

_SHARED = "family"


def _topic(*segments: str) -> str | None:
    return resolve_topic_target(list(segments), shared_bucket=_SHARED)


def _person(*segments: str) -> str | None:
    return resolve_person_target(list(segments))


class TestTopicResolution:

    def test_shared_topic(self):
        assert _topic("camping") == "family/camping/about"

    def test_explicit_shared_bucket_path(self):
        # The literal vault path still works for people who type it out.
        assert _topic("family", "camping") == "family/camping/about"

    def test_personal_topic(self):
        assert _topic("homer", "gravel") == "homer/gravel/about"

    def test_topic_todos(self):
        assert _topic("camping", "todo") == "family/camping/todos"

    def test_topic_todos_plural(self):
        assert _topic("camping", "todos") == "family/camping/todos"

    def test_personal_topic_todos(self):
        assert _topic("homer", "gravel", "todos") == "homer/gravel/todos"

    def test_todo_leaf_is_case_insensitive(self):
        assert _topic("camping", "TODO") == "family/camping/todos"

    def test_empty(self):
        assert _topic() is None

    def test_todo_with_no_scope(self):
        assert _topic("todo") is None

    def test_too_deep(self):
        # A topic path is <bucket>/<slug> at most; deeper is malformed.
        assert _topic("random", "x", "y") is None


class TestPersonResolution:

    def test_person_home(self):
        assert _person("homer") == "homer/about"

    def test_person_todos(self):
        assert _person("homer", "todo") == "homer/todos"

    def test_person_todos_plural(self):
        assert _person("marge", "todos") == "marge/todos"

    def test_empty(self):
        assert _person() is None

    def test_too_many_segments(self):
        # A person is a single top-level bucket, not a nested path.
        assert _person("homer", "gravel") is None


# ── build_redirect — the full target URL ────────────────────────────────


from resolver import build_redirect  # noqa: E402


def _b(kind, *rest, docs="https://docs.home.tld", wiki="https://wiki.home.tld"):
    return build_redirect(
        kind, list(rest), docs_base=docs, wiki_base=wiki, shared_bucket=_SHARED,
    )


class TestBuildRedirect:

    def test_docs(self):
        assert _b("docs", "247") == "https://docs.home.tld/documents/247/details"

    def test_docs_non_numeric_id(self):
        assert _b("docs", "abc") is None

    def test_docs_extra_segments(self):
        assert _b("docs", "1", "2") is None

    def test_person_home(self):
        assert _b("person", "homer") == "https://wiki.home.tld/homer/about"

    def test_topic_shared(self):
        assert _b("topic", "camping") == "https://wiki.home.tld/family/camping/about"

    def test_topic_todos(self):
        assert _b("topic", "camping", "todo") == "https://wiki.home.tld/family/camping/todos"

    def test_person_todos(self):
        assert _b("person", "homer", "todo") == "https://wiki.home.tld/homer/todos"

    def test_unknown_kind(self):
        assert _b("photos", "1") is None

    def test_port_mode_base_no_trailing_slash(self):
        # Port-mode bases arrive as http://ip:port — the join must not
        # double the slash or drop the path.
        assert build_redirect(
            "topic", ["camping", "todo"], docs_base="http://10.0.0.5:42000",
            wiki_base="http://10.0.0.5:42070", shared_bucket=_SHARED,
        ) == "http://10.0.0.5:42070/family/camping/todos"


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
        shared_bucket=_SHARED,
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
            f"{_WIKI_BASE}/family/camping/todos"

    def test_person_link_resolves_to_the_member_page(self):
        assert _click(go_person("homer")) == f"{_WIKI_BASE}/homer/about"

    def test_public_form_is_what_goes_into_the_message(self):
        # The base carries the `/go` prefix (core's LINK_BASE_URL), so
        # the emitted link is base + logical path, no separator surprises.
        assert public(go_docs(247), "https://home.example.org/go") == \
            "https://home.example.org/go/docs/247"
