# Menu bar app (prototype)

A native macOS menu bar app for the stack on this Mac. **Overview** answers,
in this order: is everything fine, what needs me, what is running, what
broke recently, and how full is the machine. **Setup** shows how the stack is
configured, what is not installed yet, and which checkout the app uses.

- **Needs attention**: `stack doctor` findings, a backup that failed or is
  overdue (when the beta backup stacklet is installed), a disk above 90%. Each item carries its fix command; `up`,
  `restart` and `down` run in the background, anything else opens in Terminal.
- **Stacklets**: state, memory, port; start, stop, restart, open, logs.
- **Errors**: containers that logged errors in the last 24 hours, newest first.
- **This Mac**: disk, memory, uptime.
- **Setup**: family members, address mode, language, AI, update time, data
  dir, from `stack config --json` (credentials hidden), with buttons that open
  `stack.toml` and `users.toml` in the text editor.

The menu bar shows the famstack mark in monochrome, as macOS expects. It
gets a badge dot only for errors: a failing stacklet, a doctor error, a
failed backup. Warnings stay in the panel. Its dots turn hollow while an
action runs, and the mark dims when the CLI does not answer.

The mark is drawn from `Sources/StackMenu/LogoOutline.swift`, generated
once from Inter Bold by `script/logo-outline.py`; the app icon is rendered
from the same drawing at build time.

It is a client of the `stack` CLI and nothing else: `status --json` every
minute, and `doctor --json`, `errors --json`, `host --json`, `config --json`
and `backup status` when the panel opens and every ten minutes. It starts no services and
keeps no state, so the CLI stays the one owner of the lifecycle. Against a
checkout older than those commands, the sections that need them are left out.

## Build and run

Needs macOS 14 and the Command Line Tools; no Xcode.

```bash
apps/macos/build.sh          # prints the path of build/famstack.app
open apps/macos/build/famstack.app
```

To check a layout change without opening the menu, render both tabs from the
real data to PNG files (`<prefix>-overview.png`, `<prefix>-setup.png`):

```bash
apps/macos/build/famstack.app/Contents/MacOS/StackMenu --snapshot /tmp/panel
```

## Which checkout

The app runs `./stack` from the checkout you choose in the panel
(**Checkout…**), and remembers it. Without a choice it tries `~/famstack`.
To set it from a shell:

```bash
defaults write dev.famstack.menubar checkout /path/to/famstack
```
