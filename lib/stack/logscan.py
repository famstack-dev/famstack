"""Find the error lines in container logs.

`stack errors` answers "what went wrong recently, anywhere?" without the
reader opening every stacklet's log. Each service writes its own format, so
a line is an error when it carries an error *level* in one of the formats
the stack's services use. The word alone does not count: "0 errors",
"error_count=0" and a request path containing "error" are ordinary lines.

This module is pure: it takes log text and returns a summary. Reading the
logs is the caller's job.
"""

from __future__ import annotations

import re

# One alternative per log format, each anchored on how that format writes
# its level, never on the message text.
_ERROR_LEVEL = re.compile("|".join([
    # A level token as a word: Python logging and loguru (`| ERROR |`,
    # `- ERROR -`, `[ERROR]`), Postgres (`ERROR:`, `FATAL:`, `PANIC:`),
    # Java/Nest style (`ERROR [main]`).
    r"\b(?:ERROR|CRITICAL|FATAL|PANIC)\b",
    # Forgejo/Gitea: a one-letter level in brackets. E error, C critical, F fatal.
    r"\s\[[ECF]\]\s",
    # nginx error log: lowercase level in brackets.
    r"\[(?:error|crit|alert|emerg)\]",
    # logfmt (Watchtower, Go services) and JSON loggers (Caddy).
    r"\blevel=(?:error|fatal|panic)\b",
    r"\"level\":\s*\"(?:error|fatal|panic)\"",
    # A Python traceback's first line; the frames after it are not counted.
    r"^Traceback \(most recent call last\):",
]))


def is_error(line: str) -> bool:
    return bool(_ERROR_LEVEL.search(line))


def _split_timestamp(line: str) -> tuple[str, str]:
    """`docker logs --timestamps` puts an RFC 3339 time and a space in front
    of every line. A line without one sorts first."""
    stamp, sep, rest = line.partition(" ")
    if sep and len(stamp) >= 20 and stamp[4] == "-" and stamp[10] == "T":
        return stamp, rest
    return "", line


def scan(log: str, keep: int = 5) -> dict:
    """Count the error lines in a timestamped log and keep the newest.

    `log` is stdout and stderr concatenated, so lines are put back in time
    order first. Returns `count`, `last_at` (the newest error's time, or
    None) and `lines`, the newest `keep` errors oldest first, each as
    `{"at", "text"}`.
    """
    errors = []
    for raw in log.splitlines():
        at, text = _split_timestamp(raw)
        if is_error(text):
            errors.append((at, text.rstrip()))
    errors.sort(key=lambda e: e[0])
    newest = errors[-keep:] if keep else []
    return {
        "count": len(errors),
        "last_at": (errors[-1][0] or None) if errors else None,
        "lines": [{"at": at or None, "text": text} for at, text in newest],
    }
