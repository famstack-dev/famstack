"""CapturePipeline — URL/text capture → classify → mirror → outcome.

Like DocumentPipeline, it does the work (no Matrix, no i18n) and returns
a CaptureOutcome the orchestrator renders. Mid-flow "fetching" goes
through a Notifier. These pin the branching with fakes; the full path
is e2e-verified by test_capture_memory_e2e.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import pytest
from werkzeug import Response

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs" / "bot"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "core" / "tools-server"))

from capture_pipeline import CapturePipeline  # noqa: E402
from card_index import find_card  # noqa: E402
from card_resolver import CardResolver  # noqa: E402
from vault_entry import capture_frontmatter, render_capture  # noqa: E402
from stack.web import FetchOutcome  # noqa: E402
from stack.web.quality import Verdict  # noqa: E402


def _source(*, text="article body", source_uri=None, title_hint="A Title"):
    return SimpleNamespace(text=text, source_uri=source_uri, title_hint=title_hint)


class FakeExtractor:
    """Stands in for the real extractor at the pipeline's seam.

    The URL path asks for `fetch`, because it needs the gate's verdict
    to explain a refusal; the text path still asks for `extract`. A
    None result means the gate refused the page, and `verdict` says
    which refusal, so a test can pin what the pipeline was told rather
    than just that something went wrong.
    """

    def __init__(self, result, verdict="challenge"):
        self._result = result
        self._verdict = verdict

    async def extract(self, _arg):
        return self._result

    async def fetch(self, url):
        if self._result is None:
            return FetchOutcome(
                verdict=Verdict(self._verdict, f"the site returned a {self._verdict}"),
                url=url,
            )
        return FetchOutcome(
            verdict=Verdict("ok", "extracted"), content=self._result, url=url,
        )


class FakeClassifier:
    def __init__(self, payload=None, raises=None):
        self._payload = payload if payload is not None else {
            "title": "Captured", "tags": ["AI"], "persons": ["Homer"], "summary": "s",
        }
        self._raises = raises

    async def classify_capture(self, *, text, person_names, existing_tags,
                               images=None, user_hint=None,
                               initial_classification=None,
                               extract_action_items=False,
                               current_list="", write_in=None):
        # Recorded so a test can assert the classifier was shown the list it
        # is about to add to; extracting blind is what grew one list from
        # thirteen items to twenty-seven.
        self.saw_current_list = current_list
        self.saw_text = text
        self.saw_user_hint = user_hint
        self.saw_write_in = write_in
        self.saw_extract_action_items = extract_action_items
        if self._raises:
            raise self._raises
        return self._payload

    async def reformat(self, ocr_text: str, *, max_chars: int = 20000):
        # Default: echo the input (capped to max_chars) prefixed so
        # tests can detect that reformat ran AND verify the cap was
        # respected. Subclasses override to return None (LLM down /
        # not useful) or a short token to exercise the sanity guard.
        return f"# Reformatted\n\n{ocr_text[:max_chars]}"


class FakeMirror:
    def __init__(self):
        self.captures: list[dict] = []
        self.emails: list[dict] = []

    async def publish_capture(self, **kwargs):
        self.captures.append(kwargs)
        # Mirrors the new publish_capture contract: return the vault
        # path (or None on failure). Stubs out a deterministic path
        # so the envelope-emission branch in _publish executes.
        kind = kwargs.get("kind") or "bookmark"
        entity = "homer"
        return f"{entity}/{kind}s/test-capture.md"

    async def publish_email_message(self, **kwargs):
        # Email folds into a thread file via its own mirror entrypoint;
        # the path is keyed by the thread, not the individual message.
        self.emails.append(kwargs)
        entity = kwargs.get("entity") or "homer"
        return f"{entity}/emails/test-thread.md"

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
        self.acknowledged = 0

    async def status(self, key, **kwargs):
        self.statuses.append((key, kwargs))

    async def acknowledge(self):
        self.acknowledged += 1


_UNSET = object()


def _pipeline(*, mirror=_UNSET, classifier=None, capture_keep_body=False,
              llm=None, text_extractor=None, url_extractor=None, vault=None,
              cards=None):
    return CapturePipeline(
        url_extractor=url_extractor or FakeExtractor(_source(source_uri="http://src")),
        text_extractor=text_extractor
        or FakeExtractor(_source(source_uri="http://embedded")),
        classifier=classifier or FakeClassifier(),
        mirror=FakeMirror() if mirror is _UNSET else mirror,
        capture_tags=FakeTags(),
        paperless=FakePaperless(),
        bot_name="archivist-bot",
        classify_max_chars=10000,
        capture_keep_body=capture_keep_body,
        capture_tag_prompt_size=50,
        llm=llm,
        vault=vault,
        cards=cards,
    )


class TestCaptureUrl:

    @pytest.mark.asyncio
    async def test_acknowledges_then_captures(self):
        mirror = FakeMirror()
        pipe = _pipeline(mirror=mirror)
        notifier = FakeNotifier()
        out = await pipe.capture_url(
            url="http://example.com", sender_mxid="@homer:s", notifier=notifier,
        )
        assert out.status == "captured"
        # The bot reacts 👀 on the source message instead of posting a
        # "Reading example.com..." status reply.
        assert notifier.acknowledged == 1
        assert len(mirror.captures) == 1
        assert mirror.captures[0]["kind"] == "bookmark"
        assert out.display_link == "http://example.com"

    @pytest.mark.asyncio
    async def test_a_blocked_page_files_a_link_card_instead_of_nothing(self):
        """A page we cannot read still files.

        Dropping it was the old behaviour and it lost the two things
        worth keeping -- the link, and whatever the sender wrote around
        it. A shop that checks for bots is exactly when "gear list for
        the camping trip" is the useful half of the message.
        """
        mirror = FakeMirror()
        pipe = _pipeline(mirror=mirror, url_extractor=FakeExtractor(None))
        notifier = FakeNotifier()

        out = await pipe.capture_url(
            url="https://www.decathlon.de/", sender_mxid="@homer:s",
            notifier=notifier, user_hint="Gear list for the camping trip",
        )

        assert out.status == "captured"
        assert len(mirror.captures) == 1
        # The 👀 acknowledgement still fires before the work.
        assert notifier.acknowledged == 1

    @pytest.mark.asyncio
    async def test_the_link_card_carries_the_gate_reason(self):
        """The verdict rides out on the outcome so the reply layer can
        name the obstacle. "Reddit wants you signed in" is actionable;
        "couldn't read that link" is not."""
        pipe = _pipeline(url_extractor=FakeExtractor(None, verdict="login"))

        out = await pipe.capture_url(
            url="https://old.reddit.com/r/x/", sender_mxid="@homer:s",
            notifier=FakeNotifier(),
        )

        assert out.blocked_reason == "login"

    @pytest.mark.asyncio
    async def test_a_readable_page_is_not_marked_blocked(self):
        """`blocked_reason` is the flag the reply layer keys on, so a
        clean capture must leave it unset or every entry grows a
        spurious "the site blocked us" line."""
        out = await _pipeline().capture_url(
            url="http://example.com", sender_mxid="@homer:s", notifier=FakeNotifier(),
        )

        assert out.status == "captured"
        assert out.blocked_reason is None

    @pytest.mark.asyncio
    async def test_the_sender_words_become_the_link_card_body(self):
        """The classifier's input is the sender's own text plus the
        link -- and deliberately not "this page was blocked", which
        would come back as an entry titled after the failure."""
        mirror = FakeMirror()
        pipe = _pipeline(mirror=mirror, url_extractor=FakeExtractor(None))

        await pipe.capture_url(
            url="https://www.decathlon.de/", sender_mxid="@homer:s",
            notifier=FakeNotifier(), user_hint="Gear list for the camping trip",
        )

        filed = mirror.captures[0]
        assert "blocked" not in str(filed).lower()

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
        c = {"tags": ["AI", "Productivity"], "persons": ["Homer"]}
        assert CapturePipeline._tag_list(c) == ["AI", "Productivity", "Person: Homer"]

    def test_single_string_tag_becomes_list(self):
        c = {"tags": "AI", "persons": ["Homer"]}
        assert CapturePipeline._tag_list(c) == ["AI", "Person: Homer"]

    def test_empty_classification_yields_empty(self):
        assert CapturePipeline._tag_list({}) == []

    def test_skips_non_string_values(self):
        c = {"tags": ["AI", None, 42, ""], "persons": ["Homer", ""]}
        assert CapturePipeline._tag_list(c) == ["AI", "Person: Homer"]

    def test_strips_whitespace(self):
        c = {"tags": ["  AI  ", "Productivity"], "persons": ["Homer"]}
        assert CapturePipeline._tag_list(c) == ["AI", "Productivity", "Person: Homer"]


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
        # Plain image -> one ImageAttachment, no extracted text, a note: a
        # photo the family posts is their own record, not a pointer.
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
        assert kind == "note"

    def test_unsupported_mime_returns_none(self):
        # Video and archives land here and fall through to None. (Audio
        # never reaches the capture pipeline at all: the transport
        # decodes it to text before any handler sees it.)
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


class TestCaptureBinaryFailures:
    """capture_binary handles the shapes it can read and says which
    shape failed, so the reply layer can word the error correctly."""

    @pytest.mark.asyncio
    async def test_unreadable_binary_returns_binary_failure_reason(self):
        """A mime the binary path can't extract (e.g. an unsupported
        video format) returns failure_reason='binary' so the user gets
        the file-shaped error message, not the link one."""
        pipe = _pipeline(mirror=FakeMirror())
        out = await pipe.capture_binary(
            file_data=b"random-bytes", mime="video/mp4",
            filename="clip.mp4", source_uri="mxc://server/v",
            sender_mxid="@homer:s",
        )
        assert out.status == "extract_failed"
        assert out.failure_reason == "binary"


class TestTopicSeedMerge:
    """The static merge helper pins the tag-seed semantics: seed first,
    classifier additions second, duplicates collapsed. The room-based
    routing in the archivist (Step 3) leans on this contract; the
    merge function is exposed as static so the archivist can verify
    its own seed handling without instantiating a pipeline."""

    def test_seed_survives_empty_classifier_tags(self):
        """The strongest property: a capture filed in a topic room
        retains the seed even when the classifier returns no tags
        at all. Room beats classifier in the empty case."""
        merged = CapturePipeline._merge_seed_topics(
            {"tags": []}, seed_topics=["camping"],
        )
        assert merged["tags"] == ["camping"]

    def test_seed_first_then_classifier_additions(self):
        """Seed precedes classifier tags in the merged list. This
        is load-bearing for downstream consumers that read the first
        tag as the canonical topic (e.g. the dream cycle's pattern
        detection)."""
        merged = CapturePipeline._merge_seed_topics(
            {"tags": ["gear", "italy-2026"]},
            seed_topics=["camping"],
        )
        assert merged["tags"] == ["camping", "gear", "italy-2026"]

    def test_seed_deduplicates_when_classifier_repeats_it(self):
        """The classifier may include the seed tag in its output
        despite the prompt's guidance. The merge collapses the
        duplicate; the seed's position wins."""
        merged = CapturePipeline._merge_seed_topics(
            {"tags": ["camping", "gear"]},
            seed_topics=["camping"],
        )
        assert merged["tags"] == ["camping", "gear"]

    def test_no_seed_is_noop(self):
        """Non-topic-room captures pass `seed_topics=None` (or omit
        the kwarg). The classifier's tags are returned unchanged."""
        merged = CapturePipeline._merge_seed_topics(
            {"tags": ["gear"]}, seed_topics=None,
        )
        assert merged["tags"] == ["gear"]
        merged_empty = CapturePipeline._merge_seed_topics(
            {"tags": ["gear"]}, seed_topics=[],
        )
        assert merged_empty["tags"] == ["gear"]

    def test_classifier_missing_tags_key(self):
        """When the classifier degraded (no tags key in the payload),
        the seed still applies. The result has a fresh `tags` list."""
        merged = CapturePipeline._merge_seed_topics(
            {"persons": ["Homer"]}, seed_topics=["camping"],
        )
        assert merged["tags"] == ["camping"]

    def test_classifier_returns_string_for_tags(self):
        """`_tag_list` already tolerates the classifier returning a
        bare string for `tags:` (some prompts produce that). The
        merge does the same coercion so the seed prepends cleanly."""
        merged = CapturePipeline._merge_seed_topics(
            {"tags": "gear"}, seed_topics=["camping"],
        )
        assert merged["tags"] == ["camping", "gear"]

    def test_seed_with_multiple_entries(self):
        """A topic room with multiple guaranteed tags (forward
        compatibility for `default_topics: [camping, outdoor]`)
        keeps all of them in order."""
        merged = CapturePipeline._merge_seed_topics(
            {"tags": ["italy-2026"]},
            seed_topics=["camping", "outdoor"],
        )
        assert merged["tags"] == ["camping", "outdoor", "italy-2026"]

    def test_seed_skips_whitespace_and_non_string(self):
        """Defensive: malformed seed entries don't pollute the
        merged list. Empty strings and non-strings drop silently."""
        merged = CapturePipeline._merge_seed_topics(
            {"tags": ["gear"]},
            seed_topics=["camping", "", None, "  ", 42],  # type: ignore[list-item]
        )
        assert merged["tags"] == ["camping", "gear"]


