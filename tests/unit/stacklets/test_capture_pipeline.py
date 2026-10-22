"""CapturePipeline — URL/text capture → classify → mirror → outcome.

Like DocumentPipeline, it does the work (no Matrix, no i18n) and returns
a CaptureOutcome the orchestrator renders. Mid-flow "fetching" goes
through a Notifier. These pin the branching with fakes; the full path
is e2e-verified by test_capture_memory_e2e.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs" / "bot"))

from capture_pipeline import CapturePipeline  # noqa: E402
from memory.card_events import TextEdit  # noqa: E402
from memory.card_history import CardHistory  # noqa: E402
from memory.note_card import NOTE, Source  # noqa: E402
from git_mirror import Filed  # noqa: E402
from vault_context import VaultContext  # noqa: E402
from vault_entry import capture_frontmatter, identity_for, render_capture, render_note  # noqa: E402
from stack.frontmatter import dump as frontmatter_dump  # noqa: E402
from stack.card_render import render_file  # noqa: E402
from stack.web import FetchOutcome  # noqa: E402
from stack.web.quality import Verdict  # noqa: E402


def _old_note(title: str, words: str = "", *, capture_id: str, path: str,
              persons=(), tags=(), summary: str = "", caption: str = "",
              replies=(), embed: str = "", filed_by: str = "marge") -> str:
    """A note as the archivist filed it before ADR-018: where it came from
    in the frontmatter, its words, caption and replies in sections of their
    own. A refile still reads one, the way migration memory/0008 converts it."""
    fm = {"type": "note", **identity_for(None, "note", path), "title": title,
          "date": "2026-10-13", "persons": list(persons), "filed_by": filed_by,
          "tags": list(tags), "capture_id": capture_id, "timestamp": "2026-10-13T08:00:00Z"}
    parts = ["---", frontmatter_dump(fm), "---", "", f"# {title}", "",
             "> **Captured** 2026-10-13 · **Kind** note", ""]
    if summary:
        parts += [f"> [!summary]\n> {summary}", ""]
    if embed:
        parts += [f"![[{embed}]]", ""]
    if words:
        parts += ["> [!quote]- Original paste",
                  *(f"> {ln}" if ln else ">" for ln in words.split("\n")), ""]
    if caption:
        parts += ["## Caption", "", caption, ""]
    if replies:
        parts += ["## Replies", ""]
        for who, text in replies:
            parts += [f"**{who}**", f"> {text}", ""]
    return "\n".join(parts)


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
    def __init__(self, payload=None, raises=None, corrected=""):
        self._payload = payload if payload is not None else {
            "title": "Captured", "tags": ["AI"], "persons": ["Homer"], "summary": "s",
        }
        self._raises = raises
        # What the correction prompt or the card conversation answers for a
        # note rebuilt from its events.
        self._corrected = corrected
        self.saw_correction = None
        self.saw_rebuild = None

    async def correct_note(self, *, words, corrections):
        self.saw_correction = (words, corrections)
        return {"clean": self._corrected, "prompt_template": "c0e1c2d3"}

    async def rebuild_note(self, *, writer, words, prior, events):
        self.saw_rebuild = (words, prior, events)
        return {"clean": self._corrected, "prompt_template": "c0e1c2d3"}

    async def classify_capture(self, *, text, person_names, existing_tags,
                               images=None, user_hint=None,
                               initial_classification=None,
                               extract_action_items=False,
                               current_list="", write_in=None,
                               kind="note", clean_words=False, classify_only=False):
        # Recorded so a test can assert the classifier was shown the list it
        # is about to add to; extracting blind is what grew one list from
        # thirteen items to twenty-seven.
        self.saw_current_list = current_list
        self.saw_existing_tags = existing_tags
        self.saw_text = text
        self.saw_user_hint = user_hint
        self.saw_write_in = write_in
        self.saw_extract_action_items = extract_action_items
        self.saw_kind = kind
        self.saw_images = images
        self.saw_clean_words = clean_words
        self.saw_classify_only = classify_only
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

    async def card_history(self, path, card_id):
        """No history read: the note is refiled from the card as it reads now."""
        return None

    async def publish_capture(self, **kwargs):
        self.captures.append(kwargs)
        # Mirrors the publish_capture contract: where the capture landed
        # and its card id (or None on failure). Deterministic, so the
        # envelope-emission branch in _publish executes.
        kind = kwargs.get("kind") or "bookmark"
        entity = "homer"
        return Filed(f"{entity}/{kind}s/test-capture.md", f"{kind}-7f3a9c21e4")

    async def publish_email_message(self, **kwargs):
        # Email folds into a thread file via its own mirror entrypoint;
        # the path is keyed by the thread, not the individual message.
        self.emails.append(kwargs)
        entity = kwargs.get("entity") or "homer"
        return Filed(f"{entity}/emails/test-thread.md", "email-0b6e2d91aa")

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
              llm=None, text_extractor=None, url_extractor=None, vault=None):
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
    """`_tag_list` is the classifier's free-form tags, normalising types and
    whitespace. It names no person: the capture's `persons` does."""

    def test_tags_without_person_tags(self):
        c = {"tags": ["AI", "Productivity"], "persons": ["Homer"]}
        assert CapturePipeline._tag_list(c) == ["AI", "Productivity"]

    def test_single_string_tag_becomes_list(self):
        c = {"tags": "AI", "persons": ["Homer"]}
        assert CapturePipeline._tag_list(c) == ["AI"]

    def test_empty_classification_yields_empty(self):
        assert CapturePipeline._tag_list({}) == []

    def test_skips_non_string_values(self):
        c = {"tags": ["AI", None, 42, ""], "persons": ["Homer", ""]}
        assert CapturePipeline._tag_list(c) == ["AI"]

    def test_strips_whitespace(self):
        c = {"tags": ["  AI  ", "Productivity"], "persons": ["Homer"]}
        assert CapturePipeline._tag_list(c) == ["AI", "Productivity"]


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
        # Mirror gets the merged list.
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
        assert tags == ["van-life", "gear", "vans"]

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
            frontmatter=fm, kind="bookmark",
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


