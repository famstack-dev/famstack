"""Health checks for parts a stacklet was told not to start.

`stack up ai --no-voice` starts the LLM side and deliberately leaves the
speech containers out. The health checks, though, are declared in the
manifest and knew nothing about that, so the start sequence waited out a
full timeout per missing container and then reported two services as
broken that were never meant to be running.

A manifest can now mark a check as belonging to an optional part, naming
the environment variable that turns that part off.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))

from stack.stack import Stack  # noqa: E402


def _stack(tmp_path) -> Stack:
    return Stack(root=tmp_path, data=tmp_path / "data")


MANIFEST = {
    "health": {
        "checks": [
            {"name": "TTS", "url": "http://localhost:42063/",
             "skip_when_env": "STACK_AI_NO_VOICE"},
            {"name": "Whisper", "url": "http://localhost:42062/",
             "skip_when_env": "STACK_AI_NO_VOICE"},
            {"name": "LLM", "url": "http://localhost:42060/v1/models"},
        ],
    },
}


class TestOptionalChecks:

    def test_all_checks_run_when_nothing_is_disabled(self, tmp_path, monkeypatch):
        monkeypatch.delenv("STACK_AI_NO_VOICE", raising=False)
        checks = _stack(tmp_path)._resolve_health_checks(MANIFEST, {})
        assert [c["name"] for c in checks] == ["TTS", "Whisper", "LLM"]

    def test_disabled_parts_are_not_waited_on(self, tmp_path, monkeypatch):
        """The whole point: no timeout spent on a container we chose not
        to start, and no failure reported for its absence."""
        monkeypatch.setenv("STACK_AI_NO_VOICE", "1")
        checks = _stack(tmp_path)._resolve_health_checks(MANIFEST, {})
        assert [c["name"] for c in checks] == ["LLM"]

    def test_the_variable_must_say_one_not_merely_exist(self, tmp_path, monkeypatch):
        """An empty or "0" value is not opting out. Anything else would
        make an accidentally-exported variable silently hide checks."""
        monkeypatch.setenv("STACK_AI_NO_VOICE", "0")
        checks = _stack(tmp_path)._resolve_health_checks(MANIFEST, {})
        assert [c["name"] for c in checks] == ["TTS", "Whisper", "LLM"]

    def test_checks_without_the_key_are_unaffected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STACK_AI_NO_VOICE", "1")
        manifest = {"health": {"checks": [
            {"name": "LLM", "url": "http://localhost:42060/v1/models"},
        ]}}
        checks = _stack(tmp_path)._resolve_health_checks(manifest, {})
        assert [c["name"] for c in checks] == ["LLM"]


class TestTheAiManifestUsesIt:
    """The bug was in the ai stacklet specifically; pin that its manifest
    actually carries the marks, not just that the framework supports them."""

    def test_voice_checks_are_marked_optional(self):
        import tomllib
        manifest = tomllib.loads(
            (_REPO_ROOT / "stacklets" / "ai" / "stacklet.toml").read_text())
        by_name = {c["name"]: c for c in manifest["health"]["checks"]}
        assert by_name["Whisper"].get("skip_when_env") == "STACK_AI_NO_VOICE"
        assert "skip_when_env" not in by_name["LLM"], \
            "the LLM is the point of the stacklet; it is never optional"


class TestPartsTurnedOffInStackToml:
    """A part can also be off in `stack.toml`: `skip_when_off = "voice"`
    drops the check while the stacklet's own `voice` is `false`. The ai
    stacklet's Whisper check is marked so: a small Mac's install stores
    `[ai] voice = false`, and `stack status` must not report the Whisper
    it never installed."""

    @staticmethod
    def _ai_checks(tmp_path, ai_section: str) -> list[str]:
        import tomllib
        (tmp_path / "stack.toml").write_text(ai_section)
        manifest = tomllib.loads((_REPO_ROOT / "stacklets" / "ai" / "stacklet.toml").read_text())
        return [c["name"] for c in _stack(tmp_path)._resolve_health_checks(manifest, {})]

    def test_whisper_is_not_checked_with_voice_false(self, tmp_path, monkeypatch):
        monkeypatch.delenv("STACK_AI_NO_VOICE", raising=False)
        assert self._ai_checks(tmp_path, "[ai]\nvoice = false\n") == ["LLM"]

    @pytest.mark.parametrize("ai_section", ["[ai]\nvoice = true\n", "[ai]\n", ""])
    def test_whisper_is_checked_with_voice_on_or_unset(self, tmp_path, monkeypatch, ai_section):
        monkeypatch.delenv("STACK_AI_NO_VOICE", raising=False)
        assert self._ai_checks(tmp_path, ai_section) == ["Whisper", "LLM"]


class TestPartsAnotherServerServes:
    """A part can be served by another server: `skip_when_set =
    "whisper_model"` drops the check while the stacklet's own
    `whisper_model` names a model. The ai stacklet's Whisper check is
    marked so: when an oMLX the admin runs transcribes, the first start
    builds no whisper.cpp, and `stack status` must not report it."""

    @staticmethod
    def _ai_checks(tmp_path, ai_section: str) -> list[str]:
        import tomllib
        (tmp_path / "stack.toml").write_text(ai_section)
        manifest = tomllib.loads((_REPO_ROOT / "stacklets" / "ai" / "stacklet.toml").read_text())
        return [c["name"] for c in _stack(tmp_path)._resolve_health_checks(manifest, {})]

    def test_whisper_is_not_checked_when_the_ai_server_transcribes(self, tmp_path, monkeypatch):
        monkeypatch.delenv("STACK_AI_NO_VOICE", raising=False)
        ai = '[ai]\nwhisper_model = "whisper-large-v3-turbo-asr-fp16"\n'
        assert self._ai_checks(tmp_path, ai) == ["LLM"]

    @pytest.mark.parametrize("ai_section", ['[ai]\nwhisper_model = ""\n', "[ai]\n"])
    def test_whisper_is_checked_without_a_speech_model(self, tmp_path, monkeypatch, ai_section):
        monkeypatch.delenv("STACK_AI_NO_VOICE", raising=False)
        assert self._ai_checks(tmp_path, ai_section) == ["Whisper", "LLM"]
