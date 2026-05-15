"""stack backup status — show the last run and current target health.

Reports per target:
  - When it was last synced and whether it succeeded
  - Per-source file counts (from the source paths on the SSD)
  - Whether the canary file is intact
  - Whether the cron entry for the next scheduled run is wired
  - Whether the disk is currently mounted

A mounted disk is the steady-state expectation for scheduled mode —
eject is sandbox-blocked from cron, so the disk stays mounted between
runs and files are kernel-immutable. "Not mounted" only deserves a
warning when:
  - The disk is encrypted and hasn't been unlocked since reboot
  - The disk is physically disconnected
  - The previous scheduled run failed to find /Volumes/<disk>

When the disk is mounted, the command also reports vault size, per-
source file counts on the vault, and free space. When it isn't, those
fields show the last known values from the previous successful run.

Outputs JSON when piped, human-readable otherwise. Matches the
convention of `stack status`, `stack errors`, `stack host`.
"""

HELP = "Show last-run status and target health"

raise NotImplementedError(
    "backup stacklet scaffold — see stacklets/backup/README.md"
)
