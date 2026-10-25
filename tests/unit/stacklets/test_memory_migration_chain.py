"""An instance updated from 0.4 comes through every memory migration.

Each migration has a test of its own, on a small vault in the shape it
expects. This one takes the memory repo of a real 0.4 instance through
all of them, in their numbered order, the way `stack up memory` runs
them after `stack update`. A migration added to the folder is part of
the chain without editing this file, so it has to pass on 0.4 data
before it lands.

The memory repo is `tests/fixtures/memory/memory-0.4.0.bundle`: a test
instance on the e2e laptop, installed as 0.3.0-beta.3 on 2026-10-19 and
updated to the 0.4.0 release candidate (`ea8e7c7`). 46 cards and 79
commits of fabricated Simpsons data, its history as the archivist, the
memory bot and the family wrote it. It is a git bundle because a bundle
holds the commits and nothing else: no remote with a token in it, no
reflog, no hooks. Before it was bundled the instance's LAN address was
replaced by `home.example.family` in every commit, and the admin's
`@home.local` address by `@home.example.family`.

The curator is the real one of `test_memory_card_links` in front of that
repository: each migration reads the curator's clone and commits through
the curator, so every migration reads what the one before it wrote.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(Path(__file__).parent))

from stacklets.memory.format_check import format_report  # noqa: E402
from stacklets.memory.graph import load_graph  # noqa: E402
from stack.card_render import render_file  # noqa: E402
from stack.cards import card_id_from  # noqa: E402
from stack.migrations import MigrationContext, Migrations  # noqa: E402
from test_memory_card_links import _commits, _git, _instance, curator# noqa: E402, F401

FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "memory" / "memory-0.4.0.bundle"
MEMORY_STACKLET = _REPO_ROOT / "stacklets" / "memory"

# What the format check still reports once every migration has run, by
# kind of finding, each with the reason no migration removes it yet. A
# change that removes one takes it off here. Empty on this fixture. The
# instance never kept a diary, so 0010 (diary cards tagged by topic id)
# finds nothing to change in it; `test_memory_diary_tags_by_id` has diary
# cards.
KNOWN_FINDINGS: dict[str, list] = {}


# ── The 0.4 memory repo on Forgejo ───────────────────────────────────────

@pytest.fixture
def memory(tmp_path) -> Path:
    """family/memory as the 0.4 instance left it."""
    repo = tmp_path / "forgejo" / "memory"
    subprocess.run(["git", "clone", "-q", "-b", "main", str(FIXTURE), str(repo)], check=True)
    _git(repo, "update-server-info")
    return repo


def _migrate(tmp_path: Path, curator, record: str) -> tuple[dict, list[str], Migrations]:  # noqa: F811
    """Every memory migration, run by the framework's runner as `stack up
    memory` runs it, with the record of what ran kept under `record`."""
    stck = _instance(tmp_path, curator)
    migrations = Migrations("memory", MEMORY_STACKLET, tmp_path / record / "memory")
    steps: list[str] = []
    outcome = migrations.run(
        lambda m: MigrationContext(m, stack=stck, stacklet_id="memory", env={}),
        steps.append)
    return outcome, steps, migrations


def _bots_before(memory: Path, before: str) -> int:
    """How many commits the bots had written by `before`: the 0.4 history."""
    return sum(1 for name in _git(memory, "log", "--format=%cn", before).splitlines()
               if name.endswith("-bot"))


def _failure(outcome: dict, steps: list[str]) -> str:
    """The migration that failed, its error, and what it said before it did:
    a migration that names the card it stopped at names it in one of them."""
    return "\n".join([*(f"{key} failed: {error}" for key, error in outcome["failed"].items()),
                      "", "What the migrations said:", *steps])


def _trailers(memory: Path, since: str) -> list[str]:
    """The `Migration:` trailer of each commit after `since`, oldest first;
    an empty string for a commit without one."""
    log = _git(memory, "log", "--reverse", "--format=%(trailers:key=Migration,valueonly)%x00",
               f"{since}..HEAD")
    return [entry.strip() for entry in log.split("\x00")[:-1]]


# ── The chain ─────────────────────────────────────────────────────────────

@pytest.fixture
def migrated(memory, curator, tmp_path):  # noqa: F811
    """The 0.4 memory after every migration ran once."""
    before = _git(memory, "rev-parse", "HEAD")
    outcome, steps, migrations = _migrate(tmp_path, curator, "data")
    assert "failed" not in outcome, _failure(outcome, steps)
    return before, outcome, migrations


def test_every_migration_runs_in_order_and_one_with_work_commits_once(migrated, memory):
    before, outcome, migrations = migrated
    shipped = [m.key for m in migrations.shipped()]
    assert outcome["applied"] == shipped

    trailers = _trailers(memory, before)
    assert "" not in trailers, "a commit without a Migration trailer"
    assert len(trailers) == len(set(trailers)), f"a migration committed twice: {trailers}"
    assert trailers == [key for key in shipped if key in trailers], "commits out of order"
    assert _commits(memory) - _bots_before(memory, before) == len(trailers)
    record = migrations.record().applied
    for m in migrations.shipped():
        if m.repo and m.key in trailers:
            assert record[m.number]["commit"] == _git(
                memory, "log", "-1", "--format=%H", f"--grep=^{m.trailer}$"), m.key


def test_the_migrated_memory_meets_the_format(migrated, memory):
    """What the card browser's gauge and every reader of a card rely on:
    the format check finds nothing new, each card renders under its own
    title, and each has an id no other card has."""
    graph = load_graph(memory)
    report = format_report(graph)
    assert {key: value for key, value in report.items()
            if key not in ("cards", "findings") and value} == KNOWN_FINDINGS

    holders: dict[str, list[str]] = {}
    for card in graph.cards.values():
        title = str(card.meta.get("title") or "").strip()
        assert title, f"{card.path}: no title"
        try:
            rendered = render_file((memory / card.path).read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001 - name the card that does not render
            pytest.fail(f"{card.path} does not render: {e!r}")
        assert rendered.markdown.startswith(f"# {title}\n"), card.path
        card_id = card_id_from(card.meta)
        assert card_id, f"{card.path}: no card_id"
        holders.setdefault(card_id, []).append(card.path)
    assert {cid: paths for cid, paths in holders.items() if len(paths) > 1} == {}


def test_run_again_changes_nothing_and_makes_no_commit(migrated, memory, curator, tmp_path):  # noqa: F811
    """As after restoring an older backup of the record: every migration
    runs again, on a memory it has migrated already."""
    head, commits = _git(memory, "rev-parse", "HEAD"), _commits(memory)
    outcome, steps, migrations = _migrate(tmp_path, curator, "restored")
    assert "failed" not in outcome, _failure(outcome, steps)
    assert outcome["applied"] == [m.key for m in migrations.shipped()]
    assert _commits(memory) == commits, _trailers(memory, head)
    assert _git(memory, "rev-parse", "HEAD") == head
    assert _git(memory, "status", "--porcelain") == ""
