# Creating a Stacklet

A stacklet is a self-contained service unit. It's a directory under `stacklets/` with a manifest, a Docker Compose file, and optional hooks.

## Minimal stacklet

```
stacklets/myapp/
  stacklet.toml          # manifest — required
  docker-compose.yml     # Docker services — required for Docker stacklets
```

That's it. Two files and `stack up myapp` works.

## The manifest: stacklet.toml

```toml
id          = "myapp"
name        = "My App"
description = "What it does in one line"
version     = "0.1.0"
category    = "productivity"      # productivity, communication, ai, etc.
port        = 42070               # primary web UI port

hints = [
    "Open {url} in your browser",
    "Log in as {admin_username} / {admin_password}",
]
```

### Template variables

Values in `{braces}` are resolved from `stack.toml` and runtime state:

| Variable | Source |
|---|---|
| `{timezone}` | `[core].timezone` |
| `{data_dir}` | `[core].data_dir` |
| `{domain}` | `[core].domain` |
| `{ip}` | LAN IP of the host |
| `{url}` | Public URL for this stacklet |
| `{admin_username}` | Tech admin username (`stackadmin`) |
| `{admin_email}` | Tech admin email (`stackadmin@home.local`) |
| `{admin_password}` | Tech admin password (generated, in secrets) |
| `{myapp_url}` | URL of any stacklet by ID |
| `{myapp_synapse_url}` | Named port URL (`[ports].synapse`) |

### Environment variables

```toml
[env]
generate = ["DB_PASSWORD", "SECRET_KEY"]   # auto-generated, stored in secrets

[env.defaults]
DATA_DIR       = "{data_dir}/myapp"
TZ             = "{timezone}"
ADMIN_USER     = "{admin_email}"
ADMIN_PASSWORD = "{admin_password}"
```

`generate` keys are random 32-char strings, created once, stable across restarts. `defaults` are rendered from template variables every time `stack up` runs.

### Health checks

```toml
# Simple — single URL
[health]
url = "http://localhost:42070/api/health"

# Multiple with failure hints
[[health.checks]]
url  = "http://localhost:42070/"
hint = "Web UI not responding — check 'docker logs stack-myapp'"

[[health.checks]]
url  = "http://localhost:42071/api"
hint = "API not responding"

# With auth headers (template vars supported)
[[health.checks]]
url  = "{some_url}/status"
hint = "Backend not reachable"
[health.checks.headers]
Authorization = "Bearer {some_key}"
```

Health checks run during `stack list` to determine online/degraded state.

### Dependencies

```toml
requires = ["messages"]   # must be set up before this stacklet
```

`stack up myapp` will refuse if dependencies aren't set up, with a helpful error message.

## Docker Compose

Follow these conventions:

```yaml
name: stack-myapp                    # project name: stack-{id}

services:
  stack-myapp:                       # container name: stack-{id}
    container_name: stack-myapp
    image: someimage:latest
    labels:
      - "com.centurylinklabs.watchtower.enable=${WATCHTOWER_ENABLE:-true}"
    networks:
      - stack                     # shared network
    ports:
      - "${PORT_BIND_IP:-0.0.0.0}:42070:8080"   # use PORT_BIND_IP
    volumes:
      - ${DATA_DIR}:/data            # use env vars from [env.defaults]
    env_file: .env                   # framework writes this
    restart: unless-stopped

networks:
  stack:
    external: true
```

Key points:
- **Project name**: `stack-{id}` — framework uses this to track container state
- **Network**: `stack` (external) — all stacklets share it, can talk to each other
- **Ports**: Use `PORT_BIND_IP` so port binding works in both dev and production
- **env_file**: `.env` — the framework renders this from your `[env.defaults]`
- **Volumes**: Use env vars, not hardcoded paths

## Hooks

Optional lifecycle scripts in a `hooks/` directory:

```
stacklets/myapp/
  hooks/
    on_configure.py       # first-run config (interactive prompts)
    on_install.sh         # first-run setup (install dependencies)
    on_start.py           # every stack up, before compose (config validation)
    on_install_success.py # after first successful health check
    on_start_ready.py     # every stack up, after health checks (seed data, sync accounts)
    on_stop.sh            # every stack down
    on_destroy.sh         # full teardown
```

Python hooks (preferred) get a `ctx` object:

```python
def run(ctx):
    ctx.step("Creating admin account")      # report progress
    ctx.shell("some-command")               # run shell command
    ctx.secret("API_KEY")                   # read a secret
    ctx.secret("API_KEY", "value")          # write a secret
    ctx.stack.run_cli_command("ai", "hello") # call other stacklets

    env = ctx.env                           # rendered env vars
    stack = ctx.stack                       # full Stack instance
```

Shell hooks (`.sh` fallback) get env vars and stream output directly.

For interactive prompts in hooks, use the shared TUI primitives:

```python
from stack.prompt import section, out, nl, ask, confirm, done

def run(ctx):
    section("My App", "Configure your widget")
    name = ask("Widget name")
    if not confirm(f"Use '{name}'?"):
        raise RuntimeError("Aborted")
    done(f"Widget configured: {name}")
```

## CLI plugins

Add commands under `cli/`:

```
stacklets/myapp/
  cli/
    status.py     # stack myapp status
    migrate.py    # stack myapp migrate
```

Each plugin defines a `run(args, stacklet, config)` function:

```python
HELP = "Show widget status"

def run(args, stacklet, config):
    data_dir = config["data_dir"]
    # ... do work
    return {"status": "ok"}
```

## Port allocation

Famstack uses the 42000-42099 range:

| Port | Stacklet |
|---|---|
| 42010 | photos |
| 42020 | docs |
| 42030-42031 | messages (element, synapse) |
| 42040 | code |
| 42050 | chatai |
| 42060-42063 | ai (omlx, lmstudio, whisper, speech) |

Pick an unused port in this range.

## Example: the Code stacklet

The simplest real stacklet — Forgejo with SQLite, no hooks, no secrets:

**`stacklets/code/stacklet.toml`**
```toml
id          = "code"
name        = "Code"
description = "Self-hosted Git server (Forgejo)"
version     = "0.1.0"
category    = "productivity"
port        = 42040

hints = [
    "Open {url} and register your admin account",
    "SSH clone: ssh://git@{ip}:222/user/repo.git",
]

[env.defaults]
CODE_DATA_DIR = "{data_dir}/code"
TZ            = "{timezone}"

[health]
url = "http://localhost:42040/api/healthz"
```

**`stacklets/code/docker-compose.yml`**
```yaml
name: stack-code

services:
  stack-code:
    container_name: stack-code
    image: codeberg.org/forgejo/forgejo:14
    networks:
      - stack
    ports:
      - "${PORT_BIND_IP:-0.0.0.0}:42040:3000"
      - "${PORT_BIND_IP:-0.0.0.0}:222:22"
    volumes:
      - ${CODE_DATA_DIR}:/data
      - /etc/localtime:/etc/localtime:ro
    env_file: .env
    restart: unless-stopped

networks:
  stack:
    external: true
```

That's 20 lines of TOML + 17 lines of YAML. Run `stack up code` and you have a Git server.
