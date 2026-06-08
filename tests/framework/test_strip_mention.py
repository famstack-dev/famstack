"""Unit tests for `MicroBot.strip_mention`.

The bot's at-mention can land in the body two ways depending on the
client: as the raw mxid (legacy) or as the display name with the mxid
hidden in an HTML anchor inside `formatted_body` (modern Matrix —
Element X, Element-web). `strip_mention` must recover the user's
actual query in both cases.
"""

from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent.parent
           / "stacklets" / "core" / "bot-runner"),
)

from microbot import MicroBot  # noqa: E402


BOT = "@archivist-bot:simpson"


# ── Path 1: raw mxid in body ────────────────────────────────────────────


class TestMxidInBody:

    def test_mxid_prefix_with_query(self):
        assert MicroBot.strip_mention(
            f"{BOT} find MLX", BOT,
        ) == "find MLX"

    def test_mxid_prefix_with_colon(self):
        assert MicroBot.strip_mention(
            f"{BOT}: find MLX", BOT,
        ) == "find MLX"

    def test_mxid_prefix_with_comma(self):
        assert MicroBot.strip_mention(
            f"{BOT}, find MLX", BOT,
        ) == "find MLX"

    def test_mxid_alone_returns_empty(self):
        assert MicroBot.strip_mention(BOT, BOT) == ""

    def test_no_mention_passthrough(self):
        assert MicroBot.strip_mention("just a sentence", BOT) == "just a sentence"


# ── Path 2: display-name mention in body + mxid in formatted_body ──────


class TestDisplayNameInBody:
    """Element X / Element-web shape: `body` reads naturally with the
    display name, the structural mxid only lives in HTML."""

    def _html(self, display: str) -> str:
        return (
            f'<a href="https://matrix.to/#/{BOT}">{display}</a>'
        )

    def test_display_name_with_colon(self):
        body = "Archivist: find MLX"
        formatted = self._html("Archivist") + " find MLX"
        assert MicroBot.strip_mention(
            body, BOT, formatted_body=formatted,
        ) == "find MLX"

    def test_display_name_with_comma(self):
        body = "Archivist, search for ADAC"
        formatted = self._html("Archivist") + ", search for ADAC"
        assert MicroBot.strip_mention(
            body, BOT, formatted_body=formatted,
        ) == "search for ADAC"

    def test_display_name_bare(self):
        body = "Archivist do the thing"
        formatted = self._html("Archivist") + " do the thing"
        assert MicroBot.strip_mention(
            body, BOT, formatted_body=formatted,
        ) == "do the thing"

    def test_display_name_alone(self):
        body = "Archivist"
        formatted = self._html("Archivist")
        assert MicroBot.strip_mention(
            body, BOT, formatted_body=formatted,
        ) == ""


# ── Edge cases ─────────────────────────────────────────────────────────


class TestEdgeCases:

    def test_empty_body(self):
        assert MicroBot.strip_mention("", BOT) == ""

    def test_neither_mxid_nor_anchor_passthrough(self):
        # A user happens to mention "Archivist" in chat as a regular
        # word; without an m.mentions anchor or the literal mxid we
        # leave the body alone -- guessing would corrupt real queries.
        body = "Archivist is a good name for a bot"
        assert MicroBot.strip_mention(body, BOT) == body

    def test_formatted_body_without_bot_anchor_passthrough(self):
        body = "Some text"
        formatted = '<a href="https://example.com">link</a> text'
        assert MicroBot.strip_mention(
            body, BOT, formatted_body=formatted,
        ) == body
