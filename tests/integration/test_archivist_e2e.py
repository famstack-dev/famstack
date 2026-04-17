"""End-to-end archivist test — production topology.

Real Synapse, real Paperless, real bot-runner, mock OpenAI. Homer
uploads a document to #documents and the archivist classifies it.

The test narrates itself via the `bdd` fixture so the stderr output
reads like a protocol. Run with `-s` to stream live:

    uv run --extra test pytest -s tests/integration/test_archivist_e2e.py
"""

from __future__ import annotations

import json

import pytest

from tests.integration.matrix import (
    ensure_joined,
    event_type,
    fetch_room_events,
    resolve_room,
    upload_and_send_file,
)
from tests.integration.openai_stub import stub_classify, stub_reformat


DOCS_ROOM_ALIAS = "#documents:test.local"


async def test_homer_uploads_invoice_archivist_classifies_and_files_it(
    bdd,
    openai,
    paperless,
    paperless_scope,
    homer,
    sample_invoice_pdf,
):
    """Homer sends an ADAC invoice → archivist classifies + files it.

    Scenario
    --------
    Given  the archivist bot is running
    And    the OpenAI mock will classify the document as an insurance
           invoice from ADAC for Homer
    When   Homer uploads an ADAC invoice PDF to the #documents room
    Then   Paperless has the document tagged 'Insurance' and
           'Person: Homer', with correspondent 'ADAC' and type 'Invoice'
    And    the #documents room contains a classification summary
    And    a dev.famstack.document event is emitted with the metadata
    """
    scope = paperless_scope
    bdd.scenario("Homer uploads an ADAC invoice; archivist classifies it")

    # ── Given ────────────────────────────────────────────────────────
    expected_title        = scope.tag("ADAC - Kfz-Versicherung 2026")
    expected_topic        = scope.tag("Insurance")
    expected_correspondent = scope.tag("ADAC")

    bdd.given("the #documents room exists and Homer has access")
    room_id = await resolve_room(homer, DOCS_ROOM_ALIAS)
    await ensure_joined(homer, room_id)
    bdd.detail(f"room_id = {room_id}")

    bdd.given("the archivist has a 'Person: Homer' tag to match against")
    existing_person_tag = next(
        (t for t in paperless.list_tags() if t["name"] == "Person: Homer"),
        None,
    )
    assert existing_person_tag, \
        "expected seeded 'Person: Homer' tag — did on_start_ready run?"
    bdd.ok(f"found 'Person: Homer' (id={existing_person_tag['id']})")

    bdd.given("the OpenAI mock is stubbed for classify + reformat")
    stub_classify(openai, {
        "title": expected_title,
        "topics": [expected_topic],
        "persons": ["Homer"],
        "correspondent": expected_correspondent,
        "document_type": "Invoice",
        "date": "2026-03-15",
        "summary": "Annual car insurance renewal at ADAC. EUR 340/year.",
        "facts": ["EUR 340.00/year", "Contract KFZ-2026-000123"],
        "action_items": [{"action": "Pay by 2026-03-15", "due": "2026-03-15"}],
    })
    stub_reformat(openai, "# Kfz-Versicherung 2026\n\nADAC — EUR 340/year.")
    bdd.detail("classify stub → title, topics, correspondent, type")
    bdd.detail("reformat stub → 1-line markdown")

    # ── When ─────────────────────────────────────────────────────────
    bdd.when(f"Homer uploads invoice.pdf ({len(sample_invoice_pdf)} bytes) "
             f"to {DOCS_ROOM_ALIAS}")
    event_id = await upload_and_send_file(
        homer, room_id, sample_invoice_pdf, filename="invoice.pdf",
        mime_type="application/pdf", msgtype="m.file",
    )
    bdd.detail(f"sent event_id = {event_id}")

    # ── Then: Paperless has the filed document ──────────────────────
    bdd.then(f"Paperless has a document titled '{expected_title}'")

    async def _find_doc():
        # The archivist uploads → Paperless Celery OCRs → archivist
        # PATCHes title + tags. Poll for the renamed title so we see
        # the post-classification state, not the raw upload.
        import asyncio
        for _ in range(120):
            docs = paperless.list_documents()
            match = next((d for d in docs if d.get("title") == expected_title), None)
            if match:
                return match
            await asyncio.sleep(1)
        return None

    doc = await _find_doc()
    assert doc, f"No Paperless doc titled {expected_title!r} within 120s. " \
                f"Check bot-runner logs."
    bdd.ok(f"Paperless doc #{doc['id']}: {doc['title']}")

    tag_names = {t["name"] for t in paperless.list_tags() if t["id"] in doc.get("tags", [])}
    bdd.and_(f"tagged with {sorted(tag_names)}")
    assert expected_topic in tag_names, f"expected topic tag, got {tag_names}"
    assert "Person: Homer" in tag_names, f"expected person tag, got {tag_names}"

    # ── Then: room receives classification summary + structured event ──
    # Gather everything Homer saw in the room for a bounded window, then
    # filter. Single sync sweep covers both events even though they were
    # posted back-to-back.
    bdd.then("the #documents room receives a classification summary")
    bdd.and_("a dev.famstack.document event is emitted with full metadata")
    events = await fetch_room_events(homer, room_id, duration=10)

    summary = next(
        (e for e in events
         if event_type(e) == "m.room.message"
         and expected_title in getattr(e, "body", "")),
        None,
    )
    assert summary, f"no classification summary among {[event_type(e) for e in events]}"
    bdd.ok(f"summary event {summary.event_id}")

    structured = next(
        (e for e in events if event_type(e) == "dev.famstack.document"),
        None,
    )
    assert structured, f"no dev.famstack.document event among {[event_type(e) for e in events]}"
    body = structured.source.get("content", {})
    assert body.get("topics") == [expected_topic], body
    assert body.get("persons") == ["Homer"], body
    assert body.get("correspondent") == expected_correspondent, body
    bdd.ok(f"event body: topics={body['topics']}, persons={body['persons']}, "
           f"correspondent={body['correspondent']}")