class TestACorrectionReadsTheHeaderOrLeavesTheEntryAlone:
    """A correction rewrites an entry from its header: type, date, id.

    A header hand-edited into something the parser refuses would have
    to be guessed at, and the rewrite would file the capture under the
    guess. The entry is left as it is instead (and the log names it).
    """

    PATH = "marge/bookmarks/test-capture.md"

    def _entry(self) -> str:
        fm = capture_frontmatter(
            title="Schulkalender", captured_at="2026-03-14", kind="bookmark",
            source_uri="https://school.example/kalender", persons=["Marge"],
            tags=["schule"], model=None, capture_id="$cal:home.local",
        )
        return render_capture(
            frontmatter=fm, kind="bookmark",
            captured_at="2026-03-14", source_uri="https://school.example/kalender",
            persons=["Marge"], from_path=self.PATH, shared_bucket="family",
            summary="The school calendar for March.", facts=[],
        )

    async def _reprocess(self, raw: str):
        mirror = FakeMirror()
        mirror._stored = {self.PATH: raw}
        outcome = await _pipeline(mirror=mirror).reprocess(
            vault_path=self.PATH, user_hint="das gehört zur Schule",
            sender_mxid="@marge:s",
        )
        return outcome, mirror.captures

    @pytest.mark.asyncio
    async def test_a_header_with_a_one_line_list_is_corrected(self):
        raw = self._entry().replace("---\n", "---\nreminders: [Elternabend, Ferien]\n", 1)
        outcome, published = await self._reprocess(raw)
        assert outcome.status == "reclassified"
        assert published[-1]["capture_id"] == "$cal:home.local"

    @pytest.mark.asyncio
    async def test_a_header_that_does_not_parse_leaves_the_entry_alone(self):
        raw = self._entry().replace("---\n", "---\nnote: Marge: bitte pruefen\n", 1)
        outcome, published = await self._reprocess(raw)
        assert outcome.status == "capture_missing"
        assert published == []


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
            kind="bookmark", captured_at="2026-03-14",
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
            kind="bookmark", captured_at="2026-10-13",
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
    """A correction names the capture it is about by the id it was filed
    with, the message's own event id, which never changes. Its path does:
    a refile used to rename it, and photos filed as bookmarks have moved
    to notes. So the capture is looked up by its id first, and the path
    the filing message recorded is only the fallback while the local
    copy of the family memory has not caught up."""

    OLD = "family/painting/bookmarks/2026/10/raku-454811.md"
    NEW = "family/painting/notes/2026/10/raku-454811.md"
    ID = "$img:home.test"

    def _entry(self, path: str) -> str:
        return _old_note("Raku", capture_id=self.ID, path=path, summary="Eine Schale.",
                         embed="/media/2026/10/img.webp", caption="Raku im Garten")

    async def _correct(self, tmp_path, monkeypatch, *, local: str | None):
        if local:
            (tmp_path / local).parent.mkdir(parents=True)
            (tmp_path / local).write_text(self._entry(local), encoding="utf-8")
        monkeypatch.setenv("MEMORY_VAULT_DIR", str(tmp_path))
        mirror = FakeMirror()
        mirror._stored = {self.NEW: self._entry(self.NEW)}
        out = await _pipeline(
            mirror=mirror, vault=VaultContext(language="de", shared_bucket="family"),
        ).reprocess(
            vault_path=self.OLD, capture_id=self.ID,
            user_hint="das ist Keramik", sender_mxid="@marge:s",
        )
        return out, mirror

    @pytest.mark.asyncio
    async def test_a_moved_capture_is_found_by_its_id(self, tmp_path, monkeypatch):
        out, mirror = await self._correct(tmp_path, monkeypatch, local=self.NEW)
        assert out.status == "reclassified"
        assert mirror.captures[-1]["existing_path"] == self.NEW

    @pytest.mark.asyncio
    async def test_a_note_is_found_by_the_id_its_sidecar_records(self, tmp_path, monkeypatch):
        """A note filed today keeps its source ids in its sidecar, not in
        its frontmatter (ADR-018)."""
        card = (tmp_path / self.NEW)
        card.parent.mkdir(parents=True)
        card.write_text("---\ntype: note\ncard_id: note-0b6e2d91aa\ntitle: Raku\n---\n\n# Raku\n")
        card.with_suffix(".json").write_text(json.dumps(
            {"card_id": "note-0b6e2d91aa", "sources": [{"role": "original", "id": self.ID}]}))
        monkeypatch.setenv("MEMORY_VAULT_DIR", str(tmp_path))
        vault = VaultContext(language="de", shared_bucket="family")
        assert vault.card_path("capture", self.ID) == self.NEW

    @pytest.mark.asyncio
    async def test_without_the_id_on_disk_the_recorded_path_is_tried(
        self, tmp_path, monkeypatch,
    ):
        """Nothing local carries the id, and the recorded path is gone:
        the correction says the capture is missing rather than guessing."""
        out, _ = await self._correct(tmp_path, monkeypatch, local=None)
        assert out.status == "capture_missing"


