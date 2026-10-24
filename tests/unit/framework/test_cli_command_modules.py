"""A stacklet's command runs as the Python module it is.

`stack <id> <cmd>` loads `stacklets/<id>/cli/<cmd>.py` from its file and
calls its `run`. A command is ordinary Python, so whatever a module may
do at import time, a command may do too: a `@dataclass` under
`from __future__ import annotations` looks its module up in
`sys.modules` while the class is built, and fails when the module is not
registered there. Each run loads the command afresh, so an edited file
takes effect on the next run.
"""

from __future__ import annotations

import sys

COMMAND = '''
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Receipt:
    what: str
    count: int


def run(args, stacklet, config):
    return {"ok": True, "receipt": asdict(Receipt(args[0], len(args))), "version": VERSION}


VERSION = 1
'''


def _command(stck_root, text: str) -> None:
    cli = stck_root / "stacklets" / "basic" / "cli"
    cli.mkdir(exist_ok=True)
    (cli / "receipt.py").write_text(text)


def test_a_command_that_defines_a_dataclass_runs_and_returns_its_result(make_stack, tmp_path):
    stck = make_stack(stacklets=["basic"])
    _command(tmp_path, COMMAND)

    result = stck.run_cli_command("basic", "receipt", ["donuts", "sprinkled"])

    assert result == {"ok": True, "receipt": {"what": "donuts", "count": 2}, "version": 1}
    assert "stacklets.basic.cli.receipt" not in sys.modules, "forgotten with the stacklet's modules"


def test_an_edited_command_runs_as_edited(make_stack, tmp_path):
    stck = make_stack(stacklets=["basic"])
    _command(tmp_path, COMMAND)
    stck.run_cli_command("basic", "receipt", ["donuts"])

    # Another size, so Python's bytecode cache cannot take it for the old file.
    _command(tmp_path, COMMAND.replace("VERSION = 1", "VERSION = 20"))

    assert stck.run_cli_command("basic", "receipt", ["donuts"])["version"] == 20