class TestPdfBodyCap:
    """PDFs file as notes (body preserved in the mirror). The body is
    capped at ``classify_max_chars`` so a multi-hundred-page manual
    doesn't bloat the vault file. Non-PDFs and short PDFs pass through
    unchanged."""

    def test_short_pdf_unchanged(self):
        pipe = _pipeline(mirror=FakeMirror())
        source = SimpleNamespace(
            text="short body", mime="application/pdf",
            title_hint="manual.pdf", source_uri="mxc://a",
        )
        result = pipe._cap_pdf_body(source)
        assert result is source

    def test_oversized_pdf_truncated(self):
        pipe = _pipeline(mirror=FakeMirror())
        pipe.classify_max_chars = 50
        source = SimpleNamespace(
            text="A" * 200, mime="application/pdf",
            title_hint="manual.pdf", source_uri="mxc://a",
        )
        result = pipe._cap_pdf_body(source)
        assert len(result.text) == 50
        assert result.mime == "application/pdf"
        assert result.title_hint == "manual.pdf"
        assert result.source_uri == "mxc://a"

    def test_non_pdf_not_capped(self):
        """Text files and audio transcripts already cap themselves
        upstream (or are short by nature). The cap is PDF-only."""
        pipe = _pipeline(mirror=FakeMirror())
        pipe.classify_max_chars = 10
        source = SimpleNamespace(
            text="A" * 100, mime="text/markdown",
            title_hint="notes.md", source_uri=None,
        )
        result = pipe._cap_pdf_body(source)
        assert result is source

    def test_empty_pdf_text_unchanged(self):
        """A vision-only PDF (scan with no text layer) has empty
        source.text. The cap leaves it alone -- there's nothing to
        truncate, and the classifier's vision pass still drives the
        summary callout."""
        pipe = _pipeline(mirror=FakeMirror())
        source = SimpleNamespace(
            text="", mime="application/pdf",
            title_hint="scan.pdf", source_uri="mxc://a",
        )
        result = pipe._cap_pdf_body(source)
        assert result is source


