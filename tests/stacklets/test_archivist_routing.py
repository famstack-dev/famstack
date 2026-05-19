"""Documents-room vs. capture-room routing in the archivist.

The archivist treats one room as the "documents" room — uploads + URLs
there flow into Paperless. Every other room is capture mode: URLs and
pasted text become summarized notes filed under the sender's own
entity bucket (`<sender>/notes/...` or `<sender>/bookmarks/...`), no
Paperless write.

These tests cover the pure routing predicate. The end-to-end flow
(extractor → classifier → mirror) is exercised by `test_extractors.py`
and `test_git_mirror.py`; here we pin that the predicate makes the
right call across the four interesting configurations.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "core" / "bot-runner"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs" / "bot"))

from archivist import ArchivistBot  # noqa: E402


# ── Helpers ──────────────────────────────────────────────────────────────

def _build_bot(tmp_path, **overrides) -> ArchivistBot:
    """Build an ArchivistBot without touching Matrix.

    Constructor reads env + settings only; no network/IO. The instance
    is fine for testing pure methods like `_normalize_alias` and
    `_is_capture_room`.
    """
    return ArchivistBot(
        homeserver="http://homeserver",
        user_id="@archivist-bot:server",
        password="x",
        session_dir=tmp_path,
        **overrides,
    )


# ── Tests ────────────────────────────────────────────────────────────────

class TestAliasNormalization:
    """`#documents:home.local` and `documents` should both match — the
    bot.toml setting carries just the local part and the canonical
    alias from the homeserver carries the full form."""

    def test_strips_hash_and_server(self):
        assert ArchivistBot._normalize_alias("#documents:home.local") == "documents"

    def test_local_only_passes_through(self):
        assert ArchivistBot._normalize_alias("documents") == "documents"

    def test_whitespace_stripped(self):
        assert ArchivistBot._normalize_alias("  documents  ") == "documents"

    def test_none_returns_empty(self):
        assert ArchivistBot._normalize_alias(None) == ""


class TestDefaultDocumentsAlias:
    """`documents_room_alias` defaults to "documents" so existing
    bot.toml configs need no edits — the alias matches `room =
    "documents"` already declared there."""

    def test_default_is_documents(self, tmp_path):
        bot = _build_bot(tmp_path)
        assert bot.documents_room_alias == "documents"

    def test_setting_overrides(self, tmp_path):
        bot = _build_bot(tmp_path, documents_room_alias="archive")
        assert bot.documents_room_alias == "archive"

    def test_setting_is_normalized(self, tmp_path):
        bot = _build_bot(tmp_path, documents_room_alias="#archive:home.local")
        assert bot.documents_room_alias == "archive"


class TestCaptureRoomPredicate:
    """`_is_capture_room` is the routing gate. The contract:
       - documents room id matches → False (Paperless path)
       - any other id → True (capture path)
       - no documents room found at startup → True everywhere
    """

    def test_documents_room_is_not_capture(self, tmp_path):
        bot = _build_bot(tmp_path)
        bot._documents_room_id = "!docs:server"
        assert bot._is_capture_room("!docs:server") is False

    def test_other_room_is_capture(self, tmp_path):
        bot = _build_bot(tmp_path)
        bot._documents_room_id = "!docs:server"
        assert bot._is_capture_room("!arthur-notes:server") is True
        assert bot._is_capture_room("!dm-with-bot:server") is True

    def test_unresolved_documents_room_defaults_to_capture(self, tmp_path):
        """If the bot can't find a room matching the documents alias —
        instance without Paperless, or alias renamed before joining —
        every room should fall through to capture mode. Failing closed
        to capture is the safer default: captures don't depend on a
        running Paperless."""
        bot = _build_bot(tmp_path)
        bot._documents_room_id = None
        assert bot._is_capture_room("!whatever:server") is True


class TestResolveDocumentsRoom:
    """`_resolve_documents_room` walks `self._client.rooms` at first
    sync and pins the room id matching the configured alias. This test
    pokes a fake `_client.rooms` dict — no Matrix needed — so we cover
    the lookup loop's match logic without a live homeserver."""

    def test_resolves_matching_room(self, tmp_path):
        bot = _build_bot(tmp_path)
        bot._client = SimpleNamespace(rooms={
            "!docs:server": SimpleNamespace(canonical_alias="#documents:server"),
            "!notes:server": SimpleNamespace(canonical_alias="#arthur-notes:server"),
        })
        bot._resolve_documents_room()
        assert bot._documents_room_id == "!docs:server"

    def test_no_match_leaves_id_none(self, tmp_path):
        bot = _build_bot(tmp_path)
        bot._client = SimpleNamespace(rooms={
            "!notes:server": SimpleNamespace(canonical_alias="#arthur-notes:server"),
        })
        bot._resolve_documents_room()
        assert bot._documents_room_id is None

    def test_handles_room_without_canonical_alias(self, tmp_path):
        """Some rooms (DMs especially) have no canonical alias. The
        loop must not crash when `canonical_alias` is None."""
        bot = _build_bot(tmp_path)
        bot._client = SimpleNamespace(rooms={
            "!dm:server": SimpleNamespace(canonical_alias=None),
            "!docs:server": SimpleNamespace(canonical_alias="#documents:server"),
        })
        bot._resolve_documents_room()
        assert bot._documents_room_id == "!docs:server"


