"""End-to-end coverage for the capture-binary path.

Captures used to be URL-only and text-only. Recent work added:

  - Images in non-documents rooms become visual bookmarks; PDFs file
    as notes with the extracted text preserved (reformat + classify,
    two LLM calls). Either way the binary stays on the homeserver
    (`source_uri: mxc://...`) instead of duplicating into Paperless.
  - `.md` / `.txt` uploads land as `type: note` with the bytes
    preserved verbatim in the mirror.
  - Replying (without `@`-mention) to a `capture.filed` confirmation
    re-runs the classifier and rewrites the entry in place, emitting
    a `capture.reclassified` envelope so further corrections can chain.
  - `@`-mention in a capture room is conversational: the mention
    bypasses the reply-to-correct router so search queries reach the
    search handler.

These four scenarios exercise the framework end-to-end against the
real container rig (Synapse, Forgejo, bot-runner) with OpenAI stubbed
deterministically via pytest-httpserver.

Run with `-s` to stream the BDD narration:

    uv run --extra test pytest -s tests/integration/test_capture_binary_e2e.py
"""

from __future__ import annotations

from tests.integration.matrix import mxid

import asyncio
import io

from nio.api import RoomVisibility
from nio.responses import RoomInviteResponse, UploadResponse

from tests.integration.forgejo import ForgejoError
from tests.integration.openai_stub import stub_classify, stub_reformat


MEMORY_OWNER = "family"
MEMORY_REPO = "memory"
ARCHIVIST_MXID = mxid("archivist-bot")
FAMSTACK_EVENT_KEY = "dev.famstack.event"


# ── Shared helpers ──────────────────────────────────────────────────────


async def _wait_for_bot_membership(client, room_id: str, bot_mxid: str,
                                   timeout: int = 30) -> None:
    """Poll until the archivist has accepted its invite and joined."""
    from nio.responses import JoinedMembersResponse
    for _ in range(timeout):
        resp = await client.joined_members(room_id)
        if isinstance(resp, JoinedMembersResponse):
            if any(m.user_id == bot_mxid for m in resp.members):
                return
        await asyncio.sleep(1)
    raise AssertionError(f"{bot_mxid} did not join {room_id} within {timeout}s")


async def _wait_for_capture_by_title(code, title: str,
                                     timeout: int = 60,
                                     stub=None) -> str | None:
    """Find a mirror file whose frontmatter title matches `title`.

    `stub` is the OpenAIStub; when given, an unexpected LLM call aborts
    the wait immediately with the offending prompt instead of burning
    the full timeout on a title that can no longer appear.
    """
    for _ in range(timeout):
        if stub is not None:
            stub.raise_if_unexpected()
        try:
            tree = code.list_tree(MEMORY_OWNER, MEMORY_REPO)
        except ForgejoError:
            await asyncio.sleep(1)
            continue
        for entry in tree:
            path = entry.get("path", "")
            if entry.get("type") != "blob" or not path.endswith(".md"):
                continue
            if path == "README.md":
                continue
            try:
                fm, _ = code.load_frontmatter(MEMORY_OWNER, MEMORY_REPO, path)
            except Exception:
                continue
            if isinstance(fm, dict) and fm.get("title") == title:
                return path
        await asyncio.sleep(1)
    return None


async def _wait_for_bot_reply(
    client, room_id: str, *, after_ts_ms: int,
    envelope_type: str | None = None,
    body_substring: str | None = None,
    timeout: int = 60,
    stub=None,
):
    """Sync until the archivist posts a message matching the predicate.

    Each round consumes one sync long-poll (~5s); bounded by ``timeout``
    seconds. Returns the matching event, or None on timeout. `stub` is
    the OpenAIStub — see `_wait_for_capture_by_title`.
    """
    from nio import SyncResponse, RoomMessageText
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if stub is not None:
            stub.raise_if_unexpected()
        resp = await client.sync(timeout=4000)
        if not isinstance(resp, SyncResponse):
            continue
        room = resp.rooms.join.get(room_id)
        if room is None:
            continue
        for ev in room.timeline.events:
            if not isinstance(ev, RoomMessageText):
                continue
            if ev.sender != ARCHIVIST_MXID:
                continue
            if ev.server_timestamp < after_ts_ms:
                continue
            if body_substring and body_substring not in ev.body:
                continue
            if envelope_type:
                env = ev.source.get("content", {}).get(FAMSTACK_EVENT_KEY) or {}
                if env.get("type") != envelope_type:
                    continue
            return ev
    return None