class TestPdfReformat:
    """The reformat pass cleans pypdf/OCR artifacts into readable
    markdown before the body lands in the mirror. Best-effort: anything
    that would lose content (long body) or fail (LLM down) falls back
    to the raw text."""

    @pytest.mark.asyncio
    async def test_short_pdf_reformatted(self):
        """Short PDF body fits within the reformat cap and gets the
        cleaned markdown back."""
        pipe = _pipeline(mirror=FakeMirror())
        source = SimpleNamespace(
            text="raw   OCR  text  with  bad  spacing", mime="application/pdf",
            title_hint="uno.pdf", source_uri="mxc://a",
        )
        result = await pipe._maybe_reformat_pdf(source, kind="note")
        assert result.text.startswith("# Reformatted")
        assert "raw   OCR" in result.text

    @pytest.mark.asyncio
    async def test_reformat_receives_classify_max_chars_cap(self):
        """The capture pipeline passes its configured classify_max_chars
        through as the reformat cap, so the reformat and classify halves
        of the pipeline stay single-sourced on the body window."""
        captured: dict = {}

        class CapAwareClassifier(FakeClassifier):
            async def reformat(self, ocr_text, *, max_chars=20000):
                captured["max_chars"] = max_chars
                captured["input_len"] = len(ocr_text)
                return f"# Reformatted\n\n{ocr_text[:max_chars]}"

        pipe = _pipeline(mirror=FakeMirror(), classifier=CapAwareClassifier())
        pipe.classify_max_chars = 8000
        source = SimpleNamespace(
            text="X" * 5000, mime="application/pdf",
            title_hint="manual.pdf", source_uri="mxc://a",
        )
        await pipe._maybe_reformat_pdf(source, kind="note")
        assert captured["max_chars"] == 8000

    @pytest.mark.asyncio
    async def test_no_classifier_falls_back_to_raw(self):
        """Without a classifier, reformat is skipped entirely. The
        capture still files; the mirror just gets the raw pypdf
        extract."""
        pipe = _pipeline(mirror=FakeMirror())
        pipe._classifier = None
        source = SimpleNamespace(
            text="raw text", mime="application/pdf",
            title_hint="x.pdf", source_uri="mxc://a",
        )
        result = await pipe._maybe_reformat_pdf(source, kind="note")
        assert result is source

    @pytest.mark.asyncio
    async def test_empty_body_skipped(self):
        """Vision-only PDFs (scans with no text layer) have empty
        source.text. There's nothing to reformat; pass through."""
        pipe = _pipeline(mirror=FakeMirror())
        source = SimpleNamespace(
            text="", mime="application/pdf",
            title_hint="scan.pdf", source_uri="mxc://a",
        )
        result = await pipe._maybe_reformat_pdf(source, kind="note")
        assert result is source

    @pytest.mark.asyncio
    async def test_non_pdf_not_reformatted(self):
        """The reformat is PDF-specific. Markdown and text uploads
        skip it -- the bytes are already the artifact the user typed."""
        pipe = _pipeline(mirror=FakeMirror())
        source = SimpleNamespace(
            text="# already markdown", mime="text/markdown",
            title_hint="notes.md", source_uri=None,
        )
        result = await pipe._maybe_reformat_pdf(source, kind="note")
        assert result is source

    @pytest.mark.asyncio
    async def test_llm_returns_none_falls_back(self):
        """LLM down or unparseable response returns None. The mirror
        keeps the raw OCR rather than getting nothing."""

        class NullReformatClassifier(FakeClassifier):
            async def reformat(self, ocr_text, *, max_chars=20000):
                return None

        pipe = _pipeline(mirror=FakeMirror(), classifier=NullReformatClassifier())
        source = SimpleNamespace(
            text="raw text here", mime="application/pdf",
            title_hint="x.pdf", source_uri="mxc://a",
        )
        result = await pipe._maybe_reformat_pdf(source, kind="note")
        assert result is source

    @pytest.mark.asyncio
    async def test_lossy_output_falls_back(self):
        """LLM returning a fragment ("ok") or otherwise much shorter
        than the input means content was lost. The ratio guard catches
        it and falls back to the raw OCR rather than overwriting it
        with a half-answer."""

        class StubClassifier(FakeClassifier):
            async def reformat(self, ocr_text, *, max_chars=20000):
                return "ok"

        pipe = _pipeline(mirror=FakeMirror(), classifier=StubClassifier())
        source = SimpleNamespace(
            text="raw text here with enough content to be meaningful",
            mime="application/pdf",
            title_hint="x.pdf", source_uri="mxc://a",
        )
        result = await pipe._maybe_reformat_pdf(source, kind="note")
        assert result is source

    @pytest.mark.asyncio
    async def test_modest_compression_kept(self):
        """A reformat that tightens whitespace and rejoins broken lines
        legitimately produces a slightly shorter output. The ratio
        guard is permissive enough to keep that result."""

        class CompressingClassifier(FakeClassifier):
            async def reformat(self, ocr_text, *, max_chars=20000):
                # Return ~75% of input length -- well above the 50% floor.
                return ocr_text[: int(len(ocr_text) * 0.75)]

        pipe = _pipeline(mirror=FakeMirror(), classifier=CompressingClassifier())
        source = SimpleNamespace(
            text="X" * 100, mime="application/pdf",
            title_hint="x.pdf", source_uri="mxc://a",
        )
        result = await pipe._maybe_reformat_pdf(source, kind="note")
        assert result.text == "X" * 75

    @pytest.mark.asyncio
    async def test_bookmark_kind_not_reformatted(self):
        """The reformat only fires for note-kind captures. Bookmarks
        (today's image PDFs) get the vision-driven summary instead."""
        pipe = _pipeline(mirror=FakeMirror())
        source = SimpleNamespace(
            text="some body", mime="application/pdf",
            title_hint="x.pdf", source_uri="mxc://a",
        )
        result = await pipe._maybe_reformat_pdf(source, kind="bookmark")
        assert result is source


