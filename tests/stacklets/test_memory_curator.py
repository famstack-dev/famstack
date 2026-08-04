"""Curator — the pure decision logic behind the wiki freshness loop.

The sidecar's job is mostly plumbing (git reads, one subprocess call);
what's worth pinning is the logic that keeps it from misfiring: the
own-commit filter that stops a rebuild from triggering itself, the
debounce that turns a 25-document ingest into one rebuild instead of
25, the member/topic selection that keeps the incremental pass bounded,
and the nightly once-per-day gate. All pure and tested
here; the end-to-end path rides the integration rig like the rest of
the wiki.

The one exception is `rebuild`, the single subprocess call. It is not
pure, but the loop that calls it has no exception handling of its own,
so what it does when the child misbehaves is the sidecar's survival and
is pinned here too.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory" / "bot"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory" / "bot" / "cli"))

import curator  # noqa: E402
from curator import (  # noqa: E402
    Debounce,
    diff_to_fileops,
    is_source_path,
    member_selection,
    nightly_due,
    only_own_commits,
    reconcile_fileops,
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

    def test_a_failed_rebuild_waits_longer_than_the_quiet_window(self):
        d = Debounce(quiet_secs=180)
        d.observe("aaa", now=1000.0)
        assert d.observe("aaa", now=1180.0) is True
        d.retry_later(now=1200.0)
        # One quiet window on its own would have fired again at 1380.
        assert d.observe("aaa", now=1380.0) is False
        assert d.observe("aaa", now=1560.0) is True

    def test_each_consecutive_failure_widens_the_window(self):
        # A rebuild fails for a reason that usually outlives one window —
        # the AI endpoint is down. Retrying on the quiet window forever
        # spends a subprocess and an LLM call every three minutes, all
        # day, and reads in the log exactly like a healthy heartbeat.
        d = Debounce(quiet_secs=180)
        d.observe("aaa", now=0.0)
        d.retry_later(now=0.0)
        assert d.observe("aaa", now=359.0) is False
        assert d.observe("aaa", now=360.0) is True
        d.retry_later(now=360.0)
        assert d.observe("aaa", now=1079.0) is False
        assert d.observe("aaa", now=1080.0) is True

    def test_backoff_stops_widening_at_the_cap(self):
        # A ceiling keeps a days-long outage retrying on a human
        # timescale rather than drifting out to never.
        d = Debounce(quiet_secs=180, max_backoff_secs=900)
        d.observe("aaa", now=0.0)
        for _ in range(10):
            d.retry_later(now=0.0)
        assert d.observe("aaa", now=899.0) is False
        assert d.observe("aaa", now=900.0) is True

    def test_a_new_head_does_not_clear_the_backoff(self):
        # Fresh commits keep arriving while the endpoint is down. They
        # are not evidence that generation works again, so they must not
        # reset the gap back to one quiet window.
        d = Debounce(quiet_secs=180)
        d.observe("aaa", now=0.0)
        d.retry_later(now=0.0)
        assert d.observe("bbb", now=100.0) is False
        assert d.observe("bbb", now=459.0) is False
        assert d.observe("bbb", now=460.0) is True

    def test_a_successful_rebuild_clears_the_backoff(self):
        d = Debounce(quiet_secs=180)
        d.observe("aaa", now=0.0)
        d.retry_later(now=0.0)
        d.retry_later(now=360.0)
        d.reset()                       # what the loop does on success
        assert d.observe("bbb", now=1000.0) is False
        assert d.observe("bbb", now=1180.0) is True

    def test_the_window_it_is_waiting_on_is_reportable(self):
        # The loop logs this, so an operator reading `docker logs` sees
        # "retrying in 12m after 3 failures" instead of a silent cycle.
        d = Debounce(quiet_secs=180)
        assert d.window() == 180
        d.retry_later(now=0.0)
        assert d.window() == 360
        assert d.failures == 1


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

    def test_shared_topic_capture_selects_topic_page(self):
        paths = ["family/camping/notes/2026/06/checklist-a1.md"]
        assert member_selection(paths, _fm({}), shared_bucket="family") == [
            "--home", "--topic", "camping",
        ]

    def test_personal_topic_capture_selects_owner_and_topic_page(self):
        paths = ["homer/gravel/notes/2026/06/training-a1.md"]
        assert member_selection(paths, _fm({}), shared_bucket="family") == [
            "--home", "--member", "homer", "--topic", "gravel",
        ]

    def test_generated_pages_never_trigger(self):
        # The wiki's own output (or a hand edit around the splice
        # markers) must not feed back into a rebuild.
        paths = ["index.md", "homer/about.md", "family/camping/about.md"]
        reader = _fm({p: {"generated": "true"} for p in paths})
        assert member_selection(paths, reader, shared_bucket="family") == []

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


# ── is_source_path ─────────────────────────────────────────────────────────


class TestIsSourcePath:
    """The mirror replays only source paths to brain. Generated page
    markers and git internals are excluded so generation's own output
    is never treated as source to copy."""

    def test_captures_are_source(self):
        assert is_source_path("family/documents/2026/06/p7.md") is True
        assert is_source_path("homer/notes/2026/06/a-1.md") is True
        assert is_source_path("ontology.toml") is True

    def test_generated_marker_excluded(self):
        assert is_source_path("homer/about.md", {"generated": "true"}) is False
        assert is_source_path("family/camping/notes/index.md", {"generated": True}) is False
        assert is_source_path("index.md", {"generated": "true"}) is False

    def test_unmarked_about_page_is_source(self):
        assert is_source_path("homer/about.md", {}) is True
        assert is_source_path("family/camping/about.md", {}) is True

    def test_git_internals_excluded(self):
        assert is_source_path(".git/config") is False

    def test_empty_path_excluded(self):
        assert is_source_path("") is False


# ── diff_to_fileops ────────────────────────────────────────────────────────


class TestDiffToFileops:
    """`git diff --name-status -M` maps to brain file operations: A/M/C/T
    copy in, D removes, R is rm-old + copy-new."""

    def test_add_and_modify_copy_in(self):
        ops = diff_to_fileops([
            "A\tfamily/documents/2026/06/new-p1.md",
            "M\thomer/notes/2026/06/edited-a1.md",
        ])
        assert ops == [
            ("copy", "family/documents/2026/06/new-p1.md",
             "family/documents/2026/06/new-p1.md"),
            ("copy", "homer/notes/2026/06/edited-a1.md",
             "homer/notes/2026/06/edited-a1.md"),
        ]

    def test_delete_removes(self):
        ops = diff_to_fileops(["D\tmarge/notes/2026/05/gone-b2.md"])
        assert ops == [("rm", "marge/notes/2026/05/gone-b2.md", "")]

    def test_rename_is_rm_old_then_copy_new(self):
        ops = diff_to_fileops([
            "R096\thomer/notes/2026/06/old-slug-a1.md\thomer/notes/2026/06/new-slug-a1.md",
        ])
        assert ops == [
            ("rm", "homer/notes/2026/06/old-slug-a1.md", ""),
            ("copy", "homer/notes/2026/06/new-slug-a1.md",
             "homer/notes/2026/06/new-slug-a1.md"),
        ]

    def test_copy_status_treated_as_add(self):
        ops = diff_to_fileops(["C075\tsrc/a.md\tfamily/documents/2026/06/c.md"])
        assert ops == [
            ("rm", "src/a.md", ""),
            ("copy", "family/documents/2026/06/c.md",
             "family/documents/2026/06/c.md"),
        ]

    def test_type_change_treated_as_modify(self):
        ops = diff_to_fileops(["T\tfamily/documents/2026/06/d.md"])
        assert ops == [
            ("copy", "family/documents/2026/06/d.md",
             "family/documents/2026/06/d.md"),
        ]

    def test_generated_page_delete_in_diff_uses_filename_backstop(self):
        # Delete and rename paths cannot read frontmatter from a gone
        # file, so the legacy generated-name backstop stays authoritative
        # for removal operations.
        ops = diff_to_fileops(["D\thomer/about.md"])
        assert ops == []

    def test_unmarked_about_page_add_is_mirrored(self):
        # A hand-written about.md in memory is source. Generation sees it
        # in brain and must leave it alone.
        ops = diff_to_fileops(["A\thomer/about.md"])
        assert ops == [("copy", "homer/about.md", "homer/about.md")]

    def test_rename_to_unmarked_about_page_copies_source(self):
        # Filename alone is not generated for a live file. A hand-written
        # about.md is source, so the rename copies the new path.
        ops = diff_to_fileops([
            "R100\thomer/notes/2026/06/a-1.md\thomer/about.md",
        ])
        assert ops == [
            ("rm", "homer/notes/2026/06/a-1.md", ""),
            ("copy", "homer/about.md", "homer/about.md"),
        ]

    def test_rename_to_marked_generated_page_degrades_to_delete(self):
        ops = diff_to_fileops(
            ["R100\thomer/notes/2026/06/a-1.md\thomer/about.md"],
            _fm({"homer/about.md": {"generated": "true"}}),
        )
        assert ops == [("rm", "homer/notes/2026/06/a-1.md", "")]

    def test_rename_of_generated_page_is_still_dropped(self):
        ops = diff_to_fileops(
            ["R100\thomer/about.md\tmarge/about.md"],
            _fm({"marge/about.md": {"generated": "true"}}),
        )
        assert ops == []

    def test_blank_and_malformed_lines_skipped(self):
        ops = diff_to_fileops(["", "  ", "A", "R096\tonly-one-field"])
        assert ops == []


# ── reconcile_fileops ──────────────────────────────────────────────────────


class TestReconcileFileops:
    """The nightly self-heal: brain's source files exactly match
    memory's. Every memory source file is copied; brain source files
    memory no longer has are removed. Generated pages are never touched."""

    def test_copies_all_memory_and_removes_orphans(self):
        memory = [
            "family/documents/2026/06/p1.md",
            "homer/notes/2026/06/a-1.md",
        ]
        brain = [
            "family/documents/2026/06/p1.md",   # in sync
            "marge/notes/2026/05/stale-b2.md",  # memory dropped it
        ]
        ops = reconcile_fileops(memory, brain)
        assert ("copy", "family/documents/2026/06/p1.md",
                "family/documents/2026/06/p1.md") in ops
        assert ("copy", "homer/notes/2026/06/a-1.md",
                "homer/notes/2026/06/a-1.md") in ops
        assert ("rm", "marge/notes/2026/05/stale-b2.md", "") in ops

    def test_generated_pages_in_brain_are_left_alone(self):
        # about.md / index.md live only in brain (generation owns them).
        # Reconcile must not remove them as orphans.
        memory = ["family/documents/2026/06/p1.md"]
        brain = ["homer/about.md", "family/camping/notes/index.md"]
        ops = reconcile_fileops(
            memory, brain, None,
            _fm({p: {"generated": "true"} for p in brain}),
        )
        rm_paths = [p for (a, p, _s) in ops if a == "rm"]
        assert "homer/about.md" not in rm_paths
        assert "family/camping/notes/index.md" not in rm_paths

    def test_already_in_sync_only_recopies_source(self):
        memory = ["ontology.toml"]
        brain = ["ontology.toml"]
        ops = reconcile_fileops(memory, brain)
        assert ops == [("copy", "ontology.toml", "ontology.toml")]


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


# ── Mirror-now trigger ────────────────────────────────────────────────

from curator import TRIGGER_NAME, consume_trigger, sleep_until_tick  # noqa: E402


class TestConsumeTrigger:
    def test_consumes_and_reports_pending_trigger(self, tmp_path):
        (tmp_path / TRIGGER_NAME).write_text("now", encoding="utf-8")
        assert consume_trigger(tmp_path) is True
        assert not (tmp_path / TRIGGER_NAME).exists()

    def test_no_trigger_is_false(self, tmp_path):
        assert consume_trigger(tmp_path) is False


class TestSleepUntilTick:
    async def test_pending_trigger_wakes_immediately(self, tmp_path):
        (tmp_path / TRIGGER_NAME).write_text("now", encoding="utf-8")
        start = time.monotonic()
        assert await sleep_until_tick(5.0, tmp_path, slice_secs=0.02) is True
        assert time.monotonic() - start < 1.0
        assert not (tmp_path / TRIGGER_NAME).exists()

    async def test_times_out_quietly_without_trigger(self, tmp_path):
        assert await sleep_until_tick(0.05, tmp_path, slice_secs=0.01) is False

    async def test_trigger_landing_mid_sleep_wakes_early(self, tmp_path):
        import asyncio

        async def drop():
            await asyncio.sleep(0.05)
            (tmp_path / TRIGGER_NAME).write_text("now", encoding="utf-8")

        start = time.monotonic()
        drop_task = asyncio.get_event_loop().create_task(drop())
        woke = await sleep_until_tick(5.0, tmp_path, slice_secs=0.02)
        await drop_task
        assert woke is True
        assert time.monotonic() - start < 1.0


# ── rebuild: the one subprocess call ─────────────────────────────────────
#
# `main()` calls this straight from its `while True` with nothing wrapped
# around it, so `rebuild` owes the loop a bool no matter how the child
# behaves. Two ways it can fail on the way in: the generation wedges (an
# LLM call that never returns), or the child never starts at all. Both
# have to come back False with the sidecar still running, and a wedged
# child has to actually die rather than be left behind holding the vault.


class TestRebuild:
    async def test_a_wedged_generation_is_killed_not_abandoned(
        self, tmp_path, monkeypatch,
    ):
        marker = tmp_path / "child-ran-to-completion"
        script = tmp_path / "hang.py"
        script.write_text(
            "import time\n"
            "time.sleep(1.0)\n"
            f"open({str(marker)!r}, 'w').close()\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(curator, "ENTRYPOINT", str(script))
        monkeypatch.setattr(curator, "REBUILD_TIMEOUT_SECS", 0.3)

        start = time.monotonic()
        assert await curator.rebuild([]) is False
        # Back on the timeout, not on the child's own schedule.
        assert time.monotonic() - start < 1.0

        # Past when the child would have finished had it survived the kill.
        await asyncio.sleep(1.2)
        assert not marker.exists()

    async def test_a_child_that_cannot_start_is_false_not_a_crash(
        self, monkeypatch,
    ):
        monkeypatch.setattr(curator.sys, "executable", "/nonexistent/python")
        assert await curator.rebuild([]) is False
