"""The check that keeps code where it runs.

`tools/arch-check` places every file of a checkout as host, service or
framework code and fails on an import across those lines: a host command
importing its stacklet's service code, a stacklet importing another, the
framework importing a stacklet. Violations that predate it are listed in
a baseline that may only shrink.

Each test builds a small checkout with its own policy and runs the tool
on it the way `make lint` and CI do, then reads its exit code and what
it printed.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
TOOL = REPO_ROOT / "tools" / "arch-check"

POLICY = """\
[framework]
units = ["__init__", "cli", "output"]

[domain]
units = ["cards", "people"]

[runtime]
paths = ["stacklets/core/bot-runner/**"]

[host]
paths = ["stacklets/*/cli/**", "stacklets/*/hooks/**"]

[entrypoints]
paths = ["stacklets/memory/bot/curator.py"]

[packages]
paths = ["lib/stack/cards"]
adapters = ["_git.py"]
io = ["subprocess", "urllib.request"]
"""

# A checkout that follows every rule: a host command that uses the
# framework and its own private helper, a service that uses the domain,
# a bot that uses the bot runtime, and a package whose git calls live in
# its adapter.
CLEAN = {
    "lib/stack/__init__.py": "",
    "lib/stack/cli.py": "from .output import say\n",
    "lib/stack/output.py": "def say(text): print(text)\n",
    "lib/stack/people.py": "from .cards import card_id\n",
    "lib/stack/cards/__init__.py": "from ._git import log\ndef card_id(): ...\n",
    "lib/stack/cards/_git.py": "import subprocess\ndef log(): ...\n",
    "lib/stack/cards/history.py": "from urllib.parse import quote\n",
    "stacklets/core/bot-runner/microbot.py": "class MicroBot: ...\n",
    "stacklets/memory/lib.py": "from stack.cards import card_id\n",
    "stacklets/memory/bot/curator.py": "import sys\nsys.path.insert(0, '/stacklets/memory')\nfrom lib import card_id\n",
    "stacklets/memory/cli/_common.py": "def call(): ...\n",
    "stacklets/memory/cli/find.py": "from stack.output import say\nfrom _common import call\n",
    "stacklets/docs/bot/archivist.py": "from microbot import MicroBot\n",
}


def checkout(root: Path, files: dict[str, str], *, baseline: str | None = None,
             policy: str = POLICY) -> Path:
    for path, text in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(textwrap.dedent(text), encoding="utf-8")
    (root / "tools").mkdir(exist_ok=True)
    (root / "tools" / "arch-check.toml").write_text(policy, encoding="utf-8")
    if baseline is not None:
        (root / "tools" / "arch-baseline.toml").write_text(baseline, encoding="utf-8")
    return root


def arch_check(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL), "--root", str(root), *args],
                          capture_output=True, text=True, timeout=60)


def with_file(path: str, text: str) -> dict[str, str]:
    return {**CLEAN, path: text}


# ── The rules ────────────────────────────────────────────────────────────


def test_a_checkout_that_follows_the_rules_passes(tmp_path):
    result = arch_check(checkout(tmp_path, CLEAN))

    assert result.returncode == 0, result.stdout
    assert "no new violations" in result.stdout


def test_a_host_command_may_not_import_its_stacklets_service_code(tmp_path):
    # The host's copy of an operation is how one operation ends up in two
    # processes: the command has to ask the service instead.
    root = checkout(tmp_path, with_file("stacklets/memory/cli/find.py", "from lib import card_id\n"))

    result = arch_check(root)

    assert result.returncode == 1
    assert "R1" in result.stdout
    assert "stacklets/memory/cli/find.py:1  imports stacklets/memory/lib.py" in result.stdout


def test_a_hook_may_not_import_famstack_domain_from_the_framework(tmp_path):
    root = checkout(tmp_path, with_file("stacklets/memory/hooks/on_start.py", "from stack import people\n"))

    result = arch_check(root)

    assert result.returncode == 1
    assert "stacklets/memory/hooks/on_start.py:1  imports lib/stack/people.py" in result.stdout


@pytest.mark.parametrize("spelling", [
    "from memory.lib import card_id",                                   # the bot runner's spelling
    "import sys\nsys.path.insert(0, '/stacklets/memory')\nfrom lib import card_id",  # through sys.path
])
def test_a_stacklet_may_not_import_another_stacklets_code(tmp_path, spelling):
    root = checkout(tmp_path, with_file("stacklets/docs/bot/git_mirror.py", spelling + "\n"))

    result = arch_check(root)

    assert result.returncode == 1
    assert "R2" in result.stdout
    assert "imports stacklets/memory/lib.py" in result.stdout


def test_every_bot_may_import_the_bot_runtime(tmp_path):
    root = checkout(tmp_path, with_file("stacklets/messages/bot/scribe.py", "from microbot import MicroBot\n"))

    assert arch_check(root).returncode == 0


def test_the_framework_may_not_import_a_stacklet(tmp_path):
    root = checkout(tmp_path, with_file("lib/stack/cli.py", "from memory.lib import card_id\n"))

    result = arch_check(root)

    assert result.returncode == 1
    assert "R3" in result.stdout
    assert "lib/stack/cli.py:1  imports stacklets/memory/lib.py" in result.stdout


def test_the_framework_may_not_import_famstack_domain(tmp_path):
    root = checkout(tmp_path, with_file("lib/stack/cli.py", "from . import people\n"))

    result = arch_check(root)

    assert result.returncode == 1
    assert "lib/stack/cli.py:1  imports lib/stack/people.py" in result.stdout


def test_a_new_framework_module_has_to_be_placed(tmp_path):
    # Whether new code in lib/stack is framework or famstack's domain is a
    # decision, so it is made in review rather than by default.
    root = checkout(tmp_path, with_file("lib/stack/recipes.py", ""))

    result = arch_check(root)

    assert result.returncode == 1
    assert "lib/stack/recipes.py: neither framework nor domain" in result.stdout


def test_a_unit_that_left_the_framework_has_to_leave_the_policy(tmp_path):
    files = {path: text for path, text in CLEAN.items() if path != "lib/stack/people.py"}

    result = arch_check(checkout(tmp_path, files))

    assert result.returncode == 1
    assert "lib/stack/people: listed in tools/arch-check.toml but not in the tree" in result.stdout


def test_only_adapters_do_io_in_a_listed_package(tmp_path):
    root = checkout(tmp_path, with_file("lib/stack/cards/history.py", "import subprocess\n"))

    result = arch_check(root)

    assert result.returncode == 1
    assert "R4" in result.stdout
    assert "lib/stack/cards/history.py:1  imports subprocess" in result.stdout


def test_a_private_module_is_imported_only_from_its_own_directory(tmp_path):
    root = checkout(tmp_path, with_file("stacklets/memory/bot/wiki.py", "from cli._common import call\n"))

    result = arch_check(root)

    assert result.returncode == 1
    assert "R5" in result.stdout
    assert "stacklets/memory/bot/wiki.py:1  imports stacklets/memory/cli/_common.py" in result.stdout


def test_only_entrypoints_edit_sys_path(tmp_path):
    root = checkout(tmp_path, with_file(
        "stacklets/memory/cli/find.py", "import sys\nsys.path.insert(0, '..')\n"))

    result = arch_check(root)

    assert result.returncode == 1
    assert "R6" in result.stdout
    assert "stacklets/memory/cli/find.py:2  edits sys.path" in result.stdout


# ── The baseline ─────────────────────────────────────────────────────────

KNOWN = """\
[R1]
"stacklets/memory/cli/find.py" = ["stacklets/memory/lib.py"]
"""


def test_a_violation_listed_in_the_baseline_passes(tmp_path):
    root = checkout(tmp_path, with_file("stacklets/memory/cli/find.py", "from lib import card_id\n"),
                    baseline=KNOWN)

    result = arch_check(root)

    assert result.returncode == 0, result.stdout
    assert "1 known" in result.stdout


def test_a_new_violation_fails_next_to_known_ones(tmp_path):
    root = checkout(tmp_path, {**with_file("stacklets/memory/cli/find.py", "from lib import card_id\n"),
                               "stacklets/memory/cli/card.py": "from lib import card_id\n"},
                    baseline=KNOWN)

    result = arch_check(root)

    assert result.returncode == 1
    assert "stacklets/memory/cli/card.py:1" in result.stdout
    assert "stacklets/memory/cli/find.py" not in result.stdout


def test_a_fixed_violation_has_to_leave_the_baseline(tmp_path):
    # So the list only shrinks: an entry nobody needs any more would let
    # the same violation come back unnoticed.
    root = checkout(tmp_path, CLEAN, baseline=KNOWN)

    result = arch_check(root)

    assert result.returncode == 1
    assert "1 fixed" in result.stdout
    assert "--prune" in result.stdout


def test_prune_removes_the_fixed_entries_and_keeps_the_rest(tmp_path):
    root = checkout(tmp_path, with_file("stacklets/memory/cli/find.py", "from lib import card_id\n"),
                    baseline=KNOWN + '"stacklets/memory/cli/card.py" = ["stacklets/memory/lib.py"]\n')

    result = arch_check(root, "--prune")

    assert result.returncode == 0, result.stdout
    baseline = (root / "tools" / "arch-baseline.toml").read_text()
    assert '"stacklets/memory/cli/find.py"' in baseline
    assert "card.py" not in baseline
    assert arch_check(root).returncode == 0


def test_writing_the_baseline_accepts_every_current_violation(tmp_path):
    root = checkout(tmp_path, with_file("stacklets/docs/bot/git_mirror.py", "from memory.lib import card_id\n"))
    assert arch_check(root).returncode == 1

    assert arch_check(root, "--write-baseline").returncode == 0

    result = arch_check(root)
    assert result.returncode == 0, result.stdout
    assert "1 known" in result.stdout
