"""Starting the AI stacklet when its engine is no longer on the Mac.

First-run setup installs oMLX and records that setup is done. The install
hook is gated on that marker, so it never runs again -- which is fine
until the binary disappears underneath it. A Python upgrade that breaks
its virtualenv, a brew cleanup, a migrated machine: all leave a stacklet
that reports itself set up and starts its containers happily, with a
failing LLM health check as the only clue.

`stack up ai` now says what is wrong before starting anything.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))

# Every stacklet has hooks with the same module names, so load this one
# from its path under its own name rather than by bare import.
_spec = importlib.util.spec_from_file_location(
    "ai_hooks_on_start",
    _REPO_ROOT / "stacklets" / "ai" / "hooks" / "on_start.py")
on_start = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(on_start)


class FakeCtx:
    def __init__(self, **cfg):
        self._cfg = cfg
        self.env: dict = {}

    def cfg(self, key, default=None):
        return self._cfg.get(key, default)


class TestManagedProviderNeedsItsEngine:

    def test_a_missing_engine_stops_the_start(self, monkeypatch):
        monkeypatch.setattr(on_start.shutil, "which", lambda _cmd: None)
        with pytest.raises(RuntimeError, match="oMLX missing"):
            on_start.run(FakeCtx(provider="managed"))

    def test_an_installed_engine_starts_normally(self, monkeypatch):
        monkeypatch.setattr(on_start.shutil, "which",
                            lambda _cmd: "/opt/homebrew/bin/omlx")
        on_start.run(FakeCtx(provider="managed"))  # must not raise

    def test_an_external_endpoint_does_not_need_a_local_engine(self, monkeypatch):
        """Someone pointing at their own server has no business being told
        to install oMLX."""
        monkeypatch.setattr(on_start.shutil, "which", lambda _cmd: None)
        on_start.run(FakeCtx(provider="external",
                             openai_url="https://ai.example.test/v1"))


class TestExistingGuardsStillHold:

    def test_no_provider_configured_still_stops(self, monkeypatch):
        monkeypatch.setattr(on_start.shutil, "which", lambda _cmd: None)
        with pytest.raises(RuntimeError, match="provider not configured"):
            on_start.run(FakeCtx())

    def test_external_without_a_url_still_stops(self, monkeypatch):
        monkeypatch.setattr(on_start.shutil, "which",
                            lambda _cmd: "/opt/homebrew/bin/omlx")
        with pytest.raises(RuntimeError, match="Missing openai_url"):
            on_start.run(FakeCtx(provider="external"))
