"""Stacklet migrations: changes to existing data, run once per instance, in order.

A stacklet that changes the shape of data it already wrote ships the
change as a file, `stacklets/<id>/migrations/NNNN_<name>.py`, with a
`def run(ctx)` and a docstring whose first line says what it changes.
`stack up <id>` runs the ones not yet applied, after the stacklet is
healthy and its `on_start_ready` hook has run, in number order. The
first failure stops the run; the rest wait for the next `stack up`.

The record of what ran lives with the data it describes, so restoring
an older backup brings the older record back and the migration runs
again:

- `<data_dir>/<id>/.migrations.json` records every migration that ran,
  and the last error of one that failed.
- A migration that changes a git repository on the stack's code host
  declares it (`REPO = "owner/name"`), puts `ctx.trailer`
  (`Migration: <id>/<NNNN>`) on its commit and returns the commit's
  sha. The JSON file then only points at that commit: the migration
  counts as applied while the commit is in the repository, and runs
  again once it is not, as after restoring the repository from an
  older backup. One that found nothing to change returns None and is
  recorded in the file alone.

Discovery reads a migration's docstring and `REPO` without running it,
so `stack doctor` and `stack update` can list migrations as data.

`stack up` starts every stacklet before it runs any migration, since a
migration may need a service another stacklet starts. Whatever acts on
a stacklet's data waits meanwhile with `until_migrated`: the bot runner
before it launches its bots, the memory curator before it touches the
memory, the API before it runs the agent's command. They read the files
and the records alone, so a process with the stacklets and the data dir
mounted can ask without the code host.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Protocol

from .hooks import StackContext, run_python_file

MIGRATIONS_DIR = "migrations"
RECORD_FILE = ".migrations.json"

# Four digits, so numbers sort as text and a gap is visible at a glance.
_FILE_NAME = re.compile(r"^(?P<number>\d{4})_(?P<name>[a-z0-9_]+)\.py$")


class MigrationError(Exception):
    """A stacklet's migrations cannot be run as shipped."""


# ── What a stacklet ships ─────────────────────────────────────────────────

@dataclass(frozen=True)
class Migration:
    """One migration file of one stacklet.

    >>> m = Migration("memory", "0001", "move_todos_to_lists", Path("0001_move_todos_to_lists.py"),
    ...               "Move each topic's old todos.md to lists/<topic>.md.")
    >>> m.key, m.trailer
    ('memory/0001', 'Migration: memory/0001')
    """

    stacklet_id: str
    number: str
    name: str
    path: Path
    summary: str
    repo: str = ""

    @property
    def key(self) -> str:
        return f"{self.stacklet_id}/{self.number}"

    @property
    def trailer(self) -> str:
        return f"Migration: {self.key}"


def migration_file(path: str) -> tuple[str, str, str] | None:
    """`(stacklet, number, name)` for a repo path that names a migration.

    >>> migration_file("stacklets/memory/migrations/0002_kind_for_variants.py")
    ('memory', '0002', 'kind_for_variants')
    >>> migration_file("stacklets/memory/hooks/on_start.py") is None
    True
    """
    parts = path.split("/")
    if len(parts) != 4 or parts[0] != "stacklets" or parts[2] != MIGRATIONS_DIR:
        return None
    match = _FILE_NAME.match(parts[3])
    return (parts[1], match["number"], match["name"]) if match else None


def discover(stacklet_id: str, stacklet_dir: Path) -> list[Migration]:
    """The migrations a stacklet ships, in the order they run.

    Raises MigrationError when two files share a number, before anything
    runs: which of the two came first would otherwise depend on their
    names, and each instance would record a different history.
    """
    directory = Path(stacklet_dir) / MIGRATIONS_DIR
    if not directory.is_dir():
        return []
    found: dict[str, Migration] = {}
    for path in sorted(directory.iterdir()):
        match = _FILE_NAME.match(path.name)
        if not match:
            continue
        number = match["number"]
        if number in found:
            raise MigrationError(
                f"{stacklet_id} has two migrations numbered {number}: "
                f"{found[number].path.name} and {path.name}")
        summary, repo = _read_declarations(path)
        found[number] = Migration(stacklet_id, number, match["name"], path,
                                  summary or match["name"].replace("_", " "), repo)
    return [found[n] for n in sorted(found)]


