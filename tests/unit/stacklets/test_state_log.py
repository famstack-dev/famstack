"""The debug state log renders one line per message.

See stacklets/agent/runtime/state_log.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent
                       / "stacklets" / "agent" / "runtime"))

from state_log import format_state_for_log  # noqa: E402


def _asst_call(tcid, name, args):
    return {"role": "assistant", "content": "",
            "tool_calls": [{"id": tcid, "type": "function",
                            "function": {"name": name, "arguments": args}}]}


class TestFormatStateForLog:
    def test_compact_one_line_per_message(self):
        msgs = [
            {"role": "system", "content": "long " * 200},
            {"role": "user", "content": "hallo\nwelt"},
            _asst_call("c1", "exec", '{"command":"x"}'),
        ]
        out = format_state_for_log(msgs)
        assert "SYSTEM" in out and "USER" in out
        assert "..." in out                    # long content clipped
        assert " / " in out                    # newline flattened to one line
        assert "->calls exec" in out           # the tool call is named
        assert out.count("\n") == len(msgs) - 1  # exactly one line per message