class TestCaptureUrlUserHint:
    """A chat-shaped URL message ("Interesting facts: <url>") is captured
    with the surrounding text passed to the classifier as a user_hint.
    The hint surfaces in the prompt's user-clarification block so the
    generated title and summary reflect the framing the user wrote."""

    @pytest.mark.asyncio
    async def test_user_hint_flows_to_classifier(self):
        """The hint reaches the classifier call. Pins the wiring all
        the way through capture_url -> _publish -> _classify."""
        captured: dict = {}

        class HintCapturingClassifier(FakeClassifier):
            async def classify_capture(self, **kwargs):
                captured.update(kwargs)
                return await super().classify_capture(**kwargs)

        pipe = _pipeline(
            mirror=FakeMirror(),
            classifier=HintCapturingClassifier(),
        )
        await pipe.capture_url(
            url="http://example.com/article",
            sender_mxid="@homer:s",
            notifier=FakeNotifier(),
            user_hint="Interesting facts",
        )
        assert captured["user_hint"] == "Interesting facts"

    @pytest.mark.asyncio
    async def test_no_user_hint_passes_none(self):
        """The kwarg is optional. Existing bare-URL captures pass
        nothing and the classifier sees user_hint=None."""
        captured: dict = {}

        class HintCapturingClassifier(FakeClassifier):
            async def classify_capture(self, **kwargs):
                captured.update(kwargs)
                return await super().classify_capture(**kwargs)

        pipe = _pipeline(
            mirror=FakeMirror(),
            classifier=HintCapturingClassifier(),
        )
        await pipe.capture_url(
            url="http://example.com/article",
            sender_mxid="@homer:s",
            notifier=FakeNotifier(),
        )
        assert captured["user_hint"] is None


