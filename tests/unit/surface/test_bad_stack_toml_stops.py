"""A stack.toml that does not parse stops every stack command.

The file names the data folder. Read as empty, it sends the stack to the
default folder, where the stacklets would start on no data. These tests run
`./stack` on an instance whose stack.toml has a typo and check that the
command stops, names the file, the line and the parser's message, and
creates no data folder.
"""

import json

import pytest

pytestmark = pytest.mark.usefixtures("docker_with_no_containers")

TYPO = '[core]\nname = "unterminated\n'


def _break(stack_cli) -> None:
    (stack_cli.instance / "stack.toml").write_text(TYPO)


@pytest.mark.parametrize("command", ["status", "list", "doctor", "config", "up photos", "down photos"])
def test_a_command_stops_with_the_file_and_the_line(stack_cli, command, tmp_path):
    _break(stack_cli)

    ran = stack_cli(*command.split())

    out = ran.out + ran.err
    assert ran.code != 0
    assert "stack.toml" in out and "line 2" in out
    assert not (tmp_path / "stack-data").exists()
    assert (stack_cli.instance / "stack.toml").read_text() == TYPO


def test_bare_stack_stops_too(stack_cli):
    _break(stack_cli)

    ran = stack_cli()

    assert ran.code != 0
    assert "line 2" in ran.out + ran.err


def test_with_json_the_error_is_one_object(stack_cli):
    _break(stack_cli)

    ran = stack_cli("status", "--json")

    assert ran.code != 0
    assert "line 2" in json.loads(ran.out)["error"]


def test_a_valid_stack_toml_still_runs(stack_cli):
    assert stack_cli("list").code == 0


def test_no_stack_toml_still_runs(stack_cli):
    (stack_cli.instance / "stack.toml").unlink()

    ran = stack_cli("list")

    assert ran.code == 0
    assert "does not parse" not in ran.err
