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
| `external-disk` | shipped | rsync + chflags uchg on attached APFS disk |
| `restic` | planned | encrypted, deduplicated, snapshotted offsite (S3/B2) |

Sources are discovered from other stacklets via a manifest contract, in
two kinds.

**`[[backup.archive]]`** is an append-only store: files are added, never
modified, never deleted. It syncs incrementally, so nothing is ever
re-copied.

```toml
# stacklets/photos/stacklet.toml
[[backup.archive]]
name = "library"
path = "{data_dir}/photos/library/library"
```

**`[[backup.snapshot]]`** is for state that cannot be rsynced. A database
changes under you continuously and a copy taken mid-write will not
restore, so it is dumped instead: one consistent `pg_dump` per run,
packed with whatever small files must travel beside it, into a dated
`.tar.gz`. Each run adds a tarball and never touches an older one, which
turns mutable state into the same append-only shape the vault keeps.

```toml
# stacklets/messages/stacklet.toml
[[backup.snapshot]]
name      = "synapse"
container = "stack-messages-db"
database  = "synapse"
user      = "synapse"
include   = ["{data_dir}/messages/synapse/homeserver.yaml",
             "{data_dir}/messages/synapse/*.signing.key"]
```

Snapshots run before the sync, so a dump is never newer than the media it
references, and their output directory then joins the source list as an
ordinary append-only source. `pg_dump` takes its own consistent view, so
nothing stops while it runs.

Each snapshot records the image versions and digests that produced it.
Nothing reads that yet; it is captured because it is the one part of a
snapshot that cannot be added afterwards, and a restore has to be able to
refuse an incompatible target.

Targets are configured in `stack.toml`. Today the only target is the
attached disk:

```toml
# stack.toml
[backup]
[backup.targets.vault]
engine = "external-disk"
disk   = "backup-vault"
```

Routing: every source, archive or snapshot, flows to every target whose
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

## Guarding the sources

Two checks run before anything is written.

The **canary** is a file with known contents planted under the data
directory. If it does not read back as planted, something is modifying
files in place and the sync aborts before the archive disk is opened.

The **shrink check** compares every source against the number of files it
held on the previous run, recorded in the engine's own history. A source
that is suddenly empty, or has lost more than half its files, aborts the
run.

That baseline used to be `min_files`, a constant each stacklet declared.
It could not work: a threshold written at authoring time cannot know the
scale of the household it guards, so a library of 50,000 photos reduced
to 11 passed `min_files = 10` without complaint. A source's own previous
count means the same thing at any size and needs no declaration.

A source that has never held anything is skipped rather than failing the
run, because a stacklet with no data yet must not cost the household
every other backup. Nothing is at risk in that case: the engine syncs
with `--ignore-existing` and never `--delete`, so an empty source copies
nothing and the vault keeps everything it already had. **That property is
load-bearing for the skip.** An engine that ever gains `--delete` has to
revisit this.

Snapshot staging directories are marked `rolling` by the coordinator and
exempt from the shrink check: they are pruned to a fixed window on
purpose, so shrinking is their normal operation.

## Destroy semantics

`stack destroy backup` removes the backup *tooling* — never the
*backups*. Specifically:

- **Removed:** cron entry, local logs, canary file under BACKUP_DATA_DIR.
- **Preserved:** every file on the archive disk. The whole point of an
  append-only archive is that it outlives the system that wrote it.
- **Preserved:** the macOS Keychain entry for the disk passphrase
  (encrypted archives only). The user may want manual disk access after
  uninstall; the command to remove it is surfaced if they want a fully
  clean state.
- **Preserved:** the Full Disk Access grant on `/usr/sbin/cron`. It
  also covers any other cron jobs on the system; the user can remove
  it manually if they prefer.

Defensive measure: `on_configure` refuses to let `BACKUP_DATA_DIR`
point at a path under `/Volumes/`. That way the framework's automatic
data-dir cleanup at destroy time can never accidentally reach external
storage.

## Recovery without restore tooling

The v1 engine writes plain files in plain directory structures. No
restore CLI exists yet — but you don't need one to get your photos
back:

```bash
# 1. Plug the archive disk into any Mac and unlock it (Finder prompts
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

The `external-disk` engine is ported and in use. Archives and snapshots
both sync; Matrix notifications go through the local `stacker-bot`.

Backed up today: Immich photo originals, Paperless archived PDFs, Matrix
uploads (voice messages, photos, files), and the Matrix timeline as
snapshots.

Not yet: Immich's and Paperless's databases. Both use the same
`[[backup.snapshot]]` contract the chat server already uses, so each is a
declaration plus a restore check rather than new design work. Without
them you get those files back but lose albums, tags, custom fields and
saved views.

Restore is still manual, by design for now. `stack backup restore` and
`on_restore` hooks are the planned shape.
