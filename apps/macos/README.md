# Menu bar app (prototype)

A native macOS menu bar app for the stack on this Mac. It answers, in this
order: is everything fine, what needs me, what is running, what broke
recently, is the data backed up, and how full is the machine.

- **Needs attention**: `stack doctor` findings, backups (missing, failed or
  overdue), a disk above 90%. Each item carries its fix command; `up`,
  `restart` and `down` run in the background, anything else opens in Terminal.
- **Stacklets**: state, memory, port; start, stop, restart, open, logs.
- **Errors**: containers that logged errors in the last 24 hours, newest first.
- **This Mac**: disk, memory, uptime.

The icon turns into a warning only for errors: a failing stacklet, a doctor
error, a failed backup. Warnings stay in the panel.

It is a client of the `stack` CLI and nothing else: `status --json` every
minute, and `doctor --json`, `errors --json`, `host --json` and `backup
status` when the panel opens and every ten minutes. It starts no services and
keeps no state, so the CLI stays the one owner of the lifecycle. Against a
checkout older than those commands, the sections that need them are left out.

## Build and run

Needs macOS 14 and the Command Line Tools; no Xcode.

```bash
apps/macos/build.sh          # prints the path of build/famstack.app
open apps/macos/build/famstack.app
```

## Which checkout

The app runs `./stack` from the checkout you choose in the panel
(**Checkout…**), and remembers it. Without a choice it tries `~/famstack`.
To set it from a shell:

```bash
defaults write dev.famstack.menubar checkout /path/to/famstack
```
