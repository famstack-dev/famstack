"""Send one message as an account, from a command-line device of its own.

An encrypted room needs a device with a key store to send into it, and
the host CLI has neither. This module is that device: it logs in as the
account under the name `<name>-cli`, syncs the one room, shares the room
key with the members' devices, sends, and exits. The device and its key
store stay in the state directory, so the next send is the same device.

It is never the device of a process that runs as the same account (a bot):
two processes on one key store break each other's encryption sessions.
The account's cross-signing keys are the exception, one set shared by all
its devices, so this device signs itself with the account's keys file and
never makes one (`Device(owns_keys=False)`).

Run inside a container that has nio, the request as JSON on stdin so no
credential is in a process's arguments:

    echo '{"user_id": "@notify:home", "password": "...", "state_dir": "/data/x",
           "room_id": "!abc:home", "content": {"msgtype": "m.text", "body": "hi"}}' \\
        | python -m stack.matrix.send

The homeserver comes from `MATRIX_HOMESERVER`. The answer is one JSON
line on stdout, `{"event_id": ...}` or `{"error": ...}`, with exit 0 or 1.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import aiohttp
from loguru import logger
from nio import RoomSendResponse

from stack.matrix.device import Device, account_name

def device_names(user_id: str) -> tuple[str, str]:
    """`(state name, name shown in a device list)` for the account's command-line device."""
    account = account_name(user_id)
    return f"{account}-cli", f"{account} (command line)"


def one_room(room_id: str) -> dict:
    """A sync filter for one room's state and nothing of its history."""
    return {"room": {"rooms": [room_id], "timeline": {"limit": 1}}}


@contextmanager
def exclusive(lock_file: Path) -> Generator[None]:
    """Hold the device's lock: a second send as the same account waits here.

    Two processes on one key store corrupt it, so sends of one account
    take turns. The lock is released when the process ends, however it ends.
    """
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_file, "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


async def send(homeserver: str, user_id: str, password: str, state_dir: Path,
               room_id: str, content: dict) -> str:
    """Send `content` into `room_id` as the account; the event id. Raises on failure."""
    name, device_name = device_names(user_id)
    with exclusive(state_dir / f"{name}.lock"):
        device = Device(homeserver, user_id, password, state_dir, name,
                        device_name=device_name, owns_keys=False)
        try:
            async with aiohttp.ClientSession() as http:
                if not await device.connect(http):
                    raise RuntimeError(f"{user_id} cannot log in")
            # No wait for new events: the room's current state is all a send needs.
            await device.sync(timeout=0, full_state=True, sync_filter=one_room(room_id))
            if room_id not in device.client.rooms:
                raise RuntimeError(f"{user_id} is not in {room_id}")
            resp = await device.client.room_send(room_id, "m.room.message", content)
        finally:
            await device.client.close()
    if not isinstance(resp, RoomSendResponse):
        raise RuntimeError(f"sending failed: {getattr(resp, 'message', resp)}")
    return resp.event_id


def main() -> int:
    """Read the request from stdin, send, answer on stdout."""
    # Progress goes to stderr, the answer alone to stdout.
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    try:
        request = json.loads(sys.stdin.read())
        event_id = asyncio.run(send(
            os.environ["MATRIX_HOMESERVER"], request["user_id"], request["password"],
            Path(request["state_dir"]), request["room_id"], request["content"]))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        return 1
    print(json.dumps({"event_id": event_id}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
