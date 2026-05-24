"""VaultContext — fresh reads of the memory vault's classifier inputs.

Ontology + correspondents live on disk in the memory vault and are
hand-editable; the bot re-reads them per call so an edit takes effect on
the next document. VaultContext owns those reads so the pipelines depend
on it directly instead of callbacks into the bot.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets"))  # memory.lib
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs" / "bot"))

from vault_context import VaultContext  # noqa: E402


def test_correspondents_section_empty_without_vault(monkeypatch):
    monkeypatch.delenv("MEMORY_VAULT_DIR", raising=False)
    vc = VaultContext(language="en", shared_bucket="family")
    assert vc.correspondents_section() == ""


def test_ontology_section_falls_back_to_seed(monkeypatch):
    # No vault dir → the shipped seed ontology, which still renders a
    # non-empty classifier vocabulary block.
    monkeypatch.delenv("MEMORY_VAULT_DIR", raising=False)
    vc = VaultContext(language="en", shared_bucket="family")
    section = vc.ontology_section()
    assert isinstance(section, str) and section.strip()


def test_ontology_object_exposes_canonicalizers(monkeypatch):
    # The pipeline needs the Ontology object itself (for match_topics /
    # canonicalize_*), not just the rendered prompt section.
    monkeypatch.delenv("MEMORY_VAULT_DIR", raising=False)
    vc = VaultContext(language="en", shared_bucket="family")
    ont = vc.ontology()
    assert hasattr(ont, "classifier_prompt_section")


def test_ontology_section_accepts_explicit_language(monkeypatch):
    monkeypatch.delenv("MEMORY_VAULT_DIR", raising=False)
    vc = VaultContext(language="en", shared_bucket="family")
    assert isinstance(vc.ontology_section("de"), str)
