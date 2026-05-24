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