class TestACorrectionKeepsTheNotesOwnWords:
    """A note keeps the words its writer sent; the summary is the model's
    reading of them. A reply that corrects the note is read against those
    words, and the refiled note still holds them as they were sent. Read
    back from the summary instead, the note's words would be replaced by a
    paraphrase, and a short note, which carries no summary, by nothing."""

    PATH = "homer/notes/test-capture.md"
    WORDS = "Replace the tires on the Plymouth before winter.\n\nAsk Lenny for a dealer."

    def _note(self, summary: str = "") -> str:
        return _old_note("Winter tires", self.WORDS, capture_id="$note:s", path=self.PATH,
                         persons=["homer"], tags=["car"], summary=summary, filed_by="homer")

    async def _correct(self, raw: str):
        mirror, classifier = FakeMirror(), FakeClassifier()
        mirror._stored = {self.PATH: raw}
        await _pipeline(mirror=mirror, classifier=classifier).reprocess(
            vault_path=self.PATH, user_hint="it is the station wagon",
            sender_mxid="@homer:s",
        )
        return mirror.captures[-1], classifier

    @pytest.mark.asyncio
    async def test_the_corrected_note_holds_the_words_as_sent(self):
        published, _ = await self._correct(self._note(summary="Homer plans new tires."))
        assert published["sources"][0].words == self.WORDS

    @pytest.mark.asyncio
    async def test_the_model_reads_the_words_not_its_old_summary(self):
        _, classifier = await self._correct(self._note(summary="Homer plans new tires."))
        assert self.WORDS in classifier.saw_text
        assert "Homer plans new tires." not in classifier.saw_text

    @pytest.mark.asyncio
    async def test_a_note_without_a_summary_is_corrected_from_its_words(self):
        published, classifier = await self._correct(self._note())
        assert self.WORDS in classifier.saw_text
        assert published["sources"][0].words == self.WORDS


