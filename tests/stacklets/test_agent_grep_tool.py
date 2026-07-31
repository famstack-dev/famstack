from __future__ import annotations

from grep_tool import _PATH_RE, _is_vault_path


def test_detects_vault_paths():
    assert _is_vault_path("vault")
    assert _is_vault_path("./vault")
    assert _is_vault_path("vault/family")
    assert _is_vault_path("./vault/family")


def test_non_vault_paths_are_not_routed():
    assert not _is_vault_path(".")
    assert not _is_vault_path("memory/history.jsonl")
    assert not _is_vault_path("not-vault/family")


def test_extracts_memory_result_paths():
    block = "#1 2026-06-23 [] family/emails/example.md score=0.7\n  Title\n"
    assert _PATH_RE.findall(block) == ["family/emails/example.md"]