def _tiny_pdf(text_lines: list[str]) -> bytes:
    """Hand-rolled single-page PDF with each line at its own y-offset.

    Just real enough for Paperless OCR + pypdf text-layer extraction
    to recover the content. Avoids pulling reportlab into the test
    dependency surface for one test scenario.
    """
    def _esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    parts = ["BT", "/F1 14 Tf", "72 760 Td"]
    for i, line in enumerate(text_lines):
        if i > 0:
            parts.append("0 -18 Td")
        parts.append(f"({_esc(line)}) Tj")
    parts.append("ET")
    stream = "\n".join(parts).encode("latin-1")
    obj1 = b"<< /Type /Catalog /Pages 2 0 R >>"
    obj2 = b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"
    obj3 = (
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
    )
    obj4 = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    obj5 = (
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
        + stream + b"\nendstream"
    )
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, body in enumerate((obj1, obj2, obj3, obj4, obj5), start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 6\n0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n"
    ).encode()
    return bytes(out)


async def _matrix_upload(client, data: bytes, filename: str, mime: str) -> str:
    """Upload bytes to the homeserver media repo; return the mxc URL."""
    upload, _ = await client.upload(
        data_provider=lambda *_: io.BytesIO(data),
        content_type=mime,
        filename=filename,
        filesize=len(data),
    )
    if not isinstance(upload, UploadResponse):
        raise AssertionError(f"matrix upload failed: {upload}")
    return upload.content_uri


async def _create_notes_room(homer, name: str) -> str:
    """Create a private notes room, invite the archivist, wait for join."""
    create = await homer.room_create(
        name=name, visibility=RoomVisibility.private,
    )
    room_id = getattr(create, "room_id", None)
    assert room_id, f"room_create returned no room_id: {create}"
    invite = await homer.room_invite(room_id, ARCHIVIST_MXID)
    assert isinstance(invite, RoomInviteResponse), f"invite failed: {invite}"
    await _wait_for_bot_membership(homer, room_id, ARCHIVIST_MXID)
    return room_id


def _now_ms() -> int:
    import time
    return int(time.time() * 1000)


# ── Scenario 1: PDF in capture room → kind=note ────────────────────────


