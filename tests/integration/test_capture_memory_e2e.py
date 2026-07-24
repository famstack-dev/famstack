"""End-to-end capture test — pasted text → `family/memory` mirror.

The archivist treats every room that isn't `#documents` as a capture
room: pasted text becomes a markdown "note" in the memory repo with
the original body preserved verbatim. This test exercises that flow
from Matrix invite to Forgejo commit, asserting that what the user
pastes is what lands in the vault.

The doc → memory path is covered by `test_git_mirror_e2e.py`; this
test covers the capture half of "documents and captures both end up
in memory."

Run with `-s` to stream the BDD narration live:

    uv run --extra test pytest -s tests/integration/test_capture_memory_e2e.py
"""

from __future__ import annotations

import asyncio

import pytest

from tests.integration.forgejo import ForgejoError
from tests.integration.openai_stub import stub_classify


# Same org/repo as the document mirror — captures and docs share the
# vault. Kept in lockstep with `FORGEJO_DOCS_OWNER` / `FORGEJO_DOCS_REPO`
# in conftest.py.
MEMORY_OWNER = "family"
MEMORY_REPO = "memory"

# Matrix ID the bot-runner registers the archivist under in the test
# instance (server name comes from `tests/integration/instance/stack.toml`).
ARCHIVIST_MXID = "@archivist-bot:test.local"

# _PASTE_MIN_CHARS = 100 inside the bot — gates `_looks_like_paste`.
# Anything shorter is treated as chat and ignored in capture rooms.
_PASTE_FLOOR = 100


async def _wait_for_bot_membership(client, room_id: str, bot_mxid: str,
                                   timeout: int = 30) -> None:
    """Poll the room state until the archivist has joined.

    Matrix invites and accepts are eventually-consistent: homer sees
    his send return long before the bot's sync picks the invite up and
    issues a join. We poll Synapse via `joined_members` rather than
    `client.rooms` so we don't depend on homer's own sync cadence.
    """
    from nio.responses import JoinedMembersResponse

    for _ in range(timeout):
        resp = await client.joined_members(room_id)
        if isinstance(resp, JoinedMembersResponse):
            if any(m.user_id == bot_mxid for m in resp.members):
                return
        await asyncio.sleep(1)
    raise AssertionError(
        f"{bot_mxid} did not join {room_id} within {timeout}s"
    )


async def _wait_for_capture_by_title(code, title: str,
                                     timeout: int = 60) -> str | None:
    """Find a mirror file whose frontmatter title matches `title`.

    Captures don't carry a Paperless id, so we can't use
    `find_by_paperless_id`. We walk the tree and match by frontmatter
    title — slow but bounded and only runs in tests.
    """
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
                fm, _ = code.load_frontmatter(MEMORY_OWNER, MEMORY_REPO, path)
            except Exception:
                continue
            if isinstance(fm, dict) and fm.get("title") == title:
                return path
        await asyncio.sleep(1)
    return None


