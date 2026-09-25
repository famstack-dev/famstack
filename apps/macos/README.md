# Menu bar app (prototype)

A native macOS menu bar app for the stack on this Mac: what is running, what
is failing, start, stop and restart, disk and memory.

It is a client of the `stack` CLI and nothing else. It runs `./stack status
--json` in a checkout every minute and when the panel opens, and runs
`./stack up|down|restart <id>` in the background. Anything interactive or
endless (a first install, logs, doctor) opens in Terminal. It starts no
services and keeps no state, so the CLI stays the one owner of the lifecycle.

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
