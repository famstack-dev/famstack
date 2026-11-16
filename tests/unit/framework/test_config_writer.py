"""Writing one value into stack.toml without touching anything else.

Hooks and CLI commands record choices in stack.toml through
`ctx.cfg(key, value)`. The file is the admin's: it carries their
comments, their commented-out alternatives, and sections that reuse the
same key names. A write that changes one value must change exactly that
value, in exactly that section, and leave a file that still parses.
"""

import tomllib


GENERATED = '''\
[core]
language = "en"

[ai]
provider = ""
openai_url = ""
# Models by RAM tier, uncomment one to switch:
default = "mlx-community/Qwen3.5-9B-MLX-8bit"  # 32 GB (selected)
# default = "mlx-community/Qwen2.5-14B-Instruct-4bit"  # 64 GB

[backup]
default = "daily"
enabled = true
'''


def _stack(make_stack):
    stck = make_stack()
    (stck.instance_dir / "stack.toml").write_text(GENERATED)
    return stck


def _text(stck):
    return (stck.instance_dir / "stack.toml").read_text()


class TestOneValueInOneSection:

    def test_a_key_shared_by_two_sections_changes_only_in_the_one_named(self, make_stack):
        stck = _stack(make_stack)
        stck._set_cfg("ai", "default", "llama3.1:8b")

        assert stck.config["ai"]["default"] == "llama3.1:8b"
        assert stck.config["backup"]["default"] == "daily"

    def test_commented_out_alternatives_stay_as_the_admin_left_them(self, make_stack):
        """The generated file lists the other model tiers as comments so
        switching is an uncomment. Rewriting them would destroy the menu."""
        stck = _stack(make_stack)
        stck._set_cfg("ai", "default", "llama3.1:8b")

        assert '# default = "mlx-community/Qwen2.5-14B-Instruct-4bit"  # 64 GB' in _text(stck)

    def test_a_trailing_comment_on_the_line_survives(self, make_stack):
        stck = _stack(make_stack)
        stck._set_cfg("ai", "default", "llama3.1:8b")

        assert 'default = "llama3.1:8b"  # 32 GB (selected)' in _text(stck)

    def test_a_value_that_is_not_a_string_is_replaced_not_duplicated(self, make_stack):
        """A second `enabled =` line in the section is a TOML error, and
        the whole stack stops reading its config."""
        stck = _stack(make_stack)
        stck._set_cfg("backup", "enabled", "false")

        assert stck.config["backup"]["enabled"] == "false"
        assert _text(stck).count("enabled =") == 1


class TestKeysAndSectionsThatDoNotExistYet:

    def test_a_new_key_lands_in_its_section(self, make_stack):
        stck = _stack(make_stack)
        stck._set_cfg("ai", "whisper_url", "http://192.168.1.20:42062/v1")

        assert stck.config["ai"]["whisper_url"] == "http://192.168.1.20:42062/v1"
        assert "whisper_url" not in stck.config["backup"]

    def test_a_new_section_is_appended(self, make_stack):
        stck = _stack(make_stack)
        stck._set_cfg("agent", "name", "Marge")

        assert stck.config["agent"]["name"] == "Marge"


class TestValuesThatNeedEscaping:

    def test_quotes_and_backslashes_round_trip(self, make_stack):
        """API keys are opaque strings; the file must still parse."""
        stck = _stack(make_stack)
        stck._set_cfg("ai", "openai_key", 'sk-"odd"\\key')

        assert tomllib.loads(_text(stck))["ai"]["openai_key"] == 'sk-"odd"\\key'


# ── Two writers at once ──────────────────────────────────────────────────

# One process writing `count` keys of its own into `[section]`, as a
# preparation and a command beside it each write theirs.
WRITER = """
import sys
from pathlib import Path
from stack import Stack
instance, section, count = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
stck = Stack(Path(sys.argv[4]), instance / "data", instance_dir=instance)
for n in range(count):
    stck._set_cfg(section, f"key{n}", str(n))
"""


def test_two_processes_writing_at_once_lose_none_of_each_others_values(make_stack):
    """Given two processes write stack.toml at the same time (`stack ai
    prepare` writes `[ai]` outside the queue of runs while another run
    writes its own section), then every value each wrote is in the file:
    a write reads, changes and writes the file under a lock, so neither
    writes back a file without the other's change."""
    import subprocess
    import sys
    from pathlib import Path

    stck = _stack(make_stack)
    root = Path(__file__).resolve().parents[3]
    env = {"PYTHONPATH": str(root / "lib"), "PATH": "/usr/bin:/bin"}
    writers = [subprocess.Popen([sys.executable, "-c", WRITER, str(stck.instance_dir),
                                 section, "150", str(root)], env=env, cwd=root)
               for section in ("ai", "backup")]
    for writer in writers:
        assert writer.wait(timeout=60) == 0

    written = tomllib.loads(_text(stck))
    assert {f"key{n}" for n in range(150)} <= written["ai"].keys()
    assert {f"key{n}" for n in range(150)} <= written["backup"].keys()


# The backup stacklet's setup writes its target block, as `stack up
# backup` does, `count` times under new names.
BACKUP_WRITER = """
import sys
from pathlib import Path
from stacklets.backup._config import write_target
toml, count = Path(sys.argv[1]), int(sys.argv[2])
for n in range(count):
    write_target(toml, f"disk{n}", {"engine": "external-disk", "disk": f"vault{n}"})
"""


def test_a_backup_target_written_beside_another_write_loses_nothing(make_stack):
    """Given the backup stacklet writes its targets while another process
    writes `[ai]`, then every target and every value is in the file: both
    write through the same lock."""
    import subprocess
    import sys
    from pathlib import Path

    stck = _stack(make_stack)
    root = Path(__file__).resolve().parents[3]
    env = {"PYTHONPATH": f"{root / 'lib'}:{root}", "PATH": "/usr/bin:/bin"}
    toml = stck.instance_dir / "stack.toml"
    writers = [
        subprocess.Popen([sys.executable, "-c", WRITER, str(stck.instance_dir), "ai", "300",
                          str(root)], env=env, cwd=root),
        subprocess.Popen([sys.executable, "-c", BACKUP_WRITER, str(toml), "300"], env=env, cwd=root),
    ]
    for writer in writers:
        assert writer.wait(timeout=60) == 0

    written = tomllib.loads(_text(stck))
    assert {f"key{n}" for n in range(300)} <= written["ai"].keys()
    assert {f"disk{n}" for n in range(300)} <= written["backup"]["targets"].keys()


def test_a_write_that_fails_leaves_no_temporary_file(make_stack):
    """Given a write that fails halfway (a value the file cannot hold),
    then stack.toml is as it was and no temporary file is left beside it."""
    import pytest
    from stacklets.backup._config import write_target

    stck = _stack(make_stack)
    before = sorted(p.name for p in stck.instance_dir.iterdir())

    with pytest.raises(UnicodeEncodeError):
        write_target(stck.instance_dir / "stack.toml", "vault", {"disk": "\udc80"})

    assert _text(stck) == GENERATED
    assert sorted(p.name for p in stck.instance_dir.iterdir()) == before
