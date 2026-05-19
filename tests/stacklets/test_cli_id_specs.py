"""ID-spec parser for docs CLI commands.

The `reprocess` (and future `mirror`) commands accept either single
integer ids or inclusive ranges like `1-13`. The shared parser handles
the lexical layer — bad input here is a CLI usage error, missing ids
are handled later by the command itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BOT_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "stacklets" / "docs" / "bot"
)
sys.path.insert(0, str(_BOT_DIR))

from cli._shared import parse_id_specs  # noqa: E402


class TestSingleIds:
    """Bare integers pass through unchanged, in argv order."""

    def test_one_id(self):
        assert parse_id_specs(["42"]) == [42]

    def test_multiple_ids_preserve_order(self):
        assert parse_id_specs(["7", "3", "11"]) == [7, 3, 11]

    def test_zero_is_accepted(self):
        # Paperless ids start at 1, but the parser is a lexical layer.
        # Treat 0 as a valid integer; the caller's get_doc lookup
        # decides whether the id exists.
        assert parse_id_specs(["0"]) == [0]


class TestRanges:
    """`N-M` expands inclusively, ascending only."""

    def test_inclusive_range(self):
        assert parse_id_specs(["1-5"]) == [1, 2, 3, 4, 5]

    def test_single_element_range(self):
        # `7-7` is degenerate but valid — still ascending, still inclusive.
        assert parse_id_specs(["7-7"]) == [7]

    def test_range_combined_with_singles(self):
        assert parse_id_specs(["1", "3-5", "9"]) == [1, 3, 4, 5, 9]

    def test_multiple_ranges(self):
        assert parse_id_specs(["1-3", "10-12"]) == [1, 2, 3, 10, 11, 12]


class TestDedup:
    """Overlapping inputs collapse to a single, order-preserving list."""

    def test_dedup_explicit_duplicates(self):
        assert parse_id_specs(["5", "5", "5"]) == [5]

    def test_dedup_range_against_single(self):
        # `5` first, then `1-10` — 5 should not appear twice.
        assert parse_id_specs(["5", "1-10"]) == [5, 1, 2, 3, 4, 6, 7, 8, 9, 10]

    def test_overlapping_ranges(self):
        assert parse_id_specs(["1-5", "3-7"]) == [1, 2, 3, 4, 5, 6, 7]


class TestErrors:
    """Bad input returns None so the caller can exit with a usage code.

    The parser doesn't print errors itself — the CLI command does — but
    None is the unambiguous signal. Callers check `is None` before use.
    """

    @pytest.mark.parametrize("bad", [
        "abc",                  # non-numeric
        "1-abc",                # bad range upper
        "abc-5",                # bad range lower
        "1-",                   # missing upper
        "-5",                   # missing lower
        "1-2-3",                # too many parts
        "",                     # empty token
        " ",                    # whitespace
    ])
    def test_returns_none_on_garbage(self, bad):
        assert parse_id_specs([bad]) is None

    def test_descending_range_rejected(self):
        # `13-1` could be auto-flipped, but a typo is more likely than
        # intent. Reject explicitly so the user re-types.
        assert parse_id_specs(["13-1"]) is None

    def test_one_bad_token_fails_the_whole_parse(self):
        # Even if other tokens are valid, a single garbage token taints
        # the run — better to fail loudly than to half-process.
        assert parse_id_specs(["1", "abc", "5"]) is None


# ── --msg extraction ──────────────────────────────────────────────────────

class TestExtractMsg:
    """The reprocess CLI accepts an optional `--msg "text"` hint that
    rides into the classifier prompt as a User clarification block
    (same lever as the Matrix reply-to-reprocess flow)."""

    @staticmethod
    def _extract(argv):
        from cli.reprocess import _extract_msg
        return _extract_msg(argv)

    def test_extracts_msg_value(self):
        rest, msg = self._extract(["7", "--msg", "Urlaub Feb 2026"])
        assert rest == ["7"]
        assert msg == "Urlaub Feb 2026"

    def test_no_msg_returns_argv_unchanged(self):
        rest, msg = self._extract(["7", "--no-reformat"])
        assert rest == ["7", "--no-reformat"]
        assert msg is None

    def test_msg_in_middle(self):
        # --msg can appear anywhere in argv; only its own value is
        # consumed, other flags survive.
        rest, msg = self._extract(["7", "--msg", "hint", "--dry"])
        assert rest == ["7", "--dry"]
        assert msg == "hint"

    def test_msg_with_equals(self):
        # `--msg=text` form lets shells that don't like long quoted
        # args still pass a hint.
        rest, msg = self._extract(["7", "--msg=urlaub"])
        assert rest == ["7"]
        assert msg == "urlaub"

    def test_trailing_msg_without_value(self):
        # --msg as the last arg with nothing after — caller's responsibility
        # to detect (msg None + --msg still in argv).
        rest, msg = self._extract(["7", "--msg"])
        assert "--msg" in rest
        assert msg is None
