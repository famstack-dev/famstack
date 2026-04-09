"""Behavior tests for the model resolution system.

The model resolver is how bots find which LLM to use for a given task.
It walks a fallback chain through the [ai.models] config:

    resolve("archivist/classifier")
      1. archivist.classifier  — task-specific override
      2. archivist             — bot-level default
      3. default               — global fallback

These tests verify each step of the chain, the interactions between
levels, and the failure mode when nothing is configured. They test
the resolver as a black box — input a path, get a model name back.
"""

import sys
from pathlib import Path

import pytest

# The shared library lives in lib/ at the repo root
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "lib"))

import stack


# ── Helpers ──────────────────────────────────────────────────────────────

def configure(default="", model_overrides=None):
    """Set up the resolver state for a test.

    Patches the module globals directly — this is how the resolver
    receives config at runtime (from env vars set by the CLI).
    """
    stack.models._DEFAULT_MODEL = default
    stack.models._MODELS = model_overrides or {}


# ── Simple setup: one model for everything ───────────────────────────────

class TestDefaultFallback:
    """Most users configure a single default model. Every bot, every task,
    same model. The simplest possible setup."""

    def test_bot_task_resolves_to_default(self):
        configure(default="Qwen2.5-14B")
        assert stack.resolve_model("archivist/classifier") == "Qwen2.5-14B"

    def test_bare_task_resolves_to_default(self):
        configure(default="Qwen2.5-14B")
        assert stack.resolve_model("classifier") == "Qwen2.5-14B"

    def test_any_bot_any_task_uses_default(self):
        configure(default="Qwen2.5-14B")
        assert stack.resolve_model("archivist/classifier") == "Qwen2.5-14B"
        assert stack.resolve_model("archivist/reformat") == "Qwen2.5-14B"
        assert stack.resolve_model("scribe/transcribe") == "Qwen2.5-14B"


# ── Bot-level override ───────────────────────────────────────────────────

class TestBotLevelOverride:
    """A user wants one bot to use a different model than the default.
    All tasks for that bot use the override. Other bots still use default."""

    def test_bot_override_takes_precedence_over_default(self):
        configure(
            default="Qwen2.5-14B",
            model_overrides={"archivist": "Qwen2.5-7B"},
        )
        assert stack.resolve_model("archivist/classifier") == "Qwen2.5-7B"
        assert stack.resolve_model("archivist/reformat") == "Qwen2.5-7B"

    def test_other_bots_still_use_default(self):
        configure(
            default="Qwen2.5-14B",
            model_overrides={"archivist": "Qwen2.5-7B"},
        )
        assert stack.resolve_model("scribe/transcribe") == "Qwen2.5-14B"


# ── Task-specific override ───────────────────────────────────────────────

class TestTaskSpecificOverride:
    """Power user: different models for different tasks within the same bot.
    Classification needs a fast model, reformatting needs a smarter one."""

    def test_task_override_wins_over_bot_and_default(self):
        configure(
            default="Qwen2.5-14B",
            model_overrides={
                "archivist": "Qwen2.5-7B",
                "archivist.classifier": "Qwen2.5-3B",
            },
        )
        assert stack.resolve_model("archivist/classifier") == "Qwen2.5-3B"

    def test_other_tasks_fall_through_to_bot_level(self):
        configure(
            default="Qwen2.5-14B",
            model_overrides={
                "archivist": "Qwen2.5-7B",
                "archivist.classifier": "Qwen2.5-3B",
            },
        )
        # reformat has no specific override → falls to archivist level
        assert stack.resolve_model("archivist/reformat") == "Qwen2.5-7B"

    def test_task_override_without_bot_level(self):
        """Task-specific override exists but no bot-level default.
        Other tasks for the same bot should fall through to global default."""
        configure(
            default="Qwen2.5-14B",
            model_overrides={"archivist.classifier": "Qwen2.5-3B"},
        )
        assert stack.resolve_model("archivist/classifier") == "Qwen2.5-3B"
        assert stack.resolve_model("archivist/reformat") == "Qwen2.5-14B"


# ── No model configured ─────────────────────────────────────────────────

class TestMissingModel:
    """When no model is configured at any level, the resolver must fail
    loudly. Silent fallback to an empty string would cause cryptic API
    errors downstream — better to catch it here with a clear message."""

    def test_raises_when_nothing_configured(self):
        configure(default="", model_overrides={})
        with pytest.raises(ValueError, match="No model configured"):
            stack.resolve_model("archivist/classifier")

    def test_error_message_includes_the_path(self):
        configure(default="")
        with pytest.raises(ValueError, match="archivist/classifier"):
            stack.resolve_model("archivist/classifier")

    def test_error_message_suggests_fix(self):
        configure(default="")
        with pytest.raises(ValueError, match="stack.toml"):
            stack.resolve_model("archivist/classifier")


# ── Edge cases ───────────────────────────────────────────────────────────

class TestEdgeCases:
    """Resolution should be robust against unusual but valid inputs."""

    def test_unknown_bot_falls_to_default(self):
        configure(default="Qwen2.5-14B")
        assert stack.resolve_model("unknownbot/sometask") == "Qwen2.5-14B"

    def test_empty_models_dict_uses_default(self):
        configure(default="Qwen2.5-14B", model_overrides={})
        assert stack.resolve_model("archivist/classifier") == "Qwen2.5-14B"

    def test_bare_bot_name_without_task(self):
        """resolve("archivist") — no task, just a bot name.
        Should check models dict, then fall to default."""
        configure(
            default="Qwen2.5-14B",
            model_overrides={"archivist": "Qwen2.5-7B"},
        )
        assert stack.resolve_model("archivist") == "Qwen2.5-7B"

    def test_bare_name_not_in_models_falls_to_default(self):
        configure(default="Qwen2.5-14B")
        assert stack.resolve_model("archivist") == "Qwen2.5-14B"
