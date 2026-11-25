# ADR-024: Snapshots and Their Manifest

## Status
Accepted, 2026-11-25.

## Context

A release can migrate a stacklet's data in one direction only. An update that breaks a page then has no way back. The
stack must keep the state from before the update and the record of what ran, on the same Mac.

A snapshot that one release writes is read by a later release. An example is a rollback from `v0.5.0` to the snapshot
that `v0.4.0` took. The layout of a snapshot and its record are thus a contract between releases.

## Decision

A snapshot is opt-in. The stack takes one only on request: `stack update --with-snapshot`, `stack restart
--with-snapshot` or `stack snapshot create`.

### The layout

The snapshots are in `.snapshots/` in the data folder. Each snapshot is one folder, named for its time in UTC:

```
~/famstack-data/.snapshots/
├── 2026-11-24T2114Z/        a snapshot
│   ├── MANIFEST.json        the release, the commit, the images and the migrations
│   ├── stack/               stack.toml, users.toml, .stack/
│   └── docs/                the data folder of docs, without its archive folders
├── .taking-2026-11-25T0100Z/  a snapshot that is not whole
└── replaced/
    └── 2026-11-25T2203Z/    the state that a rollback replaced, with ROLLBACK.json
```

- **`.snapshots/` starts with a dot.** No part of the stack reads it as the data folder of a stacklet, because a
  stacklet's id never starts with a dot.
- **A folder whose name starts with a dot is not whole.** A take writes into `.taking-<name>/` and renames the folder
  last. A remove renames the folder to `.removing-<name>/` first. A rollback never goes to such a folder.
- **A snapshot leaves out the archive folders** that a stacklet declares with `[[backup.archive]]`, for example
  Paperless's `media`. A rollback does not replace them. See the
  [stacklet reference](../stack-reference.md#backup).
- **The stacklets are stopped during the take,** because a database that writes during the copy gives a broken copy.
  On APFS each file is a clone. On another file system each file is a copy.

### The manifest

`MANIFEST.json` is the record of a snapshot. The take writes it last. A folder without it is thus not a snapshot.

```json
{
  "kind": "snapshot", "name": "2026-11-24T2300Z",
  "version": "0.4.0-rc.1", "commit": "f5834a67", "applies": "0.4.0-rc.2",
  "stacklets": {
    "docs": {"up": true, "commit": "f5834a67",
             "migrations": ["0001_move_tags"],
             "images": {"stack-docs-redis": {
                 "name": "docker.io/valkey/valkey:8.1-alpine",
                 "id": "sha256:8a1d…",
                 "digest": "valkey/valkey@sha256:9971…"}}}
  }
}
```

| Field | What it holds |
|---|---|
| `kind` | `snapshot`. A later backup writes the same file with its own kind. |
| `version`, `commit` | The release and the commit that the stacklets ran. |
| `applies` | The release that the update applies, for the snapshot of a restart. |
| `stacklets.<id>.up` | Whether the stacklet ran. A rollback starts only those that ran. |
| `stacklets.<id>.commit` | The commit that the stacklet started from. |
| `stacklets.<id>.migrations` | The migrations that ran on the stacklet's data. |
| `stacklets.<id>.images` | The image of each service: its name, its id, and its digest or the base of a built image. |

These rules keep the contract:

- **A release may add a field.** A release ignores a field that it does not know.
- **A release does not rename or remove a field,** because an older snapshot then reads as broken.
- **A broken file is an error, never an empty manifest.** A rollback from an empty manifest restores nothing and
  reports success.
- **A write is all or nothing.** The file is written beside the old one and renamed over it.

## Reason

A rollback goes back to the same code, the same images and the same migrations as before the update. The commit names
the code. An image tag can point to a new image on the next night. Thus only the id and the digest name the image that
ran. The list of migrations names the data changes that the snapshot holds.

A folder per snapshot with one record in it is simple to list, to remove and to read by hand. A clone on APFS takes
no space until the live file changes. Thus three snapshots cost little.

## Considered

- **An APFS snapshot of the volume** (`tmutil localsnapshot`). It holds the whole disk, not one stacklet. It cannot
  take back one stacklet and keep the others.
- **A database dump per stacklet.** Each service needs its own dump and restore, and a stacklet without a database
  has none. A clone of the stopped data folder works for every stacklet.

## Consequences

- A snapshot is not a backup. It stays on the same disk as the data, and Time Machine skips `.snapshots/`.
- A file in an archive folder that was renamed or deleted after the snapshot is not restored. The admin guide names
  the way out.
- A stacklet that is stopped at the update gets no snapshot. The admin takes one with `./stack snapshot create <id>`.
