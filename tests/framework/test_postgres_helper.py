"""Talking to a Postgres running in a container.

Three stacklets keep their state in Postgres and all three need the same
two operations: get a consistent dump out, put one back. This is that,
in one place, so the knowledge of how to drive `pg_dump` does not end up
copied into a backup coordinator, a restore hook, and whatever comes
after.

The command construction is pure and tested directly; the subprocess call
around it is a thin wrapper with nothing to assert.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "lib"))

from stack.postgres import dump_command, restore_command, version_command  # noqa: E402


class TestDumpCommand:

    def test_it_dumps_through_the_container(self):
        assert dump_command("stack-messages-db", "synapse", "synapse") == [
            "docker", "exec", "stack-messages-db",
            "pg_dump", "-U", "synapse", "-d", "synapse",
        ]

    def test_the_role_and_database_are_separate(self):
        """Paperless runs its database under a differently named role, so
        these cannot be collapsed into one argument."""
        cmd = dump_command("stack-docs-db", "paperless", "paperless_user")
        assert "-U" in cmd and cmd[cmd.index("-U") + 1] == "paperless_user"
        assert "-d" in cmd and cmd[cmd.index("-d") + 1] == "paperless"


class TestRestoreCommand:
    """Nothing calls this yet. It lives here so that whatever eventually
    restores a snapshot does not have to reinvent the invocation, and so
    the two halves stay next to each other where they can be kept in
    step."""

    def test_it_loads_a_dump_into_a_named_database(self):
        assert restore_command("stack-messages-db", "synapse", "synapse") == [
            "docker", "exec", "-i", "stack-messages-db",
            "psql", "-U", "synapse", "-d", "synapse",
        ]

    def test_it_reads_the_dump_from_stdin(self):
        """`-i` is the difference between a restore and a hang: without it
        docker gives psql no stdin and it waits forever."""
        assert "-i" in restore_command("c", "d", "u")


class TestVersionCommand:

    def test_it_asks_the_server_what_it_is(self):
        cmd = version_command("stack-messages-db", "synapse", "synapse")
        assert cmd[:3] == ["docker", "exec", "stack-messages-db"]
        assert "show server_version;" in cmd
        # -tA: no header, no alignment. The caller wants a bare value.
        assert "-tAc" in cmd
