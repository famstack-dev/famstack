"""Secret storage with stacklet-scoped namespacing.

v1 implementation: TOML file at .stack/secrets.toml.
The interface is designed to be backend-swappable — a future v2 could
use macOS Keychain, HashiCorp Vault, or any other store without
changing callers.

Namespacing: secrets are prefixed with {stacklet_id}__ (e.g. photos__DB_PASSWORD).
Reading falls back from stacklet-specific to global (global__ADMIN_PASSWORD).
Writing always targets the stacklet namespace.

    store = TomlSecretStore(instance / ".stack" / "secrets.toml")
    store.ensure("photos", "DB_PASSWORD")   # generated once, kept after
    store.get("photos", "ADMIN_PASSWORD")   # finds global__ADMIN_PASSWORD

A file that cannot be read is never "no secrets". Every write reads the
whole file, changes one key and writes the whole file back, so reading a
broken file as empty would save only the new key and lose every other
password, token and key of the instance. Instead every read and write of
such a file raises `SecretsUnreadable`, which names the file and the
parse error and leaves the file as it is; `./stack` prints its message
(`stack.cli.main`). A file that does not exist yet is the normal start
and reads as empty.

A write never leaves a half-written file: the new content goes to a
temporary file in the same folder, created with mode 0600, and replaces
the old file in one rename. A write stopped before the rename leaves the
old file as it was.
"""
from __future__ import annotations

import os
import tempfile
import tomllib
from pathlib import Path

__all__ = ["SecretsUnreadable", "TomlSecretStore"]


class SecretsUnreadable(Exception):
    """The secrets file exists and cannot be read; nothing was changed.

    Raised by every read and write of `TomlSecretStore` over such a
    file. Its message is the one people see:

        The stack's secrets file `/Users/homer/famstack/.stack/secrets.toml`
        cannot be read: Expected '=' after a key in a key/value pair (at
        line 4, column 1). Nothing was changed. Fix or restore the file,
        then run the command again.
    """

    def __init__(self, path: Path, error: Exception):
        self.path = path
        super().__init__(
            f"The stack's secrets file `{path}` cannot be read: {error}. "
            "Nothing was changed. Fix or restore the file, then run the command again.")


class TomlSecretStore:
    """File-based secret storage using .stack/secrets.toml.

    The file format is flat TOML: key = "value", one per line.
    No sections, no nesting — keeps it simple and diffable.
    """

    def __init__(self, path: Path):
        self._path = Path(path)

    @property
    def path(self) -> Path:
        """The secrets file this store reads and writes."""
        return self._path

    def _load(self) -> dict:
        """Every secret in the file; `{}` only when there is no file yet.

        Raises `SecretsUnreadable` when the file exists and does not open
        or parse. Never answer `{}` then: the caller's next `_save` would
        write only its own key over everything else.
        """
        if not self._path.exists():
            return {}
        try:
            with open(self._path, "rb") as f:
                return tomllib.load(f)
        except (tomllib.TOMLDecodeError, OSError) as error:
            raise SecretsUnreadable(self._path, error) from error

    def _save(self, secrets: dict) -> None:
        """Replace the file with `secrets`, in one rename, readable only by its owner.

        mkstemp creates the temporary file with mode 0600, so no other
        user can read a secret at any moment, and in the same folder, so
        the rename never crosses a file system. A failure before the
        rename removes the temporary file and leaves the old one.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# stack secrets — auto-generated, do not commit", ""]
        for k, v in sorted(secrets.items()):
            escaped = str(v).replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{k} = "{escaped}"')
        fd, temporary = tempfile.mkstemp(
            dir=self._path.parent, prefix=f".{self._path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write("\n".join(lines) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, self._path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    def get(self, stacklet_id: str, name: str) -> str | None:
        """Read a secret. Checks stacklet-specific first, global fallback.

        This fallback means get("photos", "ADMIN_PASSWORD") finds
        global__ADMIN_PASSWORD without every stacklet needing its own copy.
        """
        secrets = self._load()
        return (secrets.get(f"{stacklet_id}__{name}")
                or secrets.get(f"global__{name}"))

    def set(self, stacklet_id: str, name: str, value: str) -> None:
        """Write a secret to the stacklet namespace."""
        secrets = self._load()
        secrets[f"{stacklet_id}__{name}"] = value
        self._save(secrets)

    def ensure(self, stacklet_id: str, name: str) -> str:
        """Generate a secret if missing. Idempotent — never overwrites.

        Uses cryptographically random tokens. Existing secrets survive
        across stack up runs — that's why passwords don't change.
        """
        existing = self.get(stacklet_id, name)
        if existing:
            return existing
        import secrets as _secrets
        value = _secrets.token_urlsafe(24)
        self.set(stacklet_id, name, value)
        return value

    def clear(self, stacklet_id: str) -> None:
        """Remove all secrets for a stacklet. Global secrets are preserved.

        Called by destroy — a fresh 'stack up' generates new credentials
        that match the new database.
        """
        secrets = self._load()
        prefix = f"{stacklet_id}__"
        purged = {k: v for k, v in secrets.items() if not k.startswith(prefix)}
        if len(purged) < len(secrets):
            self._save(purged)

    def all(self) -> dict:
        """Return all secrets as a flat dict. Used for template rendering."""
        return self._load()
