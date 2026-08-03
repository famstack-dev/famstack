"""`stack memory capture` — the seam that lets a caller file into the vault.

Filing knowledge is a *memory* capability. Until now the only way to
reach it was to be the archivist reading a Matrix room, which left every
other caller (the agent, a script, a person at a terminal) with no way to
put anything in. This command is that way in.

The command noun lives in memory's namespace because memory owns the
vault; the handler still sits beside the pipeline under `docs/bot` for
now, so the two move together when the pipeline goes where it belongs.
These tests pin the *seam*, not that placement: the argument grammar a
caller writes and the receipt it reads back. Both survive the move.

Why the receipt matters enough to test: the agent relays it to the
family verbatim. A receipt that reads the same whether or not anything
was filed is exactly what lets an agent claim success it never had, so
the cases below pin that a failure never renders as a filing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs" / "bot"))

from capture_pipeline import CaptureOutcome  # noqa: E402

sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs" / "bot" / "cli"))
from capture import capture_kind, parse_args, render_receipt  # noqa: E402


class TestTheArgumentGrammar:
    """What a caller has to type, and what it is allowed to leave out."""

    def test_the_text_is_the_argument(self):
        spec = parse_args(["Bart has a peanut allergy", "--by", "homer"])

        assert spec.text == "Bart has a peanut allergy"
        assert spec.sender == "homer"

    def test_a_multi_word_body_survives_as_one_body(self):
        """The plaintext socket splits on shlex, so a quoted body arrives
        whole but an unquoted one arrives in pieces. Rejoining is the
        difference between filing a note and filing its first word."""
        spec = parse_args(["Liste:", "Heringe", "Kuehlbox", "--by", "marge"])

        assert spec.text == "Liste: Heringe Kuehlbox"

    def test_the_bucket_is_optional_and_means_the_sender(self):
        """No bucket is the common case: a person filing their own note.
        The pipeline routes it to their personal bucket from the sender."""
        assert parse_args(["a note", "--by", "homer"]).bucket is None

    def test_a_topic_bucket_routes_the_capture_to_the_topic(self):
        spec = parse_args(["a note", "--by", "homer",
                           "--bucket", "family/camping"])

        assert spec.bucket == "family/camping"

    def test_a_sender_may_be_given_as_a_full_mxid(self):
        """The agent knows people as `@homer:simpson`; a person at a
        terminal types `homer`. Both name the same human."""
        assert parse_args(["a note", "--by", "@homer:simpson"]).sender == "homer"

    def test_filing_as_nobody_is_refused(self):
        """Every vault write is attributed. A capture with no author
        would commit as a ghost, so it must not be expressible."""
        with pytest.raises(ValueError, match="--by"):
            parse_args(["a note"])

    def test_filing_nothing_is_refused(self):
        with pytest.raises(ValueError, match="nothing to capture"):
            parse_args(["--by", "homer"])

    def test_a_file_needs_no_text_beside_it(self):
        spec = parse_args(["--file", "/tmp/receipt.jpg", "--by", "homer"])

        assert spec.file == "/tmp/receipt.jpg"
        assert spec.text == ""

    def test_a_caption_on_a_file_is_refused_rather_than_dropped(self):
        """The pipeline reads a binary's meaning out of the bytes and has
        nowhere to put a caption. Accepting one would file the image and
        silently lose the words the caller thought they were filing."""
        with pytest.raises(ValueError, match="separately"):
            parse_args(["Rechnung vom Zeltladen", "--file", "/tmp/r.jpg",
                        "--by", "homer"])


class TestWhichShapeOfCaptureThisIs:
    """A link, an image and a note are three different filings."""

    def test_a_bare_url_is_fetched_as_a_bookmark(self):
        spec = parse_args(["https://example.com/tent-review", "--by", "homer"])

        assert capture_kind(spec) == "link"

    def test_a_file_is_read_as_a_binary(self):
        spec = parse_args(["--file", "/tmp/receipt.jpg", "--by", "homer"])

        assert capture_kind(spec) == "file"

    def test_prose_citing_a_link_stays_a_note(self):
        """Deliberate divergence from the archivist.

        Reading a room, a link buried in chatter usually *is* the point,
        so the archivist fetches it and treats the words as framing.
        A caller here wrote the sentence on purpose; fetching its source
        and filing that instead would discard what they said. The link
        still survives -- TextExtractor surfaces it as the note's link.
        """
        spec = parse_args(["Bart has a peanut allergy, see",
                           "https://example.com/allergies", "--by", "homer"])

        assert capture_kind(spec) == "note"


class TestTheReceipt:
    """What the caller reads back, and what the agent relays."""

    def test_a_filed_note_names_what_was_filed_and_where(self):
        receipt = render_receipt(CaptureOutcome(
            status="captured",
            classification={"title": "Packliste für Campingausflug"},
            vault_path="family/camping/notes/2026/08/packliste-277e6e.md",
            scope="family/camping",
        ))

        assert "Packliste für Campingausflug" in receipt
        assert "family/camping/notes/2026/08/packliste-277e6e.md" in receipt

    def test_a_filed_note_says_captured(self):
        """The agent is told to relay this word rather than invent its
        own. It has to actually be here to relay."""
        receipt = render_receipt(CaptureOutcome(
            status="captured", classification={"title": "T"},
            vault_path="homer/notes/2026/08/t.md", scope="homer",
        ))

        assert receipt.startswith("Captured:")

    @pytest.mark.parametrize("outcome,expected", [
        (CaptureOutcome(status="empty"), "nothing"),
        (CaptureOutcome(status="no_mirror"), "vault"),
        (CaptureOutcome(status="extract_failed", failure_reason="url"), "read"),
    ])
    def test_a_failure_never_reads_as_a_filing(self, outcome, expected):
        """The whole point. Whatever went wrong, the receipt must not
        contain the word a caller scans for to conclude it worked."""
        receipt = render_receipt(outcome)

        assert not receipt.startswith("Captured:")
        assert expected in receipt.lower()

    def test_an_untitled_capture_still_gets_a_readable_receipt(self):
        """The classifier degrades to `{}` when the LLM is down, and the
        capture still files. A receipt that crashed here would turn a
        working capture into a failed command."""
        receipt = render_receipt(CaptureOutcome(
            status="captured", classification={},
            vault_path="homer/notes/2026/08/untitled.md", scope="homer",
        ))

        assert "homer/notes/2026/08/untitled.md" in receipt