class TestANoteIsFiledAsACleanVersionOfItsWords:
    """A note in its writer's own words is filed as a clean version of
    them, then the words themselves. People jot notes down the way they
    type in a chat, and a summary of that says it again in other words
    (memory cards review, C1), so the card carries no summary and no
    facts, and its to-dos go to the topic's list only. What the model
    cleans is checked in code: a clean version that lost a number is not
    filed. A long paste keeps its summary, and so does a photo."""

    MESSY = ("ok so for the trip: homer needs to call the hotel abt the late checkin, "
             "bart forgot his swimsuit AGAIN buy one at the mall b4 sat, also lisa "
             "wants the museum thing on sunday not sat. budget max 200")
    CLEAN = ("For the trip:\n\n"
             "- Homer needs to call the hotel about the late check-in.\n"
             "- Bart forgot his swimsuit again; buy one at the mall before Saturday.\n"
             "- Lisa wants the museum visit on Sunday, not Saturday.\n"
             "- Budget: at most 200.")
    LONG = "The school newsletter lists the science fair, a field trip and a bake sale. " * 30
    ANSWER = {"title": "Trip to-dos", "tags": ["trip"], "persons": ["Homer"],
              "summary": "The family plans a trip.", "facts": ["Budget: 200"],
              "clean": CLEAN, "action_items": ["Call the hotel about the late check-in"]}

    async def _note(self, words: str, answer: dict | None = None):
        mirror = FakeMirror()
        classifier = FakeClassifier(payload=dict(answer or self.ANSWER))
        outcome = await _pipeline(
            mirror=mirror, classifier=classifier,
            text_extractor=FakeExtractor(_source(text=words))).capture_text(
            text=words, sender_mxid="@marge:s")
        return mirror.captures[0], classifier, outcome

    @staticmethod
    def _card(published: dict) -> str:
        """The file the mirror writes from what the pipeline published, as a card."""
        cls = published["classification"]
        path = "marge/notes/test-capture.md"
        text = render_note(
            frontmatter={"type": "note", "title": cls["title"], "tags": published["tags"]},
            sources=published["sources"], persons=cls["persons"], from_path=path,
            shared_bucket="family", summary=cls.get("summary"),
            facts=cls.get("facts"), action_items=cls.get("action_items"),
            clean=published.get("clean"),
        )
        return render_file(text).markdown

    @pytest.mark.asyncio
    async def test_the_model_is_asked_for_a_clean_version(self):
        _, classifier, _ = await self._note(self.MESSY)
        assert classifier.saw_clean_words is True

    @pytest.mark.asyncio
    async def test_the_card_shows_the_clean_version_then_the_words(self):
        published, _, _ = await self._note(self.MESSY)
        card = self._card(published)
        clean_at, words_at = card.index("- Homer needs to call"), card.index(f"> {self.MESSY}")
        assert clean_at < words_at
        assert card.count("\n- ") == 4, "an intro line and four list items"
        assert self.ANSWER["summary"] not in card
        assert "Budget: 200" not in card, "no facts"
        assert "To do" not in card, "the to-dos are on the topic's list, not the card"
        assert "\n\n\n" not in card, "no empty block"

    @pytest.mark.asyncio
    async def test_its_to_dos_still_reach_the_topics_list(self):
        published, classifier, _ = await self._note(self.MESSY)
        assert classifier.saw_extract_action_items is True
        assert published["classification"]["action_items"] == self.ANSWER["action_items"]

    @pytest.mark.asyncio
    async def test_a_note_already_clean_shows_its_words_once(self):
        words = "Lisa's saxophone lesson moved to Thursday at 4pm"
        published, _, _ = await self._note(words, {**self.ANSWER, "clean": words + "."})
        card = self._card(published)
        assert card.count("saxophone lesson moved") == 1
        assert card.rstrip().endswith(f"> {words}")

    @pytest.mark.asyncio
    async def test_a_clean_version_that_lost_a_number_is_not_filed(self):
        lossy = self.CLEAN.replace("- Budget: at most 200.", "- Keep to the budget.")
        published, _, outcome = await self._note(self.MESSY, {**self.ANSWER, "clean": lossy})
        card = self._card(published)
        assert "Keep to the budget" not in card
        assert card.rstrip().endswith(f"> {self.MESSY}"), "the words alone"
        assert "200" in outcome.clean_rejected

    @pytest.mark.asyncio
    async def test_a_long_paste_keeps_its_summary(self):
        published, classifier, _ = await self._note(self.LONG)
        assert classifier.saw_clean_words is False
        assert published["classification"]["summary"] == self.ANSWER["summary"]
        assert published.get("clean") is None

    @pytest.mark.asyncio
    async def test_a_photo_keeps_its_summary(self):
        mirror, classifier = FakeMirror(), FakeClassifier(payload=dict(self.ANSWER))
        await _pipeline(mirror=mirror, classifier=classifier).capture_binary(
            file_data=b"\x89PNG\r\n", mime="image/png", filename="IMG_1.png",
            source_uri="mxc://home.test/x", sender_mxid="@marge:s", caption="Lisa")
        assert classifier.saw_clean_words is False
        assert mirror.captures[0]["classification"]["summary"] == self.ANSWER["summary"]
        assert mirror.captures[0].get("clean") is None


