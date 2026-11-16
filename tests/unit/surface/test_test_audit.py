"""`tools/test-audit` keeps mocks and patched code out of the tests.

The tool is the gate that stops new stand-ins for code from entering the test
suite while the listed files are cleaned up. These tests run it as a
subprocess over a small tree of fabricated test modules and read its exit
code and its output, as CI does.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parents[3] / "tools" / "test-audit"

CLEAN = "def test_a():\n    assert 1 + 1 == 2\n"
ONE_PATCH = "def test_a(monkeypatch):\n    monkeypatch.setattr('x.y', lambda: 1)\n"
TWO_PATCHES = ONE_PATCH + "    monkeypatch.setattr('x.z', lambda: 2)\n"
ENVIRONMENT_ONLY = "def test_a(monkeypatch, tmp_path):\n    monkeypatch.setenv('A', '1')\n    monkeypatch.chdir(tmp_path)\n"


def audit(tree: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(TOOL), "--root", str(tree), *args],
                          capture_output=True, text=True)


def tree_with(tmp_path: Path, source: str) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tools").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text(source)
    return tmp_path


def test_a_clean_tree_passes(tmp_path):
    assert audit(tree_with(tmp_path, CLEAN)).returncode == 0


def test_environment_changes_are_not_mocks(tmp_path):
    assert audit(tree_with(tmp_path, ENVIRONMENT_ONLY)).returncode == 0


def test_a_new_patch_fails_and_names_the_file(tmp_path):
    result = audit(tree_with(tmp_path, ONE_PATCH))

    assert result.returncode == 1
    assert "tests/test_a.py" in result.stdout


def test_a_listed_file_passes_until_it_gains_a_use(tmp_path):
    tree = tree_with(tmp_path, ONE_PATCH)
    audit(tree, "--write-baseline")
    assert audit(tree).returncode == 0

    (tree / "tests" / "test_a.py").write_text(TWO_PATCHES)

    assert audit(tree).returncode == 1


def test_a_fixed_file_fails_until_the_baseline_is_pruned(tmp_path):
    tree = tree_with(tmp_path, ONE_PATCH)
    audit(tree, "--write-baseline")
    (tree / "tests" / "test_a.py").write_text(CLEAN)

    assert audit(tree).returncode == 1
    audit(tree, "--prune")
    assert audit(tree).returncode == 0
