"""End-to-end test for markdown documents.

Markdown files take the same pipeline as PDFs — upload → Paperless →
classify (optional) → mirror — with two differences:

- Paperless only has a parser for `text/plain`, so the archivist
  renames `.md` to `.txt` at upload time. Paperless stores the doc
  with a `.txt` internal name.
- Reformat is skipped (the content is already clean markdown).

The mirror keeps the original markdown bytes as its body and uses the
original filename as its fallback title, so `family/documents` ends
up with a `.md` entry that round-trips byte-for-byte.

Run with `-s` to stream the BDD narration live:

    uv run --extra test pytest -s tests/integration/test_markdown_mirror_e2e.py
"""

from __future__ import annotations

import asyncio

from tests.integration.matrix import (
    ensure_joined,
    upload_and_send_file,
    wait_for_room,
)
from tests.integration.openai_stub import stub_classify


DOCS_ROOM_ALIAS = "#documents:test.local"
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
        path = code.find_by_paperless_id(DOCS_OWNER, DOCS_REPO, paperless_id)
        if path:
            return path
        await asyncio.sleep(1)
    return None


async def test_archivist_files_and_mirrors_a_markdown_document(
    bdd,
    openai,
    paperless,
    paperless_scope,
    mirror_scope,
    code,
    homer,
):
    """Homer drops a .md file; archivist files + mirrors it.

    Scenario
    --------
    Given  the code + docs stacklets are running and AI is stubbed
    When   Homer uploads `robot-protocol.md` to #documents
    Then   Paperless has the doc filed (internally as .txt)
    And    `family/documents` has a mirror entry ending in `-p<id>.md`
    And    the mirror body is the original markdown content
    And    `processing: original` in frontmatter (body is the source bytes)
    And    no `model` key in frontmatter (reformat didn't run)
    """
    scope = mirror_scope
    bdd.scenario("Archivist files + mirrors a markdown document")

    expected_title = scope.tag("Robot Protocol Notes")
    expected_topic = scope.tag("Projects")
    markdown_content = (
        f"# Robot Protocol Notes — {scope.uid}\n"
        "\n"
        "## Actuators\n"
        "\n"
        "- Servo A: 0–180°, 5V, pulse width 500–2500μs\n"
        "- Servo B: 360° continuous, 6V\n"
        "\n"
        "## Firmware notes\n"
        "\n"
        "```python\n"
        "def step(motor, angle):\n"
        "    motor.write(angle)\n"
        "```\n"
        "\n"
        "Ref: internal doc unique to this test scope.\n"
    )
    markdown_bytes = markdown_content.encode("utf-8")

    bdd.given("the #documents room exists and Homer has access")
    room_id = await wait_for_room(homer, DOCS_ROOM_ALIAS, timeout=90)
    await ensure_joined(homer, room_id)

    bdd.given("the OpenAI mock is stubbed for classify (reformat is skipped)")
    stub_classify(openai, {
        "title": expected_title,
        "topics": [expected_topic],
        "persons": ["Homer"],
        "correspondent": None,
        "document_type": "Notes",
        "date": None,
        "summary": "Hardware + firmware notes for a servo-based robot project.",
        "facts": [],
        "action_items": [],
    })
    # Intentionally NO stub_reformat — text-like files must skip reformat.
    # pytest-httpserver will fail if reformat hits unexpectedly.

    bdd.when(f"Homer uploads robot-protocol.md ({len(markdown_bytes)} bytes)")
    await upload_and_send_file(
        homer, room_id, markdown_bytes,
        filename="robot-protocol.md",
        mime_type="text/markdown",
        msgtype="m.file",
    )

    bdd.then(f"Paperless has a document titled '{expected_title}'")
    doc = await _wait_for_paperless_doc(paperless, expected_title)
    assert doc, f"No Paperless doc titled {expected_title!r} within 120s."
    paperless_id = doc["id"]
    bdd.ok(f"Paperless doc #{paperless_id}")

    bdd.and_("Paperless stored the markdown as its content")
    stored = doc.get("content", "") or ""
    assert "Robot Protocol Notes" in stored, \
        f"Paperless content missing the markdown header: {stored[:200]!r}"

    bdd.then(f"Forgejo has a mirror file for paperless #{paperless_id}")
    path = await _wait_for_mirror_file(code, paperless_id)
    assert path, f"No mirror file for paperless #{paperless_id} within 60s."
    assert path.endswith(f"-p{paperless_id}.md"), f"unexpected path: {path}"
    bdd.ok(f"mirror path = {path}")

    bdd.and_("the mirror body is the ORIGINAL markdown bytes")
    fm, body = code.load_frontmatter(DOCS_OWNER, DOCS_REPO, path)
    # Body begins with the rendered H1 + optional wiki-link header, then the
    # original content. Assert the unique scope-tagged content survives.
    assert scope.uid in body, f"scope marker missing from body: {body[:300]!r}"
    assert "Servo A: 0–180°" in body, f"markdown content missing: {body[:300]!r}"
    assert "```python" in body, f"code fence missing: {body[:300]!r}"

    bdd.and_("frontmatter says processing=original and no model is recorded")
    assert fm.get("processing") == "original", \
        f"expected processing=original, got {fm.get('processing')}"
    assert "model" not in fm, f"model key should be absent, got fm={fm}"
    assert fm.get("paperless_id") == paperless_id
    bdd.ok(f"fm: processing={fm['processing']}, paperless_id={fm['paperless_id']}")