class TestPastePredicate:
    """`_looks_like_paste` is the gate between "chat in a capture room"
    and "this is content to summarize and file." The heuristic is
    length-based: a paste is at least 100 stripped characters.

    Anything shorter is treated as conversation and ignored. This is
    a deliberate undershoot — users who want short notes captured
    will paste them with enough context to clear the threshold."""

    @pytest.mark.parametrize("text", [
        # A typical Reddit-style paste — multi-paragraph, multi-line.
        ("Came across this thread on local inference benchmarks.\n\n"
         "Top comment claims 60 tok/s on M2 Pro with 8B quantized.\n"
         "Source: https://reddit.com/r/LocalLLaMA/comments/xyz"),
        # A long single-line paste — no newline but well over 100 chars.
        "This is a long-form paste typed as one continuous line by a "
        "user who really wanted to capture this thought in full, " * 2,
    ])
    def test_recognizes_paste(self, text):
        assert ArchivistBot._looks_like_paste(text)

    @pytest.mark.parametrize("text", [
        "ok",
        "thanks!",
        "?",
        "what's the status",
        "hello bot",
        "scan",
        # Multi-line short reply — still chat-shaped.
        "yes\nno",
    ])
    def test_short_chat_not_paste(self, text):
        assert not ArchivistBot._looks_like_paste(text)

    def test_empty_not_paste(self):
        assert not ArchivistBot._looks_like_paste("")
        assert not ArchivistBot._looks_like_paste("   \n\n  ")


class TestCaptureTagList:
    """The mirror's `tags:` field: capture tags (free-form, from the
    classifier's `tags` field) + `Person: X` for each attributed
    person. Stable across rerun, easy to query via Dataview."""

    def test_tags_and_persons(self):
        c = {"tags": ["AI", "Productivity"], "persons": ["Arthur"]}
        assert ArchivistBot._capture_tag_list(c) == [
            "AI", "Productivity", "Person: Arthur",
        ]

    def test_single_string_tag_becomes_list(self):
        c = {"tags": "AI", "persons": ["Arthur"]}
        assert ArchivistBot._capture_tag_list(c) == ["AI", "Person: Arthur"]

    def test_empty_classification_yields_empty(self):
        assert ArchivistBot._capture_tag_list({}) == []

    def test_skips_non_string_values(self):
        c = {"tags": ["AI", None, 42, ""], "persons": ["Arthur", ""]}
        assert ArchivistBot._capture_tag_list(c) == ["AI", "Person: Arthur"]

    def test_strips_whitespace(self):
        # The classifier sometimes returns "  AI  " — leading/trailing
        # spaces are noise, not a distinct tag.
        c = {"tags": ["  AI  ", "Productivity"], "persons": ["Arthur"]}
        assert ArchivistBot._capture_tag_list(c) == [
            "AI", "Productivity", "Person: Arthur",
        ]


