"""pg_dump and psql against a Postgres running in a container.

Three stacklets keep their state in Postgres: messages (the Matrix
timeline), docs (Paperless) and photos (Immich). Each needs the same two
operations, extracting a consistent dump and loading one back, so the
invocations live here rather than inside whichever component needed them
first. The backup coordinator calls `dump` today and a restore path will
call `restore`.

Command construction is separated from execution so the exact arguments
can be asserted in tests without a running database.
"""

from __future__ import annotations

import subprocess
from typing import List


class PostgresError(RuntimeError):
    """A pg_dump or psql invocation that exited non-zero."""


# ── Commands ───────────────────────────────────────────────────────────────

def dump_command(container: str, database: str, user: str) -> List[str]:
    """Arguments for a plain-SQL dump of `database`.

    Plain SQL rather than pg_dump's custom format, because the output is
    read back by `psql`, which exists in every Postgres image. A custom
    dump would additionally require a `pg_restore` of compatible version.
    """
    return ["docker", "exec", container,
            "pg_dump", "-U", user, "-d", database]


def restore_command(container: str, database: str, user: str) -> List[str]:
    """Arguments for loading a plain-SQL dump from stdin.

    `-i` keeps the container's stdin connected. Without it `psql`
    receives no input and blocks indefinitely.
    """
    return ["docker", "exec", "-i", container,
            "psql", "-U", user, "-d", database]


def version_command(container: str, database: str, user: str) -> List[str]:
    """Arguments for reading the server version.

    `-t` drops the column header and `-A` the alignment padding, leaving
    the bare value on stdout.
    """
    return ["docker", "exec", container,
            "psql", "-U", user, "-d", database,
            "-tAc", "show server_version;"]


# ── Execution ──────────────────────────────────────────────────────────────

def dump(container: str, database: str, user: str) -> bytes:
    """Return a consistent SQL dump of `database`.

    `pg_dump` reads from a single MVCC snapshot, so the service keeps
    running and accepting writes for the duration.

    Raises `PostgresError` carrying the beginning of stderr, which is
    where Postgres reports an unknown database or a failed authentication.
    """
    proc = subprocess.run(
        dump_command(container, database, user), capture_output=True,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode(errors="replace").strip()[:400]
        raise PostgresError(
            f"pg_dump failed for {database} in {container}: {detail}"
        )
    return proc.stdout


def server_version(container: str, database: str, user: str) -> str:
    """Return the server version, or an empty string if it cannot be read.

    This is recorded as metadata beside a dump rather than used for any
    decision, so an unreachable or stopped container is reported as
    unknown instead of raising.
    """
    try:
        proc = subprocess.run(
            version_command(container, database, user),
            capture_output=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.decode(errors="replace").strip()
