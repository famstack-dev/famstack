"""Unit tests for `stack backup status`.

The command is pure reads: it never syncs, mounts, or touches the
crontab for real here — `cron.is_installed` is patched. JSON output
(stdout not a TTY under pytest) is parsed and asserted.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "stacklets" / "backup" / "cli"))
sys.path.insert(0, str(REPO_ROOT / "stacklets" / "backup" / "engines" / "external-disk"))
sys.path.insert(0, str(REPO_ROOT / "stacklets" / "backup"))
sys.path.insert(0, str(REPO_ROOT / "lib"))

import status  # noqa: E402
from sync import CANARY_STRING  # noqa: E402


def _config(tmp_path: Path, *, disk: str = "backup-vault") -> dict:
    return {
        "data_dir": str(tmp_path),
        "manifest": {"env": {"defaults": {"BACKUP_DATA_DIR": "{data_dir}/backup"}}},
        "stack": {
            "backup": {
                "targets": {
                    "vault": {
                        "engine": "external-disk",
                        "disk": disk,
                        "schedule": "0 2 * * *",
                    }
                }
            }
        },
    }


def _write_history(tmp_path: Path, entry: dict) -> None:
    logs = tmp_path / "backup" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "history.jsonl").write_text(json.dumps(entry) + "\n")


def _plant_canary(tmp_path: Path, content: str) -> None:
    backup = tmp_path / "backup"
    backup.mkdir(parents=True, exist_ok=True)
    (backup / "canary").write_text(content)


@pytest.fixture(autouse=True)
def _no_real_crontab(monkeypatch):
    # Never shell out to the real crontab during a status read.
    monkeypatch.setattr(status.cron, "is_installed", lambda name: True)


def test_no_targets_returns_error(tmp_path):
    config = {"data_dir": str(tmp_path), "manifest": {}, "stack": {}}
    result = status.run([], None, config)
    assert "error" in result


def test_reports_last_run_for_matching_disk(tmp_path, capsys):
    _plant_canary(tmp_path, CANARY_STRING + "\n")
    _write_history(tmp_path, {
        "success": True,
        "vault_disk": "backup-vault",
        "ended_at": "2026-06-14T02:03:00Z",
        "duration_seconds": 192,
        "vault_size": "412G",
        "sources": [
            {"id": "photos/library", "display": "Photos",
             "status": "ok", "total_files": 48293, "new_files": 12},
        ],
    })

    result = status.run([], None, _config(tmp_path))
    assert result["ok"] is True

    payload = json.loads(capsys.readouterr().out)
    target = payload["targets"][0]
    assert target["name"] == "vault"
    assert target["canary"] == "intact"
    assert target["cron_installed"] is True
    assert target["last_run"]["vault_size"] == "412G"


def test_canary_missing_is_flagged(tmp_path, capsys):
    # No canary planted.
    result = status.run([], None, _config(tmp_path))
    assert result["ok"] is True
    payload = json.loads(capsys.readouterr().out)
    assert payload["targets"][0]["canary"] == "missing"


def test_canary_tampered_is_flagged(tmp_path, capsys):
    _plant_canary(tmp_path, "not the canary you are looking for\n")
    status.run([], None, _config(tmp_path))
    payload = json.loads(capsys.readouterr().out)
    assert payload["targets"][0]["canary"] == "tampered"


def test_other_targets_run_is_not_picked_up(tmp_path, capsys):
    # History only has a run for a different disk; this target reports
    # "never" rather than borrowing the other target's result.
    _plant_canary(tmp_path, CANARY_STRING + "\n")
    _write_history(tmp_path, {
        "success": True, "vault_disk": "some-other-disk",
        "ended_at": "2026-06-14T02:03:00Z",
    })
    status.run([], None, _config(tmp_path, disk="backup-vault"))
    payload = json.loads(capsys.readouterr().out)
    assert payload["targets"][0]["last_run"] is None