class TestACorrectedNoteIsRefiledFromItsWords:
    """A correction refiles a note the way it is filed today: as a clean
    version of its words, here with the correction worked in by a prompt of
    its own that reads the words and the corrections alone; a note a person
    edited by hand is rebuilt as the card conversation instead. The classifier
    then reads that corrected text as the note, so its title and tags
    follow what the card shows; the entry's tags are offered as tags, and
    a topic's note keeps its topic."""

    PATH = "family/itchy-scratchy-land/notes/2026/10/trip-3a338e.md"
    WORDS = "homer calls the hotel thu, budget max 200"
    CORRECTED = "Homer calls the hotel on Friday. Budget: at most 200."
    ANSWER = {"title": "Trip", "tags": ["hotel"], "persons": ["Homer"]}

    async def _correct(self, corrected=CORRECTED):
        mirror = FakeMirror()
        classifier = FakeClassifier(payload=dict(self.ANSWER), corrected=corrected)
        mirror._stored = {self.PATH: _old_note(
            "Trip", self.WORDS, capture_id="$n:s", path=self.PATH, persons=["homer"],
            tags=["itchy-scratchy-land", "hotel"])}
        await _pipeline(mirror=mirror, classifier=classifier).reprocess(
            vault_path=self.PATH, user_hint="Marge: it is Friday, not Thursday",
            sender_mxid="@marge:s", reply_text="it is Friday, not Thursday")
        return mirror.captures[-1], classifier

    @pytest.mark.asyncio
    async def test_the_correction_is_worked_into_the_words_alone(self):
        _, classifier = await self._correct()
        assert classifier.saw_correction == (self.WORDS, "Marge: it is Friday, not Thursday")
        assert classifier.saw_rebuild is None

    @pytest.mark.asyncio
    async def test_an_edited_note_that_shows_no_clean_version_starts_from_the_one_written_today(self):
        """The model keeps the shape of the clean version it is given: a
        card that shows the words alone is given a tidy one first, by the
        capture prompt, and the conversation starts from that."""
        tidy = "Homer calls the hotel on Thursday. Budget: at most 200."
        mirror = FakeMirror()
        classifier = FakeClassifier(payload={**self.ANSWER, "clean": tidy}, corrected=self.CORRECTED)
        mirror._stored = {self.PATH: _old_note(
            "Trip", self.WORDS, capture_id="$n:s", path=self.PATH, persons=["homer"], tags=["hotel"])}
        edit = TextEdit("lisa", "2026-10-21T09:00:00+00:00", (("max 200", "max 220"),))
        await _pipeline(mirror=mirror, classifier=classifier).reprocess(
            vault_path=self.PATH, user_hint="", sender_mxid="@marge:s",
            reply_text="it is Friday, not Thursday", history=CardHistory(edits=(edit,)))
        assert classifier.saw_rebuild[1] == tidy
        assert classifier.saw_rebuild[2] == [edit.message(), "Marge: it is Friday, not Thursday"]

    @pytest.mark.asyncio
    async def test_a_reply_puts_a_hand_edited_quote_back_and_reads_the_edit_as_a_note(self):
        """A reply in the chat refiles a note by the rule `stack memory
        reprocess` follows (ADR-018 §4): the quote Lisa edited by hand, as
        the card's history shows it, goes back to the words as sent, and her
        words are her note, a correction before the reply."""
        edited = "Homer calls the hotel on Thursday, budget max 200"
        mirror = FakeMirror()
        classifier = FakeClassifier(payload=dict(self.ANSWER), corrected=self.CORRECTED)
        mirror._stored = {self.PATH: _old_note(
            "Trip", edited, capture_id="$n:s", path=self.PATH, persons=["homer"], tags=["hotel"])}
        note = Source(NOTE, "lisa", "2026-10-21T09:00:00+00:00", "cards", "text",
                      "cards:0a1b2c3d4e5f6a7b", quote=edited)
        await _pipeline(mirror=mirror, classifier=classifier).reprocess(
            vault_path=self.PATH, user_hint="", sender_mxid="@marge:s",
            reply_text="it is Friday, not Thursday",
            history=CardHistory(quotes_as_sent=(("$n:s", self.WORDS),), quote_edits=(note,)))
        assert [(s.role, s.person, s.words) for s in mirror.captures[-1]["sources"]] == [
            ("original", "marge", self.WORDS), ("note", "lisa", edited),
            ("reply", "marge", "it is Friday, not Thursday")]
        assert classifier.saw_correction == (
            self.WORDS, f"Lisa: {edited}\nMarge: it is Friday, not Thursday")

    @pytest.mark.asyncio
    async def test_the_classifier_reads_the_corrected_text_as_the_note(self):
        _, classifier = await self._correct()
        assert classifier.saw_text == self.CORRECTED
        assert (classifier.saw_classify_only, classifier.saw_clean_words) == (True, False)
        assert classifier.saw_user_hint is None
        assert "hotel" in classifier.saw_existing_tags

    @pytest.mark.asyncio
    async def test_the_note_keeps_its_topic_and_files_the_corrected_text(self):
        published, _ = await self._correct()
        assert published["tags"][0] == "itchy-scratchy-land"
        assert published["clean"] == self.CORRECTED
        assert published["made"]["correction_template"] == "c0e1c2d3"
        assert [(s.role, s.person, s.words) for s in published["sources"]] == [
            ("original", "marge", self.WORDS), ("reply", "marge", "it is Friday, not Thursday")]

    @pytest.mark.asyncio
    async def test_a_refused_corrected_text_leaves_the_words_and_the_correction_to_the_classifier(self):
        """When the corrected text lost the budget, the card shows the words
        alone, and the title and tags are read from the words with the
        correction beside them."""
        published, classifier = await self._correct(corrected="Homer calls the hotel on Friday.")
        assert published["clean"] is None
        assert classifier.saw_text == self.WORDS
        assert classifier.saw_user_hint == "Marge: it is Friday, not Thursday"