# ── Reply fallback stripping ──────────────────────────────────────────────

class TestStripReplyFallback:
    """Matrix injects a `>`-quoted fallback at the top of a reply body so
    clients without rich-reply support still see context. The bot wants
    only the user's actual text — what comes after the fallback."""

    def test_strips_single_line_quoted_fallback(self):
        from archivist import _strip_reply_fallback
        body = (
            "> <@bot:test.local> Filed: ADAC Kfz-Versicherung (#42)\n"
            "\n"
            "this is for Sabrina, not Homer"
        )
        assert _strip_reply_fallback(body) == "this is for Sabrina, not Homer"

    def test_strips_multi_line_quoted_fallback(self):
        from archivist import _strip_reply_fallback
        body = (
            "> <@bot:test.local> Filed: ADAC (#42)\n"
            "> \n"
            "> Insurance | Homer | Invoice | ADAC | 2026-03-15\n"
            "\n"
            "wrong year, it's actually 2025"
        )
        assert _strip_reply_fallback(body) == "wrong year, it's actually 2025"

    def test_no_quoted_block_returns_body_as_is(self):
        # A direct message without a reply — strip should leave it alone.
        from archivist import _strip_reply_fallback
        assert _strip_reply_fallback("plain question") == "plain question"

    def test_only_quoted_block_returns_empty(self):
        # If the body has no content after the fallback, the user's reply
        # is empty. Handler upstream should ignore an empty hint.
        from archivist import _strip_reply_fallback
        assert _strip_reply_fallback("> <@bot> Filed (#42)") == ""


# ── Vision-attach policy ──────────────────────────────────────────────────

class TestShouldAttachVision:
    """The single decision point for whether the archivist attaches
    rendered PDF pages alongside the OCR text. Policy is pure logic
    over three inputs so it's safe to unit-test without containers."""

    @staticmethod
    def _decide(**kw):
        from archivist import _should_attach_vision
        return _should_attach_vision(**kw)

    def test_scan_without_text_layer_attaches_vision(self):
        # No text layer → vision is the only signal. Render anything
        # available, regardless of length.
        assert self._decide(
            has_text_layer=False, has_ocr_text_layer=False, page_count=0,
        ) is True

    def test_native_text_short_skips_vision(self):
        # A 2-page generated invoice — trustworthy text layer, vision
        # would waste tokens.
        assert self._decide(
            has_text_layer=True, has_ocr_text_layer=False, page_count=2,
        ) is False

    def test_native_text_long_skips_vision(self):
        # A 30-page research paper — same reasoning, more emphasis.
        assert self._decide(
            has_text_layer=True, has_ocr_text_layer=False, page_count=30,
        ) is False

    def test_ocr_text_layer_short_attaches_vision(self):
        # A Booking.com / OCRmyPDF-routed scan — text layer is jumbled,
        # vision must override.
        assert self._decide(
            has_text_layer=True, has_ocr_text_layer=True, page_count=2,
        ) is True

    def test_ocr_text_layer_at_cap_still_attaches(self):
        # The cap is inclusive: exactly 5 pages → still vision.
        from archivist import _VISION_MAX_PDF_PAGES
        assert self._decide(
            has_text_layer=True, has_ocr_text_layer=True,
            page_count=_VISION_MAX_PDF_PAGES,
        ) is True

    def test_ocr_text_layer_long_skips_vision(self):
        # A 30-page contract re-OCR'd by Paperless — trust the (imperfect)
        # text layer rather than burn one image token per page.
        assert self._decide(
            has_text_layer=True, has_ocr_text_layer=True, page_count=30,
        ) is False
