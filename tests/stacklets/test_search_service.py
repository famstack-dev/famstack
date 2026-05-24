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
        ontology_section=lambda: "",
    )


async def _noop_announce(_text):
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


class TestRun:

    @pytest.mark.asyncio
    async def test_no_results(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MEMORY_VAULT_DIR", raising=False)
        svc = _service(FakePaperless(results=[]))
        reply = await svc.run(query="ADAC", sender="@homer:test", announce=_noop_announce)
        assert reply.startswith("search_no_results")

    @pytest.mark.asyncio
    async def test_literal_paperless_hits(self, monkeypatch):
        monkeypatch.delenv("MEMORY_VAULT_DIR", raising=False)
        hits = [
            {"id": 10, "title": "ADAC Invoice", "created": "2026-03-15"},
            {"id": 11, "title": "ADAC Renewal", "created": "2025-03-01"},
        ]
        svc = _service(FakePaperless(results=hits))
        reply = await svc.run(query="ADAC", sender="@homer:test", announce=_noop_announce)
        # Literal mode: a Paperless section header + one line per hit.
        assert "search_paperless_results" in reply
        assert "ADAC Invoice" in reply
        assert "ADAC Renewal" in reply
        # No question-mode rewrite header on a literal query.
        assert "search_rewritten" not in reply
