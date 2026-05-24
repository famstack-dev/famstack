"""DocumentPipeline branching — the upload→file→enrich→mirror flow.

The pipeline does the work (no Matrix, no i18n) and returns a
FilingOutcome the orchestrator renders. These pin the control flow —
which outcome each Paperless result produces, and that the mirror is
always reached for a filed doc — with in-memory fakes of our own
collaborators. The full happy "filed" path (real classify + envelope)
is covered by the archivist e2e against real Paperless/OpenAI.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs" / "bot"))

from document_pipeline import DocumentPipeline  # noqa: E402
from pipeline import PaperlessDuplicateError  # noqa: E402


class FakePaperless:
    """In-memory stand-in for PaperlessAPI's pipeline-facing surface."""

    def __init__(self, *, task_id="task-1", doc_id=1, doc=None, duplicate=None):
        self._task_id = task_id
        self._doc_id = doc_id
        self._doc = doc
        self._duplicate = duplicate
        self.uploads: list[tuple] = []

    async def upload(self, filename, data, content_type=None):
        self.uploads.append((filename, content_type))
        return self._task_id

    async def wait_task(self, task_id):
        if self._duplicate is not None:
            raise self._duplicate
        return self._doc_id

    async def get_doc(self, doc_id):
        return self._doc


class FakeMirror:
    def __init__(self):
        self.published: list[dict] = []

    async def publish(self, **kwargs):
        self.published.append(kwargs)


class FakeClassifier:
    async def has_vision(self):
        return False


class _FakeVault:
    def ontology(self):
        return None
    def ontology_section(self, language=None):
        return ""
    def correspondents_section(self):
        return ""


def _pipeline(paperless, *, mirror=None, classify_enabled=True, reformat_enabled=True):
    return DocumentPipeline(
        paperless=paperless,
        classifier=FakeClassifier(),
        mirror=mirror,
        bot_name="archivist-bot",
        language="en",
        classify_enabled=classify_enabled,
        reformat_enabled=reformat_enabled,
        classify_max_chars=10000,
        paperless_public_url="http://paperless",
        actor="@archivist-bot:test.local",
        vault=_FakeVault(),
    )


async def _process(pipeline, *, filename="note.txt", data=b"hello world this is text"):
    return await pipeline.process(
        filename=filename, display_name=filename, file_data=data,
    )


class TestEarlyExits:

    @pytest.mark.asyncio
    async def test_upload_failure(self):
        out = await _process(_pipeline(FakePaperless(task_id=None)))
        assert out.status == "upload_failed"
        assert out.doc_id is None

    @pytest.mark.asyncio
    async def test_duplicate(self):
        dup = PaperlessDuplicateError(doc_id=42, title="Existing")
        out = await _process(_pipeline(FakePaperless(duplicate=dup)))
        assert out.status == "duplicate"
        assert out.duplicate is dup

    @pytest.mark.asyncio
    async def test_ocr_failed_when_no_doc_id(self):
        out = await _process(_pipeline(FakePaperless(doc_id=None)))
        assert out.status == "ocr_failed"

    @pytest.mark.asyncio
    async def test_filed_no_details_when_doc_unreadable(self):
        # Upload accepted, doc_id assigned, but get_doc comes back empty.
        mirror = FakeMirror()
        out = await _process(_pipeline(FakePaperless(doc_id=7, doc=None), mirror=mirror))
        assert out.status == "filed_no_details"
        assert out.doc_id == 7
        assert out.link == "http://paperless/documents/7/details"
        # Mirror still reached so Paperless ⇄ mirror stay 1:1.
        assert len(mirror.published) == 1
        assert mirror.published[0]["fallback_title"] == "note.txt"


class TestReprocess:

    @pytest.mark.asyncio
    async def test_doc_missing(self):
        # The doc the reply targets was deleted — reprocess can't enrich it.
        pipe = _pipeline(FakePaperless(doc=None))
        out = await pipe.reprocess(doc_id=99, user_hint="it's from Globex")
        assert out.status == "doc_missing"
        assert out.doc_id == 99


class TestEnrichedOutcomes:

    @pytest.mark.asyncio
    async def test_no_text_skips_classification(self):
        # Doc filed but OCR text is too short to classify.
        doc = {"id": 5, "content": "x"}
        mirror = FakeMirror()
        out = await _process(
            _pipeline(FakePaperless(doc_id=5, doc=doc), mirror=mirror),
            data=b"x",
        )
        assert out.status == "enriched"
        assert out.has_text is False
        assert out.classification == {}
        assert out.envelope is None
        assert len(mirror.published) == 1  # mirrored regardless

    @pytest.mark.asyncio
    async def test_classify_disabled_files_without_llm(self):
        doc = {"id": 6, "content": "a fully readable document body here"}
        out = await _process(
            _pipeline(FakePaperless(doc_id=6, doc=doc), classify_enabled=False),
        )
        assert out.status == "enriched"
        assert out.classify_enabled is False
        assert out.has_text is True
        assert out.classification == {}  # no classify ran
