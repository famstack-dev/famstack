from __future__ import annotations

"""The Stack class — core framework orchestrator.

Stack coordinates config, discovery, secrets, hooks, and lifecycle.
It holds no global state — everything is explicit via constructor params.

Usage:
    s = Stack(root=Path("/path/to/yourstack"), data=Path("~/yourstack-data"))
    s.up("photos")     # render env, run hooks, write .env
    s.down("photos")   # run on_stop hook
    s.destroy("photos") # full teardown: hooks, secrets, data, markers
"""

import collections
import json
import platform
import shutil
import subprocess
import sys
from ._compat import tomllib
from pathlib import Path

from .docker import running_project_ids
from .hooks import HookResolver, build_hook_ctx
from .output import SilentOutput
from .secrets import TomlSecretStore


class StackletNotHealthyError(RuntimeError):
    """wait_for_healthy timed out waiting for the declared [health] probe."""

    def __init__(self, stacklet_id: str, timeout: float):
        self.stacklet_id = stacklet_id
        self.timeout = timeout
        super().__init__(
            f"Stacklet '{stacklet_id}' did not become healthy within {timeout:.1f}s"
        )


class Stack:
    """The stack framework.

    Created with explicit paths — no global state, no filesystem walking
    on import. All operations are methods, all state is instance attributes.

    Accepts an optional output object for progress reporting. If not
    provided, output is silently discarded. Pass a CollectorOutput for
    tests or a TerminalOutput for the CLI.
    """

    def __init__(self, root: Path, data: Path, instance_dir: Path | None = None, output=None):
        self.root = Path(root)
        # Instance dir holds config (stack.toml, users.toml) and runtime state
        # (.stack/secrets.toml, setup markers). Defaults to root so one repo
        # can power one instance. Splitting them lets tests and sandboxes
        # share stacklet definitions without cloning the repo.
        self.instance_dir = Path(instance_dir) if instance_dir is not None else self.root
        self.data = Path(data)
        self.secrets = TomlSecretStore(self.instance_dir / ".stack" / "secrets.toml")
        self.output = output or SilentOutput()

    def product_name(self) -> str:
        """Product name from stack.toml [core] name, defaults to 'stack'."""
        return self._cfg("core", "name", "stack")

    # ── Config ────────────────────────────────────────────────────────

    @property
    def config(self) -> dict:
        """Always read stack.toml fresh from disk. No stale cache."""
        path = self.instance_dir / "stack.toml"
        if not path.exists():
            return {}
        try:
            with open(path, "rb") as f:
                return tomllib.load(f)
        except (tomllib.TOMLDecodeError, OSError):
            return {}

    def _cfg(self, section: str, key: str, default: str = "") -> str:
        """Read a config value from stack.toml."""
        val = self.config.get(section, {}).get(key)
        if val is not None:
            return str(val)
        return default

    def _set_cfg(self, section: str, key: str, value: str):
        """Write a config value to stack.toml.

        Updates an existing key or appends to the section. Creates the
        section if it doesn't exist.
        """
        import re
        toml_path = self.instance_dir / "stack.toml"
        if not toml_path.exists():
            return
        content = toml_path.read_text()

        pattern = rf'{re.escape(key)}\s*=\s*"[^"]*"'
        replacement = f'{key} = "{value}"'

        if re.search(pattern, content):
            content = re.sub(pattern, replacement, content)
        elif f"[{section}]" in content:
            content = content.replace(f"[{section}]", f"[{section}]\n{replacement}")
        else:
            content += f"\n[{section}]\n{replacement}\n"

        toml_path.write_text(content)

    # ── Discovery ─────────────────────────────────────────────────────

    def discover(self) -> list[dict]:
        """Find all stacklets under root/stacklets/.

        Walks the filesystem — no registry needed. A directory with a
        stacklet.toml is a stacklet.
        """
        stacklets_dir = self.root / "stacklets"
        if not stacklets_dir.exists():
            return []

        result = []
        for manifest_path in sorted(stacklets_dir.glob("*/stacklet.toml")):
            try:
                with open(manifest_path, "rb") as f:
                    raw = tomllib.load(f)
            except (tomllib.TOMLDecodeError, OSError):
                continue

            sid = raw.get("id", manifest_path.parent.name)
            result.append({
                "id":          sid,
                "name":        raw.get("name", sid),
                "description": raw.get("description", ""),
                "version":     raw.get("version", ""),
                "port":        raw.get("port"),
                "category":    raw.get("category", ""),
                "always_on":   raw.get("always_on", False),
                "enabled":     self._is_set_up(sid),
                "path":        str(manifest_path.parent),
                "manifest":    raw,
            })
        return result

    def _find_stacklet(self, stacklet_id: str) -> dict | None:
        """Find a stacklet by ID. Returns the stacklet dict or None."""
        for s in self.discover():
            if s["id"] == stacklet_id:
                return s
        return None

    # ── Env rendering ─────────────────────────────────────────────────

    def _build_template_vars(self) -> dict:
        """Build the full set of template variables from all sources.

        This is the central registry of what's available in {curly braces}
        inside stacklet.toml [env.defaults]. Sources:
          - stack.toml config values
          - AI service URLs (host + docker variants)
          - All secrets (cross-stacklet references like {docs__API_TOKEN})
          - Public URLs for all stacklets (e.g. {photos_url})
          - Admin user from users.toml
        """
        ai_openai_url = self._cfg("ai", "openai_url", "http://localhost:8000/v1")
        ai_whisper_url = self._cfg("ai", "whisper_url", "http://localhost:42062/v1")
        ai_openai_key = (self.secrets.get("", "AI_API_KEY")
                         or self._cfg("ai", "openai_key"))

        template_vars = {
            # Core config
            "data_dir":              str(self.data),
            "domain":                self._cfg("core", "domain"),
            "language":              self._cfg("core", "language", self._cfg("ai", "language", "en")),
            "timezone":              self._cfg("core", "timezone", "UTC"),
            # AI service URLs — host-side for CLI, docker-side for containers
            "ai_openai_url":         ai_openai_url,
            "ai_openai_url_docker":  ai_openai_url.replace("://localhost", "://host.docker.internal"),
            "ai_openai_key":         ai_openai_key,
            "ai_whisper_url":        ai_whisper_url,
            "ai_whisper_url_docker": ai_whisper_url.replace("://localhost", "://host.docker.internal"),
            "ai_language":           self._cfg("ai", "language", "en"),
            "ai_tts_voice":          "onyx" if self._cfg("ai", "language", "en").startswith("de") else "alloy",
            "ai_default_model":      self._cfg("ai", "default"),
            "ai_default_model_short": self._cfg("ai", "default").rsplit("/", 1)[-1],
            "ai_models_json":        "",  # populated when [ai.models] exists
            "messages_server_name":  self._cfg("messages", "server_name"),
        }

        # All secrets available as template vars — stacklets can reference
        # each other's secrets (e.g. bots needs {docs__API_TOKEN})
        for k, v in self.secrets.all().items():
            template_vars[k] = v

        # Public URLs for all stacklets and their named ports
        for s in self.discover():
            port = s.get("port")
            if port:
                template_vars[f"{s['id']}_url"] = self._public_url(s["id"], port)
            for port_name, port_num in s.get("manifest", {}).get("ports", {}).items():
                template_vars[f"{s['id']}_{port_name}_url"] = self._public_url(s["id"], port_num)

        # Tech admin — internal service account for all stacklets
        from .users import (
            TECH_ADMIN_USERNAME, TECH_ADMIN_EMAIL,
            get_admin_password, load_users, user_id,
        )
        template_vars["admin_username"] = TECH_ADMIN_USERNAME
        template_vars["admin_email"] = TECH_ADMIN_EMAIL
        template_vars["admin_password"] = get_admin_password(self.secrets) or ""

        # Comma-separated user IDs of all admin-role users from users.toml
        admin_ids = [
            user_id(u) for u in load_users(self.instance_dir)
            if u.get("role") == "admin"
        ]
        template_vars["admin_user_ids"] = ",".join(admin_ids)

        return template_vars

    def _lan_ip(self) -> str:
        """Get the LAN IP of this machine."""
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except OSError:
            return "localhost"

    def _public_url(self, stacklet_id: str, port: int) -> str:
        """Build the URL a user would click to reach a service.

        In domain mode: http(s)://{stacklet}.{domain}
        In port mode: http://{ip}:{port} (LAN-reachable)

        Set [core].https = true when a reverse proxy terminates TLS in front
        of the stack.
        """
        domain = self._cfg("core", "domain")
        if domain:
            scheme = "https" if self._cfg("core", "https") else "http"
            return f"{scheme}://{stacklet_id}.{domain}"
        return f"http://{self._lan_ip()}:{port}"

    def env(self, stacklet_id: str) -> dict:
        """Render the complete environment for a stacklet.

        This is the core config pipeline — values from stack.toml, secrets,
        users.toml, and other stacklets flow through template rendering into
        a flat dict that becomes the .env file for docker compose.

        Also generates any declared secrets ([env].generate) if they don't
        exist yet. This is intentional: env rendering is the natural trigger
        for secret generation because it's called on every 'stack up' and
        the secrets need to exist before the container starts.
        """
        stacklets = {s["id"]: s for s in self.discover()}
        if stacklet_id not in stacklets:
            raise ValueError(f"Stacklet '{stacklet_id}' not found")

        s = stacklets[stacklet_id]
        manifest = s.get("manifest", {})
        env_defaults = manifest.get("env", {}).get("defaults", {})

        # Generate declared secrets before rendering — templates may
        # reference them (e.g. DB_PASSWORD = "{with_secrets__DB_PASSWORD}")
        for key in manifest.get("env", {}).get("generate", []):
            self.secrets.ensure(stacklet_id, key)

        template_vars = self._build_template_vars()
        template_vars["stacklet_id"] = stacklet_id
        template_vars["url"] = self._public_url(stacklet_id, s.get("port", 0))
        template_vars["ip"] = self._lan_ip()

        # Render templates — warn on missing vars (typos cause silent failures)
        import re
        rendered = {}
        for k, v in env_defaults.items():
            if not isinstance(v, str):
                continue
            # Find all {var} references in the template
            refs = re.findall(r'\{(\w+)\}', v)
            missing = [r for r in refs if r not in template_vars]
            if missing:
                self.output.debug(f"{stacklet_id}: unresolved template var(s) in {k}: {{{', '.join(missing)}}}")
            safe_vars = collections.defaultdict(str, template_vars)
            rendered[k] = v.format_map(safe_vars)

        # Generated secrets also go into the env — docker compose needs them
        # as env vars (e.g. ${DB_PASSWORD} in volume paths, healthchecks)
        for key in manifest.get("env", {}).get("generate", []):
            val = self.secrets.get(stacklet_id, key)
            if val:
                rendered[key] = val

        # Watchtower auto-update label — derived from upstream.channel
        channel = manifest.get("upstream", {}).get("channel", "patch")
        rendered["WATCHTOWER_ENABLE"] = "true" if channel == "patch" else "false"

        # Port bind IP — 0.0.0.0 in port mode (LAN-reachable),
        # 127.0.0.1 in domain mode (only Caddy reaches containers)
        rendered["PORT_BIND_IP"] = "127.0.0.1" if self._cfg("core", "domain") else "0.0.0.0"

        return rendered

    # ── List ──────────────────────────────────────────────────────────

    def list(self) -> dict:
        """Return all stacklets with their current state.

        Six states:
          online    — set up and all health checks pass
          starting  — containers coming up, not yet healthy
          degraded  — set up but some health checks fail (with hints)
          stopped   — set up, no containers running
          failing   — containers in a crash/restart loop
          available — never set up
        """
        from .docker import project_states, check_health

        stacklets = self.discover()
        # Single Docker query — maps stacklet ID to state string
        docker_states = project_states()
        template_vars = self._build_template_vars()

        for s in stacklets:
            sid = s["id"]
            docker_state = docker_states.get(sid, "")
            is_set_up = self._is_set_up(sid)
            s["enabled"] = is_set_up
            s["failing"] = docker_state == "failing"
            s["starting"] = docker_state == "starting"
            s["online"] = docker_state == "running"
            s["degraded"] = False
            s["health_issues"] = []

            # Only check health for fully running stacklets — stopped or
            # starting services will obviously fail their health checks
            if not is_set_up or not s["online"]:
                continue

            # Resolve health checks from manifest
            checks = self._resolve_health_checks(
                s.get("manifest", {}), template_vars)
            for check in checks:
                if not check.get("url"):
                    continue
                if not check_health(check["url"], headers=check.get("headers", {})):
                    s["degraded"] = True
                    s["health_issues"].append(check.get("hint") or f"{check['url']} not reachable")

        online = [s for s in stacklets if s["online"] and not s["degraded"]]
        set_up = [s for s in stacklets if s["enabled"]]

        return {
            "stacklets": stacklets,
            "total": len(stacklets),
            "enabled": len(set_up),
            "online": len(online),
        }

    def status(self) -> dict:
        """Full system status: version, runtime, host, stacklets."""
        from . import docker
        from .cli import VERSION

        info = {
            "name": self.product_name(),
            "version": VERSION,
            "commit": self._git_commit(),
            "python": platform.python_version(),
            "os": platform.system(),
            "arch": platform.machine(),
            "runtime": self._docker_runtime(),
            "docker_version": self._docker_version(),
            "host": self._host_stats(),
            "data_dir": str(self.data),
            "config": {
                "domain": self._cfg("core", "domain", ""),
                "timezone": self._cfg("core", "timezone", ""),
            },
        }
        info.update(self.list())
        return info

    def _git_commit(self) -> str:
        try:
            r = subprocess.run(
                ["git", "-C", str(self.root), "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=5,
            )
            return r.stdout.strip() if r.returncode == 0 else "unknown"
        except Exception:
            return "unknown"

    def _docker_runtime(self) -> str:
        from . import docker
        return docker._context or "default"

    def _docker_version(self) -> str:
        from . import docker
        try:
            r = docker._docker(
                "version", "--format", "{{.Server.Version}}",
                capture_output=True, text=True, timeout=5,
            )
            return r.stdout.strip() if r.returncode == 0 else "unknown"
        except Exception:
            return "unknown"

    def _host_stats(self) -> dict:
        stats = {}
        try:
            # Total memory (macOS)
            r = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                total = int(r.stdout.strip())
                stats["memory_total_gb"] = round(total / (1024 ** 3))
            # Used memory (macOS vm_stat)
            r = subprocess.run(
                ["vm_stat"], capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                pages = {}
                for line in r.stdout.splitlines():
                    if ":" in line:
                        key, val = line.split(":", 1)
                        val = val.strip().rstrip(".")
                        if val.isdigit():
                            pages[key.strip()] = int(val)
                page_size = 16384  # Apple Silicon
                active = pages.get("Pages active", 0)
                wired = pages.get("Pages wired down", 0)
                compressed = pages.get("Pages occupied by compressor", 0)
                used_bytes = (active + wired + compressed) * page_size
                stats["memory_used_gb"] = round(used_bytes / (1024 ** 3), 1)
        except Exception:
            pass
        try:
            # Disk usage for data dir
            usage = shutil.disk_usage(self.data if self.data.exists() else self.root)
            stats["disk_total_gb"] = round(usage.total / (1024 ** 3))
            stats["disk_free_gb"] = round(usage.free / (1024 ** 3))
            stats["disk_used_pct"] = round((usage.used / usage.total) * 100)
        except Exception:
            pass
        return stats

    def _resolve_health_checks(self, manifest, template_vars):
        """Parse health config into list of check dicts.

        Each dict has: url, name, hint, headers.

        Supports three formats:
          [health]
          url = "..."                          # single, no hint

          [health]
          urls = ["...", "..."]                # multiple, no hints

          [[health.checks]]                   # multiple with hints + headers
          url = "..."
          name = "..."
          hint = "..."
          [health.checks.headers]
          Authorization = "Bearer {api_key}"
        """
        health = manifest.get("health", {})
        checks_list = health.get("checks", [])

        if checks_list:
            result = []
            for c in checks_list:
                url_tpl = c.get("url", "")
                name = c.get("name", "")
                hint = c.get("hint", "")
                raw_headers = c.get("headers", {})
                url = self._render_template(url_tpl, template_vars)
                headers = {
                    k: self._render_template(v, template_vars)
                    for k, v in raw_headers.items()
                }
                # Drop headers with unresolved templates
                headers = {k: v for k, v in headers.items()
                           if v and not v.startswith("Bearer {")}
                if url and not url.startswith("{"):
                    result.append({"url": url, "name": name, "hint": hint, "headers": headers})
                elif hint:
                    result.append({"url": "", "name": name, "hint": hint, "headers": {}})
            return result

        # Fallback: urls[] or url
        urls = health.get("urls", [])
        if not urls and health.get("url"):
            urls = [health["url"]]

        timeout = health.get("timeout")
        return [
            {"url": self._render_template(u, template_vars), "name": "", "hint": "", "headers": {},
             **({"timeout": timeout} if timeout else {})}
            for u in urls
            if self._render_template(u, template_vars)
            and not self._render_template(u, template_vars).startswith("{")
        ]

    def _render_template(self, template: str, template_vars: dict) -> str:
        try:
            return template.format_map(
                collections.defaultdict(str, template_vars))
        except Exception:
            return template

    def _running_project_ids(self) -> set[str]:
        """Query Docker for stacklet IDs with running containers."""
        return running_project_ids()

    # ── Secrets (delegated to SecretStore) ────────────────────────────

    def secret(self, stacklet_id: str, name: str) -> str | None:
        return self.secrets.get(stacklet_id, name)

    def set_secret(self, stacklet_id: str, name: str, value: str) -> None:
        self.secrets.set(stacklet_id, name, value)

    def ensure_secret(self, stacklet_id: str, name: str) -> str:
        return self.secrets.ensure(stacklet_id, name)

    def clear_secrets(self, stacklet_id: str) -> None:
        self.secrets.clear(stacklet_id)

    # ── Lifecycle ─────────────────────────────────────────────────────

    def _setup_done_marker(self, stacklet_id: str) -> Path:
        """Path to the marker that gates once-only hooks."""
        return self.instance_dir / ".stack" / f"{stacklet_id}.setup-done"

    def _is_first_run(self, stacklet_id: str) -> bool:
        return not self._setup_done_marker(stacklet_id).exists()

    def _is_set_up(self, stacklet_id: str) -> bool:
        """A stacklet is 'set up' if its setup-done marker exists.
        Used for dependency checking."""
        return self._setup_done_marker(stacklet_id).exists()

    # ── State queries: installed / running / healthy ─────────────────
    #
    # Three distinct states, one word each. They used to be conflated
    # behind `stacklet["enabled"]`, which broke any caller that meant
    # "reachable" (the proxy held by coincidence). Now:
    #
    #   is_installed  — first-run setup completed (setup-done marker)
    #   is_running    — any container under stack-{id}-* is running
    #   is_healthy    — the declared [health] probe currently responds
    #
    # CLI plugins almost always want is_healthy.

    def is_installed(self, stacklet_id: str) -> bool:
        """True once on_install + on_install_success have both succeeded."""
        return self._is_set_up(stacklet_id)

    def is_running(self, stacklet_id: str) -> bool:
        """True if any container belonging to the stacklet is running.
        Does not imply the service is serving requests — see is_healthy."""
        return stacklet_id in self._running_project_ids()

    def is_healthy(self, stacklet_id: str) -> bool:
        """True if the stacklet's declared [health] probe responds.

        For stacklets with no [health] block, trivially healthy when
        running. Uses the same probe config the framework runs at
        `stack up` time — no duplicate health knobs per plugin.
        """
        s = self._find_stacklet(stacklet_id)
        if not s:
            return False
        manifest = s.get("manifest", {})
        template_vars = self._build_template_vars()
        checks = self._resolve_health_checks(manifest, template_vars)
        if not checks:
            return self.is_running(stacklet_id)

        # An "auth" response (401/403) counts as healthy: the HTTP layer
        # is up, just gating credentials. Matches wait_for_health's
        # bootstrap semantics — is_healthy is liveness, not authorization.
        from .docker import probe_health
        for check in checks:
            url = check.get("url")
            if not url:
                continue
            if probe_health(url, headers=check.get("headers", {})) not in ("ready", "auth"):
                return False
        return True

    def wait_for_healthy(self, stacklet_id: str,
                         timeout: float = 60.0,
                         interval: float = 0.5) -> None:
        """Poll is_healthy until the stacklet responds or timeout expires.

        Raises StackletNotHealthyError on timeout, naming the stacklet
        and elapsed time — callers get an actionable message instead
        of a bare False/None.
        """
        import time as _time
        deadline = _time.monotonic() + timeout
        while _time.monotonic() < deadline:
            if self.is_healthy(stacklet_id):
                return
            _time.sleep(interval)
        raise StackletNotHealthyError(stacklet_id, timeout)

    def _write_env_file(self, stacklet_dir: Path, env_dict: dict) -> None:
        """Write rendered env vars to .env for docker compose.

        Values are quoted and escaped to handle passwords with special chars.
        """
        lines = ["# Auto-generated by stack — do not edit", ""]
        for k, v in sorted(env_dict.items()):
            # Escape backslashes and double quotes, then wrap in quotes
            escaped = str(v).replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{k}="{escaped}"')
        (stacklet_dir / ".env").write_text("\n".join(lines) + "\n")

    def refresh_env(self, stacklet_id: str) -> dict:
        """Re-render env from current config and write .env file.

        Called on restart and up — ensures config changes in stack.toml
        take effect without a full destroy/up cycle.
        Returns the rendered env dict.
        """
        s = self._find_stacklet(stacklet_id)
        if not s:
            raise ValueError(f"Stacklet '{stacklet_id}' not found")
        env_dict = self.env(stacklet_id)
        self._write_env_file(Path(s["path"]), env_dict)
        return env_dict

    def up(self, stacklet_id: str) -> dict:
        """Bring a stacklet up: render env, run hooks, write .env.

        First run:  check deps → render env → generate secrets →
                    on_install → write .env → mark done
        Subsequent: render env → write .env

        Docker operations (pull, compose up, health) are NOT here —
        they belong in the CLI layer.
        """
        s = self._find_stacklet(stacklet_id)
        if not s:
            return {"error": f"Stacklet '{stacklet_id}' not found"}

        manifest = s.get("manifest", {})
        first_run = self._is_first_run(stacklet_id)
        stacklet_dir = Path(s["path"])

        # ── Check dependencies ────────────────────────────────────
        for dep in manifest.get("requires", []):
            if not self._is_set_up(dep):
                dep_stacklet = self._find_stacklet(dep)
                dep_name = dep_stacklet.get("name", dep) if dep_stacklet else dep
                return {
                    "error": f"{dep_name} must be set up first",
                    "missing": [dep],
                    "hint": f"stack up {dep}",
                }

        # ── Render env + generate secrets ─────────────────────────
        self.output.step("Rendering environment")
        env_dict = self.env(stacklet_id)

        generate_keys = manifest.get("env", {}).get("generate", [])
        if generate_keys:
            self.output.step("Generating secrets")
            for key in generate_keys:
                self.secrets.ensure(stacklet_id, key)

        # ── Hooks + lifecycle steps ───────────────────────────────
        steps = []
        resolver = HookResolver(stacklet_dir)

        def _step(msg):
            """Report progress through both the output adapter and the
            steps list (for the result dict and backward compat)."""
            steps.append(msg)
            self.output.step(msg)

        if first_run:
            ctx = build_hook_ctx(stacklet_id, env=env_dict, step_fn=_step, stack=self)

            self.output.step("Configuring")
            if not resolver.run("on_configure", ctx):
                self.output.error("Configuration failed")
                return {"error": "Configuration failed", "steps": steps}

            # Re-render env — on_configure may have written to stack.toml
            env_dict = self.env(stacklet_id)
            ctx = build_hook_ctx(stacklet_id, env=env_dict, step_fn=_step, stack=self)

            self.output.step("Installing")
            if not resolver.run("on_install", ctx):
                self.output.error("on_install hook failed")
                return {"error": "on_install hook failed", "steps": steps}
            # The setup-done marker is promoted by run_on_install_success,
            # not here. If health or post-install work fails, a retry must
            # re-enter the full first-run chain. on_install is idempotent
            # per convention, so re-running it is safe.

        self.output.step("Writing .env")
        self._write_env_file(stacklet_dir, env_dict)

        # on_start: runs every up — validates config, starts native services
        if resolver.resolve("on_start"):
            self.output.step("Starting services")
        ctx = build_hook_ctx(stacklet_id, env=env_dict, step_fn=_step, stack=self)
        if not resolver.run("on_start", ctx):
            self.output.error("on_start hook failed")
            return {"error": "on_start hook failed", "steps": steps}

        # Render manifest hints with template vars so credentials are visible
        template_vars = self._build_template_vars()
        template_vars["url"] = self._public_url(stacklet_id, s.get("port", 0))
        template_vars["ip"] = self._lan_ip()
        raw_hints = manifest.get("hints", [])
        hints = []
        for h in raw_hints:
            try:
                hints.append(h.format_map(collections.defaultdict(str, template_vars)))
            except Exception:
                hints.append(h)

        return {
            "ok": True,
            "stacklet": stacklet_id,
            "first_run": first_run,
            "env": env_dict,
            "steps": steps,
            "name": s.get("name", stacklet_id),
            "description": s.get("description", ""),
            "port": s.get("port"),
            "hints": hints,
        }

    def run_on_install_success(self, stacklet_id: str, step_fn=None) -> bool:
        """Run the on_install_success hook with the full ctx.

        Called by the CLI after the health check passes on first run.
        Provides secret/http helpers that on_install doesn't need but
        post-setup hooks do (obtaining API tokens, creating accounts).

        On success, promotes the setup-done marker so subsequent
        `stack up` calls skip the first-run chain. If the hook fails
        (or errors), the marker is NOT touched — a retry re-enters
        bootstrap so transient failures (network, timing) don't leave
        the stacklet half-initialised.
        """
        s = self._find_stacklet(stacklet_id)
        if not s:
            return False

        stacklet_dir = Path(s["path"])
        resolver = HookResolver(stacklet_dir)

        if not resolver.resolve("on_install_success"):
            # No hook = trivially successful post-install work.
            self._mark_setup_done(stacklet_id)
            return True

        env_dict = self.env(stacklet_id)
        step_fn = step_fn or (lambda msg: None)
        ctx = build_hook_ctx(stacklet_id, env=env_dict, step_fn=step_fn, stack=self)
        success = resolver.run("on_install_success", ctx)
        if success:
            self._mark_setup_done(stacklet_id)
        return success

    def _mark_setup_done(self, stacklet_id: str) -> None:
        """Touch the setup-done marker. Gates first_run detection."""
        marker = self._setup_done_marker(stacklet_id)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()

    def run_cli_command(self, stacklet_id: str, command: str, args: list | None = None) -> dict | None:
        """Run a stacklet's CLI plugin command.

        CLI plugins live in stacklets/{id}/cli/{command}.py and define
        a run(args, stacklet, config) function. This method loads and
        executes them through the same path as 'stack {id} {command}'.
        """
        import importlib.util

        stacklet = self._find_stacklet(stacklet_id)
        if not stacklet:
            return {"error": f"Stacklet '{stacklet_id}' not found"}

        cli_dir = Path(stacklet["path"]) / "cli"
        module_path = cli_dir / f"{command}.py"
        if not module_path.exists():
            return {"error": f"Command '{command}' not found for {stacklet_id}"}

        try:
            spec = importlib.util.spec_from_file_location(
                f"{stacklet_id}.cli.{command}", module_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, "run"):
                # TODO: secrets.all() exposes every secret to every plugin.
                # Fine for trusted stacklets, but if we ever support community
                # plugins, scope this to the stacklet's own secrets only.
                from .users import load_users
                config = {
                    "domain": self._cfg("core", "domain"),
                    "data_dir": str(self.data),
                    "repo_root": str(self.root),
                    "instance_dir": str(self.instance_dir),
                    "manifest": stacklet.get("manifest", {}),
                    "stack": self.config,
                    "secrets": self.secrets.all(),
                    "users": load_users(self.instance_dir),
                    # Lazy health probe — plugins call config["is_healthy"]()
                    # when they need to gate work on the stacklet actually
                    # responding. Zero-arg closure so we don't pay the HTTP
                    # round-trip on every plugin invocation.
                    "is_healthy": lambda: self.is_healthy(stacklet_id),
                }
                return mod.run(args or [], stacklet, config)
        except Exception as e:
            return {"error": str(e)}

    def down(self, stacklet_id: str) -> dict:
        """Stop a stacklet. Data and setup state preserved.

        Runs on_stop hook for native services. Docker compose stop
        is handled by the CLI layer — it needs the stacklet path
        returned here to find the compose file.
        """
        s = self._find_stacklet(stacklet_id)
        if not s:
            return {"error": f"Stacklet '{stacklet_id}' not found"}

        resolver = HookResolver(Path(s["path"]))
        ctx = build_hook_ctx(stacklet_id, env={}, step_fn=lambda msg: None, stack=self)
        resolver.run("on_stop", ctx)

        return {"ok": True, "stacklet": stacklet_id, "path": s["path"]}

    def destroy(self, stacklet_id: str) -> dict:
        """Remove a stacklet completely — back to available state.

        on_stop → on_destroy → clear secrets → remove marker →
        delete data → delete .env
        """
        s = self._find_stacklet(stacklet_id)
        if not s:
            return {"error": f"Stacklet '{stacklet_id}' not found"}

        stacklet_dir = Path(s["path"])
        resolver = HookResolver(stacklet_dir)
        ctx = build_hook_ctx(stacklet_id, env={}, step_fn=self.output.step, stack=self)

        resolver.run("on_stop", ctx)
        resolver.run("on_destroy", ctx)

        self.secrets.clear(stacklet_id)
        self.output.step("Secrets cleared")

        marker = self._setup_done_marker(stacklet_id)
        if marker.exists():
            marker.unlink()

        data_dir = self.data / stacklet_id
        if data_dir.exists():
            shutil.rmtree(data_dir)
            self.output.step("Data deleted")

        env_file = stacklet_dir / ".env"
        if env_file.exists():
            env_file.unlink()

        return {"ok": True, "stacklet": stacklet_id}