async def test_pdf_in_capture_room_becomes_a_note(
    bdd, openai, paperless, mirror_scope, code, homer,
):
    """Homer drops a small text-layer PDF in a notes room → note.

    PDFs file as kind=note with the extracted text preserved in the
    mirror (4513ff8), so a later `?` query can grep the actual content.
    The pipeline makes TWO LLM calls per PDF: reformat (cleans the
    pypdf text into markdown), then classify.

    Scenario
    --------
    Given  a notes room with the archivist invited
    And    the OpenAI mock will reformat the body, then classify it
    When   Homer uploads a small PDF
    Then   family/memory has the entry under `homer/notes/`
    And    the frontmatter carries `type: note`, the mxc source_uri,
           and the capture_id matching the upload event_id
    """
    scope = mirror_scope
    bdd.scenario("PDF in notes room becomes a kind=note mirror entry")

    expected_title = scope.tag("Springfield travel notes")
    expected_tag = scope.tag("Travel")

    bdd.given(f"Homer creates a notes room and invites {ARCHIVIST_MXID}")
    room_id = await _create_notes_room(homer, f"Homer Notes {scope.uid}")
    bdd.detail(f"room_id = {room_id}")

    bdd.given("the OpenAI mock will reformat the PDF body, then classify it")
    stub_reformat(openai, (
        "# Springfield Travel Notes\n\n"
        f"Marker: {scope.uid}\n\n"
        "Plan a trip in May for the annual chili cook-off."
    ))
    stub_classify(openai, {
        "title": expected_title,
        "persons": ["Homer"],
        "tags": [expected_tag],
        "summary": "Notes about a planned trip to Springfield's annual chili cook-off.",
        "facts": ["Annual cook-off in spring", "Family of four attending"],
    })

    bdd.when("Homer uploads a small PDF to the notes room")
    pdf = _tiny_pdf([
        "Springfield Travel Notes",
        f"Marker: {scope.uid}",
        "Plan a trip in May for the annual chili cook-off.",
    ])
    filename = f"travel-{scope.uid}.pdf"
    mxc = await _matrix_upload(homer, pdf, filename, "application/pdf")
    before = _now_ms()
    send = await homer.room_send(
        room_id=room_id, message_type="m.room.message",
        content={
            "msgtype": "m.file",
            "body": filename,
            "url": mxc,
            "info": {"mimetype": "application/pdf", "size": len(pdf)},
        },
    )
    upload_event_id = send.event_id
    bdd.detail(f"upload event_id = {upload_event_id}")

    bdd.then(f"family/memory has a capture entry titled '{expected_title}'")
    path = await _wait_for_capture_by_title(code, expected_title, stub=openai)
    assert path, (
        f"No capture file titled {expected_title!r} appeared in "
        f"{MEMORY_OWNER}/{MEMORY_REPO} within 60s. Check bot-runner logs."
    )
    bdd.ok(f"mirror path = {path}")

    bdd.and_("the path lives under homer/notes/")
    assert path.startswith("homer/notes/"), \
        f"expected homer/notes/ prefix, got {path}"

    bdd.then("frontmatter carries type=note, mxc source_uri, capture_id")
    fm, _body = code.load_frontmatter(MEMORY_OWNER, MEMORY_REPO, path)
    assert fm.get("type") == "note", f"expected type=note, got {fm.get('type')!r}"
    assert fm.get("title") == expected_title
    assert fm.get("resource") == mxc, \
        f"expected resource={mxc}, got {fm.get('resource')!r}"
    assert fm.get("capture_id") == upload_event_id, (
        f"expected capture_id={upload_event_id!r}, got {fm.get('capture_id')!r} "
        "-- Matrix event_id MUST persist as the stable correlation key"
    )
    bdd.ok(f"type={fm['type']} capture_id={fm['capture_id'][:24]}...")

    bdd.and_("a capture.filed envelope was emitted on the bot's reply")
    reply = await _wait_for_bot_reply(
        homer, room_id, after_ts_ms=before,
        envelope_type="capture.filed",
        body_substring=expected_title,
        stub=openai,
    )
    assert reply is not None, "no capture.filed envelope on the bot's reply"
    env = reply.source["content"][FAMSTACK_EVENT_KEY]
    assert env["data"]["vault_path"] == path, \
        f"envelope vault_path={env['data']['vault_path']} != mirror path={path}"
    assert env["data"]["capture_id"] == upload_event_id


# ── Scenario 2: .md upload → kind=note, body preserved ─────────────────


