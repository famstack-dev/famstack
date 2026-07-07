"""Source-only memory to brain sync command."""

from __future__ import annotations

import sys
import importlib.util
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MEMORY_DIR = _REPO_ROOT / "stacklets" / "memory"
sys.path.insert(0, str(_MEMORY_DIR))

_SPEC = importlib.util.spec_from_file_location(
    "memory_cli_sync",
    _MEMORY_DIR / "cli" / "sync.py",
)
assert _SPEC and _SPEC.loader
memory_sync = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(memory_sync)

is_source_path = memory_sync.is_source_path
reconcile_source_files = memory_sync.reconcile_source_files


class TestSourcePath:
    def test_generated_pages_are_not_source(self):
        assert not is_source_path("family/camping/about.md")
        assert not is_source_path("family/camping/notes/index.md")

    def test_todos_are_source(self):
        assert is_source_path("family/camping/todos.md")


class TestReconcileSourceFiles:
    def test_copies_todos_and_leaves_generated_page(self, tmp_path):
        memory = tmp_path / "memory"
        brain = tmp_path / "brain"
        (memory / "family" / "camping").mkdir(parents=True)
        (brain / "family" / "camping").mkdir(parents=True)
        (memory / "family" / "camping" / "todos.md").write_text(
            "# Camping\n\n- [ ] book pitch\n",
            encoding="utf-8",
        )
        (brain / "family" / "camping" / "about.md").write_text(
            "<!-- begin: generated -->\nold\n<!-- end: generated -->\n",
            encoding="utf-8",
        )

        copied, removed = reconcile_source_files(
            memory, brain,
            ["family/camping/todos.md"],
            ["family/camping/about.md"],
        )

        assert copied == 1
        assert removed == 0
        assert (brain / "family" / "camping" / "todos.md").read_text(
            encoding="utf-8",
        ) == "# Camping\n\n- [ ] book pitch\n"
        assert (brain / "family" / "camping" / "about.md").exists()

    def test_removes_stale_source_file(self, tmp_path):
        memory = tmp_path / "memory"
        brain = tmp_path / "brain"
        (memory / "family").mkdir(parents=True)
        (brain / "family" / "old").mkdir(parents=True)
        (brain / "family" / "old" / "todos.md").write_text(
            "# Old\n",
            encoding="utf-8",
        )

        copied, removed = reconcile_source_files(
            memory, brain,
            [],
            ["family/old/todos.md"],
        )

        assert copied == 0
        assert removed == 1
        assert not (brain / "family" / "old" / "todos.md").exists()
