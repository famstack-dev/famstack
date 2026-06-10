"""End-to-end archivist test — production topology.

Real Synapse, real Paperless, real bot-runner, mock OpenAI. Homer
uploads a document to #documents and the archivist classifies it.

The test narrates itself via the `bdd` fixture so the stderr output
reads like a protocol. Run with `-s` to stream live:

    uv run --extra test pytest -s tests/integration/test_archivist_e2e.py
"""

from __future__ import annotations

import asyncio

from nio import AsyncClient
from nio.api import RoomVisibility
from nio.responses import JoinedMembersResponse, RoomInviteResponse

from tests.integration.matrix import (
    ensure_joined,
    event_type,
    fetch_room_events,
    resolve_room,
    upload_and_send_file,
)
from tests.integration.openai_stub import stub_classify, stub_reformat


DOCS_ROOM_ALIAS = "#documents:test.local"
ARCHIVIST_MXID = "@archivist-bot:test.local"


# ── Helpers for reply / DM / mention scenarios ────────────────────────────


def _client_from(creds) -> AsyncClient:
    client = AsyncClient(creds.homeserver, creds.user_id)
    client.access_token = creds.access_token
    client.device_id = creds.device_id
    client.user_id = creds.user_id
    return client


async def _wait_for_bot_membership(client, room_id: str, timeout: int = 30) -> None:
    """Poll Synapse until the archivist has accepted its invite and joined."""
    for _ in range(timeout):
        resp = await client.joined_members(room_id)
        if isinstance(resp, JoinedMembersResponse):
            if any(m.user_id == ARCHIVIST_MXID for m in resp.members):
                return
        await asyncio.sleep(1)
    raise AssertionError(f"{ARCHIVIST_MXID} did not join {room_id} within {timeout}s")


