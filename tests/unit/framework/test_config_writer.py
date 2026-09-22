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
