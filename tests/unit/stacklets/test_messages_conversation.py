"""Driving a conversation from the terminal with `stack messages`.

`send` answers a message (a reply, or inside a thread) and hands back the id
of what it posted; `react` puts a reaction on a message as a family member;
`read` tells what came after a given message and from whom. Together they let
an admin or an agent hold the same exchange with the bots a phone does: send
"Archivist, where do we meet?", then read the archivist's answer to exactly
that message.

Synapse is replaced at its HTTP boundary by pytest-httpserver; what is checked
is the event a real client would send, and what `read` makes of real events.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "messages" / "cli"))
sys.path.insert(0, str(_REPO_ROOT / "lib"))

from _matrix import MatrixClient  # noqa: E402
from read import message_of, select  # noqa: E402

ROOM = "!picnic:simpson"


def _event(eid, sender, body, relates=None):
    content = {"msgtype": "m.text", "body": body}
    if relates:
        content["m.relates_to"] = relates
    return {"event_id": eid, "sender": f"@{sender}:simpson", "origin_server_ts": 1,
            "type": "m.room.message", "content": content}


class TestReadingAConversation:

    def test_a_message_says_which_thread_or_message_it_answers(self):
        in_thread = message_of(_event("$b", "archivist-bot", "✅ Filed", {
            "rel_type": "m.thread", "event_id": "$a", "m.in_reply_to": {"event_id": "$a"}}))
        a_reply = message_of(_event("$c", "marge", "this is Marge's car",
                                    {"m.in_reply_to": {"event_id": "$b"}}))

        assert (in_thread["thread"], in_thread["reply_to"]) == ("$a", None)
        assert (a_reply["thread"], a_reply["reply_to"]) == (None, "$b")
        assert a_reply["sender"] == "marge"

    def test_the_answer_to_a_message_is_what_a_bot_said_after_it(self):
        messages = [message_of(e) for e in [
            _event("$old", "archivist-bot", "an earlier answer"),
            _event("$ask", "marge", "Archivist, where do we meet?"),
            _event("$chat", "homer", "mmm, picnic"),
            _event("$ans", "archivist-bot", "At the north entrance"),
        ]]

        answer = select(messages, after="$ask", sender="archivist")

        assert [m["event_id"] for m in answer] == ["$ans"]

    def test_a_message_not_in_the_window_selects_nothing(self):
        messages = [message_of(_event("$x", "archivist-bot", "hi"))]
        assert select(messages, after="$gone", sender=None) == []


def _client(httpserver):
    client = MatrixClient(httpserver.url_for("").rstrip("/"), "simpson", str(_REPO_ROOT))
    client.token = "t"
    return client


def _prefix_matching(httpserver):
    # Transaction ids end the path; match on the part before them.
    from pytest_httpserver import URIPattern

    class Prefix(URIPattern):
        def __init__(self, prefix):
            self.prefix = prefix

        def match(self, uri):
            return uri.startswith(self.prefix)
    return Prefix


class TestSendingIntoAConversation:

    def _recorded(self, httpserver):
        Prefix = _prefix_matching(httpserver)
        sent = []
        from werkzeug import Response

        def record(request):
            sent.append({"path": request.path, "body": json.loads(request.data)})
            return Response(json.dumps({"event_id": "$new"}), content_type="application/json")
        for kind in ("m.room.message", "m.reaction"):
            httpserver.expect_request(Prefix(f"/_matrix/client/v3/rooms/{ROOM}/send/{kind}/"),
                                      method="PUT").respond_with_handler(record)
        return sent

    def test_a_reply_answers_one_message_without_a_thread(self, httpserver):
        sent = self._recorded(httpserver)

        ok, event_id = _client(httpserver).send(ROOM, "this is Marge's car", reply_to="$filed")

        assert (ok, event_id) == (True, "$new")
        assert sent[0]["body"]["m.relates_to"] == {"m.in_reply_to": {"event_id": "$filed"}}

    def test_a_reaction_is_an_annotation_on_the_message(self, httpserver):
        sent = self._recorded(httpserver)

        ok, _ = _client(httpserver).react(ROOM, "$note", "📌")

        assert ok
        assert "/send/m.reaction/" in sent[0]["path"]
        assert sent[0]["body"] == {"m.relates_to": {
            "rel_type": "m.annotation", "event_id": "$note", "key": "📌"}}
