# backup — append-only backup of stacklet data

## What it does

Coordinates nightly backups of stacklet data to attached external disks.
The model is an **append-only archive**: files are added, never modified,
never deleted. Once a photo or document lands on the backup disk, the
kernel itself refuses to let anything change it. The threat model is
*ransomware, accidents, mistakes* — not a sophisticated targeted
attack.

See `engines/external-disk/README.md` for the full protection layers and
the rationale behind each one.

## Architecture

Backup is **a coordinator, not a backup tool**. The actual work is done
by *engines*, each of which implements one well-defined backup strategy
with explicit guarantees:

| Engine | Status | What it does |
|---|---|---|
| `external-disk` | scaffolded, port pending | rsync + chflags uchg on attached APFS disk |
| `restic` | planned | encrypted, deduplicated, snapshotted offsite (S3/B2) |

Sources are discovered from other stacklets via a manifest contract.
Every stacklet that declares `[[backup.archive]]` (an append-only store)
in its `stacklet.toml` contributes one source path to the next sync:

```toml
# stacklets/photos/stacklet.toml
[[backup.archive]]
name      = "library"
path      = "{data_dir}/photos/library/library"
min_files = 10
```

Targets are configured in `stack.toml`. Today the only target is the
attached disk:

```toml
# stack.toml
[backup]
[backup.targets.vault]
engine = "external-disk"
disk   = "backup-vault"
```

Routing: every `[[backup.archive]]` source flows to every target whose
engine supports append-only semantics. Adding a second target later
(offsite restic) is purely additive — no manifest change on photos/docs.

## CLI

```
stack backup sync     [--dry-run] [--no-eject] [--verbose]
stack backup status   # last run, source counts, cron presence
```

Per-stacklet aliases (`stack photos backup`, `stack docs backup`) and
restore (`stack backup restore --source=…`) are intentionally not in
v1 — they'll layer on once the engine port lands and the manifest
contract has been exercised on at least one production sync.

## Destroy semantics

`stack destroy backup` removes the backup *tooling* — never the
*backups*. Specifically:

- **Removed:** cron entry, FamstackVaultSync.app bundle, local logs,
  canary file under BACKUP_DATA_DIR.
- **Preserved:** every file on the vault disk. The whole point of an
  append-only archive is that it outlives the system that wrote it.
- **Preserved:** the macOS Keychain entry for the disk passphrase
  (encrypted vaults only). The user may want manual disk access after
  uninstall; the command to remove it is surfaced if they want a fully
  clean state.

Defensive measure: `on_configure` refuses to let `BACKUP_DATA_DIR`
point at a path under `/Volumes/`. That way the framework's automatic
data-dir cleanup at destroy time can never accidentally reach external
storage.

## Recovery without restore tooling

The v1 engine writes plain files in plain directory structures. No
restore CLI exists yet — but you don't need one to get your photos
back:

```bash
# 1. Plug the vault disk into any Mac and unlock it (Finder prompts
#    for the passphrase if encrypted)

# 2. Browse to the originals
ls /Volumes/backup-vault/data/photos/library/

# 3. Files are immutable. Unlock the ones you want to recover:
sudo chflags -R nouchg /Volumes/backup-vault/data/photos/library/

# 4. Copy them wherever you need
cp -R /Volumes/backup-vault/data/photos/library/ ~/recovered-photos/
```

This is the "survivalist" property the append-only design buys: no
special software needed to read the archive. The future restore CLI will
automate this and run stacklet-specific recovery via `on_restore`
hooks (DB import, search-index rebuild). For v1, manual recovery
is the documented path.

## Status

This stacklet is currently **scaffold only**. The hooks and CLI files
raise `NotImplementedError`. The next step is porting `vault-sync.sh`
from `family-server/backup/` into `engines/external-disk/`, with two
adaptations: source discovery via the manifest contract, and Matrix
notifications via the local `stacker-bot` instead of the legacy
`kit-control-bot`.
