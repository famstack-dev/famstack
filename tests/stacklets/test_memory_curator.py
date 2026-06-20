"""Curator — the pure decision logic behind the wiki freshness loop.

The sidecar's job is mostly plumbing (git reads, one subprocess call);
what's worth pinning is the logic that keeps it from misfiring: the
own-commit filter that stops a rebuild from triggering itself, the
debounce that turns a 25-document ingest into one rebuild instead of
25, the persons-only selection that keeps the incremental pass at 2-3
LLM calls, and the nightly once-per-day gate. All pure and tested
here; the end-to-end path rides the integration rig like the rest of
the wiki.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory" / "bot"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory" / "bot" / "cli"))

from curator import (  # noqa: E402
    Debounce,
    member_selection,
    nightly_due,
    only_own_commits,
)
from wiki import COMMIT_PREFIX  # noqa: E402


def _local(hhmm: str) -> time.struct_time:
    """A struct_time for today at HH:MM local — enough for nightly_due."""
    return time.strptime(f"2026-06-11 {hhmm}", "%Y-%m-%d %H:%M")


# ── only_own_commits ─────────────────────────────────────────────────────


class TestOnlyOwnCommits:
    def test_all_wiki_publishes_are_ours(self):
        subjects = [
            f"{COMMIT_PREFIX} the family wiki home page",
            f"{COMMIT_PREFIX} homer's wiki page",
            f"{COMMIT_PREFIX} family/camping topic page",
        ]
        assert only_own_commits(subjects) is True

    def test_a_filing_in_the_batch_means_rebuild(self):
        subjects = [
            f"{COMMIT_PREFIX} homer's wiki page",
            "learn: Car Insurance Renewal 2026",
        ]
        assert only_own_commits(subjects) is False

    def test_empty_range_is_not_ours(self):
        # HEAD moved but the log range came back empty — a history
        # rewrite. Must read as "rebuild", never as "skip".
        assert only_own_commits([]) is False

    def test_prefix_must_anchor_the_subject(self):
        assert only_own_commits([f"revert: {COMMIT_PREFIX} home page"]) is False


# ── Debounce ─────────────────────────────────────────────────────────────


class TestDebounce:
    def test_new_head_never_fires_immediately(self):
        d = Debounce(quiet_secs=180)
        assert d.observe("aaa", now=1000.0) is False

    def test_fires_after_quiet_window(self):
        d = Debounce(quiet_secs=180)
        assert d.observe("aaa", now=1000.0) is False
        assert d.observe("aaa", now=1100.0) is False
        assert d.observe("aaa", now=1180.0) is True

    def test_burst_restarts_the_window(self):
        # Each new commit during an ingest moves HEAD — the window
        # restarts and the rebuild waits for the burst to settle.
        d = Debounce(quiet_secs=180)
        assert d.observe("aaa", now=1000.0) is False
        assert d.observe("bbb", now=1170.0) is False
        assert d.observe("bbb", now=1340.0) is False
        assert d.observe("bbb", now=1350.0) is True

    def test_reset_forgets_the_head(self):
        d = Debounce(quiet_secs=180)
        d.observe("aaa", now=1000.0)
        d.reset()
        assert d.observe("aaa", now=2000.0) is False
        assert d.observe("aaa", now=2180.0) is True

    def test_retry_later_defers_a_failed_rebuild(self):
        d = Debounce(quiet_secs=180)
        d.observe("aaa", now=1000.0)
        assert d.observe("aaa", now=1180.0) is True
        d.retry_later(now=1200.0)
        assert d.observe("aaa", now=1300.0) is False
        assert d.observe("aaa", now=1380.0) is True


# ── member_selection ─────────────────────────────────────────────────────


def _fm(mapping):
    """fm_reader stub: path -> frontmatter dict."""
    return lambda path: mapping.get(path, {})


class TestMemberSelection:
    def test_document_with_persons_selects_their_pages(self):
        paths = ["family/documents/2026/06/2026-06-10-car-insurance-p7.md"]
        reader = _fm({paths[0]: {"persons": ["Homer Simpson", "Marge Simpson"]}})
        assert member_selection(paths, reader, shared_bucket="family") == [
            "--home",
            "--member", "Homer Simpson",
            "--member", "Marge Simpson",
        ]

    def test_personal_capture_selects_the_bucket_owner(self):
        paths = ["homer/notes/2026/06/duff-recipe-ab12.md"]
        assert member_selection(paths, _fm({}), shared_bucket="family") == [
            "--home", "--member", "homer",
        ]

    def test_generated_pages_never_trigger(self):
        # The wiki's own output (or a hand edit around the splice
        # markers) must not feed back into a rebuild.
        paths = ["index.md", "homer/about.md", "family/camping/about.md"]
        assert member_selection(paths, _fm({}), shared_bucket="family") == []

    def test_skipped_dirs_never_trigger(self):
        paths = ["wiki/config.json", ".obsidian/workspace.json", "private/x.md"]
        assert member_selection(paths, _fm({}), shared_bucket="family") == []

    def test_relevant_change_without_persons_still_refreshes_home(self):
        # A shared-bucket document with no persons frontmatter: home
        # aggregates it, member pages are untouched.
        paths = ["family/documents/2026/06/2026-06-10-electricity-p9.md"]
        assert member_selection(paths, _fm({}), shared_bucket="family") == ["--home"]

    def test_no_duplicate_members(self):
        paths = [
            "homer/notes/2026/06/a-1111.md",
            "homer/notes/2026/06/b-2222.md",
        ]
        reader = _fm({p: {"persons": ["Homer Simpson"]} for p in paths})
        sel = member_selection(paths, reader, shared_bucket="family")
        assert sel.count("--member") == 2  # homer (bucket) + Homer Simpson (fm)
        assert sel[0] == "--home"


# ── nightly_due ──────────────────────────────────────────────────────────


class TestNightlyDue:
    def test_due_after_configured_time(self):
        assert nightly_due("03:30", "", _local("03:31")) is True

    def test_not_due_before_configured_time(self):
        assert nightly_due("03:30", "", _local("02:59")) is False

    def test_runs_once_per_day(self):
        assert nightly_due("03:30", "2026-06-11", _local("04:00")) is False
        assert nightly_due("03:30", "2026-06-10", _local("04:00")) is True

    def test_empty_or_garbage_disables(self):
        assert nightly_due("", "", _local("12:00")) is False
        assert nightly_due("never", "", _local("12:00")) is False
        assert nightly_due("3x:99y", "", _local("12:00")) is False
