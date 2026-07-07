"""CLI behaviour of the memory stacklet.

Three commands ship in Phase 1:

  - `stack memory check`   — verify the seed ontology mirrors the docs
                             taxonomy.  Exits 1 with a drift report on
                             mismatch.
  - `stack memory lookup`  — resolve a free-form term to a topic or
                             doctype.  Exits 1 on no match.
  - `stack memory prompt`  — render the classifier prompt section for
                             a language.

These tests are the canonical contract: tests + the user invoke the
same binary, both reading the same seed file and taxonomy.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent
                       / "stacklets" / "memory" / "cli"))

import _todos  # noqa: E402


class TestCheck:
    """`stack memory check` is the no-drift gate between the docs
    taxonomy and the memory seed ontology."""

    def test_returns_zero_when_in_sync(self, stack_cli):
        code, out, err = stack_cli("memory", "check")
        assert code == 0, f"expected sync, got drift:\n{out}\n{err}"

    def test_prints_per_language_counts(self, stack_cli):
        code, out, _ = stack_cli("memory", "check")
        assert code == 0
        # Both languages reported, with tag and type counts.
        assert "[en]" in out
        assert "[de]" in out
        assert "tags:" in out
        assert "types:" in out


class TestLookup:
    """`stack memory lookup` resolves a phrase to a canonical id."""

    def test_resolves_canonical_english_topic(self, stack_cli):
        code, out, _ = stack_cli("memory", "lookup", "Insurance")
        assert code == 0
        assert "topic: insurance" in out
        assert "Insurance" in out

    def test_resolves_english_synonym(self, stack_cli):
        code, out, _ = stack_cli("memory", "lookup", "coverage")
        assert code == 0
        assert "topic: insurance" in out

    def test_resolves_german_name_with_lang_flag(self, stack_cli):
        code, out, _ = stack_cli("memory", "lookup", "--lang=de", "Versicherung")
        assert code == 0
        assert "topic: insurance" in out
        # Output is in the requested language.
        assert "Versicherung" in out

    def test_resolves_doctype_by_synonym(self, stack_cli):
        # "bill" is a synonym of Invoice in the English doctype block.
        code, out, _ = stack_cli("memory", "lookup", "bill")
        assert code == 0
        assert "doctype: invoice" in out

    def test_returns_error_for_unknown_term(self, stack_cli):
        code, _, _ = stack_cli("memory", "lookup", "badminton")
        assert code != 0

    def test_returns_error_with_no_args(self, stack_cli):
        code, _, _ = stack_cli("memory", "lookup")
        assert code != 0


class TestPrompt:
    """`stack memory prompt` renders the classifier prompt section."""

    def test_renders_english_topics_and_types(self, stack_cli):
        code, out, _ = stack_cli("memory", "prompt")
        assert code == 0
        assert "Topics" in out
        assert "Document types" in out
        assert "Insurance" in out
        assert "Invoice" in out

    def test_swaps_to_german_with_lang_flag(self, stack_cli):
        code, out, _ = stack_cli("memory", "prompt", "--lang=de")
        assert code == 0
        assert "Versicherung" in out
        assert "Rechnung" in out
        # English names must not leak into the German prompt.
        assert "Insurance" not in out

    def test_includes_synonyms_inline(self, stack_cli):
        code, out, _ = stack_cli("memory", "prompt")
        assert code == 0
        # `coverage` is a synonym of Insurance; the LLM must see it.
        assert "coverage" in out


class TestTopicReaderRouting:
    """Bare topic reads brain; todo reads stay on source memory."""

    def test_bare_topic_reads_about_page_from_brain(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr(_todos, "refresh_vault_if_stale", lambda path: "up_to_date")
        vault = tmp_path / "memory" / "vault" / "family" / "camping"
        brain = tmp_path / "memory" / "brain" / "family" / "camping"
        vault.mkdir(parents=True)
        brain.mkdir(parents=True)
        (vault / "about.md").write_text(
            "---\ntitle: stale\n---\n\nsource memory should not be read\n",
            encoding="utf-8",
        )
        (brain / "about.md").write_text(
            "---\ntitle: Camping\ngenerated: true\n---\n\n"
            "<!-- begin: generated -->\n"
            "# Camping\n\nBrain projection overview [1]\n"
            "<!-- end: generated -->\n",
            encoding="utf-8",
        )

        result = _todos.show_topic("camping", config={"data_dir": str(tmp_path)})

        assert result == {"ok": True, "scope": "camping", "bucket": "family"}
        out = capsys.readouterr().out
        assert "Brain projection overview" in out
        assert "[1]" not in out
        assert "source memory should not be read" not in out

    def test_todo_reader_still_reads_memory_source(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr(_todos, "refresh_vault_if_stale", lambda path: "up_to_date")
        todos = tmp_path / "memory" / "vault" / "family" / "camping" / "todos.md"
        todos.parent.mkdir(parents=True)
        todos.write_text("# Camping\n\n- [ ] Pack the stove\n", encoding="utf-8")

        result = _todos.list_todos(
            "camping", show_all=False, config={"data_dir": str(tmp_path)},
        )

        assert result["ok"] is True
        assert result["lists"][0]["open"] == ["Pack the stove"]
        assert "- [ ] Pack the stove" in capsys.readouterr().out
