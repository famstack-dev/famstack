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
                               images=None, user_hint=None,
                               initial_classification=None):
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


def _pipeline(*, mirror, classifier=None, capture_keep_body=False,
              transcriber=None, llm=None):
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
        transcriber=transcriber,
        llm=llm,
    )


class FakeTranscriber:
    """Stand-in for stack.ai.client.Transcriber — records calls and
    returns a configured transcript or raises a configured error.

    Mirrors the real signature including ``cleanup_with`` so the capture
    pipeline can pass an LLM through and we can assert the routing.
    """

    def __init__(self, transcript: str = "I forgot to renew the boiler service",
                 error: Exception | None = None):
        self.transcript = transcript
        self.error = error
        self.calls: list[dict] = []

    async def transcribe(self, audio: bytes, *, filename: str = "voice.ogg",
                         model: str | None = None,
                         cleanup_with=None) -> str:
        self.calls.append({
            "audio": audio, "filename": filename,
            "cleanup_with": cleanup_with,
        })
        if self.error is not None:
            raise self.error
        return self.transcript


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


class TestCaptureVoiceBatch:
    """The ( ... ) batch flow: N voice memos arrive, each gets transcribed
    on its way in (with LLM cleanup if available), then `)` combines them
    into one note via capture_voice_batch."""

    @pytest.mark.asyncio
    async def test_concatenates_transcripts_with_paragraph_breaks(self):
        mirror = FakeMirror()
        pipe = _pipeline(mirror=mirror)
        out = await pipe.capture_voice_batch(
            transcripts=[
                "First memo about the boiler.",
                "Second memo about Bart's prescription.",
                "Third memo with shopping list.",
            ],
            primary_mxc="mxc://server/first",
            sender_mxid="@homer:s",
        )
        assert out.status == "captured"
        body = mirror.captures[0]["body_text"]
        assert "First memo about the boiler." in body
        assert "Second memo about Bart's prescription." in body
        assert "Third memo with shopping list." in body
        # Paragraph breaks between memos so the LLM and the human reader
        # both see them as distinct thoughts.
        assert "boiler.\n\nSecond" in body

    @pytest.mark.asyncio
    async def test_empty_transcripts_returns_empty_status(self):
        """A `(` ... `)` with no voice memos (or all silent ones) drops
        out as empty -- the orchestrator silently no-ops, matching the
        existing scan_cancelled semantics for empty PDF batches."""
        pipe = _pipeline(mirror=FakeMirror())
        out = await pipe.capture_voice_batch(
            transcripts=[], primary_mxc=None, sender_mxid="@homer:s",
        )
        assert out.status == "empty"

    @pytest.mark.asyncio
    async def test_blank_transcripts_filtered(self):
        """Empty / whitespace-only transcripts are dropped silently so
        one silent memo in the middle of a batch doesn't add a hole
        of blank lines to the combined body."""
        mirror = FakeMirror()
        pipe = _pipeline(mirror=mirror)
        out = await pipe.capture_voice_batch(
            transcripts=["real one", "   ", "", "another real one"],
            primary_mxc="mxc://server/first",
            sender_mxid="@homer:s",
        )
        assert out.status == "captured"
        body = mirror.captures[0]["body_text"]
        assert body == "real one\n\nanother real one"

    @pytest.mark.asyncio
    async def test_primary_mxc_threads_through_as_source_uri(self):
        """The first memo's mxc becomes the vault note's link -- the
        wiki entry points back to the start of the conversation."""
        mirror = FakeMirror()
        pipe = _pipeline(mirror=mirror)
        out = await pipe.capture_voice_batch(
            transcripts=["just one"],
            primary_mxc="mxc://server/start-of-batch",
            sender_mxid="@homer:s",
        )
        assert mirror.captures[0]["source_uri"] == "mxc://server/start-of-batch"
        assert out.display_link == "mxc://server/start-of-batch"

    @pytest.mark.asyncio
    async def test_outcome_carries_transcript_for_reply_echo(self):
        """The mime=audio/ogg on the synthetic SourceContent triggers
        the same transcript-in-reply behaviour single-memo captures
        get -- so the sender sees the combined text quoted back."""
        pipe = _pipeline(mirror=FakeMirror())
        out = await pipe.capture_voice_batch(
            transcripts=["one", "two"],
            primary_mxc="mxc://server/x",
            sender_mxid="@homer:s",
        )
        assert out.transcript == "one\n\ntwo"


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
        # Plain image -> one ImageAttachment, no extracted text, bookmark kind.
        pipe = self._pipe()
        source, images, kind = pipe._source_from_binary(
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
        assert kind == "bookmark"

    def test_unsupported_mime_returns_none(self):
        # _source_from_binary itself doesn't handle audio; capture_binary
        # routes audio through _source_from_audio upstream. Video and
        # archives still land here and fall through to None.
        pipe = self._pipe()
        source, images, _kind = pipe._source_from_binary(
            file_data=b"riff-wave",
            mime="video/mp4",
            filename="clip.mp4",
            source_uri="mxc://server/xyz",
        )
        assert source is None
        assert images == []

    def test_markdown_file_decodes_as_note(self):
        # An .md file: bytes are the artifact, kept as a note (no
        # vision call, body preserved). The text comes out decoded.
        pipe = self._pipe()
        body = "# Title\n\nSome notes about MLX.\n"
        source, images, kind = pipe._source_from_binary(
            file_data=body.encode("utf-8"),
            mime="text/markdown",
            filename="notes.md",
            source_uri="mxc://server/md1",
        )
        assert source is not None
        assert source.text == body
        assert source.mime == "text/markdown"
        assert images == []
        assert kind == "note"

    def test_txt_file_by_extension_when_mime_unknown(self):
        # Some clients send application/octet-stream for plain .txt
        # files; the extension still routes them to the note path.
        pipe = self._pipe()
        body = "Just a plain text note.\n"
        source, images, kind = pipe._source_from_binary(
            file_data=body.encode("utf-8"),
            mime="application/octet-stream",
            filename="thought.txt",
            source_uri="mxc://server/t1",
        )
        assert source is not None
        assert source.text == body
        assert kind == "note"
        assert images == []


# ── Audio capture (voice memos) ────────────────────────────────────────


class TestSourceFromAudio:
    """The audio branch: transcribe via the injected Transcriber, return
    a note-shaped SourceContent. When no transcriber is wired, soft-skip
    with None so the orchestrator surfaces a friendly extract_failed."""

    @pytest.mark.asyncio
    async def test_transcribes_audio_into_note(self):
        tr = FakeTranscriber(transcript="Reminder: book the boiler service")
        pipe = _pipeline(mirror=FakeMirror(), transcriber=tr)
        source, images, kind = await pipe._source_from_audio(
            file_data=b"opus-bytes", mime="audio/ogg",
            filename="voice-2026-06-09.ogg",
            source_uri="mxc://server/abc",
        )
        assert len(tr.calls) == 1
        assert tr.calls[0]["audio"] == b"opus-bytes"
        assert tr.calls[0]["filename"] == "voice-2026-06-09.ogg"
        assert source is not None
        assert source.text == "Reminder: book the boiler service"
        assert source.mime == "audio/ogg"
        assert source.title_hint == "voice-2026-06-09.ogg"
        assert source.source_uri == "mxc://server/abc"
        assert images == []
        assert kind == "note"

    @pytest.mark.asyncio
    async def test_no_transcriber_soft_skips(self):
        # WHISPER_URL unset at bootstrap -> the pipeline drops audio
        # rather than raising. Bot replies with extract_failed.
        pipe = _pipeline(mirror=FakeMirror(), transcriber=None)
        source, images, _kind = await pipe._source_from_audio(
            file_data=b"opus-bytes", mime="audio/ogg",
            filename="voice.ogg", source_uri="mxc://server/abc",
        )
        assert source is None
        assert images == []

    @pytest.mark.asyncio
    async def test_transcriber_error_soft_skips(self):
        # Whisper is misconfigured or down -> log + drop, same shape as
        # the no-transcriber case so the user-facing reply is uniform.
        from stack.ai.client import LLMUnavailableError
        tr = FakeTranscriber(error=LLMUnavailableError("whisper down"))
        pipe = _pipeline(mirror=FakeMirror(), transcriber=tr)
        source, images, _kind = await pipe._source_from_audio(
            file_data=b"opus-bytes", mime="audio/ogg",
            filename="voice.ogg", source_uri="mxc://server/abc",
        )
        assert source is None
        assert images == []

    @pytest.mark.asyncio
    async def test_empty_transcript_soft_skips(self):
        # whisper.cpp sometimes returns "" for a silent clip; treat it
        # like an unreadable scan -- no point classifying empty bytes.
        tr = FakeTranscriber(transcript="   \n  ")
        pipe = _pipeline(mirror=FakeMirror(), transcriber=tr)
        source, images, _kind = await pipe._source_from_audio(
            file_data=b"silence", mime="audio/ogg",
            filename="voice.ogg", source_uri="mxc://server/abc",
        )
        assert source is None
        assert images == []


class TestCaptureBinaryAudioRouting:
    """capture_binary peeks at the mime: audio/* -> _source_from_audio,
    everything else -> _source_from_binary. The classify + mirror tail
    runs identically afterwards, so a transcribed voice memo lands in
    the mirror as a fully classified note."""

    @pytest.mark.asyncio
    async def test_audio_mime_routes_through_transcriber(self):
        mirror = FakeMirror()
        tr = FakeTranscriber(transcript="Pick up Bart's prescription Friday")
        pipe = _pipeline(mirror=mirror, transcriber=tr)
        out = await pipe.capture_binary(
            file_data=b"opus-data", mime="audio/ogg",
            filename="voice-2026-06-09.ogg",
            source_uri="mxc://server/abc",
            sender_mxid="@homer:s",
        )
        # The transcript reached the mirror via the classify tail.
        assert len(tr.calls) == 1
        assert tr.calls[0]["audio"] == b"opus-data"
        assert tr.calls[0]["filename"] == "voice-2026-06-09.ogg"
        assert out.status == "captured"
        assert mirror.captures, "expected the transcript to be mirrored as a note"
        published = mirror.captures[0]
        # The body the mirror writes IS the transcript -- voice memo as a
        # searchable note in the sender's bucket.
        assert "Pick up Bart's prescription Friday" in published["body_text"]
        assert published["kind"] == "note"
        # The outcome carries the transcript so the reply renderer can
        # echo it back to the sender; PDF/URL/note captures get None.
        assert out.transcript == "Pick up Bart's prescription Friday"

    @pytest.mark.asyncio
    async def test_non_audio_capture_outcome_has_no_transcript(self):
        """A markdown note capture must not surface a transcript field --
        only audio captures get one, so the reply layer can branch on
        presence rather than mime-sniffing again."""
        mirror = FakeMirror()
        pipe = _pipeline(mirror=mirror, transcriber=FakeTranscriber())
        out = await pipe.capture_binary(
            file_data=b"# Hello\n\nbody",
            mime="text/markdown", filename="note.md",
            source_uri="mxc://server/md1", sender_mxid="@homer:s",
        )
        assert out.status == "captured"
        assert out.transcript is None

    @pytest.mark.asyncio
    async def test_audio_without_transcriber_returns_extract_failed(self):
        # Mirrors a bot booted before `stack up ai` -- the rest of the
        # bot is alive, but voice messages can't be processed yet.
        pipe = _pipeline(mirror=FakeMirror(), transcriber=None)
        out = await pipe.capture_binary(
            file_data=b"opus-data", mime="audio/ogg",
            filename="voice.ogg", source_uri="mxc://server/abc",
            sender_mxid="@homer:s",
        )
        assert out.status == "extract_failed"

    @pytest.mark.asyncio
    async def test_non_audio_mime_bypasses_transcriber(self):
        # An image upload must not get sent to whisper -- the transcriber
        # is for audio only, so non-audio mimes never touch it.
        tr = FakeTranscriber()
        pipe = _pipeline(mirror=FakeMirror(), transcriber=tr)
        await pipe.capture_binary(
            file_data=b"\xff\xd8jpg", mime="image/jpeg",
            filename="recipe.jpg", source_uri="mxc://server/abc",
            sender_mxid="@homer:s",
        )
        assert tr.calls == []

    @pytest.mark.asyncio
    async def test_llm_passed_to_transcriber_as_cleanup_with(self):
        """The CapturePipeline.llm kwarg threads through to the Transcriber's
        cleanup_with on every audio capture. This is the wiring contract
        that powers transcript cleanup -- the Transcriber owns the prompt;
        the pipeline only has to hand it the LLM."""
        # Sentinel object: not actually called by FakeTranscriber, but we
        # assert it shows up in the recorded call.
        sentinel_llm = object()
        tr = FakeTranscriber(transcript="raw words")
        pipe = _pipeline(
            mirror=FakeMirror(), transcriber=tr, llm=sentinel_llm,
        )
        await pipe.capture_binary(
            file_data=b"opus-data", mime="audio/ogg",
            filename="voice.ogg", source_uri="mxc://server/abc",
            sender_mxid="@homer:s",
        )
        assert tr.calls[0]["cleanup_with"] is sentinel_llm

    @pytest.mark.asyncio
    async def test_no_llm_means_no_cleanup(self):
        """When the LLM isn't wired (e.g. archivist boots before
        OPENAI_URL is set), cleanup_with is None and the Transcriber
        returns the raw transcript verbatim."""
        tr = FakeTranscriber(transcript="raw words")
        pipe = _pipeline(mirror=FakeMirror(), transcriber=tr, llm=None)
        await pipe.capture_binary(
            file_data=b"opus-data", mime="audio/ogg",
            filename="voice.ogg", source_uri="mxc://server/abc",
            sender_mxid="@homer:s",
        )
        assert tr.calls[0]["cleanup_with"] is None


