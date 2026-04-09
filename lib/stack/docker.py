from __future__ import annotations

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

import json
import platform
import ssl
import subprocess
import time
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


def compose_up(compose_file: str | Path, env: dict = None) -> tuple[int, str]:
    """Start containers. Returns (exit_code, error_output)."""
    full_env = {**__import__("os").environ, **(env or {})}
    result = _docker(
        "compose", "-f", str(compose_file), "up", "-d",
        capture_output=True, text=True, timeout=300, env=full_env,
    )
    return result.returncode, result.stderr


def compose_stop(compose_file: str | Path) -> tuple[int, str]:
    """Stop containers without removing them. Returns (exit_code, output)."""
    code, stdout, stderr = compose(compose_file, "stop")
    return code, (stdout + stderr).strip()


def compose_down(compose_file: str | Path) -> tuple[int, str]:
    """Stop and remove containers + volumes. Returns (exit_code, output)."""
    code, stdout, stderr = compose(compose_file, "down", "-v", "--remove-orphans")
    return code, (stdout + stderr).strip()


def compose_pull(compose_file: str | Path, env: dict = None) -> None:
    """Pull images for a compose file. Streams output."""
    full_env = {**__import__("os").environ, **(env or {})}
    _docker(
        "compose", "-f", str(compose_file), "pull",
        timeout=600, env=full_env,
    )


def compose_build(compose_file: str | Path, env: dict = None) -> None:
    """Build images for a compose file. Streams output."""
    full_env = {**__import__("os").environ, **(env or {})}
    _docker(
        "compose", "-f", str(compose_file), "build",
        timeout=600, env=full_env,
    )


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
        f"famstack is tested with OrbStack only.\n"
        f"      Docker Desktop is not recommended and can cause high CPU usage.\n"
        f"      Install OrbStack: https://orbstack.dev"
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


def check_health(url: str, headers: dict = None) -> bool:
    """Quick probe — returns True if URL responds with 2xx."""
    try:
        req = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(req, timeout=3, context=_SSL):
            return True
    except Exception:
        return False


def wait_for_health(url: str, timeout: int = 120, interval: int = 3,
                    headers: dict = None) -> str:
    """Poll a URL until it responds. Returns 'ready', 'auth', or 'timeout'.

    Used after compose up to wait for a service to actually be ready,
    not just container-started.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(req, timeout=5, context=_SSL):
                return "ready"
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return "auth"
            pass
        except Exception:
            pass
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


def running_project_ids() -> set[str]:
    """Convenience wrapper — stacklet IDs with running containers."""
    states = project_states()
    return {sid for sid, state in states.items() if state in ("running", "starting")}


def all_project_ids() -> set[str]:
    """Convenience wrapper — all stacklet IDs with any container state."""
    return set(project_states().keys())