class TestTopicSeedEndToEnd:
    """The seed propagates through `_publish`: the mirror call receives
    the merged tag list, the envelope carries it, the capture-tag
    cache records it. Three entry points are pinned; the fourth
    (capture_binary) is exercised through the markdown file path."""

    @pytest.mark.asyncio
    async def test_capture_text_with_seed_files_seed_tag(self):
        """End-to-end: a topic-routed text capture lands in the mirror
        with the seed even when the classifier returns no tags."""
        mirror = FakeMirror()
        pipe = _pipeline(
            mirror=mirror,
            classifier=FakeClassifier(payload={
                "title": "Note",
                "tags": [],
                "persons": ["Homer"],
                "summary": "s",
            }),
        )
        out = await pipe.capture_text(
            text="we packed everything in the roof box",
            sender_mxid="@homer:s",
            seed_topics=["camping"],
        )
        assert out.status == "captured"
        # Mirror gets the merged list (seed + Person: Homer).
        assert "camping" in mirror.captures[0]["tags"]
        # Classification dict also carries the merge.
        assert "camping" in out.classification["tags"]

    @pytest.mark.asyncio
    async def test_capture_url_with_seed_keeps_classifier_additions(self):
        """A URL captured in a topic room: seed first, classifier
        additions kept. Both end up in the mirror's tag list."""
        mirror = FakeMirror()
        pipe = _pipeline(
            mirror=mirror,
            classifier=FakeClassifier(payload={
                "title": "Article",
                "tags": ["gear", "vans"],
                "persons": ["Homer"],
                "summary": "s",
            }),
        )
        out = await pipe.capture_url(
            url="http://example.com/best-vanlife-gear",
            sender_mxid="@homer:s",
            notifier=FakeNotifier(),
            seed_topics=["van-life"],
        )
        assert out.status == "captured"
        tags = mirror.captures[0]["tags"]
        assert "van-life" in tags
        assert "gear" in tags
        assert "vans" in tags
        # Seed first, then classifier additions in order.
        non_person = [t for t in tags if not t.startswith("Person: ")]
        assert non_person == ["van-life", "gear", "vans"]

    @pytest.mark.asyncio
    async def test_seed_recorded_in_capture_tag_cache(self):
        """The capture-tag cache feeds the next capture's prompt as
        existing-tags context. A seeded topic-room capture should
        push the seed into that cache so the camping tag stays warm
        even when the classifier never invented it on its own."""
        mirror = FakeMirror()
        tags = FakeTags()
        pipe = CapturePipeline(
            url_extractor=FakeExtractor(_source(source_uri="http://src")),
            text_extractor=FakeExtractor(_source(source_uri="http://embedded")),
            classifier=FakeClassifier(payload={
                "title": "Note", "tags": [], "persons": ["Homer"],
            }),
            mirror=mirror,
            capture_tags=tags,
            paperless=FakePaperless(),
            bot_name="archivist-bot",
            classify_max_chars=10000,
            capture_keep_body=False,
            capture_tag_prompt_size=50,
        )
        await pipe.capture_text(
            text="some pasted thought",
            sender_mxid="@homer:s",
            seed_topics=["camping"],
        )
        # The seed survives into the topic-tag list the cache records.
        all_recorded = [t for entry in tags.recorded for t in entry[0]]
        assert "camping" in all_recorded

    @pytest.mark.asyncio
    async def test_bucket_override_routes_to_topic_folder(self):
        """A topic-room capture files under the topic bucket regardless
        of who sent it. Sender stays attributed via `persons:` (the
        classifier returns it); only the on-disk path changes."""
        mirror = FakeMirror()
        pipe = _pipeline(mirror=mirror)
        out = await pipe.capture_text(
            text="we packed everything in the roof box",
            sender_mxid="@homer:s",
            seed_topics=["camping"],
            bucket="camping",
        )
        assert out.status == "captured"
        # Mirror sees the topic bucket as entity, not homer.
        assert mirror.captures[0]["entity"] == "camping"

    @pytest.mark.asyncio
    async def test_bucket_override_handles_nested_personal_bucket(self):
        """Personal topic rooms nest under the sender bucket
        (`homer/camping/`). The mirror writer treats the entity
        string as a path prefix, so this just works."""
        mirror = FakeMirror()
        pipe = _pipeline(mirror=mirror)
        out = await pipe.capture_text(
            text="solo trip planning",
            sender_mxid="@homer:s",
            seed_topics=["gravel"],
            bucket="homer/gravel",
        )
        assert out.status == "captured"
        assert mirror.captures[0]["entity"] == "homer/gravel"

    @pytest.mark.asyncio
    async def test_no_bucket_override_falls_back_to_sender(self):
        """Without a bucket override the capture routes to the sender's
        own personal bucket (today's default for non-topic rooms)."""
        mirror = FakeMirror()
        pipe = _pipeline(mirror=mirror)
        out = await pipe.capture_text(
            text="a personal note",
            sender_mxid="@homer:s",
        )
        assert out.status == "captured"
        assert mirror.captures[0]["entity"] == "homer"

    @pytest.mark.asyncio
    async def test_seed_propagates_to_envelope(self):
        """The Matrix envelope carries `resolved_tags` so downstream
        consumers (the deriver, future cross-reference passes) see
        the merged tag list, not just whatever the classifier guessed."""
        mirror = FakeMirror()
        pipe = _pipeline(
            mirror=mirror,
            classifier=FakeClassifier(payload={
                "title": "Note", "tags": [], "persons": ["Homer"],
            }),
        )
        out = await pipe.capture_text(
            text="a note",
            sender_mxid="@homer:s",
            seed_topics=["camping"],
        )
        assert out.envelope is not None
        envelope_tags = out.envelope.get("data", {}).get("tags", [])
        assert "camping" in envelope_tags




class TestCaptureBinaryAttribution:
    """A bot-posted binary (email attachment) is filed on behalf of the
    source: `default_person=False` keeps the bot out of `persons:`, and the
    provenance seed tags ride into the mirror."""

    @pytest.mark.asyncio
    async def test_default_person_false_omits_bot_and_keeps_seed_tags(self):
        mirror = FakeMirror()
        pipe = _pipeline(
            mirror=mirror,
            classifier=FakeClassifier(payload={
                "title": "Permission slip", "tags": [], "summary": "s",
            }),
        )
        out = await pipe.capture_binary(
            file_data=b"# Permission slip\n\nPlease sign and return.",
            mime="text/markdown",
            filename="slip.md",
            source_uri="mxc://s/abc",
            sender_mxid="@mail-bot:s",
            seed_topics=["email", "Sender: office@school.example"],
            default_person=False,
        )
        assert out.status == "captured"
        cls = mirror.captures[0]["classification"]
        assert not cls.get("persons")  # the bot is not filed as a person
        tags = mirror.captures[0]["tags"]
        assert "email" in tags
        assert "Sender: office@school.example" in tags

    @pytest.mark.asyncio
    async def test_default_person_true_still_attributes_sender(self):
        # Contrast: a human upload (default) still falls the sender in.
        mirror = FakeMirror()
        pipe = _pipeline(
            mirror=mirror,
            classifier=FakeClassifier(payload={
                "title": "Scan", "tags": [], "summary": "s",
            }),
        )
        await pipe.capture_binary(
            file_data=b"# Scan\n\nbody",
            mime="text/markdown", filename="scan.md",
            source_uri="mxc://s/d", sender_mxid="@homer:s",
        )
        assert mirror.captures[0]["classification"].get("persons") == ["Homer"]


