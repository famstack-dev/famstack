"""The family's side: what a member does in a room, and what the bots answered.

Every action goes through `stack messages` on the instance, as that member.
Each returns the stack's own answer; the event id in it is what the next step
(`--thread`, `--reply-to`, `answer --after`) takes.
"""

from __future__ import annotations

from pathlib import Path

from instance import Instance
from speech import voice_message

PREVIEW = 140  # characters of a message shown in a listing


def say(instance: Instance, who: str, room: str, text: str, *,
        thread: str | None = None, reply_to: str | None = None) -> dict:
    relation = (["--thread", thread] if thread else
                ["--reply-to", reply_to] if reply_to else [])
    return instance.stack("messages", "send", room, text, "--as", who, *relation)


def speak(instance: Instance, who: str, room: str, text: str, recording: str | None) -> dict:
    audio = Path(recording) if recording else voice_message(text)
    return send(instance, who, room, audio)


def send(instance: Instance, who: str, room: str, path: Path) -> dict:
    return instance.stack("messages", "upload", room, instance.put(path), "--as", who)


def react(instance: Instance, who: str, room: str, event: str, emoji: str) -> dict:
    return instance.stack("messages", "react", room, event, emoji, "--as", who)


def recent(instance: Instance, room: str, limit: int) -> list[dict]:
    return instance.stack("messages", "read", room, "--limit", str(limit))["messages"]


def answers(instance: Instance, room: str, bot: str, after: str, timeout: int) -> list[dict]:
    """What `bot` posted after event `after`, waiting up to `timeout` seconds for it."""
    return instance.stack("messages", "read", room, "--from", bot, "--wait", str(timeout),
                          "--after", after)["messages"]


# ── Showing messages ─────────────────────────────────────────────────────

def listing(messages: list[dict], *, whole: bool = False) -> str:
    return "\n".join(_entry(m, whole) for m in messages)


def _entry(message: dict, whole: bool) -> str:
    body = message["body"] if whole else (message["body"].splitlines() or [""])[0][:PREVIEW]
    return (f"{message['sender']:14} {message['event_id']}{_relation(message)}\n  "
            + body.replace("\n", "\n  "))


def _relation(message: dict) -> str:
    if message.get("thread"):
        return f" [thread {message['thread'][:10]}]"
    if message.get("reply_to"):
        return f" [reply {message['reply_to'][:10]}]"
    return ""
