# Menu bar app (beta)

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
cp -R apps/macos/build/famstack.app /Applications/
open /Applications/famstack.app
```

The build is signed ad hoc, for the Mac it was built on. After
`./stack update`, build and copy it again.

To check a layout change without opening the menu, render both tabs from the
real data to PNG files (`<prefix>-overview.png`, `<prefix>-setup.png`):

```bash
apps/macos/build/famstack.app/Contents/MacOS/StackMenu --snapshot /tmp/panel
```

## Which machine

**Setup → Connection** switches between a checkout on this Mac and one on
another Mac, and remembers both.

- **This Mac**: the folder you run `./stack` from, chosen with **Change…**.
  Without a choice the app tries `~/famstack`.
- **Remote**: an SSH host (an alias from `~/.ssh/config` or `user@host`) and
  the checkout path on it, `~/famstack` unless you say otherwise. The app
  runs `ssh <host> 'zsh -lc "cd <checkout> && ./stack …"'` with your SSH key
  and never asks for a password, so the key must work without one (or be in
  the agent). The server needs **Remote Login** turned on. Logs, doctor and
  editing open Terminal with an SSH session to that Mac; stacklets open at the
  address the stack names for them, its LAN address in port mode.

Every call gives up after a minute, lifecycle actions after fifteen, so an
unreachable machine shows an error instead of a panel that never updates.

To set the local checkout from a shell:

```bash
defaults write dev.famstack.menubar checkout /path/to/famstack
```
