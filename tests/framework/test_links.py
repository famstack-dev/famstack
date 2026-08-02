"""The one link builder — logical paths, and the rule that keeps it one.

`stack.links` builds the `/go` paths bots post into Matrix. The point of
the module is not the string formatting, which is trivial; it is that no
emitter anywhere builds a service URL by hand, because a link frozen in
chat history has to survive a domain change, a hosting-mode flip, and a
moved backend.

So this file has two halves: what the builder promises, and a guard that
the promise stays singular.
"""

from __future__ import annotations

import re
from pathlib import Path

from stack.links import go_docs, go_person, go_topic, public

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# ── What the builder promises ────────────────────────────────────────────

class TestLogicalPaths:
    """Paths name the entity kind, so the resolver never has to guess."""

    def test_document(self):
        assert go_docs(247) == "/docs/247"

    def test_shared_topic_by_slug(self):
        # A bare slug resolves under the shared bucket; the emitter does
        # not need to know what that bucket is called.
        assert go_topic("camping") == "/topic/camping"

    def test_topic_with_explicit_bucket(self):
        assert go_topic("family/camping") == "/topic/family/camping"

    def test_topic_todo_leaf(self):
        assert go_topic("family/camping", "todo") == "/topic/family/camping/todo"

    def test_person(self):
        assert go_person("homer") == "/person/homer"

    def test_person_todo_leaf(self):
        assert go_person("homer", "todo") == "/person/homer/todo"

    def test_scope_slashes_are_tolerated(self):
        # Scopes arrive from vault paths and room bindings, which carry
        # stray slashes; the caller should not have to trim them.
        assert go_topic("/family/camping/") == "/topic/family/camping"


class TestPublicUrl:
    """Joining a logical path onto the configured `/go` base."""

    def test_joins_base_and_path(self):
        assert public(go_docs(247), "https://home.example.org/go") == \
            "https://home.example.org/go/docs/247"

    def test_trailing_slash_on_base_does_not_double(self):
        assert public(go_docs(247), "https://home.example.org/go/") == \
            "https://home.example.org/go/docs/247"

    def test_port_mode_base(self):
        # Port mode hands out ip:port with no vanity host; same join.
        assert public(go_topic("family/camping", "todo"), "http://10.0.0.5:42000/go") == \
            "http://10.0.0.5:42000/go/topic/family/camping/todo"

    def test_no_base_means_no_link(self):
        # Core has not rendered LINK_BASE_URL yet. An unresolvable link
        # is worse than none, so emitters get "" and fall back to their
        # unlinked rendering.
        assert public(go_docs(247), "") == ""


# ── The rule: exactly one builder ────────────────────────────────────────
#
# Generic guard for a "there MUST be exactly one implementation" rule.
# The URL builder is not the only place we have written that sentence in
# a spec and then grown a second implementation anyway (vault-format.md
# §8 says one frontmatter writer; there are three). Nothing in the suite
# catches that class of drift, because each duplicate is individually
# correct — only the count is wrong. Walking the tree for the shape is
# inelegant and would have caught both. Copy this test, change the
# pattern and the allowlist.

# Paperless's document detail page. Every hand-built copy of this string
# was a link that died on the next domain change.
DOC_URL_SHAPE = re.compile(r"/documents/.*?/details")

# Only the two halves of the link seam may name that shape: `links.py`
# builds the logical path an emitter posts, `resolver.py` maps it to
# wherever the document lives right now.
ALLOWED = {
    "lib/stack/links.py",
    "stacklets/core/tools-server/resolver.py",
    # S6 of the memory-architecture pass replaces this with the logical
    # `/go/docs/<id>` path. It writes `resource` into vault frontmatter,
    # so it needs a vault-format.md §5 spec change plus a migration of
    # entries already on disk. Delete this line when S6 lands.
    "stacklets/docs/bot/vault_entry.py",
}

SCANNED_ROOTS = ("lib", "stacklets", "tools")


def _offenders() -> list[str]:
    hits = []
    for root in SCANNED_ROOTS:
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel in ALLOWED:
                continue
            for n, line in enumerate(path.read_text().splitlines(), start=1):
                if DOC_URL_SHAPE.search(line):
                    hits.append(f"{rel}:{n}: {line.strip()}")
    return hits


class TestExactlyOneDocumentUrlBuilder:

    def test_no_module_builds_a_document_url_by_hand(self):
        offenders = _offenders()
        assert not offenders, (
            "A document URL is built outside the link seam:\n  "
            + "\n  ".join(offenders)
            + "\n\nEmit `stack.links.public(go_docs(id), link_base_url)` instead. "
            "Only lib/stack/links.py and the tools-server resolver may know "
            "what a document URL looks like."
        )

    def test_the_guard_actually_looks_at_files(self):
        # A tree walk that silently matches nothing passes forever. Pin
        # that the pattern still fires on the one file we allow.
        resolver = REPO_ROOT / "stacklets/core/tools-server/resolver.py"
        assert DOC_URL_SHAPE.search(resolver.read_text())