async def test_markdown_upload_in_capture_room_becomes_a_note(
    bdd, openai, paperless, mirror_scope, code, homer,
):
    """An .md file is content-bearing, not a pointer → kind=note.

    Scenario
    --------
    Given  a notes room with the archivist invited
    And    the OpenAI mock classifies the upload as a note
    When   Homer uploads a Markdown file
    Then   the mirror entry is `type: note`, lives under `homer/notes/`,
           and the original bytes are preserved verbatim in the body
    """
    scope = mirror_scope
    bdd.scenario("Markdown upload becomes kind=note with body preserved")

    expected_title = scope.tag("Quick LLM notes")

    bdd.given(f"Homer creates a notes room and invites {ARCHIVIST_MXID}")
    room_id = await _create_notes_room(homer, f"Homer Md {scope.uid}")

    bdd.given("the OpenAI mock returns a note classification")
    stub_classify(openai, {
        "title": expected_title,
        "persons": ["Homer"],
        "tags": [scope.tag("LLMs")],
        "summary": "A few quick notes on running quantised models locally.",
        "facts": [],
    })

    bdd.when("Homer uploads a Markdown file")
    body_text = (
        "# Quick LLM notes\n\n"
        "Random observations about MLX-LM running on Apple Silicon.\n\n"
        f"- Marker: {scope.uid}\n"
        "- Quantised models keep the memory footprint reasonable.\n"
        "- `mlx_lm.server` exposes an OpenAI-compatible API.\n"
    )
    data = body_text.encode("utf-8")
    filename = f"notes-{scope.uid}.md"
    mxc = await _matrix_upload(homer, data, filename, "text/markdown")
    send = await homer.room_send(
        room_id=room_id, message_type="m.room.message",
        content={
            "msgtype": "m.file",
            "body": filename,
            "url": mxc,
            "info": {"mimetype": "text/markdown", "size": len(data)},
        },
    )
    bdd.detail(f"upload event_id = {send.event_id}")

    bdd.then(f"family/memory has a note titled '{expected_title}'")
    path = await _wait_for_capture_by_title(code, expected_title)
    assert path, f"no note titled {expected_title!r} appeared within 60s"
    bdd.ok(f"mirror path = {path}")

    bdd.and_("the path lives under homer/notes/")
    assert path.startswith("homer/notes/"), \
        f"expected homer/notes/ prefix, got {path}"

    bdd.then("kind=note and the original bytes are preserved in the body")
    fm, body = code.load_frontmatter(MEMORY_OWNER, MEMORY_REPO, path)
    assert fm.get("type") == "note", f"expected type=note, got {fm.get('type')!r}"
    assert fm.get("resource") == mxc
    # The mirror renders the original under a collapsible callout; each
    # line is `> `-prefixed. Check the marker landed there verbatim.
    assert f"> - Marker: {scope.uid}" in body, (
        f"original paste missing from body. First 600 chars: {body[:600]!r}"
    )


# ── Scenario 3: reply-to-correct rewrites the capture in place ─────────


