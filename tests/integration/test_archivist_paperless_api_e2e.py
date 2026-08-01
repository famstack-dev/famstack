"""The archivist's Paperless client, driven against a real Paperless-ngx.

Every other test in this repo asks the archivist a question and checks
what it says. This one asks Paperless directly, through the exact client
class the bot uses (``stacklets/docs/bot/pipeline.PaperlessAPI``), and
pins the answers.

It exists because of the 2.x to 3.x migration. Paperless 3.0 reshaped
three things the archivist depends on, and every one of them is the kind
of change a hand-written fixture will happily agree with while the real
server disagrees:

* notes gained stricter request validation,
* duplicate rejection stopped being a task *failure* and became a
  successful task carrying a structured result,
* object permissions grew owner awareness, so "I uploaded it" and "I can
  see it" became two different questions.

So none of the payloads below are written by us. They are whatever the
running container returns. If upstream changes shape again, these tests
go red first and the unit fixtures in ``tests/stacklets/test_pipeline.py``
get corrected from what is asserted here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import aiohttp
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs" / "bot"))

from pipeline import (  # noqa: E402
    PaperlessAPI as BotPaperlessAPI,
    PaperlessDuplicateError,
    _task_document_id,
    _task_duplicate_id,
)


@pytest.fixture
async def bot_api(paperless):
    """The archivist's own Paperless client, pointed at the test rig.

    Deliberately the production class rather than the test client in
    ``tests/integration/paperless.py``: the question these tests answer
    is whether *the bot's* parsing survives 3.x, so the bot's parser has
    to be the thing under test.
    """
    async with aiohttp.ClientSession() as session:
        yield BotPaperlessAPI(session, paperless.url, paperless.token)


async def _file(api: BotPaperlessAPI, data: bytes, name: str) -> int:
    """Upload and wait, failing the test if the document never lands."""
    task_id = await api.upload(name, data, content_type="application/pdf")
    assert task_id, f"Paperless refused the upload of {name}"
    doc_id = await api.wait_task(task_id, timeout=180)
    assert doc_id, f"{name} produced no document id"
    return doc_id


# ── Notes ────────────────────────────────────────────────────────────────

class TestNotes:
    """The archivist writes its classification summary as a document note.

    3.0 tightened validation on ``/api/documents/<id>/notes/``, which is
    the endpoint that carries the single most user-visible artefact the
    bot produces. A rejected note is a silently empty summary.
    """

    async def test_a_note_posted_by_the_bot_can_be_read_back(
        self, bot_api, sample_invoice_pdf, paperless_scope, bdd,
    ):
        bdd.given("a document filed by the bot")
        doc_id = await _file(bot_api, sample_invoice_pdf,
                             f"{paperless_scope.uid}-notes.pdf")

        bdd.when("the bot posts a note")
        text = f"## Summary\n\nRef {paperless_scope.uid}"
        assert await bot_api.add_note(doc_id, text) is True

        bdd.then("the note comes back with its text intact")
        notes = await bot_api.list_notes(doc_id)
        assert [n["note"] for n in notes if n.get("note") == text], (
            f"note did not round-trip; Paperless returned {notes!r}"
        )

    async def test_the_bot_can_delete_a_note_it_wrote(
        self, bot_api, sample_invoice_pdf, paperless_scope, bdd,
    ):
        """The summary is rewritten on reprocess, which means deleting the
        prior one. 3.0 made the `?id=` query parameter validated rather
        than merely read, so a silently-ignored delete would leave the
        document accumulating a stale summary per run."""
        bdd.given("a document carrying a bot note")
        doc_id = await _file(bot_api, sample_invoice_pdf,
                             f"{paperless_scope.uid}-delete.pdf")
        assert await bot_api.add_note(doc_id, "first pass") is True
        notes = await bot_api.list_notes(doc_id)
        assert notes, "precondition failed: no note to delete"
        note_id = notes[0]["id"]

        bdd.when("the bot deletes it by id")
        assert await bot_api.delete_note(doc_id, note_id) is True

        bdd.then("it is gone")
        remaining = await bot_api.list_notes(doc_id)
        assert note_id not in [n.get("id") for n in remaining]


# ── Duplicates ───────────────────────────────────────────────────────────

class TestDuplicateRejection:
    """Re-filing the same document must say "already filed", not file it twice.

    This is the behaviour 3.0 changed most quietly. In 2.x the consumer
    always failed the task. In 3.0 it only rejects when
    ``PAPERLESS_CONSUMER_DELETE_DUPLICATES`` is set, and even then the
    task *succeeds* with ``result_data.duplicate_of``. Both halves have
    to hold or the family gets two copies of every letter they send
    twice.
    """

    async def test_reuploading_the_same_bytes_raises_a_duplicate_error(
        self, bot_api, sample_invoice_pdf, paperless_scope, bdd,
    ):
        bdd.given("a document already filed")
        first_id = await _file(bot_api, sample_invoice_pdf,
                               f"{paperless_scope.uid}-dup.pdf")

        bdd.when("the identical bytes are uploaded again")
        task_id = await bot_api.upload(
            f"{paperless_scope.uid}-dup-again.pdf", sample_invoice_pdf,
            content_type="application/pdf",
        )
        assert task_id

        bdd.then("the client raises, naming the document already on file")
        with pytest.raises(PaperlessDuplicateError) as exc:
            await bot_api.wait_task(task_id, timeout=180)
        assert exc.value.doc_id == first_id
        assert exc.value.title, (
            "duplicate carried no title; the chat reply would say '(no title)'"
        )

    async def test_the_duplicate_is_not_filed_a_second_time(
        self, bot_api, sample_invoice_pdf, paperless_scope, bdd,
    ):
        """Guards the guard. The rejection above is only meaningful if
        Paperless also declined to store the copy. Were
        ``PAPERLESS_CONSUMER_DELETE_DUPLICATES`` unset, 3.0 would consume
        the duplicate anyway - and a test that only checked for the
        raised error would still pass while the archive quietly grew a
        twin."""
        bdd.given("a document filed once")
        await _file(bot_api, sample_invoice_pdf,
                    f"{paperless_scope.uid}-twin.pdf")
        before = await bot_api.search(paperless_scope.uid, limit=100)

        bdd.when("the same bytes are uploaded again and rejected")
        task_id = await bot_api.upload(
            f"{paperless_scope.uid}-twin-again.pdf", sample_invoice_pdf,
            content_type="application/pdf",
        )
        with pytest.raises(PaperlessDuplicateError):
            await bot_api.wait_task(task_id, timeout=180)

        bdd.then("the archive still holds exactly one copy")
        after = await bot_api.search(paperless_scope.uid, limit=100)
        assert len(after) == len(before)


# ── Owner scoping ────────────────────────────────────────────────────────

class TestOwnerScoping:
    """What the bot uploads, the bot must still be able to find.

    3.0 tightened object permissions and API uploads now stamp the
    uploading user as ``owner``. If the bot's token ever stopped being
    able to list its own uploads, search and reprocess would both return
    nothing while filing kept appearing to work.
    """

    async def test_a_document_the_bot_uploaded_is_visible_to_the_bot(
        self, bot_api, sample_invoice_pdf, paperless_scope, bdd,
    ):
        bdd.given("a document uploaded with the bot's token")
        doc_id = await _file(bot_api, sample_invoice_pdf,
                             f"{paperless_scope.uid}-owned.pdf")

        bdd.when("the same token fetches it directly and by search")
        direct = await bot_api.get_doc(doc_id)
        found = await bot_api.search(paperless_scope.uid, limit=100)

        bdd.then("both see it")
        assert direct is not None, "owner-scoped read hid the bot's own upload"
        assert doc_id in [d["id"] for d in found], (
            "owner-scoped list hid the bot's own upload"
        )


# ── The contract the unit fixtures pin ───────────────────────────────────

class TestTaskApiShape:
    """Prove the unit fixtures describe this server, not our memory of it.

    ``tests/stacklets/test_pipeline.py`` parses task payloads offline, so
    it can only ever prove the parser agrees with the fixture. These two
    tests are the link back to reality: they hand the *real* payload to
    the *real* parser. If upstream reshapes the tasks API again, these
    fail and the offline fixtures are rewritten from what lands here.
    """

    async def _raw_task(self, bot_api, task_id: str) -> dict:
        body, status = await bot_api._req("GET", "/api/tasks/",
                                          params={"task_id": task_id})
        assert status == 200, f"tasks endpoint returned {status}"
        tasks = body.get("results", []) if isinstance(body, dict) else body
        assert tasks, f"no task recorded for {task_id}"
        return tasks[0]

    async def test_a_filed_document_reports_its_id_where_the_parser_looks(
        self, bot_api, sample_invoice_pdf, paperless_scope, bdd,
    ):
        bdd.given("a successfully filed document")
        task_id = await bot_api.upload(
            f"{paperless_scope.uid}-shape.pdf", sample_invoice_pdf,
            content_type="application/pdf",
        )
        doc_id = await bot_api.wait_task(task_id, timeout=180)
        assert doc_id

        bdd.when("the raw task payload is read back")
        task = await self._raw_task(bot_api, task_id)

        bdd.then("the parser finds the id, and finds no duplicate")
        assert _task_document_id(task) == doc_id
        assert _task_duplicate_id(task) is None, (
            f"a successful filing looked like a duplicate: {task!r}"
        )

    async def test_a_rejected_duplicate_reports_its_twin_where_the_parser_looks(
        self, bot_api, sample_invoice_pdf, paperless_scope, bdd,
    ):
        bdd.given("a document filed once")
        first_id = await _file(bot_api, sample_invoice_pdf,
                               f"{paperless_scope.uid}-shape-dup.pdf")

        bdd.when("the identical bytes are rejected")
        task_id = await bot_api.upload(
            f"{paperless_scope.uid}-shape-dup2.pdf", sample_invoice_pdf,
            content_type="application/pdf",
        )
        with pytest.raises(PaperlessDuplicateError):
            await bot_api.wait_task(task_id, timeout=180)
        task = await self._raw_task(bot_api, task_id)

        bdd.then("the parser reads the twin out of the real payload")
        assert _task_duplicate_id(task) == first_id, (
            f"duplicate id not where the parser looks: {task!r}"
        )
