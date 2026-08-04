"""Driving a threaded conversation from the terminal.

A Matrix thread is a conversation, and the agent treats a thread it is
part of as addressed to it (see `test_agent_thread_trigger.py`). That
behaviour was unreachable from the CLI: `send` could only post at the
top level, and `read` never showed the event ids you would thread onto.
So the two flags here are a pair -- `read --ids` tells you what to pass
to `send --thread`, and together they let a whole threaded exchange,
including a reply to a message you did not send, be driven and checked
without a Matrix client.

What is pinned here is the wire format and the flag contract. That the
agent then *answers* such a message is the shim's business, not this
module's.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "messages" / "cli"))

import _matrix  # noqa: E402
import read  # noqa: E402


class _CapturingClient(_matrix.MatrixClient):
    """A real MatrixClient with the one network call intercepted.

    Subclassed rather than stubbed wholesale so the body under test is
    built by the actual `send`, including the alias handling and the
    mention payload it shares with every other send.
    """

    def __init__(self):
        self.token = "tok"
        self.server_name = "simpson"
        self.base_url = "http://localhost:42031"
        self.sent: dict = {}

    def _url(self, path):
        return self.base_url + path

    def _full_user(self, user):
        return f"@{user}:{self.server_name}"


def _send(client, **kwargs):
    """Run `send` with the HTTP PUT captured instead of performed."""
    def fake_put(url, body, token=None):
        client.sent = body
        return 200, {"event_id": "$new"}

    original, _matrix._put = _matrix._put, fake_put
    try:
        return client.send("!room:simpson", "hello", **kwargs)
    finally:
        _matrix._put = original


class TestSendingIntoAThread:

    def test_a_threaded_send_carries_the_matrix_thread_relation(self):
        """The relation is what makes it a thread rather than a quote, and
        the root is what a bot reads to decide which conversation this is."""
        client = _CapturingClient()
        ok, event_id = _send(client, thread_root="$root:simpson")

        assert ok and event_id == "$new"
        relation = client.sent["m.relates_to"]
        assert relation["rel_type"] == "m.thread"
        assert relation["event_id"] == "$root:simpson"

    def test_it_also_falls_back_to_a_reply_for_thread_blind_clients(self):
        """Matrix v1.4: a threaded message carries an `m.in_reply_to`
        pointer flagged `is_falling_back`, so a client that does not
        render threads still shows it in context instead of orphaned."""
        client = _CapturingClient()
        _send(client, thread_root="$root:simpson")

        relation = client.sent["m.relates_to"]
        assert relation["is_falling_back"] is True
        assert relation["m.in_reply_to"] == {"event_id": "$root:simpson"}

    def test_an_ordinary_send_is_still_top_level(self):
        # The flag is opt-in; every existing caller must be unaffected.
        client = _CapturingClient()
        _send(client)

        assert "m.relates_to" not in client.sent

    def test_a_thread_reply_can_still_mention_someone(self):
        # How you pull a bot *into* someone else's thread in the first place.
        client = _CapturingClient()
        _send(client, thread_root="$root:simpson", mentions=["stacky-bot"])

        assert client.sent["m.mentions"] == {"user_ids": ["@stacky-bot:simpson"]}
        assert client.sent["m.relates_to"]["rel_type"] == "m.thread"


class TestAskingForEventIds:
    """`read --ids`. Without it there is no way to learn the id of a
    message you did not send, which is exactly the message you want to
    thread onto: the bot's own answer."""

    def test_ids_are_off_unless_asked_for(self):
        room, limit, show_ids, err = read._parse_args(["chat"])
        assert (room, limit, show_ids, err) == ("chat", 20, False, None)

    def test_the_flag_turns_them_on_without_eating_the_room(self):
        room, _, show_ids, err = read._parse_args(["chat", "--ids"])
        assert (room, show_ids, err) == ("chat", True, None)

    def test_it_composes_with_limit_in_either_order(self):
        assert read._parse_args(["--ids", "chat", "--limit", "3"])[:3] == \
            ("chat", 3, True)
        assert read._parse_args(["chat", "--limit", "3", "--ids"])[:3] == \
            ("chat", 3, True)
