"""VaultContext — the classifier's prompt inputs, read fresh from the vault.

Ontology and correspondents live on disk in the memory vault and are
hand-editable (Obsidian, Forgejo's web UI, the memory CLI). Both are
re-read on every call so a hand edit takes effect on the next filed
document — no restart, no caching. The reads are cheap (small TOML,
dozens of tiny markdown files); the LLM call that follows dwarfs them.

Owning these reads here lets DocumentPipeline / SearchService depend on
a VaultContext directly, rather than on callbacks into the bot.
"""

from __future__ import annotations

import os
from pathlib import Path

from memory.lib import (
    correspondents_prompt_section,
    get_ontology,
    load_correspondents_from_vault,
)


class VaultContext:
    """Reads the memory vault's ontology + correspondents for classification."""

    def __init__(self, *, language: str, shared_bucket: str):
        self.language = language
        self.shared_bucket = shared_bucket

    def _vault_path(self) -> Path | None:
        """Current `MEMORY_VAULT_DIR`, read fresh; None when unset.

        The bot-runner mounts the host data dir at `/data`, so the vault
        lives at `/data/memory/vault/` and `MEMORY_VAULT_DIR` points at it.
        """
        env = os.environ.get("MEMORY_VAULT_DIR", "")
        return Path(env) if env else None

    def ontology(self):
        """The memory ontology object (the shipped seed when no vault).

        Returned whole so callers can use the bilingual canonicals —
        `match_topics` / `canonicalize_*` normalise LLM output back to the
        household language and reject cross-field hallucinations.
        """
        return get_ontology(self._vault_path())

    def ontology_section(self, language: str | None = None) -> str:
        """Render the classifier vocabulary block from the ontology."""
        return self.ontology().classifier_prompt_section(language or self.language)

    def correspondents_section(self) -> str:
        """Build the correspondents block from `<shared_bucket>/correspondents/*.md`.

        Empty when the vault isn't seeded yet — the prompt then falls back
        to the flat Paperless correspondents list (no alias signal).
        """
        path = self._vault_path()
        if path is None:
            return ""
        correspondents = load_correspondents_from_vault(path, self.shared_bucket)
        return correspondents_prompt_section(correspondents)