def _read_declarations(path: Path) -> tuple[str, str]:
    """The docstring's first line and the `REPO` constant, read without
    running the file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    doc = ast.get_docstring(tree) or ""
    repo = ""
    for node in tree.body:
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "REPO" for t in node.targets)
                and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
            repo = node.value.value
    return (doc.strip().splitlines() or [""])[0], repo


# ── What ran ──────────────────────────────────────────────────────────────

@dataclass
class Record:
    """`<data_dir>/<id>/.migrations.json`: what ran on this instance's data.

    Keyed by number, so renaming a file changes nothing. Written to a
    temporary file and renamed, so an interrupted write leaves the
    previous record rather than half of one.
    """

    path: Path
    applied: dict[str, dict] = field(default_factory=dict)
    failed: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Record:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls(Path(path))
        return cls(Path(path), dict(data.get("applied", {})), dict(data.get("failed", {})))

    def mark_applied(self, migration: Migration, commit: str | None) -> None:
        entry = {"name": migration.name, "at": _now()}
        if commit:
            entry["commit"] = commit
        self.applied[migration.number] = entry
        self.failed.pop(migration.number, None)
        self._save()

    def mark_failed(self, migration: Migration, error: str) -> None:
        self.failed[migration.number] = {"name": migration.name, "at": _now(), "error": error}
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps({"applied": self.applied, "failed": self.failed},
                                  indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class RepoHistory(Protocol):
    """Answers whether a commit is still in a repository."""

    def carries(self, repo: str, sha: str, trailer: str) -> bool: ...


class ForgejoHistory:
    """`RepoHistory` over the stack's Forgejo.

    Only a definite answer counts as absent: Forgejo saying it has no
    such commit, or the commit not carrying the trailer. A Forgejo that
    does not answer leaves the record as it is, because a migration
    rerun for every `stack up` while the code host is down tells nobody
    anything.
    """

    def __init__(self, client):
        self._client = client

    def carries(self, repo: str, sha: str, trailer: str) -> bool:
        from .forgejo import ForgejoError
        owner, _, name = repo.partition("/")
        try:
            commit = self._client.get_commit(owner, name, sha)
        except (ForgejoError, OSError):
            return True
        if commit is None:
            return False
        message = str((commit.get("commit") or {}).get("message") or "")
        return trailer in (line.strip() for line in message.splitlines())


# ── One stacklet's migrations ─────────────────────────────────────────────

@dataclass(frozen=True)
class Status:
    """Where a stacklet's data stands against the migrations it ships.

    `newer` holds applied numbers above every shipped one: data a newer
    release migrated, now read by older code. Only numbers above the
    highest count, so deleting old migration files raises no alarm.
    """

    pending: list[Migration]
    failed: dict[str, str]
    newer: list[str]


class MigrationContext(StackContext):
    """The hook context, plus who is running: `ctx.migration` is the
    key (`memory/0002`), `ctx.trailer` the line its commit carries."""

    def __init__(self, migration: Migration, **kwargs):
        super().__init__(**kwargs)
        self.migration = migration.key
        self.trailer = migration.trailer


class Migrations:
    """The migrations of one stacklet: what it ships, what ran, what is left.

        migrations = Migrations("memory", stacklet_dir, data_dir / "memory", history)
        migrations.status().pending       # what the next `stack up` runs
        migrations.run(context_for, step) # run them, stop at the first failure
    """

    def __init__(self, stacklet_id: str, stacklet_dir: Path, data_dir: Path,
                 history: RepoHistory | None = None):
        self.stacklet_id = stacklet_id
        self.stacklet_dir = Path(stacklet_dir)
        self.record_path = Path(data_dir) / RECORD_FILE
        self._history = history

    def shipped(self) -> list[Migration]:
        return discover(self.stacklet_id, self.stacklet_dir)

    def record(self) -> Record:
        return Record.load(self.record_path)

    def status(self) -> Status:
        shipped, record = self.shipped(), self.record()
        pending = [m for m in shipped if not self._applied(m, record)]
        highest = shipped[-1].number if shipped else ""
        return Status(
            pending=pending,
            failed={f"{self.stacklet_id}/{n}": e.get("error", "")
                    for n, e in sorted(record.failed.items())
                    if any(m.number == n for m in pending)},
            newer=[f"{self.stacklet_id}/{n}" for n in sorted(record.applied) if n > highest],
        )

    def _applied(self, migration: Migration, record: Record) -> bool:
        entry = record.applied.get(migration.number)
        if entry is None:
            return False
        commit = entry.get("commit")
        if migration.repo and commit and self._history is not None:
            return self._history.carries(migration.repo, commit, migration.trailer)
        return True

    def run(self, context_for: Callable[[Migration], StackContext],
            step: Callable[[str], None]) -> dict:
        """Run what is pending, in order; stop at the first failure.

        Returns `{}` when nothing was pending, else `{"applied": [keys]}`
        plus `"failed": {key: error}` when one raised. Each outcome is
        recorded as soon as it is known, so an interrupted run leaves
        the ones after it pending and nothing claimed that did not run.
        """
        pending = self.status().pending
        if not pending:
            return {}
        record = self.record()
        applied: list[str] = []
        for migration in pending:
            step(f"Migrating {self.stacklet_id} {migration.number}: {migration.summary}")
            try:
                result = run_python_file(self.stacklet_dir, migration.path, context_for(migration))
            except Exception as e:  # noqa: BLE001 - any failure is recorded, then reported
                error = str(e) or type(e).__name__
                record.mark_failed(migration, error)
                return {"applied": applied, "failed": {migration.key: error}}
            commit = result if migration.repo and isinstance(result, str) else None
            record.mark_applied(migration, commit)
            applied.append(migration.key)
        return {"applied": applied}


# ── Waiting for them ──────────────────────────────────────────────────────

def unmigrated(migrations: Iterable[Migrations]) -> list[str]:
    """The keys of what these stacklets ship and their data has not had.

    Pass them without a code host (`history=None`): the record alone
    answers, as it does for a process in a container. A stacklet whose
    files cannot run counts too, under its id, since `stack up` refuses
    to run anything of it until they are fixed.
    """
    keys: list[str] = []
    for m in migrations:
        try:
            keys += [p.key for p in m.status().pending]
        except MigrationError as e:
            keys.append(f"{m.stacklet_id} ({e})")
    return keys


async def until_migrated(migrations: list[Migrations], say: Callable[[list[str]], None],
                         poll: float) -> None:
    """Return once these stacklets have no migration left to run.

    Asks at once, so with nothing pending it returns without waiting.
    `say` hears what is outstanding each time that changes. Nothing
    ends the wait but the migrations: one that failed holds until a
    `stack up` gets it through.
    """
    told: list[str] = []
    while keys := unmigrated(migrations):
        if keys != told:
            say(keys)
            told = keys
        await asyncio.sleep(poll)
