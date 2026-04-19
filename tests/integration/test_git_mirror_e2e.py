"""End-to-end git mirror test — production topology.

Real Paperless, real Synapse, real bot-runner, real Forgejo, mocked
OpenAI. Homer uploads a document; the archivist classifies it, emits
its Matrix event, and mirrors the result into the `family/documents`
Forgejo repo (org name is configurable via `bot.toml`) with YAML
frontmatter and a commit trailer.

Reprocessing the same Paperless document produces an `update:` commit
at the same filepath — idempotency via the `-p<id>` filename suffix.

Run with `-s` to stream the BDD narration live:

    uv run --extra test pytest -s tests/integration/test_git_mirror_e2e.py
"""

from __future__ import annotations

import asyncio

import pytest

from tests.integration.forgejo import ForgejoError
from tests.integration.matrix import (
    ensure_joined,
    upload_and_send_file,
    wait_for_room,
)
from tests.integration.openai_stub import stub_classify, stub_reformat


DOCS_ROOM_ALIAS = "#documents:test.local"
# Repo owner = the Forgejo org `mirror_org` in the archivist's bot.toml.
# Default is "family"; stays in sync with `FORGEJO_DOCS_OWNER` in conftest.
DOCS_OWNER = "family"
DOCS_REPO = "documents"


async def _wait_for_paperless_doc(paperless, title: str, timeout: int = 120) -> dict | None:
    for _ in range(timeout):
        docs = paperless.list_documents()
        match = next((d for d in docs if d.get("title") == title), None)
        if match:
            return match
        await asyncio.sleep(1)
    return None


async def _wait_for_mirror_file(code, paperless_id: int, timeout: int = 60) -> str | None:
    for _ in range(timeout):
        try:
            path = code.find_by_paperless_id(DOCS_OWNER, DOCS_REPO, paperless_id)
            if path:
                return path
        except ForgejoError:
            pass
        await asyncio.sleep(1)
    return None


