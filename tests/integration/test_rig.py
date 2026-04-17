"""Smoke tests for the integration test rig itself.

These tests don't touch the archivist — they just verify that the
Paperless fixture boots, tags can be created and cleaned up by prefix,
and two tests running back-to-back don't see each other's entities.

If these fail, nothing else in this directory will work.
"""

from __future__ import annotations

import pytest


def test_paperless_responds(paperless):
    """Bare smoke: the Paperless API is reachable and auth works."""
    tags = paperless.list_tags()
    # Seeded taxonomy + person tags should exist on any fresh test stack.
    assert isinstance(tags, list)


def test_scope_prefix_is_unique(paperless_scope):
    assert paperless_scope.uid.startswith("t-")
    assert len(paperless_scope.uid) >= 10


def test_scope_cleans_up_created_tag(paperless, paperless_scope):
    name = paperless_scope.tag("Insurance")
    created = paperless.create_tag(name, color="#4caf50")
    assert created["name"] == name
    assert any(t["name"] == name for t in paperless.list_tags())

    paperless_scope.cleanup()

    assert not any(t["name"] == name for t in paperless.list_tags()), \
        "cleanup should have deleted the prefixed tag"
    paperless_scope.on_cleanup.clear()  # don't double-clean on fixture teardown


async def test_homer_is_logged_in(homer):
    """Real Synapse round-trip — whoami confirms the session works."""
    resp = await homer.whoami()
    assert resp.user_id == "@homer:test.local"


async def test_openai_mock_serves_canned_response(openai):
    """pytest-httpserver mock returns the stubbed classification payload."""
    import aiohttp
    from tests.integration.openai_stub import stub_classify

    stub_classify(openai, {"title": "Test", "topics": ["Demo"]})
    async with aiohttp.ClientSession() as sess:
        async with sess.post(
            f"{openai.url_for('/v1/chat/completions')}",
            json={"model": "x", "messages": [{"role": "user", "content": "hi"}]},
        ) as resp:
            body = await resp.json()

    import json as _json
    content = _json.loads(body["choices"][0]["message"]["content"])
    assert content == {"title": "Test", "topics": ["Demo"]}


def test_two_scopes_dont_collide(paperless, request):
    """Two consecutive scopes should have different uids and not clean
    each other's entities."""
    from tests.integration.conftest import Scope
    import uuid
    from tests.integration.paperless import cleanup_prefix

    a = Scope(uid=f"t-{uuid.uuid4().hex[:8]}")
    b = Scope(uid=f"t-{uuid.uuid4().hex[:8]}")
    assert a.uid != b.uid

    tag_a = paperless.create_tag(a.tag("X"))
    tag_b = paperless.create_tag(b.tag("X"))
    try:
        cleanup_prefix(paperless, a.uid)
        names = {t["name"] for t in paperless.list_tags()}
        assert tag_a["name"] not in names, "a's tag should be gone"
        assert tag_b["name"] in names, "b's tag must survive a's cleanup"
    finally:
        cleanup_prefix(paperless, b.uid)
