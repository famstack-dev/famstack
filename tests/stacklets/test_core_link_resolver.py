"""The logical link resolver — `/wiki/<scope>` to a current wiki path.

Pins the pure mapping that keeps Matrix-frozen links from rotting: a
member name resolves to that member's home, a bare topic to the shared
bucket, an explicit `bucket/topic` to itself, and a `todo`/`todos` leaf
to the scope's task list. Unknown shapes return None so the HTTP layer
404s instead of guessing.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "core" / "tools-server"))

from resolver import resolve_wiki_target  # noqa: E402

_MEMBERS = {"homer", "marge", "bart"}
_SHARED = "family"


def _r(*segments: str) -> str | None:
    return resolve_wiki_target(
        list(segments), members=_MEMBERS, shared_bucket=_SHARED,
    )


class TestWikiResolution:

    def test_member_home(self):
        assert _r("homer") == "homer/about"

    def test_shared_topic(self):
        # A lone non-member segment is a shared topic under the bucket.
        assert _r("camping") == "family/camping/about"

    def test_explicit_shared_bucket_path(self):
        # The literal vault path still works for people who type it out.
        assert _r("family", "camping") == "family/camping/about"

    def test_personal_topic(self):
        assert _r("homer", "gravel") == "homer/gravel/about"

    def test_topic_todos(self):
        assert _r("camping", "todo") == "family/camping/todos"

    def test_topic_todos_plural(self):
        assert _r("camping", "todos") == "family/camping/todos"

    def test_member_todos(self):
        assert _r("homer", "todo") == "homer/todos"

    def test_personal_topic_todos(self):
        assert _r("homer", "gravel", "todos") == "homer/gravel/todos"

    def test_todo_leaf_is_case_insensitive(self):
        assert _r("camping", "TODO") == "family/camping/todos"


class TestUnresolvable:

    def test_empty(self):
        assert _r() is None

    def test_todo_with_no_scope(self):
        assert _r("todo") is None

    def test_unknown_deep_path(self):
        # First segment is neither a member nor the shared bucket.
        assert _r("random", "x", "y") is None


# ── build_redirect — the full target URL ────────────────────────────────


from resolver import build_redirect  # noqa: E402


def _b(kind, *rest, docs="https://docs.home.tld", wiki="https://wiki.home.tld"):
    return build_redirect(
        kind, list(rest), docs_base=docs, wiki_base=wiki,
        members=_MEMBERS, shared_bucket=_SHARED,
    )


class TestBuildRedirect:

    def test_docs(self):
        assert _b("docs", "247") == "https://docs.home.tld/documents/247/details"

    def test_docs_non_numeric_id(self):
        assert _b("docs", "abc") is None

    def test_docs_extra_segments(self):
        assert _b("docs", "1", "2") is None

    def test_wiki_member_home(self):
        assert _b("wiki", "homer") == "https://wiki.home.tld/homer/about"

    def test_wiki_shared_topic(self):
        assert _b("wiki", "camping") == "https://wiki.home.tld/family/camping/about"

    def test_wiki_topic_todos(self):
        assert _b("wiki", "camping", "todo") == "https://wiki.home.tld/family/camping/todos"

    def test_unknown_kind(self):
        assert _b("photos", "1") is None

    def test_port_mode_base_no_trailing_slash(self):
        # Port-mode bases arrive as http://ip:port — the join must not
        # double the slash or drop the path.
        assert build_redirect(
            "wiki", ["camping", "todo"], docs_base="http://10.0.0.5:42000",
            wiki_base="http://10.0.0.5:42070", members=_MEMBERS,
            shared_bucket=_SHARED,
        ) == "http://10.0.0.5:42070/family/camping/todos"
