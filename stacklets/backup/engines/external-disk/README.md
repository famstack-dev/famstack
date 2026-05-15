# external-disk engine

Backs up stacklet data to an APFS-formatted external disk attached via
USB or Thunderbolt. The disk is mounted by macOS itself (auto-mount for
plain APFS, login-time Keychain unlock for encrypted) and the engine
writes to it as it finds it.

The engine implements **append-only archive** semantics — in the
storage-industry vocabulary this is called WORM (Write Once Read Many).
The user-facing word is "archive"; "WORM" stays as the technical term
for engine internals and threat-model discussion.

## Guarantees

This engine commits to three guarantees. If any of them cannot be
satisfied, the engine **refuses to run** rather than degrading silently.

1. **Kernel-enforced immutability.** Every file on the destination has
   the BSD `uchg` flag set. The kernel refuses modify, delete, rename,
   and unlink, even to the owner.
2. **Append-only.** rsync runs with `--ignore-existing`. Existing files
   on the destination are never touched. No `--delete`.
3. **Zero unlock window.** Files are never unlocked during a sync —
   `--ignore-existing` skips them entirely, so the immutability flag
   stays on. Only newly-written files get the flag applied afterward.

Eject after sync is a best-effort bonus, not a guarantee. From an
interactive Terminal session, `diskutil eject` works and the disk goes
offline; from the scheduled cron context, eject is sandbox-blocked and
the disk stays mounted. The three guarantees above carry the append-
only contract on their own — eject just adds an extra "invisible to OS"
layer on top when it's available.

## Why not network shares

SMB and NFS mounts technically appear under `/Volumes/`, but they break
every one of the guarantees above:

- `chflags uchg` is a BSD filesystem flag — not transmitted over SMB or
  NFS. The kernel can't enforce immutability on a remote filesystem.
- The share stays reachable as long as the network is up; there's no
  "physical disconnect" protection layer available.
- APFS encryption is irrelevant; the bytes live on the NAS, not here.

If `external-disk` detects a non-APFS / non-HFS filesystem on the
destination, it aborts with a message pointing the user at the future
`restic` engine. NAS-based backup will use restic's own append-only
mode, which is enforced by the restic repo format and works over SFTP/
SMB/S3 alike.

## Sandbox notes

`diskutil` operations (mount, eject, unlock) are restricted by macOS TCC
when called from `cron`, `launchd`, or any binary that hasn't been
granted Full Disk Access. The fix is the `.app` wrapper: a minimal app
bundle whose only purpose is to receive the FDA grant. Cron invokes it
via `open /path/to/FamstackVaultSync.app`, which routes through the
proper macOS app lifecycle and inherits the FDA permission.

`diskutil eject` from cron is sandbox-blocked even with FDA. The disk
stays mounted after scheduled runs (uchg flags still protect the data);
manual runs from Terminal eject normally.

See `family-server/backup/docs/MACOS-SANDBOX-BACKUP-SCRIPT.md` for the
full history of approaches that were tried and failed.

## Files

| File | Status | Role |
|---|---|---|
| `sync.py` | shipping | the append-only sync (ported from family-server) |
| `restore.py` | pending | copy files back to a target path, remove `uchg` flags |

The filesystem capability check lives inside `sync.py` as
`probe_filesystem()` rather than a separate file — it's one function
call, no separate process needed.

## Input contract

`sync.py` reads three environment variables. The orchestrator
(`cli/sync.py`) is responsible for populating them.

| Variable | Purpose |
|---|---|
| `BACKUP_DATA_DIR` | Host-side state directory (canary, logs, result JSON). Refused if under `/Volumes/`. |
| `VAULT_DISK` | APFS volume name. Mount point is `/Volumes/<name>`. |
| `SOURCES` | Newline-separated, pipe-delimited records: `<id>\|<display>\|<src_path>\|<vault_subdir>\|<min_files>` |

Arguments are POSIX-style: `--dry-run`, `--no-eject`, `--verbose`,
`--verify`.

## Output contract

| Output | Always written? | Schema / purpose |
|---|---|---|
| stdout | yes | Human-readable progress (TTY-aware coloring) |
| stderr | on failure | Warnings + errors |
| `$BACKUP_DATA_DIR/logs/sync.log` | yes (best-effort) | Human-readable audit log |
| `$BACKUP_DATA_DIR/logs/history.jsonl` | yes — even on crash | One JSON object per run, append-only |
| Exit code | yes | `0` ok, `1` hard failure |

### `history.jsonl` schema

Each line is a self-contained JSON object representing one run. The
caller reads the latest run by tail-scanning the file (the last good
JSON line wins). New runs append; old runs are never modified.

```json
{
  "success": true,
  "dry_run": false,
  "failure_reason": null,
  "duration_seconds": 125,
  "started_at": "2026-05-14T02:00:00Z",
  "ended_at": "2026-05-14T02:02:05Z",
  "run_context": "cron",
  "run_user": "arthur",
  "vault_disk": "backup-vault",
  "vault_state": "mounted",
  "vault_size": "8.2G",
  "sources": [
    {
      "id": "photos/library",
      "display": "Photos",
      "status": "ok",
      "total_files": 48293,
      "new_files": 12
    }
  ]
}
```

`vault_state` is one of `mounted`, `ejected`, `not_connected`. The
orchestrator treats an empty or missing `history.jsonl` as "the engine
crashed before it could report" — distinct from a written failure run.

Writes are atomic: each line is one `write()` syscall under the POSIX
PIPE_BUF limit (4KB), so concurrent appenders and crashed engines
can't interleave or truncate a record mid-line.

## Testing

`tests/stacklets/test_backup_engine.py` covers the pure-Python parts:
source parsing, canary creation and tampering, preflight thresholds,
filesystem capability classification, result-file shape. The
rsync/diskutil/eject flows need a real disk and aren't part of the
unit suite.

Run with: `uv run --extra test pytest tests/stacklets/test_backup_engine.py`