class TestCaptureEmail:
    """Email is a capture source that *accumulates*: body → text, subject
    → title, but every message folds into one thread file keyed by the
    thread root, not a single-shot entry. So it routes through the mirror's
    `publish_email_message` (append a section) rather than `publish_capture`
    (replace). The classify/tag/envelope tail is shared with URLs/notes."""

    @pytest.mark.asyncio
    async def test_files_email_through_thread_fold_path(self):
        mirror = FakeMirror()
        pipe = _pipeline(mirror=mirror)
        out = await pipe.capture_email(
            subject="Elternabend am Freitag",
            body="Bitte das Formular bis Freitag zurücksenden.",
            message_id="<abc123@school.example>",
            sender_mxid="@homer:s",
            from_addr="schule@example.org",
        )
        assert out.status == "captured"
        # Routed to the fold path, not the single-shot capture path.
        assert mirror.captures == []
        assert len(mirror.emails) == 1
        msg = mirror.emails[0]
        # Thread-starting message is its own thread root.
        assert msg["thread_uri"] == "mid:abc123@school.example"
        assert msg["message_id"] == "<abc123@school.example>"
        assert msg["from_addr"] == "schule@example.org"
        assert msg["title_hint"] == "Elternabend am Freitag"
        assert msg["body_text"] == "Bitte das Formular bis Freitag zurücksenden."

    @pytest.mark.asyncio
    async def test_reply_folds_into_same_thread(self):
        # Two messages, distinct Message-IDs, same thread root → both land
        # in the same thread file (same thread_uri) as separate sections.
        mirror = FakeMirror()
        pipe = _pipeline(mirror=mirror)
        await pipe.capture_email(
            subject="Elternabend", body="first message",
            message_id="<root@school.example>", sender_mxid="@homer:s",
        )
        await pipe.capture_email(
            subject="Re: Elternabend", body="the reply",
            message_id="<reply@school.example>",
            thread_root="<root@school.example>",
            sender_mxid="@homer:s",
        )
        assert len(mirror.emails) == 2
        assert mirror.emails[0]["thread_uri"] == "mid:root@school.example"
        assert mirror.emails[1]["thread_uri"] == "mid:root@school.example"
        assert mirror.emails[0]["message_id"] != mirror.emails[1]["message_id"]

    @pytest.mark.asyncio
    async def test_does_not_file_the_bot_as_a_person(self):
        # When the classifier names no persons, an email must NOT fall the
        # sender (the mail bot) in as the person — unlike a human paste.
        mirror = FakeMirror()
        pipe = _pipeline(
            mirror=mirror,
            classifier=FakeClassifier(payload={"title": "T", "tags": [], "summary": "s"}),
        )
        await pipe.capture_email(
            subject="Statement", body="amount due", message_id="<m@h>",
            sender_mxid="@mail-bot:s",
        )
        cls = mirror.emails[0]["classification"]
        assert not cls.get("persons")  # no "Mail-bot" person

    @pytest.mark.asyncio
    async def test_empty_body_is_empty_outcome(self):
        mirror = FakeMirror()
        pipe = _pipeline(mirror=mirror)
        out = await pipe.capture_email(
            subject="x", body="   ", message_id="<i@h>", sender_mxid="@homer:s",
        )
        assert out.status == "empty"
        assert mirror.emails == []
        assert mirror.captures == []


class TestCaptureOrigin:
    """The footer tells the family where a capture came from. Speech and
    a paste are not the same provenance and must not read the same."""

    # Spoken words rarely contain a URL, so the footer falls through to
    # the origin label — which is the whole point of the label.
    NO_LINK = staticmethod(lambda: FakeExtractor(_source(source_uri=None)))

    @pytest.mark.asyncio
    async def test_a_transcribed_note_reads_as_a_voice_message(self):
        pipe = _pipeline(mirror=FakeMirror(), text_extractor=self.NO_LINK())
        out = await pipe.capture_text(
            text="Sort out the loft before Christmas.",
            sender_mxid="@homer:s", transcribed=True,
        )
        assert out.display_link == "(voice message)"

    @pytest.mark.asyncio
    async def test_a_typed_note_still_reads_as_pasted_text(self):
        pipe = _pipeline(mirror=FakeMirror(), text_extractor=self.NO_LINK())
        out = await pipe.capture_text(
            text="Sort out the loft before Christmas.",
            sender_mxid="@homer:s",
        )
        assert out.display_link == "(pasted text)"


class TestCorrectingAnEntryKeepsItsFile:
    """A correction re-renders the whole entry from the prior one and
    never sees the original bytes again. The archived file has to
    survive that, or replying "this is about school, not work" would
    quietly take the photograph off the page.

    The entry fed in here is rendered by `vault_entry`, not hand-written,
    so the read and the write cannot drift apart without this failing.
    """

    PATH = "marge/bookmarks/test-capture.md"

    def _entry(self, **kept) -> str:
        fm = capture_frontmatter(
            title="Schulkalender", captured_at="2026-03-14", kind="bookmark",
            source_uri="mxc://home.local/abc", persons=["Marge"],
            tags=["schule"], model=None, capture_id="$shot:home.local",
        )
        return render_capture(
            frontmatter=fm, body="", kind="bookmark",
            captured_at="2026-03-14", source_uri="mxc://home.local/abc",
            persons=["Marge"], from_path=self.PATH, shared_bucket="family",
            summary="The school calendar for March.", facts=[],
            kept_media=kept or None,
        )

    async def _reprocess(self, raw: str) -> dict:
        mirror = FakeMirror()
        mirror._stored = {self.PATH: raw}
        pipe = _pipeline(mirror=mirror)
        await pipe.reprocess(
            vault_path=self.PATH, user_hint="das gehört zur Schule",
            sender_mxid="@marge:s",
        )
        return mirror.captures[-1]

    @pytest.mark.asyncio
    async def test_the_corrected_entry_still_names_the_file(self):
        published = await self._reprocess(self._entry(
            name="Bildschirmfoto.png",
            original="/media/2026/03/shot.png",
            embed="/media/2026/03/shot.webp",
        ))

        assert published["kept_media"] == {
            "name": "Bildschirmfoto.png",
            "original": "/media/2026/03/shot.png",
            "embed": "/media/2026/03/shot.webp",
        }

    @pytest.mark.asyncio
    async def test_a_document_stays_a_document_through_a_correction(self):
        """It had no embed before the correction and must not gain one:
        a PDF in an iframe is what the entry deliberately does not do."""
        published = await self._reprocess(self._entry(
            name="Anmeldung.pdf",
            original="/media/2026/03/form.pdf",
            embed="",
        ))

        assert published["kept_media"]["embed"] == ""
        assert published["kept_media"]["original"] == "/media/2026/03/form.pdf"

    @pytest.mark.asyncio
    async def test_an_entry_that_never_had_a_file_does_not_grow_one(self):
        published = await self._reprocess(self._entry())

        assert published["kept_media"] is None


