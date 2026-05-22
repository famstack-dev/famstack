# pi-dev

Sandboxed [pi coding agent](https://github.com/earendil-works/pi) for working
on famstack on the server. Pi runs in a Docker container with the current
repo mounted as the workspace; data directories are opt-in.

## Threat model

This protects the **host** from malicious npm packages bundled into pi or its
transitive dependencies. It does **not** restrict the container's network.
If you mount a data directory while running an untrusted snapshot of pi, that
data can be exfiltrated.

What the sandbox does:

- Runs as the unprivileged `node` user (UID 1000) inside the container.
- Drops all Linux capabilities and disables privilege escalation.
- Root filesystem is read-only; only the workspace, data mounts, `/tmp`,
  and `~/.pi` are writable.
- No host SSH keys, no docker socket, no host home directory exposed.
- Pi is installed with `--ignore-scripts` (blocks postinstall hooks).

What it doesn't do:

- No network restriction. The container can reach anything you can reach.
- No CPU/memory limits.
- No protection against bugs in Docker itself.

## One-time setup

1. Install Docker.
2. Make sure `~/.pi/agent/models.json` exists on the host with at least one
   provider configured. Run `pi` once on the host (or hand-write the file).
3. Copy the data-dirs template if you want to mount data dirs:
   ```
   cp tools/pi-dev/data-dirs.toml.example tools/pi-dev/data-dirs.toml
   ```
   Edit the paths to match this machine. The file is gitignored.
4. Symlink the launcher onto `$PATH`:
   ```
   ln -s "$(pwd)/tools/pi-dev/pi-dev" ~/.local/bin/pi-dev
   ```

The image is built on first run.

## Usage

```
cd ~/famstack/famstack
pi-dev                          # source RW, no data dirs
pi-dev --with vault             # + memory vault RO
pi-dev --with vault,paperless   # + paperless RO
pi-dev --rw vault               # vault mounted RW (rare)
pi-dev --list-data              # show configured data dirs
pi-dev --dry-run                # print docker command, do not run
pi-dev --rebuild                # rebuild the image
pi-dev -- --print "summarise README.md"   # passthrough to pi
```

Mounts inside the container:

| Container path     | Host path                | Mode |
|--------------------|--------------------------|------|
| `/workspace`       | `git rev-parse --show-toplevel` of $PWD | rw |
| `/home/node/.pi`   | `~/.pi`                  | rw |
| `/data/<name>`     | from `data-dirs.toml`    | per --with / --rw |

## Git pushes

Pi commits inside `/workspace`; you push from the host after reviewing the
diff. The container has no credentials to push anywhere.
