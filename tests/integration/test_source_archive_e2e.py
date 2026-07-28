"""End-to-end: react 📎 on a mail card → the whole email archived to Paperless.

Posts what the mail bot produces — a `dev.famstack.source` card plus a
threaded attachment carrying the generic `source_event` back-reference — then
reacts 📎 and asserts the archivist assembled the lot into one Paperless
document: the header as a searchable note, a flat `email` provenance tag, and
a single PDF whose page count proves the body page and the two attachment
pages were combined.

This drives the generic source-archive path end to end (reaction dispatch →
thread gather → PDF assembly → Paperless filing) without the mail bot's IMAP
side, which is covered by `test_email_imap_e2e.py`. The assertions avoid
depending on OCR: the doc is found by its header note (exact text, not OCR'd
content), and combination is proven by downloading the archived PDF and
counting pages.

Run with `-s` to stream the BDD narration:

    uv run --extra test pytest -s tests/integration/test_source_archive_e2e.py
"""

from __future__ import annotations

import asyncio
import io
import urllib.request

import pytest
from PIL import Image
from pypdf import PdfReader

from tests.integration.openai_stub import stub_classify, stub_reformat


# The framework contract the archivist consumes — deliberately spelled out
# here (not imported) so this test pins the wire shape the mail bot emits.
SOURCE_KEY = "dev.famstack.source"
ATTACHMENT_KEY = "dev.famstack.attachment"
ARCHIVIST_MXID = "@archivist-bot:test.local"
PAPERCLIP = "📎"


async def _wait_for_bot_membership(client, room_id: str, timeout: int = 30) -> None:
    from nio.responses import JoinedMembersResponse

    for _ in range(timeout):
        resp = await client.joined_members(room_id)
        if isinstance(resp, JoinedMembersResponse):
            if any(m.user_id == ARCHIVIST_MXID for m in resp.members):
                return
        await asyncio.sleep(1)
    raise AssertionError(f"{ARCHIVIST_MXID} did not join {room_id} within {timeout}s")


def _two_page_pdf() -> bytes:
    """A minimal two-page PDF fixture (solid colour pages)."""
    imgs = [Image.new("RGB", (300, 200), (200, 40, 40)),
            Image.new("RGB", (300, 200), (40, 40, 200))]
    buf = io.BytesIO()
    imgs[0].save(buf, "PDF", save_all=True, append_images=imgs[1:])
    return buf.getvalue()