class TestABookmarkIsClassifiedAsOne:
    """A bookmark's summary says why it was kept, then what the page is,
    which the classifier can only do knowing it is reading a bookmark."""

    @pytest.mark.asyncio
    async def test_a_shared_link_reaches_the_classifier_as_a_bookmark(self):
        classifier = FakeClassifier()
        await _pipeline(classifier=classifier).capture_url(
            url="http://src", sender_mxid="@marge:s", notifier=FakeNotifier(),
            user_hint="for the trip in June")
        assert classifier.saw_kind == "bookmark"
        assert classifier.saw_clean_words is False


class TestANoteIsFiledWithItsOriginal:
    """A new note's one source is what was sent (ADR-018 §3): by whom,
    when, on which channel, under the channel's own id."""

    @pytest.mark.asyncio
    async def test_a_chat_message_keeps_its_event_id_and_its_time(self):
        mirror = FakeMirror()
        await _pipeline(mirror=mirror, text_extractor=FakeExtractor(_source(text="buy milk"))
                        ).capture_text(text="buy milk", sender_mxid="@marge:simpson",
                                       capture_id="$m1:simpson",
                                       sent_at="2026-10-21T08:55:00+00:00")
        [original] = mirror.captures[0]["sources"]
        assert original.record() == {"role": "original", "person": "marge",
                                     "time": "2026-10-21T08:55:00+00:00", "channel": "chat",
                                     "kind": "text", "id": "$m1:simpson"}
        assert original.quote == "buy milk"

    @pytest.mark.asyncio
    async def test_a_capture_from_the_command_line_gets_an_id_of_its_own(self):
        mirror = FakeMirror()
        await _pipeline(mirror=mirror, text_extractor=FakeExtractor(_source(text="buy milk"))
                        ).capture_text(text="buy milk", sender_mxid="homer")
        [original] = mirror.captures[0]["sources"]
        assert original.channel == "cli" and original.id.startswith("cli:")

    @pytest.mark.asyncio
    async def test_a_photo_is_embedded_in_its_quote_with_its_caption_and_its_hash(self):
        mirror = FakeMirror()
        await _pipeline(mirror=mirror).capture_binary(
            file_data=b"\x89PNG\r\n", mime="image/png", filename="IMG_1.png",
            source_uri="mxc://home.test/x", sender_mxid="@marge:s", caption="Bart drew this",
            capture_id="$p1:s",
            kept_media={"name": "IMG_1.png", "original": "/media/2026/10/p1.png",
                        "embed": "/media/2026/10/p1.jpg"})
        [original] = mirror.captures[0]["sources"]
        assert (original.kind, original.quote) == ("image", "![[/media/2026/10/p1.jpg]]\n\nBart drew this")
        assert original.media == "sha256:" + hashlib.sha256(b"\x89PNG\r\n").hexdigest()

    @pytest.mark.asyncio
    async def test_how_it_was_made_goes_with_it(self, monkeypatch):
        monkeypatch.setenv("STACK_COMMIT", "9a5bdc9")
        mirror = FakeMirror()
        await _pipeline(mirror=mirror, classifier=FakeClassifier(payload={
            "title": "Milk", "tags": ["milk"], "prompt_template": "3f9a1c2b7d4e"}),
            text_extractor=FakeExtractor(_source(text="buy milk"))).capture_text(
            text="buy milk", sender_mxid="@marge:s")
        made = mirror.captures[0]["made"]
        assert (made["code_commit"], made["prompt_template"], made["requested_by"]) == (
            "9a5bdc9", "3f9a1c2b7d4e", "marge")
        assert "prompt_template" not in mirror.captures[0]["classification"]


