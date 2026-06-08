"""CapturePipeline — URL/text capture → classify → mirror → outcome.

Like DocumentPipeline, it does the work (no Matrix, no i18n) and returns
a CaptureOutcome the orchestrator renders. Mid-flow "fetching" goes
through a Notifier. These pin the branching with fakes; the full path
is e2e-verified by test_capture_memory_e2e.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs" / "bot"))

from capture_pipeline import CapturePipeline  # noqa: E402


def _source(*, text="article body", source_uri=None, title_hint="A Title"):
    return SimpleNamespace(text=text, source_uri=source_uri, title_hint=title_hint)


class FakeExtractor:
    def __init__(self, result):
        self._result = result

    async def extract(self, _arg):
        return self._result


class FakeClassifier:
    def __init__(self, payload=None, raises=None):
        self._payload = payload if payload is not None else {
            "title": "Captured", "tags": ["AI"], "persons": ["Homer"], "summary": "s",
        }
        self._raises = raises

    async def classify_capture(self, *, text, person_names, existing_tags,
                               images=None, user_hint=None):
        if self._raises:
            raise self._raises
        return self._payload


class FakeMirror:
    def __init__(self):
        self.captures: list[dict] = []

    async def publish_capture(self, **kwargs):
        self.captures.append(kwargs)
        # Mirrors the new publish_capture contract: return the vault
        # path (or None on failure). Stubs out a deterministic path
        # so the envelope-emission branch in _publish executes.
        kind = kwargs.get("kind") or "bookmark"
        entity = "homer"
        return f"{entity}/{kind}s/test-capture.md"

    async def read_capture(self, path):
        # The simplest re-readable shape for reprocess tests.
        return self._stored.get(path)

    _stored: dict = {}


class FakeTags:
    def __init__(self):
        self.recorded: list[tuple] = []

    def top(self, n):
        return []

    def record(self, tags, when):
        self.recorded.append((tuple(tags), when))

    def save(self):
        pass


class FakePaperless:
    async def get_tags(self):
        return ["Person: Homer", "Insurance"]


class FakeNotifier:
    def __init__(self):
        self.statuses: list[tuple] = []

    async def status(self, key, **kwargs):
        self.statuses.append((key, kwargs))


def _pipeline(*, mirror, classifier=None, capture_keep_body=False):
    return CapturePipeline(
        url_extractor=FakeExtractor(_source(source_uri="http://src")),
        text_extractor=FakeExtractor(_source(source_uri="http://embedded")),
        classifier=classifier or FakeClassifier(),
        mirror=mirror,
        capture_tags=FakeTags(),
        paperless=FakePaperless(),
        bot_name="archivist-bot",
        classify_max_chars=10000,
        capture_keep_body=capture_keep_body,
        capture_tag_prompt_size=50,
    )


class TestCaptureUrl:

    @pytest.mark.asyncio
    async def test_announces_fetching_then_captures(self):
        mirror = FakeMirror()
        pipe = _pipeline(mirror=mirror)
        notifier = FakeNotifier()
        out = await pipe.capture_url(
            url="http://example.com", sender_mxid="@homer:s", notifier=notifier,
        )
        assert out.status == "captured"
        assert ("capture_fetching", {"url": "http://example.com"}) in notifier.statuses
        assert len(mirror.captures) == 1
        assert mirror.captures[0]["kind"] == "bookmark"
        assert out.display_link == "http://example.com"

    @pytest.mark.asyncio
    async def test_extract_failure(self):
        pipe = CapturePipeline(
            url_extractor=FakeExtractor(None),  # extraction fails
            text_extractor=FakeExtractor(None),
            classifier=FakeClassifier(),
            mirror=FakeMirror(),
            capture_tags=FakeTags(),
            paperless=FakePaperless(),
            bot_name="b", classify_max_chars=100,
            capture_keep_body=False, capture_tag_prompt_size=50,
        )
        notifier = FakeNotifier()
        out = await pipe.capture_url(url="http://x", sender_mxid="@homer:s", notifier=notifier)
        assert out.status == "extract_failed"
        # Fetching was still announced before the failed extract.
        assert notifier.statuses[0][0] == "capture_fetching"

    @pytest.mark.asyncio
    async def test_no_mirror(self):
        pipe = _pipeline(mirror=None)
        out = await pipe.capture_url(url="http://x", sender_mxid="@homer:s", notifier=FakeNotifier())
        assert out.status == "no_mirror"


class TestCaptureText:

    @pytest.mark.asyncio
    async def test_note_capture_uses_embedded_uri_as_link(self):
        mirror = FakeMirror()
        pipe = _pipeline(mirror=mirror)
        out = await pipe.capture_text(text="a long pasted note", sender_mxid="@marge:s")
        assert out.status == "captured"
        assert mirror.captures[0]["kind"] == "note"
        assert out.display_link == "http://embedded"

    @pytest.mark.asyncio
    async def test_empty_text_is_dropped(self):
        pipe = CapturePipeline(
            url_extractor=FakeExtractor(None),
            text_extractor=FakeExtractor(None),  # nothing extracted
            classifier=FakeClassifier(),
            mirror=FakeMirror(),
            capture_tags=FakeTags(),
            paperless=FakePaperless(),
            bot_name="b", classify_max_chars=100,
            capture_keep_body=False, capture_tag_prompt_size=50,
        )
        out = await pipe.capture_text(text="   ", sender_mxid="@marge:s")
        assert out.status == "empty"


class TestTagList:
    """`_tag_list` mixes the classifier's free-form tags with a `Person: X`
    tag per attributed person, normalising types and whitespace."""

    def test_tags_and_persons(self):
        c = {"tags": ["AI", "Productivity"], "persons": ["Arthur"]}
        assert CapturePipeline._tag_list(c) == ["AI", "Productivity", "Person: Arthur"]

    def test_single_string_tag_becomes_list(self):
        c = {"tags": "AI", "persons": ["Arthur"]}
        assert CapturePipeline._tag_list(c) == ["AI", "Person: Arthur"]

    def test_empty_classification_yields_empty(self):
        assert CapturePipeline._tag_list({}) == []

    def test_skips_non_string_values(self):
        c = {"tags": ["AI", None, 42, ""], "persons": ["Arthur", ""]}
        assert CapturePipeline._tag_list(c) == ["AI", "Person: Arthur"]

    def test_strips_whitespace(self):
        c = {"tags": ["  AI  ", "Productivity"], "persons": ["Arthur"]}
        assert CapturePipeline._tag_list(c) == ["AI", "Productivity", "Person: Arthur"]


class TestClassifyDegradation:

    @pytest.mark.asyncio
    async def test_classifier_failure_falls_back_to_sender(self):
        from pipeline import LLMUnavailableError
        pipe = _pipeline(
            mirror=FakeMirror(),
            classifier=FakeClassifier(raises=LLMUnavailableError("down")),
        )
        out = await pipe.capture_url(url="http://x", sender_mxid="@bart:s", notifier=FakeNotifier())
        assert out.status == "captured"
        # Degraded classification: sender as the only person, hint title.
        assert out.classification["persons"] == ["Bart"]
        assert out.classification["title"] == "A Title"  # source title_hint


# ── Binary capture (PDFs and images) ───────────────────────────────────


class TestSourceFromBinary:
    """Internal: how _source_from_binary picks text vs vision for each
    incoming binary shape. The router is small but load-bearing for the
    PDF-in-notes-room story, so each branch gets its own assertion."""

    def _pipe(self, *, vision_max_pdf_pages=5):
        # Pipe with a vision cap we can override per test; the
        # classifier is irrelevant for this layer.
        p = _pipeline(mirror=FakeMirror())
        p.vision_max_pdf_pages = vision_max_pdf_pages
        return p

    def test_image_returns_single_image_no_text(self):
        # Plain image -> one ImageAttachment, no extracted text.
        pipe = self._pipe()
        source, images = pipe._source_from_binary(
            file_data=b"\xff\xd8fake-jpeg-bytes",
            mime="image/jpeg",
            filename="recipe.jpg",
            source_uri="mxc://server/abc",
        )
        assert source is not None
        assert source.source_uri == "mxc://server/abc"
        assert source.title_hint == "recipe.jpg"
        assert source.text == ""
        assert len(images) == 1
        assert images[0].mime == "image/jpeg"

    def test_unsupported_mime_returns_none(self):
        # Audio, video, archives: out of scope for v1 captures.
        pipe = self._pipe()
        source, images = pipe._source_from_binary(
            file_data=b"riff-wave",
            mime="audio/wav",
            filename="voice.wav",
            source_uri="mxc://server/xyz",
        )
        assert source is None
        assert images == []


