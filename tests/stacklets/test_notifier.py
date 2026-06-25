"""MatrixNotifier — the mid-flow status port for pipelines/services.

Collaborators that need to post an ephemeral progress message ("fetching
…", "looking deeper …") take a Notifier rather than reaching for Matrix
or the message catalogue. MatrixNotifier binds a room + reply thread to
the bot's send + translator, so the collaborator just names a key.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs" / "bot"))

from notifier import MatrixNotifier  # noqa: E402


@pytest.mark.asyncio
async def test_status_translates_key_and_sends_to_thread():
    sends = []

    async def send(room_id, text, reply_to):
        sends.append((room_id, text, reply_to))

    def t(key, **kw):
        return f"{key}:{kw.get('url', '')}"

    n = MatrixNotifier(room_id="!r:server", reply_to="$e:server", send=send, t=t)
    await n.status("capture_fetching", url="http://example.com")

    assert sends == [("!r:server", "capture_fetching:http://example.com", "$e:server")]


@pytest.mark.asyncio
async def test_status_without_kwargs():
    sends = []

    async def send(room_id, text, reply_to):
        sends.append((room_id, text, reply_to))

    n = MatrixNotifier(room_id="!r", reply_to=None, send=send, t=lambda k, **kw: k)
    await n.status("search_looking_deeper")
    assert sends == [("!r", "search_looking_deeper", None)]


async def _noop_send(room_id, text, reply_to):
    return None


@pytest.mark.asyncio
async def test_acknowledge_reacts_on_the_source_event():
    # The 👀 replacement for the "Reading ..." status text: the notifier
    # reacts on the message it is bound to rather than posting a reply.
    reacts = []

    async def react(room_id, event_id):
        reacts.append((room_id, event_id))

    n = MatrixNotifier(
        room_id="!r:server", reply_to="$e:server",
        send=_noop_send, t=lambda k, **kw: k, react=react,
    )
    await n.acknowledge()
    assert reacts == [("!r:server", "$e:server")]


@pytest.mark.asyncio
async def test_acknowledge_noop_without_react_transport():
    # No react bound (e.g. a notifier built for a text-only flow) — the
    # acknowledgement is simply skipped, not an error.
    n = MatrixNotifier(room_id="!r", reply_to="$e", send=_noop_send, t=lambda k, **kw: k)
    await n.acknowledge()


@pytest.mark.asyncio
async def test_acknowledge_noop_without_source_event():
    reacts = []

    async def react(room_id, event_id):
        reacts.append((room_id, event_id))

    n = MatrixNotifier(
        room_id="!r", reply_to=None,
        send=_noop_send, t=lambda k, **kw: k, react=react,
    )
    await n.acknowledge()
    assert reacts == []