class TestANoteIsRefiledFromItsSources:
    """A refile reads the note's sources from its card and its sidecar,
    never its interpretation (ADR-018 §6). A reply is appended as a source
    with its event id and time; sources are only appended."""

    PATH = "family/trip/notes/2026/10/trip-3a338e.md"
    CARD = ("---\ntype: note\ncard_id: note-0b6e2d91aa\ntitle: Trip\ntags:\n  - hotel\n---\n\n"
            "# Trip\n\nHomer calls the hotel on Thursday.\n\n## Sources\n\n"
            "> homer calls the hotel thu\n\n> and my swimsuit\n")
    SIDECAR = json.dumps({"card_id": "note-0b6e2d91aa", "filed_by": "marge", "sources": [
        {"role": "original", "person": "marge", "time": "2026-10-20T08:00:00+00:00",
         "channel": "chat", "kind": "text", "id": "$n:s"},
        {"role": "reply", "person": "bart", "time": "2026-10-20T09:00:00+00:00",
         "channel": "chat", "kind": "text", "id": "$r1:s"}]})

    async def _correct(self, reply="it is Friday"):
        mirror, classifier = FakeMirror(), FakeClassifier(payload={"title": "Trip", "tags": []})
        mirror._stored = {self.PATH: self.CARD, self.PATH.removesuffix(".md") + ".json": self.SIDECAR}
        await _pipeline(mirror=mirror, classifier=classifier).reprocess(
            vault_path=self.PATH, user_hint="ignored", sender_mxid="@marge:s",
            reply_text=reply, reply_id="$r2:s", reply_at="2026-10-21T10:00:00+00:00")
        return mirror.captures[-1], classifier

    @pytest.mark.asyncio
    async def test_the_model_reads_the_words_and_the_replies_never_the_clean_version(self):
        _, classifier = await self._correct()
        assert classifier.saw_correction == (
            "homer calls the hotel thu", "Bart: and my swimsuit\nMarge: it is Friday")

    @pytest.mark.asyncio
    async def test_the_reply_is_appended_with_its_event_and_its_time(self):
        published, _ = await self._correct()
        assert [s.id for s in published["sources"]] == ["$n:s", "$r1:s", "$r2:s"]
        assert published["sources"][-1].time == "2026-10-21T10:00:00+00:00"
        assert published["made"]["requested_by"] == "marge"

    @pytest.mark.asyncio
    async def test_a_card_whose_quotes_and_sidecar_do_not_pair_is_left_alone(self):
        mirror = FakeMirror()
        mirror._stored = {self.PATH: self.CARD.replace("\n\n> and my swimsuit", ""),
                          self.PATH.removesuffix(".md") + ".json": self.SIDECAR}
        out = await _pipeline(mirror=mirror).reprocess(
            vault_path=self.PATH, user_hint="", sender_mxid="@marge:s", reply_text="x")
        assert out.status == "capture_missing"
        assert mirror.captures == []

    @pytest.mark.asyncio
    async def test_a_photo_is_looked_at_again_from_the_media_store(self, tmp_path, monkeypatch):
        (tmp_path / "2026" / "10").mkdir(parents=True)
        (tmp_path / "2026" / "10" / "p1.jpg").write_bytes(b"\xff\xd8\xff photo")
        monkeypatch.setenv("MEDIA_ARCHIVE_DIR", str(tmp_path))
        card = self.CARD.replace("> homer calls the hotel thu",
                                 "> ![[/media/2026/10/p1.jpg]]\n>\n> Bart drew this")
        sidecar = self.SIDECAR.replace('"kind": "text", "id": "$n:s"', '"kind": "image", "id": "$n:s"')
        mirror, classifier = FakeMirror(), FakeClassifier(payload={"title": "Drawing", "tags": []})
        mirror._stored = {self.PATH: card, self.PATH.removesuffix(".md") + ".json": sidecar}
        await _pipeline(mirror=mirror, classifier=classifier).reprocess(
            vault_path=self.PATH, user_hint="", sender_mxid="@marge:s")
        assert classifier.saw_text == "Bart drew this"
        assert [i.data for i in classifier.saw_images] == [b"\xff\xd8\xff photo"]