@pytest.mark.smoke
async def test_archivist_captures_pasted_text_to_memory(
    bdd,
    openai,
    paperless,
    mirror_scope,
    code,
    homer,
):
    """Homer pastes text in a notes room → mirrored to family/memory.

    Scenario
    --------
    Given  the code stacklet is up and Homer has a Matrix client
    When   Homer creates a private notes room and invites the archivist
    And    Homer pastes a wall of text into the room
    Then   `family/memory` contains a capture entry under `homer/notes/`
    And    the frontmatter marks it `type: note`
    And    the body preserves the pasted text verbatim
    """
    scope = mirror_scope
    bdd.scenario("Archivist captures pasted text into family/memory")

    expected_title = scope.tag("Capybara field notes")
    expected_tag = scope.tag("Capybaras")

    # ── Given ────────────────────────────────────────────────────────
    bdd.given("the code (Forgejo) stacklet is reachable")
    assert code.ping(), "Forgejo API unreachable at http://localhost:42040"

    bdd.given(f"Homer creates a private notes room and invites {ARCHIVIST_MXID}")
    from nio.api import RoomVisibility
    create = await homer.room_create(
        name=f"Homer Notes {scope.uid}",
        visibility=RoomVisibility.private,
    )
    room_id = getattr(create, "room_id", None)
    assert room_id, f"room_create returned no room_id: {create}"
    bdd.detail(f"room_id = {room_id}")

    from nio.responses import RoomInviteResponse
    invite_resp = await homer.room_invite(room_id, ARCHIVIST_MXID)
    assert isinstance(invite_resp, RoomInviteResponse), \
        f"invite failed: {invite_resp}"

    bdd.given("the archivist auto-accepts and joins")
    await _wait_for_bot_membership(homer, room_id, ARCHIVIST_MXID)
    bdd.ok(f"{ARCHIVIST_MXID} joined")

    bdd.given("the OpenAI mock returns a capture classification")
    stub_classify(openai, {
        "title": expected_title,
        "persons": ["Homer"],
        "tags": [expected_tag],
        "summary": "Field notes about capybara social behavior.",
        "facts": ["Largest living rodent", "Lives in groups of 10-20"],
    })

    # ── When ─────────────────────────────────────────────────────────
    pasted_text = (
        "Capybaras are the largest living rodents in the world, native "
        "to South America. They are highly social animals that often "
        "live in groups of 10 to 20 individuals, grazing along "
        f"riverbanks and lake shores. [ref: {scope.uid}]"
    )
    assert len(pasted_text) >= _PASTE_FLOOR, \
        f"pasted_text {len(pasted_text)} < {_PASTE_FLOOR} won't trigger capture"

    bdd.when(f"Homer pastes {len(pasted_text)} chars into the notes room")
    send_resp = await homer.room_send(
        room_id=room_id,
        message_type="m.room.message",
        content={"msgtype": "m.text", "body": pasted_text},
    )
    bdd.detail(f"sent event_id = {getattr(send_resp, 'event_id', send_resp)}")

    # ── Then ─────────────────────────────────────────────────────────
    bdd.then(f"family/memory has a capture entry titled '{expected_title}'")
    path = await _wait_for_capture_by_title(code, expected_title)
    assert path, (
        f"No capture file titled {expected_title!r} appeared in "
        f"{MEMORY_OWNER}/{MEMORY_REPO} within 60s — check bot-runner logs"
    )
    bdd.ok(f"mirror path = {path}")

    bdd.and_("the path lives under the sender's notes bucket")
    # Homer pasted this — entity-rooted layout files it under his
    # bucket, not a global captures pool.
    assert path.startswith("homer/notes/"), \
        f"expected homer/notes/ prefix, got {path}"

    bdd.then("the frontmatter marks it type: note")
    fm, body = code.load_frontmatter(MEMORY_OWNER, MEMORY_REPO, path)
    assert fm.get("type") == "note", f"expected type=note, got {fm.get('type')!r}"
    assert fm.get("title") == expected_title
    bdd.ok(f"type={fm['type']} title={fm['title']}")

    bdd.and_("tags carry the classified tag and Person: Homer")
    tags = fm.get("tags") or []
    assert expected_tag in tags, \
        f"expected {expected_tag!r} in tags, got {tags}"
    assert "Person: Homer" in tags, \
        f"expected 'Person: Homer' in tags, got {tags}"
    bdd.ok(f"tags = {tags}")

    bdd.and_("no action items section is rendered")
    # Captures must never grow a `## Action items` block — a Reddit
    # paste is not a todo. Drop the prompt field and the section
    # together so the system doesn't manufacture chores.
    assert "## Action items" not in body, \
        f"captures must not render action items, body[:400]={body[:400]!r}"

    bdd.and_("the original paste is preserved in a collapsible callout")
    # `> [!quote]- Original paste` is the Obsidian-native collapsed
    # callout. The paste is line-prefixed with `> `; this is a single
    # line in the test, so the assertion is on the prefixed form.
    assert "> [!quote]- Original paste" in body, (
        f"missing callout header — first 400 chars: {body[:400]!r}"
    )
    assert f"> {pasted_text}" in body, (
        f"body missing prefixed paste — first 400 chars: {body[:400]!r}"
    )
    bdd.ok(f"body length = {len(body)} chars")