async def test_archivist_mirrors_classified_document_to_forgejo(
    bdd,
    openai,
    paperless,
    paperless_scope,
    mirror_scope,
    code,
    homer,
    sample_invoice_pdf,
):
    """Archivist files a document and mirrors it as markdown in Forgejo.

    Scenario
    --------
    Given  the code stacklet is running and the archivist is configured
    When   Homer uploads an ADAC invoice to #documents
    Then   Paperless has the classified document
    And    Forgejo has a mirror file at YYYY/MM/<slug>-p<id>.md
    And    the file's frontmatter carries the full classification
    And    the commit message contains a Paperless-Id trailer
    And    Homer and Marge are collaborators on the documents repo
    """
    scope = mirror_scope
    bdd.scenario("Archivist mirrors a classified document to Forgejo")

    # ── Given ────────────────────────────────────────────────────────
    expected_title = scope.tag("ADAC - Kfz-Versicherung 2026")
    expected_topic = scope.tag("Insurance")
    expected_correspondent = scope.tag("ADAC")
    expected_date = "2026-03-15"

    bdd.given("the code (Forgejo) stacklet is reachable")
    assert code.ping(), "Forgejo API unreachable at http://localhost:42040"
    bdd.ok(f"Forgejo /api/v1/version returned OK")

    bdd.given("the #documents room exists and Homer has access")
    room_id = await wait_for_room(homer, DOCS_ROOM_ALIAS, timeout=90)
    await ensure_joined(homer, room_id)
    bdd.detail(f"room_id = {room_id}")

    bdd.given("the OpenAI mock is stubbed for classify + reformat")
    stub_classify(openai, {
        "title": expected_title,
        "topics": [expected_topic],
        "persons": ["Homer"],
        "correspondent": expected_correspondent,
        "document_type": "Invoice",
        "date": expected_date,
        "summary": "Annual car insurance renewal at ADAC. EUR 340/year.",
        "facts": ["EUR 340.00/year", "Contract KFZ-2026-000123"],
        "action_items": [{"action": "Pay by 2026-03-15", "due": expected_date}],
    })
    reformatted = f"# {expected_title}\n\nADAC — EUR 340/year. Contract KFZ-2026-000123."
    stub_reformat(openai, reformatted)

    # ── When ─────────────────────────────────────────────────────────
    bdd.when(f"Homer uploads invoice.pdf to {DOCS_ROOM_ALIAS}")
    await upload_and_send_file(
        homer, room_id, sample_invoice_pdf, filename="invoice.pdf",
        mime_type="application/pdf", msgtype="m.file",
    )

    # ── Then: Paperless has the filed document ──────────────────────
    bdd.then(f"Paperless has a document titled '{expected_title}'")
    doc = await _wait_for_paperless_doc(paperless, expected_title)
    assert doc, f"No Paperless doc titled {expected_title!r} within 120s."
    paperless_id = doc["id"]
    bdd.ok(f"Paperless doc #{paperless_id}: {doc['title']}")

    # ── Then: Forgejo has the mirror file ────────────────────────────
    bdd.then(f"Forgejo has a mirror file for paperless #{paperless_id}")
    path = await _wait_for_mirror_file(code, paperless_id)
    assert path, f"No mirror file for paperless #{paperless_id} within 60s."
    bdd.ok(f"mirror path = {path}")

    bdd.and_(f"path follows YYYY/MM/YYYY-MM-DD-<slug>-p{paperless_id}.md")
    y, m, _ = expected_date.split("-")
    assert path.startswith(f"{y}/{m}/{expected_date}-"), \
        f"unexpected path prefix: {path}"
    assert path.endswith(f"-p{paperless_id}.md"), \
        f"unexpected path suffix: {path}"

    # ── Then: frontmatter carries the classification ────────────────
    fm, body = code.load_frontmatter(DOCS_OWNER, DOCS_REPO, path)
    bdd.then("the frontmatter carries the full classification")
    assert fm.get("title") == expected_title
    assert fm.get("paperless_id") == paperless_id
    assert fm.get("date") == expected_date
    assert fm.get("correspondent") == expected_correspondent
    assert fm.get("document_type") == "Invoice"
    assert fm.get("persons") == ["Homer"]
    assert fm.get("processing") == "ai_formatted"
    assert fm.get("source") == "paperless"
    # Resolved topic (scope-tagged) should appear in tags
    assert any(expected_topic == t for t in (fm.get("tags") or [])), \
        f"expected topic in tags, got {fm.get('tags')}"
    bdd.ok(f"processing={fm['processing']} correspondent={fm['correspondent']}")

    bdd.and_("the body contains the AI-reformatted content")
    assert "ADAC — EUR 340/year" in body, f"body missing reformatted text: {body[:200]!r}"
    bdd.ok(f"body length = {len(body)} chars")

    # ── Then: commit carries the Paperless-Id trailer ───────────────
    bdd.then(f"the latest commit message carries Paperless-Id: {paperless_id}")
    commits = code.list_commits(DOCS_OWNER, DOCS_REPO, path=path, limit=5)
    assert commits, f"no commits found for {path}"
    latest_msg = commits[0]["commit"]["message"]
    assert f"Paperless-Id: {paperless_id}" in latest_msg, \
        f"trailer missing in commit message: {latest_msg!r}"
    assert "Processing: ai" in latest_msg
    assert latest_msg.startswith("learn:"), \
        f"first commit should be 'learn:', got: {latest_msg.splitlines()[0]!r}"
    bdd.ok(f"commit: {latest_msg.splitlines()[0]}")

    # ── Then: admin users + bot are in the org's Owners team ───────
    bdd.then(f"archivist-bot, Homer, and Marge are members of the `{DOCS_OWNER}` org")
    members = set(code.list_org_members(DOCS_OWNER))
    assert "archivist-bot" in members, f"archivist-bot not in {DOCS_OWNER} org: {members}"
    assert "homer" in members, f"homer not in {DOCS_OWNER} org: {members}"
    assert "marge" in members, f"marge not in {DOCS_OWNER} org: {members}"
    bdd.ok(f"org members = {sorted(members)}")