async def test_capture_reply_to_correct_rewrites_mirror_and_emits_reclassified(
    bdd, openai, paperless, mirror_scope, code, homer,
):
    """Homer files a capture, then replies with a correction.

    Scenario
    --------
    Given  the archivist filed a capture (envelope captured)
    When   Homer replies to the Gespeichert message with a correction
    Then   the archivist re-classifies the entry, rewrites the mirror
           in place (new title + tags), and emits a capture.reclassified
           envelope so further corrections can chain
    """
    scope = mirror_scope
    bdd.scenario("Reply-to-correct rewrites the capture entry in place")

    initial_title = scope.tag("Initial title")
    corrected_title = scope.tag("Corrected title")
    initial_tag = scope.tag("Misclassified")
    corrected_tag = scope.tag("Correct")

    bdd.given(f"Homer creates a notes room and invites {ARCHIVIST_MXID}")
    room_id = await _create_notes_room(homer, f"Homer Correct {scope.uid}")

    bdd.given("the OpenAI mock will reformat once and classify twice")
    # The PDF upload runs reformat → classify; the reprocess pass after
    # Homer's correction re-reads the mirror summary, so classify only.
    stub_reformat(openai, (
        "# Capture under test\n\n"
        f"Marker: {scope.uid}\n\n"
        "Some captured content for the bot to summarise."
    ))
    stub_classify(openai, {
        "title": initial_title,
        "persons": ["Homer"],
        "tags": [initial_tag],
        "summary": "Initial classification of a captured PDF.",
        "facts": [],
    })
    # Reprocess pass. The bot reads the prior summary + tags from the
    # mirror, classifier sees them in the source text and the chain
    # walker's hint; we stub a fresh payload that the rewrite uses.
    stub_classify(openai, {
        "title": corrected_title,
        "persons": ["Homer"],
        "tags": [corrected_tag],
        "summary": "Corrected classification reflecting Homer's hint.",
        "facts": [],
    })

    bdd.when("Homer uploads a small PDF to the notes room")
    pdf = _tiny_pdf([
        "Capture under test",
        f"Marker: {scope.uid}",
        "Some captured content for the bot to summarise.",
    ])
    filename = f"capture-{scope.uid}.pdf"
    mxc = await _matrix_upload(homer, pdf, filename, "application/pdf")
    before_initial = _now_ms()
    await homer.room_send(
        room_id=room_id, message_type="m.room.message",
        content={
            "msgtype": "m.file",
            "body": filename,
            "url": mxc,
            "info": {"mimetype": "application/pdf", "size": len(pdf)},
        },
    )

    bdd.then(f"the initial capture is filed as '{initial_title}'")
    initial_path = await _wait_for_capture_by_title(code, initial_title, stub=openai)
    assert initial_path, "initial capture not found within 60s"
    bdd.ok(f"initial path = {initial_path}")

    bdd.and_("the bot's filing reply carries a capture.filed envelope")
    filed_reply = await _wait_for_bot_reply(
        homer, room_id, after_ts_ms=before_initial,
        envelope_type="capture.filed",
        body_substring=initial_title,
        stub=openai,
    )
    assert filed_reply is not None, "missing capture.filed envelope"

    bdd.when("Homer replies to the Gespeichert message with a correction")
    correction = "Actually the title should reflect a Correct classification."
    before_reprocess = _now_ms()
    await homer.room_send(
        room_id=room_id, message_type="m.room.message",
        content={
            "msgtype": "m.text",
            "body": correction,
            "m.relates_to": {
                "m.in_reply_to": {"event_id": filed_reply.event_id},
            },
            "format": "org.matrix.custom.html",
            "formatted_body": (
                f'<mx-reply><blockquote>'
                f'<a href="https://matrix.to/#/{room_id}/{filed_reply.event_id}">'
                f'In reply to</a> '
                f'<a href="https://matrix.to/#/{ARCHIVIST_MXID}">{ARCHIVIST_MXID}</a>'
                f'<br>{filed_reply.body[:80]}'
                f'</blockquote></mx-reply>{correction}'
            ),
        },
    )

    bdd.then(f"the mirror is rewritten with the new title '{corrected_title}'")
    new_path = await _wait_for_capture_by_title(code, corrected_title, stub=openai)
    assert new_path, "rewritten capture not found within 60s"
    bdd.ok(f"new path = {new_path}")

    bdd.and_("the old entry is removed (rename, not duplicate)")
    if new_path != initial_path:
        # Title-slug renames remove the prior file. When the new title
        # happens to slug-equal the old one (unlikely with our scoped
        # tags) the same path survives -- skip the assertion in that case.
        try:
            still_there = await _wait_for_capture_by_title(
                code, initial_title, timeout=5,
            )
        except Exception:
            still_there = None
        assert still_there is None, (
            f"old entry {initial_path} still present after rename to {new_path}"
        )

    bdd.then("frontmatter shows the corrected tags and persists capture_id")
    fm, _ = code.load_frontmatter(MEMORY_OWNER, MEMORY_REPO, new_path)
    tags = fm.get("tags") or []
    assert corrected_tag in tags, f"expected {corrected_tag!r} in {tags}"
    assert initial_tag not in tags, (
        f"stale {initial_tag!r} survived in {tags} -- delta semantics broken"
    )
    assert isinstance(fm.get("capture_id"), str), \
        f"capture_id missing from rewritten entry: {fm}"

    bdd.and_("the bot replied with a capture.reclassified envelope")
    reclassed = await _wait_for_bot_reply(
        homer, room_id, after_ts_ms=before_reprocess,
        envelope_type="capture.reclassified",
        body_substring=corrected_title,
        stub=openai,
    )
    assert reclassed is not None, (
        "no capture.reclassified envelope -- chained corrections will not work"
    )
    env = reclassed.source["content"][FAMSTACK_EVENT_KEY]
    assert env["data"]["vault_path"] == new_path


# ── Scenario 4: @-mention with a search query hits search, not reprocess ─