async def _wait_for_reply(client, room_id: str, *, predicate, timeout: int = 45):
    """Sync the room until an event satisfies `predicate`, or time out."""
    for _ in range(timeout // 5 + 1):
        events = await fetch_room_events(client, room_id, duration=5.0)
        hit = next((e for e in events if predicate(e)), None)
        if hit is not None:
            return hit
    return None


def _envelope(event) -> dict | None:
    return event.source.get("content", {}).get("dev.famstack.event")


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
    And    the summary m.room.message carries a dev.famstack.event
           envelope (Matrix is the canonical ledger — one event per
           filing, full payload on the visible message)
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
        # The archivist picks the upload off its sync loop, uploads →
        # Paperless Celery OCRs → archivist PATCHes title + tags. On a
        # cold bot-runner the very first event can sit a full sync
        # long-poll (~30s) before pickup, so poll to the 120s the
        # assertion advertises; a warm bot files in a few seconds.
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

    # ── Then: Paperless has the classifier summary as a note ────────
    # The archivist writes the structured summary (Summary / Facts /
    # Parties) as a Paperless note after the title/tags PATCH. Notes
    # feed Paperless's full-text search, so asserting here also proves
    # the search-indexed copy of the summary is in place.
    bdd.then("Paperless has the classifier summary stored as a note")
    import asyncio as _asyncio
    notes: list[dict] = []
    for _ in range(15):
        notes = paperless.list_notes(doc["id"])
        if notes:
            break
        await _asyncio.sleep(1)
    assert notes, f"no classifier note on doc #{doc['id']} after 15s"
    body = "\n".join(n.get("note", "") for n in notes)
    # The note carries the archivist marker so the next reprocess can
    # sweep it; sections are untitled (no '## Summary' label) so the
    # language stays native to the document.
    assert "<!-- archivist-bot -->" in body, f"missing marker in: {body!r}"
    assert "Annual car insurance renewal at ADAC" in body, body
    assert "EUR 340.00/year" in body, body
    assert f"{expected_correspondent} → Homer" in body, body
    bdd.ok(f"summary note present ({len(body)} chars)")

    # ── Then: room receives classification summary + structured event ──
    # Gather everything Homer saw in the room for a bounded window, then
    # filter. Single sync sweep covers both events even though they were
    # posted back-to-back.
    bdd.then("the #documents room receives a classification summary")
    bdd.and_("the m.room.message carries a dev.famstack.event envelope")
    events = await fetch_room_events(homer, room_id, duration=10)

    summary = next(
        (e for e in events
         if event_type(e) == "m.room.message"
         and expected_title in getattr(e, "body", "")),
        None,
    )
    assert summary, f"no classification summary among {[event_type(e) for e in events]}"
    bdd.ok(f"summary event {summary.event_id}")

    # Single event per filing: the visible m.room.message is also the
    # ledger record, with `dev.famstack.event` riding as a content field.
    envelope = summary.source.get("content", {}).get("dev.famstack.event")
    assert envelope, f"missing dev.famstack.event on summary content: {summary.source!r}"
    assert envelope["source"] == "docs", envelope
    assert envelope["type"] == "document.filed", envelope
    data = envelope.get("data") or {}
    assert data.get("topics") == [expected_topic], data
    assert data.get("persons") == ["Homer"], data
    assert data.get("correspondent") == expected_correspondent, data
    bdd.ok(f"envelope: type={envelope['type']}, topics={data['topics']}, "
           f"persons={data['persons']}, correspondent={data['correspondent']}")


# ── Reply-to-reprocess ────────────────────────────────────────────────────


async def test_homer_replies_to_filing_and_archivist_reprocesses(
    bdd, openai, paperless, paperless_scope, homer, sample_invoice_pdf,
):
    """Homer replies to a filing with a correction; the archivist re-runs
    classification with the reply as a hint and confirms with a
    `document.reclassified` envelope.

    Scenario
    --------
    Given  the archivist filed Homer's invoice (correspondent ADAC)
    When   Homer replies to the filing message with "this is from Globex"
    Then   the archivist reprocesses doc and posts a reclassified
           confirmation carrying the user's hint in its envelope
    """
    scope = paperless_scope
    bdd.scenario("Homer corrects a filing by replying to it")

    title = scope.tag("ADAC - Kfz-Versicherung reprocess")
    expected_correspondent = scope.tag("ADAC")
    bdd.given("the OpenAI mock will classify, reformat, then reclassify")
    # Initial filing pass: classify + reformat.
    stub_classify(openai, {
        "title": title, "topics": [scope.tag("Insurance")], "persons": ["Homer"],
        "correspondent": expected_correspondent, "document_type": "Invoice",
        "date": "2026-03-15", "summary": "Car insurance renewal.",
        "facts": ["EUR 340.00/year"], "action_items": [],
    })
    stub_reformat(openai, "# Kfz-Versicherung\n\nADAC.")
    # Reprocess pass (triggered by the reply): classify only.
    stub_classify(openai, {
        "title": title, "topics": [scope.tag("Insurance")], "persons": ["Homer"],
        "correspondent": scope.tag("Globex"), "document_type": "Invoice",
        "date": "2026-03-15", "summary": "Reclassified: correspondent is Globex.",
        "facts": ["EUR 340.00/year"], "action_items": [],
    })

    bdd.given("the #documents room exists and Homer has access")
    room_id = await resolve_room(homer, DOCS_ROOM_ALIAS)
    await ensure_joined(homer, room_id)

    bdd.when("Homer uploads the invoice and the archivist files it")
    await upload_and_send_file(
        homer, room_id, sample_invoice_pdf, filename="invoice.pdf",
        mime_type="application/pdf", msgtype="m.file",
    )
    # Match THIS test's filing by its scope-tagged correspondent. In a
    # batch run the #documents room still holds prior tests' filings, and
    # those docs were deleted at their teardown — a bare `document.filed`
    # match could target a now-missing doc, and the reply would resolve
    # to a deleted paperless_id (reprocess_doc_missing, no reclassify).
    filing = await _wait_for_reply(
        homer, room_id,
        predicate=lambda e: (
            event_type(e) == "m.room.message"
            and (_envelope(e) or {}).get("type") == "document.filed"
            and (_envelope(e) or {}).get("data", {}).get("correspondent")
            == expected_correspondent
        ),
    )
    assert filing, "archivist never posted a document.filed envelope"
    paperless_id = _envelope(filing)["data"]["paperless_id"]
    bdd.ok(f"filed doc #{paperless_id}, event {filing.event_id}")

    bdd.when("Homer replies to the filing with a correction")
    hint = "this is from Globex, not ADAC"
    await homer.room_send(
        room_id, "m.room.message",
        {
            "msgtype": "m.text", "body": hint,
            "m.relates_to": {"m.in_reply_to": {"event_id": filing.event_id}},
        },
    )

    bdd.then("the archivist posts a document.reclassified confirmation")
    reclassified = await _wait_for_reply(
        homer, room_id,
        predicate=lambda e: (_envelope(e) or {}).get("type") == "document.reclassified",
    )
    assert reclassified, "archivist never posted a document.reclassified envelope"
    env = _envelope(reclassified)
    assert env["data"]["paperless_id"] == paperless_id, env
    assert env["data"].get("user_hint") == hint, env
    bdd.ok(f"reclassified #{paperless_id} with hint {hint!r}")


# ── DM: reacts without a mention ──────────────────────────────────────────


async def test_dm_help_returns_welcome(bdd, paperless, matrix):
    """In a 2-member room (a DM), the archivist reacts without an @-mention.
    Homer DMs `help` and gets the welcome message back."""
    bdd.scenario("Homer DMs the archivist for help")

    homer = _client_from(matrix["homer"])
    try:
        bdd.given("Homer opens a DM and the archivist joins")
        create = await homer.room_create(
            name="Homer ⇄ Archivist", visibility=RoomVisibility.private,
        )
        room_id = create.room_id
        invite = await homer.room_invite(room_id, ARCHIVIST_MXID)
        assert isinstance(invite, RoomInviteResponse), f"invite failed: {invite}"
        await _wait_for_bot_membership(homer, room_id)
        bdd.ok(f"DM {room_id} with the archivist")

        bdd.when("Homer sends `help` (no mention)")
        await homer.room_send(
            room_id, "m.room.message", {"msgtype": "m.text", "body": "help"},
        )

        bdd.then("the archivist replies with the welcome text")
        reply = await _wait_for_reply(
            homer, room_id,
            predicate=lambda e: (
                getattr(e, "sender", None) == ARCHIVIST_MXID
                and "documents" in getattr(e, "body", "").lower()
            ),
        )
        assert reply, "archivist did not respond to `help` in the DM"
        bdd.ok("welcome reply received in DM")
    finally:
        await homer.close()


# ── Group room: @-mention triggers a reaction ─────────────────────────────


async def test_group_mention_triggers_search(bdd, paperless, matrix):
    """In a 3-member room (not a DM), a plain message is ignored but an
    @-mention makes the archivist react — here, run a (literal) search
    and reply, even if it finds nothing."""
    bdd.scenario("Homer @-mentions the archivist in a group room")

    homer = _client_from(matrix["homer"])
    marge = _client_from(matrix["marge"])
    try:
        bdd.given("a 3-member room with Homer, Marge, and the archivist")
        create = await homer.room_create(
            name="Family Chat", visibility=RoomVisibility.private,
        )
        room_id = create.room_id
        assert isinstance(
            await homer.room_invite(room_id, marge.user_id), RoomInviteResponse,
        )
        await marge.join(room_id)
        assert isinstance(
            await homer.room_invite(room_id, ARCHIVIST_MXID), RoomInviteResponse,
        )
        await _wait_for_bot_membership(homer, room_id)
        bdd.ok(f"group room {room_id}")

        bdd.when("Homer @-mentions the archivist with a search term")
        await homer.room_send(
            room_id, "m.room.message",
            {
                "msgtype": "m.text",
                "body": f"{ARCHIVIST_MXID} ADAC",
                "m.mentions": {"user_ids": [ARCHIVIST_MXID]},
            },
        )

        bdd.then("the archivist reacts with a search reply")
        reply = await _wait_for_reply(
            homer, room_id,
            predicate=lambda e: (
                getattr(e, "sender", None) == ARCHIVIST_MXID
                and event_type(e) == "m.room.message"
            ),
        )
        assert reply, "archivist did not react to the @-mention"
        bdd.ok(f"archivist replied: {getattr(reply, 'body', '')[:60]!r}")
    finally:
        await homer.close()
        await marge.close()


# ── Topic room: welcome posts exactly once ────────────────────────────────


async def test_topic_room_welcomes_only_once(bdd, openai, paperless, code, homer):
    """The archivist greets a `Topic:` room once, on join — not again on
    later messages.

    Regression guard: the welcome gate is the `dev.famstack.welcome`
    state event written right after the greeting. If that write fails
    (e.g. the bot lacks power to send state events in a user-created
    room) or the gate is skipped on the per-event fallback path, the
    bot re-welcomes on every message in the room.
    """
    bdd.scenario("Homer creates a topic room and posts two messages")

    welcome_marker = "drop in this room"  # from `welcome_topic` (en)

    def _is_welcome(e) -> bool:
        return (
            getattr(e, "sender", None) == ARCHIVIST_MXID
            and event_type(e) == "m.room.message"
            and welcome_marker in getattr(e, "body", "")
        )

    bdd.given("Homer creates `Topic: Treehouse` and invites the archivist")
    create = await homer.room_create(
        name="Topic: Treehouse", visibility=RoomVisibility.private,
    )
    room_id = create.room_id
    assert isinstance(
        await homer.room_invite(room_id, ARCHIVIST_MXID), RoomInviteResponse,
    )
    await _wait_for_bot_membership(homer, room_id)
    bdd.ok(f"topic room {room_id}")

    bdd.then("the archivist posts the topic welcome")
    first = await _wait_for_reply(homer, room_id, predicate=_is_welcome)
    assert first, "archivist never posted the topic welcome"
    bdd.ok(f"welcome {first.event_id}")

    bdd.when("Homer posts two ordinary messages")
    extra_welcomes = []
    for body in ("Buy planks and rope", "Bart wants a rope ladder"):
        await homer.room_send(
            room_id, "m.room.message", {"msgtype": "m.text", "body": body},
        )
        events = await fetch_room_events(homer, room_id, duration=10.0)
        extra_welcomes += [e for e in events if _is_welcome(e)]

    bdd.then("no further welcome lands")
    assert not extra_welcomes, (
        f"archivist re-welcomed {len(extra_welcomes)} time(s) after "
        f"messages: {[e.event_id for e in extra_welcomes]}"
    )
    bdd.ok("welcome stayed a one-time greeting")
