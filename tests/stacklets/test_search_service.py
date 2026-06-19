"""SearchService — query resolution, dual search, formatting.

The service does the work (no Matrix) and returns the final reply text,
calling an injected `announce` only for the mid-flow "looking deeper"
status. These pin the scope logic, the no-results path, and literal
formatting with fakes; the synthesis/deep-dive LLM path is moved
verbatim from the bot and exercised via the e2e rig.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
# `stacklets/` for the sibling `memory.lib`; the bot dir for search_service
# and its peers (nl_query, recall, search_format) — mirrors archivist's own
# sys.path setup so the module imports standalone in tests.
sys.path.insert(0, str(_REPO_ROOT / "stacklets"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs" / "bot"))

from search_service import SearchService  # noqa: E402


def _t(key, **kw):
    q = kw.get("query", "")
    return f"{key}:{q}" if q else key


class FakePaperless:
    def __init__(self, results=None):
        self._results = results or []

    async def search(self, query):
        return list(self._results)


class FakeClassifier:
    """Literal/no-result paths never call the classifier."""


class _FakeVault:
    def ontology(self):
        return None
    def ontology_section(self, language=None):
        return ""
    def correspondents_section(self):
        return ""


def _service(paperless, *, shared_bucket="family"):
    return SearchService(
        classifier=FakeClassifier(),
        paperless=paperless,
        t=_t,
        language="en",
        code_public_url="http://code",
        mirror_org="family",
        paperless_public_url="http://paperless",
        shared_bucket=shared_bucket,
        vault=_FakeVault(),
    )


class _FakeNotifier:
    async def status(self, key, **kwargs):
        pass


class TestScopesForSender:

    def test_unknown_sender_gets_shared_only(self):
        svc = _service(FakePaperless())
        assert svc.scopes_for_sender(None) == ["family/"]

    def test_known_sender_adds_their_entity(self):
        svc = _service(FakePaperless())
        assert svc.scopes_for_sender("@marge:home.local") == ["family/", "marge/"]

    def test_sender_equal_to_bucket_not_duplicated(self):
        svc = _service(FakePaperless(), shared_bucket="family")
        assert svc.scopes_for_sender("@family:home") == ["family/"]


class TestScopesWithTopicBucket:
    """A `?` query asked in a topic room scopes the memory search to
    that topic's bucket only. The room is the implicit `--topic` flag;
    no shared bucket, no personal bucket. The user can still reach
    wider search by asking outside the topic room (or by passing
    `--global` to the CLI)."""

    def test_topic_bucket_overrides_sender_defaults(self):
        svc = _service(FakePaperless())
        scopes = svc.scopes_for_sender(
            "@homer:home", topic_bucket="camping",
        )
        assert scopes == ["camping/"]

    def test_topic_bucket_overrides_for_unknown_sender(self):
        """An unknown sender in a topic room still gets the topic's
        scope. The room's room state already vouches for who belongs
        here; there's no leakage."""
        svc = _service(FakePaperless())
        scopes = svc.scopes_for_sender(None, topic_bucket="camping")
        assert scopes == ["camping/"]

    def test_no_topic_bucket_falls_back_to_default(self):
        """The default scoping rules apply when no topic_bucket is
        passed -- normal rooms behave exactly as before."""
        svc = _service(FakePaperless())
        assert svc.scopes_for_sender(
            "@marge:home", topic_bucket=None,
        ) == ["family/", "marge/"]

    def test_personal_topic_bucket_passes_through_intact(self):
        """A personal topic (`homer/camping`) is a nested bucket;
        the scope writer must not strip the slash inside."""
        svc = _service(FakePaperless())
        scopes = svc.scopes_for_sender(
            "@homer:home", topic_bucket="homer/camping",
        )
        assert scopes == ["homer/camping/"]


class TestRun:

    @pytest.mark.asyncio
    async def test_no_results(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MEMORY_VAULT_DIR", raising=False)
        svc = _service(FakePaperless(results=[]))
        reply = await svc.run(query="Duff Insurance", sender="@homer:test", notifier=_FakeNotifier())
        assert reply.startswith("search_no_results")

    @pytest.mark.asyncio
    async def test_literal_paperless_hits(self, monkeypatch):
        monkeypatch.delenv("MEMORY_VAULT_DIR", raising=False)
        hits = [
            {"id": 10, "title": "Duff Insurance Invoice", "created": "2026-03-15"},
            {"id": 11, "title": "Duff Insurance Renewal", "created": "2025-03-01"},
        ]
        svc = _service(FakePaperless(results=hits))
        reply = await svc.run(query="Duff Insurance", sender="@homer:test", notifier=_FakeNotifier())
        # Literal mode: a Paperless section header + one line per hit.
        assert "search_paperless_results" in reply
        assert "Duff Insurance Invoice" in reply
        assert "Duff Insurance Renewal" in reply
        # No question-mode rewrite header on a literal query.
        assert "search_rewritten" not in reply