async def test_mention_in_capture_room_routes_to_search_not_reprocess(
    bdd, openai, paperless, mirror_scope, code, homer,
):
    """`@`-mention overrides the reply-to-correct router.

    Element X attaches `m.in_reply_to` to mentioned messages even when
    they're conversational. Without the mention-guard the search query
    would be eaten by capture-reprocess and never reach
    `_handle_search`. We verify the routing here by sending a mention-
    shaped reply to a `capture.filed` confirmation and asserting the
    bot's response is a search-shaped reply (not a reclassified one).

    Scenario
    --------
    Given  the archivist filed a capture
    When   Homer @-mentions the bot with a search query, threaded as a
           reply to the Gespeichert message
    Then   the bot's reply is a search result (search emoji, not the
           reclassify emoji) AND no capture.reclassified envelope was
           emitted
    """
    scope = mirror_scope
    bdd.scenario("Mention with search query bypasses reprocess")

    filed_title = scope.tag("Indexable capture")

    bdd.given(f"Homer creates a notes room and invites {ARCHIVIST_MXID}")
    room_id = await _create_notes_room(homer, f"Homer Search {scope.uid}")

    bdd.given("the OpenAI mock will reformat and classify the upload")
    # The PDF upload runs reformat → classify. The mention-search that
    # follows makes NO LLM call -- the capture-room search returns
    # deterministic results from the test vault state. (The routed stub
    # proved the rewrite stub the old order-blind version queued here
    # was never consumed.)
    stub_reformat(openai, (
        "# Indexable capture\n\n"
        f"Marker: {scope.uid}\n\n"
        "Content to be searched later."
    ))
    stub_classify(openai, {
        "title": filed_title,
        "persons": ["Homer"],
        "tags": [scope.tag("Searchable")],
        "summary": "A captured doc Homer will later search for.",
        "facts": [],
    })

    bdd.when("Homer uploads a small PDF and waits for the filing reply")
    pdf = _tiny_pdf([
        "Indexable capture", f"Marker: {scope.uid}", "Content to be searched later.",
    ])
    filename = f"index-{scope.uid}.pdf"
    mxc = await _matrix_upload(homer, pdf, filename, "application/pdf")
    before_initial = _now_ms()
    await homer.room_send(
        room_id=room_id, message_type="m.room.message",
        content={
            "msgtype": "m.file", "body": filename, "url": mxc,
            "info": {"mimetype": "application/pdf", "size": len(pdf)},
        },
    )
    filed = await _wait_for_bot_reply(
        homer, room_id, after_ts_ms=before_initial,
        envelope_type="capture.filed", body_substring=filed_title,
        stub=openai,
    )
    assert filed is not None

    bdd.when("Homer @-mentions the bot with a search query as a reply")
    before_search = _now_ms()
    await homer.room_send(
        room_id=room_id, message_type="m.room.message",
        content={
            "msgtype": "m.text",
            "body": "Archivist: searchable",
            "format": "org.matrix.custom.html",
            "formatted_body": (
                f'<a href="https://matrix.to/#/{ARCHIVIST_MXID}">Archivist</a>'
                ': searchable'
            ),
            "m.mentions": {"user_ids": [ARCHIVIST_MXID]},
            "m.relates_to": {
                "m.in_reply_to": {"event_id": filed.event_id},
            },
        },
    )

    bdd.then("the bot replies with a search-shaped message")
    # Search replies start with the search emoji (`\U0001F50D` 🔍) per
    # `messages/archivist.yml`. A reclassified reply would start with
    # the loop emoji (`\U0001F501` 🔁). Asserting on the emoji is the
    # cheapest unambiguous shape check.
    reply = await _wait_for_bot_reply(
        homer, room_id, after_ts_ms=before_search,
        body_substring="\U0001F50D",
        stub=openai,
    )
    assert reply is not None, (
        "no search-shaped reply -- the mention guard regressed and search "
        "got swallowed by reprocess"
    )
    assert "\U0001F501" not in reply.body[:4], (
        f"reply looks like a reclassification, not a search: {reply.body[:80]!r}"
    )

    bdd.and_("no capture.reclassified envelope was emitted")
    reclassed = await _wait_for_bot_reply(
        homer, room_id, after_ts_ms=before_search,
        envelope_type="capture.reclassified", timeout=10,
    )
    assert reclassed is None, (
        "capture.reclassified envelope appeared -- the mention-guard let "
        "the search query slip into reprocess"
    )
