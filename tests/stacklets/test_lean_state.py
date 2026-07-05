"""Lean agent state: previous-turn tool results become a pointer naming the call.

The transcript (Matrix) keeps the full result; the state we feed the model keeps
only the call that produced it, so the model re-fetches instead of reciting a
stale payload. See stacklets/agent/runtime/lean_state.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent
                       / "stacklets" / "agent" / "runtime"))

from lean_state import format_state_for_log, lean_messages  # noqa: E402


def _asst_call(tcid, name, args):
    return {"role": "assistant", "content": "",
            "tool_calls": [{"id": tcid, "type": "function",
                            "function": {"name": name, "arguments": args}}]}


def _tool_result(tcid, content):
    return {"role": "tool", "tool_call_id": tcid, "content": content}


class TestLeanMessages:
    def test_prior_tool_result_becomes_a_named_pointer(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "was ist offen?"},
            _asst_call("c1", "exec", '{"command": "stack memory topic x todo"}'),
            _tool_result("c1", "8 open\n- a\n- b"),
            {"role": "assistant", "content": "8 offen"},
            {"role": "user", "content": "und jetzt?"},   # current turn boundary
        ]
        out = lean_messages(msgs)
        tool_msg = out[3]
        assert tool_msg["role"] == "tool"
        assert tool_msg["tool_call_id"] == "c1"          # pairing preserved
        assert "8 open" not in tool_msg["content"]       # stale payload gone
        assert 'exec({"command": "stack memory topic x todo"})' in tool_msg["content"]
        assert "re-run" in tool_msg["content"]

    def test_current_turn_tool_result_is_kept(self):
        msgs = [
            {"role": "user", "content": "erste frage"},
            _tool_result("old", "STALE"),                  # a previous turn
            {"role": "user", "content": "zweite frage"},   # current turn boundary
            _asst_call("c2", "exec", '{"command": "grep foo"}'),
            _tool_result("c2", "FRESH RESULT"),            # current turn -> kept
        ]
        out = lean_messages(msgs)
        assert out[1]["content"] != "STALE"
        assert out[4]["content"] == "FRESH RESULT"

    def test_non_tool_messages_and_pairing_untouched(self):
        msgs = [
            {"role": "user", "content": "q"},
            _asst_call("c1", "exec", '{"command": "x"}'),
            _tool_result("c1", "big result"),
            {"role": "assistant", "content": "an answer"},
            {"role": "user", "content": "next"},
        ]
        out = lean_messages(msgs)
        assert out[1]["tool_calls"]                        # assistant call kept
        assert out[3]["content"] == "an answer"            # assistant text kept
        assert len(out) == len(msgs)                       # nothing dropped

    def test_orphan_tool_result_falls_back(self):
        msgs = [_tool_result("orphan", "data"),
                {"role": "user", "content": "now"}]
        out = lean_messages(msgs)
        assert "the tool" in out[0]["content"]
        assert "data" not in out[0]["content"]

    def test_long_args_are_capped(self):
        big = '{"content": "' + "x" * 500 + '"}'
        msgs = [
            {"role": "user", "content": "q"},
            _asst_call("c1", "write_file", big),
            _tool_result("c1", "ok"),
            {"role": "user", "content": "next"},
        ]
        out = lean_messages(msgs)
        assert "..." in out[2]["content"]                  # args truncated
        assert len(out[2]["content"]) < len(big)


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