class TestAPhotosCaption:
    """A photo posted with a caption ("Bart, first time without training
    wheels") carries the only words about it the family wrote. The model
    reads the caption with the picture, so the summary says what the
    sender said; the entry keeps the caption word for word; and a later
    correction, which re-reads the entry rather than the picture, still
    has it."""

    CAPTION = "Bart fährt zum ersten Mal ohne Stützräder"

    async def _post(self, caption: str):
        mirror, classifier = FakeMirror(), FakeClassifier()
        await _pipeline(mirror=mirror, classifier=classifier).capture_binary(
            file_data=b"\x89PNG\r\n", mime="image/png", filename="IMG_1.png",
            source_uri="mxc://home.test/x", sender_mxid="@marge:server",
            caption=caption,
        )
        return mirror.captures[0], classifier

    @pytest.mark.asyncio
    async def test_the_model_reads_the_caption_with_the_picture(self):
        _, classifier = await self._post(self.CAPTION)
        assert classifier.saw_user_hint == self.CAPTION

    @pytest.mark.asyncio
    async def test_the_entry_keeps_the_caption(self):
        published, _ = await self._post(self.CAPTION)
        assert published["caption"] == self.CAPTION

    @pytest.mark.asyncio
    async def test_a_photo_is_filed_as_a_note(self):
        """A photo the family posts is their own record, like a note they
        type, not a pointer to something elsewhere like a saved link."""
        published, _ = await self._post(self.CAPTION)
        assert published["kind"] == "note"

    @pytest.mark.asyncio
    async def test_a_photo_without_a_caption_is_read_on_its_own(self):
        published, classifier = await self._post("")
        assert classifier.saw_user_hint is None
        assert not published["caption"]

    @pytest.mark.asyncio
    async def test_a_recordings_caption_is_kept_like_a_photos(self):
        mirror, classifier = FakeMirror(), FakeClassifier()
        await _pipeline(mirror=mirror, classifier=classifier).capture_text(
            text="Die Glasur dünn auftragen.", sender_mxid="@marge:server",
            transcribed=True, caption=self.CAPTION,
        )
        assert classifier.saw_user_hint == self.CAPTION
        assert mirror.captures[0]["caption"] == self.CAPTION

    @pytest.mark.asyncio
    async def test_a_correction_keeps_the_caption(self):
        path = "marge/bookmarks/test-capture.md"
        entry = render_capture(
            frontmatter=capture_frontmatter(
                title="Fahrrad", captured_at="2026-03-14", kind="bookmark",
                source_uri="mxc://home.test/x", persons=["Marge"],
                tags=["familie"], model=None, capture_id="$img:home.test",
            ),
            body="", kind="bookmark", captured_at="2026-03-14",
            source_uri="mxc://home.test/x", persons=["Marge"],
            from_path=path, shared_bucket="family",
            summary="Ein Kind auf einem Fahrrad.", facts=[],
            caption=self.CAPTION,
        )
        mirror, classifier = FakeMirror(), FakeClassifier()
        mirror._stored = {path: entry}
        await _pipeline(mirror=mirror, classifier=classifier).reprocess(
            vault_path=path, user_hint="das ist Bart", sender_mxid="@marge:s",
        )

        assert mirror.captures[-1]["caption"] == self.CAPTION
        assert self.CAPTION in classifier.saw_text


class TestARepliesWordsStayOnTheEntry:
    """A reply in a capture's thread refiles it with the reply as the
    sender's note. The model works the reply into the summary in its own
    words; the entry also keeps the reply as it was written, with whoever
    wrote it, so nothing the family said about the item is lost to a
    paraphrase. Each reply adds to the ones before it."""

    PATH = "family/painting/bookmarks/test-capture.md"

    def _entry(self, **over) -> str:
        args = dict(
            frontmatter=capture_frontmatter(
                title="Raku-Schale", captured_at="2026-10-13", kind="bookmark",
                source_uri="mxc://home.test/x", persons=["Marge"],
                tags=["keramik"], model=None, capture_id="$img:home.test",
            ),
            body="", kind="bookmark", captured_at="2026-10-13",
            source_uri="mxc://home.test/x", persons=["Marge"],
            from_path=self.PATH, shared_bucket="family",
            summary="Eine Raku-Schale.", facts=[],
        )
        args.update(over)
        return render_capture(**args)

    async def _reply(self, entry: str, text: str, sender: str = "@marge:s"):
        mirror = FakeMirror()
        mirror._stored = {self.PATH: entry}
        await _pipeline(mirror=mirror).reprocess(
            vault_path=self.PATH, user_hint=text, sender_mxid=sender,
            reply_text=text,
        )
        return mirror.captures[-1]

    @pytest.mark.asyncio
    async def test_a_reply_is_kept_with_its_author(self):
        published = await self._reply(self._entry(), "950 Grad war zu heiß, nächstes Mal 900.")
        assert published["replies"] == [("Marge", "950 Grad war zu heiß, nächstes Mal 900.")]

    @pytest.mark.asyncio
    async def test_a_second_reply_joins_the_first_and_the_caption_stays(self):
        entry = self._entry(caption="Raku im Garten",
                            replies=[("Marge", "Glasur dünner auftragen.")])
        published = await self._reply(entry, "Sofort ins Sägemehl.", sender="@homer:s")
        assert published["replies"] == [
            ("Marge", "Glasur dünner auftragen."), ("Homer", "Sofort ins Sägemehl."),
        ]
        assert published["caption"] == "Raku im Garten"

    @pytest.mark.asyncio
    async def test_the_same_reply_twice_is_kept_once(self):
        entry = self._entry(replies=[("Marge", "Glasur dünner auftragen.")])
        published = await self._reply(entry, "Glasur dünner auftragen.")
        assert published["replies"] == [("Marge", "Glasur dünner auftragen.")]


class TestTheRoomsLanguageReachesTheClassifier:
    """A room can choose the language its captures are written in
    (`!config language`). Every capture entry point hands that choice to
    the classifier; without one the classifier uses the household's."""

    @pytest.mark.asyncio
    async def test_a_note(self):
        classifier = FakeClassifier()
        await _pipeline(classifier=classifier).capture_text(
            text="Kubernetes upgrade notes", sender_mxid="@homer:server",
            write_in="en",
        )
        assert classifier.saw_write_in == "en"

    @pytest.mark.asyncio
    async def test_a_pasted_image(self):
        classifier = FakeClassifier()
        await _pipeline(classifier=classifier).capture_binary(
            file_data=b"\x89PNG\r\n", mime="image/png", filename="image.png",
            source_uri="mxc://home.test/x", sender_mxid="@homer:server",
            write_in="source",
        )
        assert classifier.saw_write_in == "source"

    @pytest.mark.asyncio
    async def test_no_choice_leaves_it_to_the_household(self):
        classifier = FakeClassifier()
        await _pipeline(classifier=classifier).capture_text(
            text="a note", sender_mxid="@homer:server",
        )
        assert classifier.saw_write_in is None


