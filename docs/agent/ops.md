# AGENT - Operator role

Goal: run famstack on a Mac safely. Install, lifecycle, troubleshoot,
back up. **No code changes from this role** - that is the engineer role
(see [dev.md](dev.md)).

For full prose, see [../admin-guide.md](../admin-guide.md). This file is
the compact decision layer; the admin guide is the manual.

## Mental model in 3 lines

1. `stack.toml` (per-host config) + each stacklet's `stacklet.toml` → render `.env` on every `stack up`. `.env` is **derived**, never edit it.
2. Every stacklet lives under `stacklets/<id>/`. All persistent data lives under `~/famstack-data/<id>/`. No exceptions.
3. State is **derived**, not stored. Container existence == "running"; container absence + data dir absence == "available". There is no enabled-list.

## Hardware preconditions

- macOS on Apple Silicon (M1+).
- 16 GB RAM minimum; 32 GB recommended (AI quality, especially non-English).
- Homebrew installed. It is the only manual dep - it pulls everything else.
- OrbStack (preferred) or Docker Desktop running.

If a precondition is missing, `./stack` prints exactly what to do. Don't improvise.

## Command map: idempotency & blast radius

| Command | Idempotent | Destructive | Touches |
|---|---|---|---|
| `./stack` | yes | no | interactive installer (first-run path) |
| `./stack up <id>` | yes | no | starts containers, renders `.env`, runs hooks |
| `./stack down <id>` | yes | no | stops containers; data preserved |
| `./stack down all` | yes | no | stops every running stacklet in reverse dep order |
| `./stack restart <id>` | yes | no | `down` + `up` |
| `./stack destroy <id>` | yes | **YES** | removes containers + `~/famstack-data/<id>/` + secrets |
| `./stack uninstall` | yes | **YES, EVERYTHING** | destroys every stacklet, network, all data, config |
| `./stack list` | yes | no | reports state |
| `./stack status` | yes | no | runs health checks |
| `./stack logs <id>` | yes | no | tail container logs |
| `./stack errors` | yes | no | recent error logs (24h) |
| `./stack host` | yes | no | disk / memory / uptime |
| `./stack updates` | yes | no | checks for newer Docker images |
| `./stack config [--secrets]` | yes | no | prints resolved config |
| `./stack ai models` | yes | no | lists installed AI models |
| `./stack setup ai` | yes | no | re-runs AI install (model swap path) |

**Output contract:** every command returns JSON when piped or when `--json` is passed. Force human output with `--pretty`. Exit code 0 == success.

## Invariants

- `stack up` is **always safe to re-run.** Failures leave the system in a valid intermediate state; re-running picks up where it failed.
- `.env` is a **derived artifact.** Never edit by hand. Overwritten on every `stack up`.
- `.stack/secrets.toml` is **gitignored** and contains generated passwords + tokens. Treat as a password export.
- Data lives **outside the repo** at `~/famstack-data/<id>/`. That's the only mandatory backup.
- `~/.omlx/models/` holds LLM model files. Re-downloadable; not in `data_dir`.
- **Two famstack instances cannot run on the same Mac at the same time** - container names collide (`stack-<id>`). `stack down` the active one first.

## Danger zones

Refuse without explicit, scoped human approval:

| Action | Why |
|---|---|
| `./stack destroy <id>` | Deletes `~/famstack-data/<id>/` permanently. No undo. |
| `./stack uninstall` | Wipes every stacklet's data, secrets, runtime state, and config. |
| `rm -rf ~/famstack-data/...` | Bypasses the CLI's safety prompts. |
| Editing `.env` directly | Will be silently overwritten next `stack up`. |
| Editing `.stack/secrets.toml` | Breaks every stacklet that depends on the changed secret. |
| Moving `data_dir` while stacklets are running | Bind mounts break. `stack down all` first, then move, then `stack up`. |
| Changing `stack.toml [core] language` | Re-seeds Paperless taxonomy. Orphan tags require manual cleanup. |
| Switching `[core] domain` empty ↔ non-empty | Switches port mode ↔ domain mode. Requires wildcard DNS + Caddy understanding. |

## Ports (42xxx range)

| Port | Stacklet | Service |
|---|---|---|
| 42010 | photos | Immich web + API |
| 42020 | docs | Paperless-ngx |
| 42030 | messages | Element web |
| 42031 | messages | Synapse (Matrix homeserver - phones connect here) |
| 42040 | code | Forgejo web |
| 222 | code | Forgejo SSH (macOS uses 22 itself) |
| 42050 | chatai | Open WebUI |
| 42060 | ai | oMLX |
| 42062 | ai | Whisper |

Port collisions: do not silently rebind. Surface them. The user's fix is "stop the offender" or switch to domain mode.

## Troubleshooting decision table

| Symptom | First check | Likely cause |
|---|---|---|
| "Docker is not running" | OrbStack/Docker Desktop icon present? | App not started. |
| "Port 42xxx already in use" | `lsof -nP -iTCP:<port> -sTCP:LISTEN` | Another service grabbed it. |
| Phone can't reach server | Same Wi-Fi? Guest network isolating clients? | Network isolation. |
| "Server not Matrix" on phone | `./stack status` → messages | Synapse down. `./stack restart messages`. |
| Mac IP keeps changing | Router DHCP behaviour | Set DHCP reservation on the router. |
| AI install fails at whisper.cpp build | `xcode-select -p` | Xcode CLT missing. `xcode-select --install`. |
| `brew: command not found` after install | PATH on Apple Silicon | Add `eval "$(/opt/homebrew/bin/brew shellenv)"` to `~/.zshrc`. |
| LLM OOM / very slow | RAM tier | Edit `[ai] default` to smaller model, then `./stack setup ai`. |
| Disk full | `./stack host` | Likely the photo library. Move `data_dir` to external SSD. |
| Element warns "browser not supported" | n/a | Click Continue. The check is outdated; Element works in every modern browser. |

For symptoms not on this table: `./stack logs <id>` + `./stack errors`, paste output to the user. **Do not invent fixes.**

## Backups

- **`~/famstack-data/`** - mandatory. Time Machine, restic, or rsync. Pick one.
- **`stack.toml`, `users.toml`, `.stack/secrets.toml`** - small, irreplaceable, gitignored. One-liner:
  ```bash
  tar czf famstack-config-$(date +%F).tgz stack.toml users.toml .stack/
  ```
- **`~/.omlx/models/`** - re-downloadable; skip.

## Boundaries

This role describes operating an existing famstack install. Do NOT, from the operator role:

- Edit code in `stacklets/`, `lib/`, or `tests/`. That's the engineer role; see [dev.md](dev.md).
- Create or merge PRs.
- Run `./stack uninstall` to "start fresh" without explicit user approval.
- Suggest exact config strings (URLs, ports, IPs, field names) from memory. Verify against `stack.toml`, the user guide, or `./stack config`.

When in doubt: `./stack status` and ask.
