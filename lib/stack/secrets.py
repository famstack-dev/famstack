from __future__ import annotations

"""Secret storage with stacklet-scoped namespacing.

v1 implementation: TOML file at .stack/secrets.toml.
The interface is designed to be backend-swappable — a future v2 could
use macOS Keychain, HashiCorp Vault, or any other store without
changing callers.

Namespacing: secrets are prefixed with {stacklet_id}__ (e.g. photos__DB_PASSWORD).
Reading falls back from stacklet-specific to global (global__ADMIN_PASSWORD).
Writing always targets the stacklet namespace.
"""

from ._compat import tomllib
from pathlib import Path


class TomlSecretStore:
    """File-based secret storage using .stack/secrets.toml.

    The file format is flat TOML: key = "value", one per line.
    No sections, no nesting — keeps it simple and diffable.
    """

    def __init__(self, path: Path):
        self._path = Path(path)

    def _load(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            with open(self._path, "rb") as f:
                return tomllib.load(f)
        except (tomllib.TOMLDecodeError, OSError):
            return {}

    def _save(self, secrets: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# stack secrets — auto-generated, do not commit", ""]
        for k, v in sorted(secrets.items()):
            escaped = str(v).replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{k} = "{escaped}"')
        self._path.write_text("\n".join(lines) + "\n")
        self._path.chmod(0o600)

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