class TestOnlyTheWritersOwnWordsBecomeTodos:
    """The classifier is asked for todos only for a note in the writer's own words.

    Copied text carries its author's suggestions; asked for tasks, the model
    finds them. `own_words` holds the rule; these pin that every way into
    the vault applies it.
    """

    @pytest.mark.asyncio
    async def test_a_short_typed_note_is_asked_for_todos(self):
        classifier = FakeClassifier()
        await _pipeline(classifier=classifier).capture_text(
            text="wir sollten bis Freitag den Müll rausbringen", sender_mxid="@marge:s")
        assert classifier.saw_extract_action_items is True

    @pytest.mark.asyncio
    async def test_a_voice_message_is_asked_for_todos(self):
        classifier = FakeClassifier()
        await _pipeline(classifier=classifier).capture_text(
            text="Nicht vergessen, " + "die Fahrräder aufpumpen. " * 20,
            sender_mxid="@marge:s", transcribed=True)
        assert classifier.saw_extract_action_items is True

    @pytest.mark.asyncio
    async def test_a_pasted_article_is_not(self):
        classifier = FakeClassifier()
        article = "Why we moved to Bun\n\nTry it on your next project. " * 5
        await _pipeline(classifier=classifier).capture_text(
            text=article, sender_mxid="@homer:s")
        assert classifier.saw_extract_action_items is False

    @pytest.mark.asyncio
    async def test_a_link_is_not(self):
        classifier = FakeClassifier()
        await _pipeline(classifier=classifier).capture_url(
            url="https://example.com/framework", sender_mxid="@homer:s",
            notifier=FakeNotifier())
        assert classifier.saw_extract_action_items is False

    @pytest.mark.asyncio
    async def test_a_topic_rooms_note_is_shown_the_topics_list(self):
        """Seeing the list keeps a re-posted list from growing in new wordings."""
        mirror = FakeMirror()
        mirror._stored = {"family/lists/camping.md": "- [ ] Kühlbox\n"}
        classifier = FakeClassifier()
        await _pipeline(mirror=mirror, classifier=classifier).capture_text(
            text="Kühlbox mitnehmen", sender_mxid="@marge:s", bucket="family/camping")
        assert classifier.saw_current_list == "- [ ] Kühlbox\n"



class TestACorrectionFindsTheCaptureWhereItIsNow:
    """A correction names the capture it is about by its card id, the
    event id of the message it was filed from, which never changes. Its
    path does: a refile used to rename it, and photos filed as bookmarks
    have moved to notes. So the archivist asks core's resolver where the
    id lives now, and the path the filing message recorded is only the
    fallback for when the resolver cannot answer.

    The resolver here is the real lookup (`card_index.find_card` over a
    copy of the family memory) behind the tools server's `/cards`
    contract, reached over HTTP by the archivist's real client."""

    OLD = "family/painting/bookmarks/2026/10/raku-454811.md"
    NEW = "family/painting/notes/2026/10/raku-454811.md"
    ID = "$img:home.test"
    # Nothing listens on the discard port: a resolver that is down.
    UNREACHABLE = "http://127.0.0.1:9"

    def _entry(self, path: str) -> str:
        return render_capture(
            frontmatter=capture_frontmatter(
                title="Raku", captured_at="2026-10-13", kind="note",
                source_uri="mxc://home.test/x", persons=[], tags=[],
                model=None, capture_id=self.ID,
            ),
            body="", kind="note", captured_at="2026-10-13",
            source_uri="mxc://home.test/x", persons=[], from_path=path,
            shared_bucket="family", summary="Eine Schale.", facts=[],
        )

    def _resolver(self, httpserver, brain: Path) -> str:
        """The tools server's `/cards/<kind>/<id>` over `brain`."""
        def answer(request):
            kind, _, card_id = request.path.removeprefix("/cards/").partition("/")
            path = find_card(kind, card_id, brain_dir=brain)
            if path is None:
                return Response('{"error": "no such card"}', status=404,
                                content_type="application/json")
            return Response(json.dumps({"kind": kind, "card_id": card_id, "path": path}),
                            content_type="application/json")
        httpserver.expect_request(re.compile("^/cards/")).respond_with_handler(answer)
        return httpserver.url_for("")

    async def _correct(self, *, resolver_url: str, stored: str):
        mirror = FakeMirror()
        mirror._stored = {stored: self._entry(stored)}
        async with aiohttp.ClientSession() as http:
            out = await _pipeline(
                mirror=mirror, cards=CardResolver(http, resolver_url),
            ).reprocess(
                vault_path=self.OLD, capture_id=self.ID,
                user_hint="das ist Keramik", sender_mxid="@marge:s",
            )
        return out, mirror

    @pytest.mark.asyncio
    async def test_a_moved_capture_is_found_through_the_resolver(
        self, tmp_path, httpserver,
    ):
        (tmp_path / self.NEW).parent.mkdir(parents=True)
        (tmp_path / self.NEW).write_text(self._entry(self.NEW), encoding="utf-8")

        out, mirror = await self._correct(
            resolver_url=self._resolver(httpserver, tmp_path), stored=self.NEW)

        assert out.status == "reclassified"
        assert mirror.captures[-1]["existing_path"] == self.NEW

    @pytest.mark.asyncio
    async def test_a_resolver_that_is_down_falls_back_to_the_recorded_path(self):
        """Core restarting must not turn every correction into "missing"."""
        out, mirror = await self._correct(resolver_url=self.UNREACHABLE, stored=self.OLD)

        assert out.status == "reclassified"
        assert mirror.captures[-1]["existing_path"] == self.OLD

    @pytest.mark.asyncio
    async def test_an_id_no_card_carries_and_a_gone_path_is_missing(
        self, tmp_path, httpserver,
    ):
        """The resolver does not know the id and the recorded path is
        gone: the correction says the capture is missing rather than
        guessing."""
        out, _ = await self._correct(
            resolver_url=self._resolver(httpserver, tmp_path), stored=self.NEW)

        assert out.status == "capture_missing"
