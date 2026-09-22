"""Docker operations for the stacklet framework.

Encapsulates all Docker Compose interactions: starting, stopping,
health checking, and network management. The Stack class and CLI
commands use this instead of calling subprocess directly.

All Docker commands are routed through a configured runtime context
(default: orbstack on macOS). This ensures famstack never accidentally
creates containers in Docker Desktop while listing from OrbStack or
vice versa. Set [core] runtime in stack.toml to override.

Keeping Docker operations in one place means:
  - Testing can mock this module instead of subprocess
  - A future podman backend swaps this file, nothing else changes
  - Error handling for Docker issues is consistent
"""
from __future__ import annotations

import json
import platform
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = ssl.CERT_NONE

# Set by init_runtime() at startup, used by _docker() for every command.
_context: str | None = None


def _docker(*args, **kwargs) -> subprocess.CompletedProcess:
    """Run a docker command, pinned to the configured context."""
    cmd = ["docker"]
    if _context:
        cmd += ["--context", _context]
    cmd += list(args)
    return subprocess.run(cmd, **kwargs)


def compose(compose_file: str | Path, *args) -> tuple[int, str, str]:
    """Run a docker compose command. Returns (exit_code, stdout, stderr)."""
    result = _docker(
        "compose", "-f", str(compose_file), *args,
        capture_output=True, text=True, timeout=300,
    )
    return result.returncode, result.stdout, result.stderr


def compose_up(compose_file: str | Path, env: dict | None = None) -> tuple[int, str]:
    """Start containers, always force-recreating. Returns (exit_code, error_output).

    `--force-recreate` is unconditional because compose's service-config
    hash does not cover env_file *contents*: tokens or settings written
    into a stacklet's .env between runs are invisible to a plain
    `up -d`, leaving running containers stuck on stale env. `stack up`
    is a deliberate user action, so bouncing healthy containers is an
    acceptable cost for a reliable config-propagation contract.

    Selecting no services at all is success, not failure. When
    COMPOSE_PROFILES excludes every service in the file, compose exits
    1 with "no service selected" — an empty selection, not a service
    that refused to start. Treating it as an error meant a stacklet
    whose containers are all optional could never finish setup, and
    anything depending on it stayed blocked. The ai stacklet under
    STACK_AI_NO_VOICE=1 is exactly that shape.
    """
    full_env = {**__import__("os").environ, **(env or {})}
    result = _docker(
        "compose", "-f", str(compose_file), "up", "-d", "--force-recreate",
        capture_output=True, text=True, timeout=300, env=full_env,
    )
    if result.returncode != 0 and (result.stderr or "").strip() == "no service selected":
        return 0, ""
    return result.returncode, result.stderr


def compose_stop(compose_file: str | Path) -> tuple[int, str]:
    """Stop containers without removing them. Returns (exit_code, output)."""
    code, stdout, stderr = compose(compose_file, "stop")
    return code, (stdout + stderr).strip()


def compose_down(compose_file: str | Path) -> tuple[int, str]:
    """Stop and remove containers + volumes. Returns (exit_code, output)."""
    code, stdout, stderr = compose(compose_file, "down", "-v", "--remove-orphans")
    return code, (stdout + stderr).strip()


def compose_pull(compose_file: str | Path, env: dict | None = None) -> None:
    """Pull images for a compose file. Streams output."""
    full_env = {**__import__("os").environ, **(env or {})}
    _docker(
        "compose", "-f", str(compose_file), "pull",
        timeout=600, env=full_env,
    )


def compose_build(compose_file: str | Path, env: dict | None = None) -> None:
    """Build images for a compose file, on current base images. Streams output.

    Without `--pull` the build cache keeps the base image it first pulled
    for good, and Watchtower does not update built images, so the base
    would never be patched. With it, a new base means a rebuild and an
    unchanged one costs a registry lookup. Offline the pull fails, and
    the build runs again from what is cached so the stacklet still starts.
    """
    full_env = {**__import__("os").environ, **(env or {})}
    pulled = _docker(
        "compose", "-f", str(compose_file), "build", "--pull",
        timeout=600, env=full_env,
    )
    if pulled.returncode != 0:
        _docker(
            "compose", "-f", str(compose_file), "build",
            timeout=600, env=full_env,
        )


def exec_in(container: str, *cmd: str) -> tuple[int, str]:
    """Run a command in a running container. Returns (exit_code, error_output)."""
    result = _docker(
        "exec", container, *cmd,
        capture_output=True, text=True, timeout=60,
    )
    return result.returncode, result.stderr


