"""Live demo-rig tests.

These tests run against the already-running Simpson demo instance in
this checkout. They do not seed config, start stacklets, or stub the AI
endpoint. Use:

    tests/integration/stacktests demo-rig -s

Each test uses a unique token and private room/file names so repeated
runs can coexist with the demo. Paperless and Forgejo artifacts are
cleaned up where the service API allows it; Matrix rooms are left as
private, uniquely named rooms.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from nio.api import RoomVisibility
from nio.responses import JoinedMembersResponse, RoomInviteResponse

from tests.integration.forgejo import ForgejoError
from tests.integration.matrix import (
    ensure_joined,
    upload_and_send_file,
    wait_for_room,
)


MEMORY_OWNER = "family"
MEMORY_REPO = "memory"


def _frontmatter_text(fm: dict) -> str:
    return json.dumps(fm, sort_keys=True, default=str)


async def _wait_for_bot_membership(client, room_id: str, bot_mxid: str,
                                   timeout: int = 60) -> None:
    for _ in range(timeout):
        resp = await client.joined_members(room_id)
        if isinstance(resp, JoinedMembersResponse):
            if any(m.user_id == bot_mxid for m in resp.members):
                return
        await asyncio.sleep(1)
    raise AssertionError(f"{bot_mxid} did not join {room_id} within {timeout}s")


async def _wait_for_memory_file_containing(code, token: str,
                                           timeout: int = 300) -> str | None:
    for _ in range(timeout):
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
                fm, body = code.load_frontmatter(MEMORY_OWNER, MEMORY_REPO, path)
            except Exception:
                continue
            if token in body or token in _frontmatter_text(fm):
                return path
        await asyncio.sleep(1)
    return None


def _cleanup_memory_files_containing(code, token: str) -> None:
    try:
        tree = code.list_tree(MEMORY_OWNER, MEMORY_REPO)
    except ForgejoError:
        return
    for entry in tree:
        path = entry.get("path", "")
        if entry.get("type") != "blob" or not path.endswith(".md"):
            continue
        try:
            fm, body = code.load_frontmatter(MEMORY_OWNER, MEMORY_REPO, path)
        except Exception:
            continue
        if token not in body and token not in _frontmatter_text(fm):
            continue
        try:
            code.delete_file(
                MEMORY_OWNER, MEMORY_REPO, path,
                f"chore: demo rig cleanup {token}",
            )
        except Exception:
            pass


def _cleanup_paperless_documents_containing(paperless, token: str) -> None:
    try:
        docs = paperless.list_documents(query=token)
    except Exception:
        return
    for doc in docs:
        try:
            paperless.delete_document(doc["id"])
        except Exception:
            pass


@pytest.mark.demo_rig
async def test_demo_rig_private_capture_reaches_memory_with_live_ai(
    bdd,
    demo_code,
    demo_homer,
    demo_server_name,
    scope,
):
    """Homer pastes a note in an isolated room; archivist files it."""
    token = f"demo-rig-capture-{scope.uid}"
    bot_mxid = f"@archivist-bot:{demo_server_name}"

    try:
        bdd.given("Homer creates a private demo-rig notes room")
        create = await demo_homer.room_create(
            name=f"Demo Rig Notes {token}",
            visibility=RoomVisibility.private,
        )
        room_id = getattr(create, "room_id", None)
        assert room_id, f"room_create returned no room_id: {create}"

        invite = await demo_homer.room_invite(room_id, bot_mxid)
        assert isinstance(invite, RoomInviteResponse), f"invite failed: {invite}"
        await _wait_for_bot_membership(demo_homer, room_id, bot_mxid)

        pasted_text = (
            f"{token}\n\n"
            "Homer is testing the live demo rig with the configured AI endpoint. "
            "This note should be captured as a memory entry, preserve this unique "
            "token in the original paste, and remain isolated to this private room. "
            "The content is intentionally long enough to trigger the capture path "
            "without relying on a stubbed classifier response."
        )

        bdd.when("Homer sends the demo-rig paste")
        await demo_homer.room_send(
            room_id=room_id,
            message_type="m.room.message",
            content={"msgtype": "m.text", "body": pasted_text},
        )

        bdd.then("family/memory contains the captured token")
        path = await _wait_for_memory_file_containing(demo_code, token)
        assert path, f"No memory file containing {token!r} appeared within 300s."
        fm, body = demo_code.load_frontmatter(MEMORY_OWNER, MEMORY_REPO, path)
        assert token in body or token in _frontmatter_text(fm)
        assert path.startswith("homer/"), f"expected Homer-owned path, got {path}"
    finally:
        _cleanup_memory_files_containing(demo_code, token)


@pytest.mark.demo_rig
async def test_demo_rig_documents_markdown_reaches_paperless_and_memory_with_live_ai(
    bdd,
    demo_code,
    demo_homer,
    demo_paperless,
    demo_server_name,
    scope,
):
    """Homer uploads markdown to #documents; Paperless and memory see it."""
    token = f"demo-rig-doc-{scope.uid}"
    docs_alias = f"#documents:{demo_server_name}"

    try:
        bdd.given("Homer joins the live #documents room")
        room_id = await wait_for_room(demo_homer, docs_alias, timeout=90)
        await ensure_joined(demo_homer, room_id)

        markdown = (
            f"# Demo Rig Markdown {token}\n\n"
            "This markdown file is uploaded to the live Simpson demo rig. "
            "The archivist should send it through Paperless, classify it "
            "with the configured AI endpoint, and mirror the preserved body "
            "into family/memory.\n\n"
            f"Unique verification token: {token}\n"
        ).encode("utf-8")

        bdd.when("Homer uploads the markdown file")
        await upload_and_send_file(
            demo_homer,
            room_id,
            markdown,
            filename=f"{token}.md",
            mime_type="text/markdown",
            msgtype="m.file",
        )

        bdd.then("Paperless indexes the unique token")
        paperless_hit = None
        for _ in range(240):
            matches = demo_paperless.list_documents(query=token)
            if matches:
                paperless_hit = matches[0]
                break
            await asyncio.sleep(1)
        assert paperless_hit, f"No Paperless document containing {token!r} within 240s."

        bdd.and_("family/memory contains the mirrored token")
        path = await _wait_for_memory_file_containing(demo_code, token)
        assert path, f"No memory mirror containing {token!r} appeared within 300s."
        fm, body = demo_code.load_frontmatter(MEMORY_OWNER, MEMORY_REPO, path)
        assert token in body or token in _frontmatter_text(fm)
        assert fm.get("paperless_id"), f"mirror frontmatter missing paperless_id: {fm}"
    finally:
        _cleanup_paperless_documents_containing(demo_paperless, token)
        _cleanup_memory_files_containing(demo_code, token)
