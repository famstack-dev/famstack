"""Unit tests for on_install's pure helpers: canary planting and .app
bundle generation.

The interactive FDA walkthrough and crontab plumbing are tested
elsewhere (test_backup_cron).
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "stacklets" / "backup" / "hooks"))
sys.path.insert(0, str(REPO_ROOT / "lib"))

import on_install  # noqa: E402


class TestGenerateAppBundle:
    @pytest.fixture
    def bundle(self, tmp_path):
        """Generate an .app bundle into tmp_path and return its path."""
        return on_install.generate_app_bundle(
            target_dir=tmp_path,
            stack_executable=Path("/path/to/stack"),
            log_path=tmp_path / "logs" / "cron.log",
        )

    def test_bundle_directory_structure(self, bundle):
        # Classic macOS .app layout — Contents/{Info.plist, MacOS/<exec>}
        assert bundle.is_dir()
        assert bundle.name == "FamstackVaultSync.app"
        assert (bundle / "Contents" / "Info.plist").is_file()
        assert (bundle / "Contents" / "MacOS" / "vault-sync").is_file()

    def test_info_plist_identifies_bundle(self, bundle):
        plist = (bundle / "Contents" / "Info.plist").read_text()
        # macOS looks these up when launching the bundle.
        assert "<key>CFBundleExecutable</key>" in plist
        assert "<string>vault-sync</string>" in plist
        assert "<key>CFBundleIdentifier</key>" in plist
        assert "<string>dev.famstack.backup</string>" in plist

    def test_info_plist_is_background_app(self, bundle):
        # LSUIElement=true keeps the bundle out of the dock and
        # Cmd-Tab — there's no UI, no reason to surface it as a
        # foreground app.
        plist = (bundle / "Contents" / "Info.plist").read_text()
        assert "<key>LSUIElement</key>" in plist
        assert "<true/>" in plist

    def test_info_plist_has_valid_xml_header(self, bundle):
        plist = (bundle / "Contents" / "Info.plist").read_text()
        # macOS is picky about the DOCTYPE; missing or malformed
        # headers cause silent launch failures.
        assert plist.startswith('<?xml version="1.0"')
        assert "<!DOCTYPE plist PUBLIC" in plist
        assert plist.rstrip().endswith("</plist>")

    def test_executable_is_executable(self, bundle):
        wrapper = bundle / "Contents" / "MacOS" / "vault-sync"
        mode = wrapper.stat().st_mode
        # User, group, and other all need x bit; cron may run with a
        # narrower umask and the .app must still launch.
        assert mode & stat.S_IXUSR
        assert mode & stat.S_IXGRP
        assert mode & stat.S_IXOTH

    def test_wrapper_invokes_stack_backup_sync(self, bundle):
        wrapper = (bundle / "Contents" / "MacOS" / "vault-sync").read_text()
        assert "/path/to/stack" in wrapper
        assert "backup sync" in wrapper

    def test_wrapper_redirects_output_to_log(self, bundle, tmp_path):
        wrapper = (bundle / "Contents" / "MacOS" / "vault-sync").read_text()
        # Cron output is invisible by default — the wrapper must redirect
        # to a known log path so a misbehaving scheduled run leaves a
        # trail the user can inspect.
        assert str(tmp_path / "logs" / "cron.log") in wrapper
        assert "2>&1" in wrapper

    def test_idempotent_regeneration(self, tmp_path):
        # Running install twice should leave the same bundle, not pile
        # up duplicates or stale state.
        on_install.generate_app_bundle(
            tmp_path, Path("/p/stack"), tmp_path / "logs" / "cron.log"
        )
        on_install.generate_app_bundle(
            tmp_path, Path("/p/stack"), tmp_path / "logs" / "cron.log"
        )
        # Still one .app, structure intact.
        bundles = list(tmp_path.glob("*.app"))
        assert len(bundles) == 1
        assert (bundles[0] / "Contents" / "Info.plist").is_file()


class TestPlantCanary:
    def test_writes_canary_with_expected_content(self, tmp_path):
        on_install.plant_canary(tmp_path)
        canary = tmp_path / "canary"
        assert canary.is_file()
        assert canary.read_text().strip() == on_install.CANARY_STRING

    def test_idempotent_does_not_clobber_existing(self, tmp_path):
        # An existing canary that's already been verified across syncs
        # must survive a re-run of install. Clobbering it would make a
        # tampered state indistinguishable from a fresh install.
        canary = tmp_path / "canary"
        canary.write_text("user-edited content (or already-verified canary)\n")
        on_install.plant_canary(tmp_path)
        assert canary.read_text() == "user-edited content (or already-verified canary)\n"

    def test_canary_string_matches_engine(self):
        # The planter writes what the verifier expects — they share the
        # constant via import, so this is really a regression guard
        # against someone redefining it in either file.
        engine_dir = REPO_ROOT / "stacklets" / "backup" / "engines" / "external-disk"
        sys.path.insert(0, str(engine_dir))
        from sync import CANARY_STRING as ENGINE_CANARY
        assert on_install.CANARY_STRING == ENGINE_CANARY