def find_compose_file(stacklet_dir: Path) -> Path | None:
    """Find docker-compose.yml for a stacklet. Returns path or None."""
    compose = stacklet_dir / "docker-compose.yml"
    return compose if compose.exists() else None


def ensure_network(name: str = "stack") -> tuple[str | None, str | None]:
    """Create the Docker network if it doesn't exist.
    Returns (success_message, error_message).
    """
    try:
        r = _docker(
            "network", "inspect", name,
            capture_output=True, timeout=10,
        )
        if r.returncode == 0:
            return f"network '{name}' exists", None
    except Exception:
        pass

    try:
        r = _docker(
            "network", "create", name,
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            return f"network '{name}' created", None
        return None, f"failed to create network: {r.stderr.strip()}"
    except Exception as e:
        return None, f"Docker error: {e}"


def init_runtime(preferred: str = "orbstack") -> tuple[str | None, str | None]:
    """Detect the Docker runtime and pin all commands to the preferred context.

    On macOS, Docker Desktop and OrbStack both provide the docker CLI
    but use different runtimes. Mixing them creates orphaned containers.
    This function picks the right context once at startup so every
    subsequent docker command goes to the same runtime.

    Behavior:
    - preferred context available: pin to it, no warning
    - preferred not available, other runtime found: pin to it, warn
    - Linux: no context pinning needed
    - Docker not installed: error

    Returns (status_message, warning_or_none).
    """
    global _context

    if platform.system() != "Darwin":
        return "Runtime check skipped (not macOS)", None

    try:
        r = subprocess.run(
            ["docker", "context", "ls", "--format", "json"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return None, "Could not detect Docker runtime"
        available = set()
        for line in r.stdout.strip().splitlines():
            available.add(json.loads(line)["Name"])
    except FileNotFoundError:
        return None, "Docker is not installed"

    if preferred in available:
        _context = preferred
        return f"{preferred.capitalize()} runtime", None

    # Preferred not available, fall back to whatever is there
    fallback = next(
        (c for c in available if c not in ("default",)),
        next(iter(available), None),
    )
    if fallback:
        _context = fallback
    warning = (
        "famstack is tested with OrbStack only.\n"
        "      Docker Desktop is not recommended and can cause high CPU usage.\n"
        "      Install OrbStack: https://orbstack.dev"
    )
    return f"Using {_context or 'default'} runtime", warning


def check_docker() -> tuple[str | None, str | None]:
    """Verify Docker is installed and running.
    Returns (success_message, error_message).
    """
    try:
        r = _docker("info", capture_output=True, timeout=10)
        if r.returncode != 0:
            return None, "Docker is not running. Start it and try again."
        return "Docker is running", None
    except FileNotFoundError:
        return None, "Docker is not installed"


def check_health(url: str, headers: dict | None = None) -> bool:
    """Quick probe — returns True if URL responds with 2xx."""
    try:
        req = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(req, timeout=3, context=_SSL):
            return True
    except Exception:
        return False


def probe_health(url: str, headers: dict | None = None, timeout: float = 3) -> str:
    """Single-shot health probe. Returns 'ready', 'auth', or 'down'.

    No retries — the caller loops if they want a wait. Used by both
    wait_for_health (bootstrap-time polling) and Stack.is_healthy
    (runtime reachability checks).
    """
    try:
        req = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL):
            return "ready"
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return "auth"
        return "down"
    except Exception:
        return "down"


def wait_for_health(url: str, timeout: int = 120, interval: int = 3,
                    headers: dict | None = None) -> str:
    """Poll a URL until it responds. Returns 'ready', 'auth', or 'timeout'.

    Used after compose up to wait for a service to actually be ready,
    not just container-started.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = probe_health(url, headers=headers, timeout=5)
        if status in ("ready", "auth"):
            return status
        time.sleep(interval)
    return "timeout"


def project_states() -> dict[str, str]:
    """Single Docker query to get the state of every compose project.

    Returns {stacklet_id: state} where state is one of:
      "running"   — all containers up
      "starting"  — containers are coming up
      "failing"   — crash loop or partial crash (restarting, or mixed exited+running)
      "stopped"   — all containers exited cleanly
      "unknown"   — unrecognized status

    docker compose ls -a reports status strings like:
      "running(3)", "exited(2)", "restarting(1), running(2)",
      "starting(1), running(2)", "exited(1), running(2)"
    """
    try:
        r = _docker(
            "compose", "ls", "-a", "--format", "json",
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return {}

        prefix = "stack-"
        result = {}
        for p in json.loads(r.stdout):
            name = p.get("Name", "")
            if not name.startswith(prefix):
                continue
            sid = name[len(prefix):]
            status = p.get("Status", "").lower()

            if "restarting" in status:
                result[sid] = "failing"
            elif "exited" in status and "running" in status:
                # Some containers crashed while others keep running
                result[sid] = "failing"
            elif "starting" in status:
                result[sid] = "starting"
            elif "running" in status:
                result[sid] = "running"
            elif "exited" in status or "dead" in status:
                result[sid] = "stopped"
            else:
                result[sid] = "unknown"

        return result
    except Exception:
        return {}


def containers_for(stacklet_id: str) -> list[dict]:
    """Every container of a stacklet, running or not.

    Returns dicts with name, state, exit_code and a human "since" string.
    `stack status` only reports the stacklet as a whole, so a single dead
    sidecar shows up as "failing" with no clue which one died.
    """
    try:
        r = _docker(
            "ps", "-a", "--filter", f"name=^stack-{stacklet_id}-",
            "--format", "{{.Names}}\t{{.State}}\t{{.Status}}",
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return []
        out = []
        for line in r.stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            name, state, status = parts
            # "Exited (128) 3 weeks ago" -> code 128, "3 weeks ago"
            code, since = 0, status
            if status.startswith("Exited ("):
                head, _, tail = status.partition(")")
                try:
                    code = int(head[len("Exited ("):])
                except ValueError:
                    code = 1
                since = tail.strip()
            out.append({"name": name, "state": state, "exit_code": code, "since": since})
        return out
    except Exception:
        return []


def _parse_env(text: str) -> dict:
    """Turn `docker inspect`'s KEY=VALUE lines into a dict."""
    env = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            env[key] = value
    return env


def container_env(name: str) -> dict:
    """The environment a container is actually running with.

    Read from the container rather than the compose file: a container keeps
    the environment it was created with, so this is the only way to see that
    it has drifted from current config.
    """
    try:
        r = _docker(
            "inspect", name, "--format", "{{range .Config.Env}}{{println .}}{{end}}",
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return {}
        return _parse_env(r.stdout)
    except Exception:
        return {}


def compose_service_env(compose_file: str | Path) -> dict[str, dict[str, str]]:
    """What compose would give each service, resolved the way it resolves it.

    `docker compose config` applies the project's `.env`, each service's
    `env_file` and its `environment:` block, with interpolation, and
    reports the result. That is the only honest thing to compare a running
    container against, because it is literally what a fresh container
    would receive.

    An unreadable compose file returns nothing, and the caller reports no
    drift rather than guessing at one.
    """
    try:
        r = _docker(
            "compose", "-f", str(compose_file), "config", "--format", "json",
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            return {}
        parsed = json.loads(r.stdout)
    except Exception:
        return {}

    services = parsed.get("services") or {}
    return {
        name: {k: "" if v is None else str(v)
               for k, v in (service.get("environment") or {}).items()}
        for name, service in services.items()
    }


def running_project_ids() -> set[str]:
    """Convenience wrapper — stacklet IDs with running containers."""
    states = project_states()
    return {sid for sid, state in states.items() if state in ("running", "starting")}


def container_commits() -> dict[str, str]:
    """The commit each stacklet's running containers were started from.

    Read from the `stack.commit` label the framework stamps at `stack up`.
    The key names the framework rather than any product built on it, the
    same way `STACK_*` env vars and the "stacklet" vocabulary do.

    A container without the label is left out rather than guessed about:
    it predates the label, or belongs to a service with no labels block,
    and an unknown answer is not a finding.

    One label per stacklet. Containers of the same stacklet are created
    together, so they agree in every state except a restart in progress.
    """
    try:
        r = _docker(
            "ps", "--filter", "label=stack.commit",
            "--format", '{{.Names}}\t{{.Label "stack.commit"}}',
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return {}
    except Exception:
        return {}

    commits: dict[str, str] = {}
    for line in r.stdout.strip().splitlines():
        name, _, commit = line.partition("\t")
        commit = commit.strip()
        parts = name.split("-")
        if not commit or commit == "unknown" or len(parts) < 2 or parts[0] != "stack":
            continue
        commits.setdefault(parts[1], commit)
    return commits


def all_project_ids() -> set[str]:
    """Convenience wrapper — all stacklet IDs with any container state."""
    return set(project_states().keys())