def _archived_page_count(paperless, doc_id: int) -> int:
    """Download the doc's PDF and count its pages (deterministic, no OCR)."""
    req = urllib.request.Request(
        f"{paperless.url.rstrip('/')}/api/documents/{doc_id}/download/",
        headers={"Authorization": f"Token {paperless.token}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    return len(PdfReader(io.BytesIO(data)).pages)


@pytest.mark.smoke
async def test_paperclip_react_archives_source_card_to_paperless(
    bdd, openai, paperless, scope, homer,
):
    """Homer 📎-reacts a mail card → one PDF (body + attachments) in Paperless.

    Scenario
    --------
    Given  a room the archivist has joined
    When   a mail source card and a threaded 2-page attachment are posted
    And    Homer reacts 📎 on the card
    Then   Paperless holds one document filed from that email
    And    it carries the header as a note (with the sender address)
    And    it carries a flat `email` provenance tag
    And    its PDF combines the body page with the two attachment pages
    """
    bdd.scenario("React 📎 on a mail card archives the whole email to Paperless")

    sender = "service@osiander.de"
    subject = scope.tag("Ihre Sendung 1434978587")

    # ── Given ────────────────────────────────────────────────────────
    from nio.api import RoomVisibility
    from nio.responses import RoomInviteResponse

    bdd.given(f"a private room the archivist joins ({scope.uid})")
    create = await homer.room_create(
        name=f"Mail {scope.uid}", visibility=RoomVisibility.private,
    )
    room_id = getattr(create, "room_id", None)
    assert room_id, f"room_create returned no room_id: {create}"
    assert isinstance(
        await homer.room_invite(room_id, ARCHIVIST_MXID), RoomInviteResponse,
    )
    await _wait_for_bot_membership(homer, room_id)
    bdd.ok(f"{ARCHIVIST_MXID} joined {room_id}")

    # Filing classifies; stub both LLM calls so the pipeline reaches the
    # tag/note PATCH deterministically.
    stub_classify(openai, {
        "title": scope.tag("Bestellbestätigung"),
        "persons": ["Homer"],
        "tags": [scope.tag("bestellung")],
        "summary": "Order confirmation.",
        "facts": ["Order 1434978587"],
    })
    stub_reformat(openai, "Order 1434978587 confirmed.")

    # ── When: the mail bot's shape ───────────────────────────────────
    bdd.when("a mail source card is posted (body carried in the source block)")
    header_body = f"📧 {subject}\nFrom {sender}\nDate 2026-07-18"
    card = await homer.room_send(
        room_id, "m.room.message",
        content={
            "msgtype": "m.text",
            "body": header_body,
            SOURCE_KEY: {
                "source": "email",
                "raw_content": f"Your order {scope.uid} has shipped.",
                "from": sender,
                "subject": subject,
                "message_id": f"<{scope.uid}@osiander.de>",
                "captured_at": "2026-07-18",
            },
        },
    )
    card_id = card.event_id
    bdd.detail(f"card event_id = {card_id}")

    bdd.and_("a 2-page attachment threaded under the card (source_event marker)")
    pdf = _two_page_pdf()
    from nio import UploadResponse
    upload, _ = await homer.upload(
        data_provider=lambda *_: io.BytesIO(pdf),
        content_type="application/pdf",
        filename="lieferschein.pdf",
        filesize=len(pdf),
    )
    assert isinstance(upload, UploadResponse), f"upload failed: {upload}"
    await homer.room_send(
        room_id, "m.room.message",
        content={
            "msgtype": "m.file",
            "body": "lieferschein.pdf",
            "filename": "lieferschein.pdf",
            "url": upload.content_uri,
            "info": {"mimetype": "application/pdf", "size": len(pdf)},
            "m.relates_to": {"rel_type": "m.thread", "event_id": card_id},
            ATTACHMENT_KEY: {
                "source": "email",
                "source_event": card_id,
                "message_id": f"<{scope.uid}@osiander.de>",
            },
        },
    )

    bdd.and_("Homer reacts 📎 on the card")
    await homer.room_send(room_id, "m.reaction", {
        "m.relates_to": {
            "rel_type": "m.annotation", "event_id": card_id, "key": PAPERCLIP,
        },
    })

    # ── Then ─────────────────────────────────────────────────────────
    # Find the doc by its header note (exact text, no OCR dependency). The
    # note is written post-filing for every archived source card, so this
    # holds even if OCR of the rendered body page is imperfect.
    bdd.then("Paperless has a document whose header note carries this email")

    async def _find_doc():
        for _ in range(120):
            for d in paperless.list_documents():
                notes = paperless.list_notes(d["id"])
                if any(scope.uid in (n.get("note") or "") for n in notes):
                    return d
            await asyncio.sleep(1)
        return None

    doc = await _find_doc()
    assert doc, (
        f"no Paperless doc with a note mentioning {scope.uid} within 120s — "
        "check bot-runner logs"
    )
    bdd.ok(f"Paperless doc #{doc['id']}")

    bdd.and_("the note carries the sender address (searchable)")
    note_text = "\n".join(n.get("note", "") for n in paperless.list_notes(doc["id"]))
    assert sender in note_text, f"sender {sender!r} missing from note: {note_text!r}"

    bdd.and_("the doc carries a flat `email` provenance tag")
    tag_names = {t["name"] for t in paperless.list_tags() if t["id"] in doc.get("tags", [])}
    assert "email" in tag_names, f"expected 'email' tag, got {sorted(tag_names)}"

    bdd.and_("the PDF combines the body page with the two attachment pages")
    pages = _archived_page_count(paperless, doc["id"])
    assert pages >= 3, f"expected body + 2 attachment pages, got {pages}"
    bdd.ok(f"doc #{doc['id']}: 'email' tag, sender in note, {pages} pages")
