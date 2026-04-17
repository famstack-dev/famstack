from __future__ import annotations

"""Stack CLI — manage your stacklets.

Entry point: main(). Invoked by the ./stack shell wrapper.

This module handles everything user-facing:
  - Argument parsing and command routing
  - Docker orchestration around Stack lifecycle
  - Colored output formatting
  - Interactive confirmation prompts
  - Stacklet CLI plugin discovery

All framework logic lives in the Stack class. Docker operations use
the docker module. This file is the glue.
"""

import argparse
import importlib.util
import json
import os
import sys
from ._compat import tomllib
from pathlib import Path

from . import docker
from .commands import COMMANDS
from .prompt import ORANGE, TEAL, GREEN, RED, DIM, BOLD, RESET
from .stack import Stack


def _refresh_core(stck, stacklet_id):
    """Re-render core's env and recreate its containers.

    Core's env references secrets from other stacklets (API tokens etc.)
    that only exist after those stacklets run on_install_success.
    Must use compose up (not docker restart) so containers pick up
    the new env vars — docker restart reuses the old environment.
    """
    if stacklet_id == "core":
        return
    from .docker import running_project_ids
    if "core" not in running_project_ids():
        return
    try:
        stck.refresh_env("core")
        core_compose = docker.find_compose_file(stck.root / "stacklets" / "core")
        if core_compose:
            docker.compose_up(core_compose)
    except Exception:
        pass


def _notify(stck, message):
    """Post a notification to Server Room. No-op if messages isn't running."""
    try:
        import subprocess
        stack_bin = stck.root / "stack"
        subprocess.run(
            [str(stack_bin), "messages", "send", "famstack", message],
            capture_output=True, timeout=10, cwd=str(stck.root),
        )
    except Exception:
        pass


_WELCOME_MESSAGES = {
    "photos": """\
📷 **Photos is live!** Your private family photo library.

Every phone in the house can now back up photos and videos automatically. No cloud, no subscriptions, no storage limits — just your own server.

**Get started:**

- Open {url}
- Log in as `{login}` / `{password}`
- Install the **Immich** app on your phone (iOS / Android)
- Enter `{url}` as the server address
- Your photos start backing up immediately

💡 Everyone in the family gets their own account. Shared albums work too.""",

    "docs": """\
📄 **Documents is live!** Your family document archive.

From now on, contracts, letters, grandma's receipts: Everything gets digitized, indexed, and searchable. No more digging through drawers.

**Get started:**

- You should see a new 'Documents' room here. 
- Head over and check it out.
- Drop a document into the room.

**Paperless-ng** 
- This is where your documents are stored. 
- You can log in as `{login}` / `{password}`

💡 It gets better: Once the AI stacklet is running, documents get classified and tagged automatically. Extracted text formatted nicely in Markdown. All local on your machine.""",

    "ai": """\
🧠 **AI is live!** Local intelligence running on your Mac's GPU.

Your server can now understand text, transcribe voice, and speak: All on your Mac.

**What's running:**

- **oMLX** — LLM inference with Metal GPU acceleration
- **Whisper** — speech-to-text for voice messages
- **Piper TTS** — text-to-speech for spoken responses

💡 Other services use this automatically. Documents get classified, voice messages get transcribed.""",

    "chatai": """\
💬 **ChatAI is live!** Your private ChatGPT.

Talk to your local AI. Ask questions, get summaries, brainstorm ideas. Everything stays on your server.

**Get started:**

- Open {url}
- Log in as `{login}` / `{password}`
- Start chatting

🎙️ **Voice mode** is wired up too. Open `http://localhost:{port}` on the server to try it.

💡 Voice needs HTTPS or localhost. From other devices, open `chrome://flags/#unsafely-treat-insecure-origin-as-secure` and add `{url}`.""",
}


def _notify_up(stck, result):
    """Post a notification after a successful stack up.

    First run: rich welcome message with getting-started guide.
    Subsequent runs: simple "is back online" one-liner.
    """
    name = result.get("name", result.get("stacklet", ""))
    sid = result.get("stacklet", "")
    if sid == "messages":
        return

    if not result.get("first_run"):
        _notify(stck, f"{name} is back online.")
        return

    port = result.get("port")
    url = stck._public_url(sid, port) if port else ""

    # Login credentials for the real admin user (not the tech admin).
    # login_field in the manifest tells us whether the service uses
    # email or username as the login identifier.
    from .users import get_admin_user, user_id, get_user_password
    admin = get_admin_user(stck.root)
    stacklet = stck._find_stacklet(sid)
    login_field = stacklet.get("manifest", {}).get("login_field", "username") if stacklet else "username"
    if admin:
        login = admin.get("email", "") if login_field == "email" else user_id(admin)
        password = get_user_password(admin, stck.secrets) or ""
    else:
        login = ""
        password = ""
    fmt = {
        "url": url,
        "port": port or "",
        "name": name,
        "login": login,
        "password": password,
    }

    # Use a dedicated welcome message if we have one
    template = _WELCOME_MESSAGES.get(sid)
    if template:
        _notify(stck, template.format(**fmt))
    else:
        # Fallback for stacklets without a welcome template
        lines = [f"**{name}** is ready."]
        if url:
            lines.append(f"\n**Open:** {url}")
        hints = result.get("hints", [])
        for hint in hints:
            if url and url in hint:
                continue
            lines.append(f"- {hint}")
        _notify(stck, "\n".join(lines))

VERSION = "0.2.0"


# ── Stack + Docker orchestration ──────────────────────────────────────────

class CLI:
    """Orchestrates Stack lifecycle with Docker container management.

    Stack.up() handles framework logic (env, hooks, secrets).
    CLI.up() adds Docker operations (network, pull, compose, health).
    """

    def __init__(self, stack: Stack):
        self.stack = stack

    def up(self, stacklet_id: str) -> dict:
        """Full up: Stack.up() + Docker compose + health check."""
        result = self.stack.up(stacklet_id)
        if "error" in result:
            return result

        stacklet = self.stack._find_stacklet(stacklet_id)
        if not stacklet:
            return result

        stacklet_dir = Path(stacklet["path"])
        manifest = stacklet.get("manifest", {})
        env_dict = result.get("env", {})
        first_run = result.get("first_run", False)

        _, net_err = docker.ensure_network()
        if net_err:
            return {"error": f"Docker network error: {net_err}"}

        compose_file = docker.find_compose_file(stacklet_dir)
        if compose_file:
            compose_env = {
                **env_dict,
                "STACK_DATA_DIR": str(self.stack.data),
                "STACK_DOMAIN": self.stack._cfg("core", "domain"),
            }

            if manifest.get("build"):
                with self.stack.output.spinner("Building service"):
                    docker.compose_build(compose_file, env=compose_env)
            elif first_run:
                with self.stack.output.spinner("Pulling images"):
                    docker.compose_pull(compose_file, env=compose_env)

            with self.stack.output.spinner("Starting containers"):
                code, err = docker.compose_up(compose_file, env=compose_env)
            if code != 0:
                return {"error": "Failed to start services", "output": err}

        if compose_file:
            # Health checks run first — hooks can assume the service is healthy
            template_vars = self.stack._build_template_vars()
            checks = self.stack._resolve_health_checks(manifest, template_vars)
            for check in checks:
                h_url = check["url"]
                h_name = check.get("name", "")
                h_hint = check.get("hint", "")
                h_headers = check.get("headers", {})
                if not h_url:
                    continue
                label = h_name or h_url
                h_timeout = check.get("timeout", 120)
                with self.stack.output.spinner(f"Health check: {label}") as sp:
                    status = docker.wait_for_health(h_url, headers=h_headers, timeout=h_timeout)
                    if status == "auth":
                        sp.fail("Reachable but requires API key")
                    elif status != "ready":
                        sp.fail(h_hint if h_hint else None)

        if first_run:
            # Hook failure must propagate — a silently-failed post-install
            # leaves the stacklet half-bootstrapped (no admin, no rooms)
            # and the marker correctly stays absent for a retry.
            success = self.stack.run_on_install_success(
                stacklet_id, step_fn=self.stack.output.step)
            if not success:
                return {"error": "on_install_success hook failed"}

        # on_start_ready: runs every up, after health checks pass.
        # The service is healthy and accepting API calls. Use this for
        # seeding data, syncing accounts, or anything that needs the
        # service running. Idempotent hooks only.
        from .hooks import HookResolver, build_hook_ctx
        ready_resolver = HookResolver(stacklet_dir)
        if ready_resolver.resolve("on_start_ready"):
            ready_ctx = build_hook_ctx(
                stacklet_id, env=env_dict,
                step_fn=self.stack.output.step, stack=self.stack,
            )
            ready_resolver.run("on_start_ready", ready_ctx)

        result["name"] = stacklet.get("name", stacklet_id)
        result["port"] = stacklet.get("port")
        result["description"] = stacklet.get("description", "")
        result["manifest"] = manifest
        return result

    def down(self, stacklet_id: str) -> dict:
        """Stop: Stack.down() + Docker compose stop.

        Special case: stacklet_id == "all" stops every currently-running
        stacklet in reverse dependency order (dependents first).
        """
        if stacklet_id == "all":
            return self._down_all()

        result = self.stack.down(stacklet_id)
        if "error" in result:
            return result

        stacklet_dir = Path(result.get("path", ""))
        compose_file = docker.find_compose_file(stacklet_dir)
        if compose_file:
            if not (stacklet_dir / ".env").exists():
                try:
                    self.stack.refresh_env(stacklet_id)
                except ValueError:
                    pass
            code, output = docker.compose_stop(compose_file)
            return {"stacklet": stacklet_id, "action": "down",
                    "success": code == 0, "output": output}
        return {"stacklet": stacklet_id, "action": "down", "success": True}

    def _down_all(self) -> dict:
        """Stop every running stacklet in reverse dependency order.

        Running is determined by Docker — stopped/available stacklets are
        untouched. Dependents shut down before their deps so services
        making outbound calls don't error on a disappearing backend.
        """
        running = docker.running_project_ids()
        order = _reverse_dependency_order(self.stack.discover(), running)

        stopped: list[str] = []
        errors: list[dict] = []
        for sid in order:
            result = self.down(sid)
            if result.get("success", result.get("ok")):
                stopped.append(sid)
            else:
                errors.append({"stacklet": sid, "result": result})

        return {"ok": not errors, "stopped": stopped, "errors": errors}

    def destroy(self, stacklet_id: str) -> dict:
        """Destroy: Docker compose down + Stack.destroy()."""
        stacklet = self.stack._find_stacklet(stacklet_id)
        if not stacklet:
            return {"error": f"Stacklet '{stacklet_id}' not found"}

        stacklet_dir = Path(stacklet["path"])
        compose_file = docker.find_compose_file(stacklet_dir)
        if compose_file:
            if not (stacklet_dir / ".env").exists():
                try:
                    self.stack.refresh_env(stacklet_id)
                except ValueError:
                    pass
            self.stack.output.step("Stopping containers...")
            code, output = docker.compose_down(compose_file)
            if code != 0:
                return {"error": "Failed to stop containers", "output": output}
            self.stack.output.step("Containers removed")

        return self.stack.destroy(stacklet_id)


# ── Topological helpers ───────────────────────────────────────────────────

def _reverse_dependency_order(stacklets: list[dict], include: set[str]) -> list[str]:
    """Return the subset of stacklet IDs in `include`, ordered so that
    dependents come before their dependencies.

    Kahn's algorithm on the reversed dependency graph: build a map of
    dep → set(dependents), then repeatedly emit stacklets whose remaining
    dependents have already been emitted. Stacklets with requires on
    stacklets NOT in `include` have those edges dropped — we only care
    about ordering among the ones we're actually stopping.
    """
    by_id = {s["id"]: s for s in stacklets if s["id"] in include}
    if not by_id:
        return []

    # Edges: for each stacklet, the deps it relies on that we're also stopping.
    remaining_deps = {
        sid: {d for d in s.get("manifest", {}).get("requires", []) if d in by_id}
        for sid, s in by_id.items()
    }
    # Who depends on me? sid → set of dependents.
    dependents: dict[str, set[str]] = {sid: set() for sid in by_id}
    for sid, deps in remaining_deps.items():
        for d in deps:
            dependents[d].add(sid)

    # Start with leaf stacklets — the ones nobody depends on.
    ready = sorted([sid for sid, d in dependents.items() if not d])
    order: list[str] = []
    while ready:
        sid = ready.pop(0)
        order.append(sid)
        # This sid shut down — its deps lose one dependent.
        for dep in remaining_deps[sid]:
            dependents[dep].discard(sid)
            if not dependents[dep]:
                ready.append(dep)
        ready.sort()  # deterministic order among siblings

    # Any cycle leaves nodes stuck — append them so we still try to stop
    # them. Warn so the underlying manifest bug gets noticed; append order
    # is dict-insertion, not meaningful.
    stuck = [sid for sid in by_id if sid not in order]
    if stuck:
        print(
            f"  {ORANGE}⚠{RESET}  Dependency cycle detected among: {', '.join(stuck)}",
            file=sys.stderr,
        )
        order.extend(stuck)

    return order


# ── Repo discovery ────────────────────────────────────────────────────────

def find_repo_root() -> Path | None:
    """Walk up from CWD to find the repo root (has stacklets/ dir)."""
    here = Path.cwd()
    for candidate in [here, *here.parents]:
        if (candidate / "stacklets").is_dir():
            return candidate
    return None


def find_instance_dir() -> Path | None:
    """Return the directory holding stack.toml / users.toml / .stack/.

    STACK_DIR overrides the default (repo root). Used for dedicated test
    instances or sandboxes that share the same stacklet definitions but
    keep their config, secrets, and state isolated.

    Returns None if STACK_DIR is set but points at a non-existent path —
    forces the caller to fail loudly rather than silently fall back.
    """
    if env := os.environ.get("STACK_DIR"):
        path = Path(env).expanduser().resolve()
        return path if path.is_dir() else None
    return find_repo_root()


def create_stack(repo_root: Path, instance_dir: Path | None = None) -> Stack:
    """Create a Stack instance.

    repo_root: where stacklets/ are discovered (and git lives).
    instance_dir: where stack.toml and runtime state live. Defaults to
    repo_root, which is the single-instance case.
    """
    from .output import TerminalOutput

    instance = instance_dir or repo_root
    config_path = instance / "stack.toml"
    cfg = {}
    if config_path.exists():
        try:
            with open(config_path, "rb") as f:
                cfg = tomllib.load(f)
        except Exception:
            pass

    name = cfg.get("core", {}).get("name", "stack")
    data_dir = cfg.get("core", {}).get("data_dir", f"~/{name}-data")
    return Stack(
        root=repo_root,
        data=Path(data_dir).expanduser(),
        instance_dir=instance,
        output=TerminalOutput(),
    )


# ── Output formatting ────────────────────────────────────────────────────

def print_error(result: dict) -> None:
    """Print an error with stack colors."""
    error = result.get("error", "unknown error")
    print(f"\n  {RED}✗{RESET}  {error}\n", file=sys.stderr)
    for p in result.get("problems", result.get("dependents", [])):
        print(f"      {p}", file=sys.stderr)
    if result.get("hint"):
        print(f"\n  Run first: {result['hint']}\n", file=sys.stderr)


def print_up_success(result: dict, stck: Stack) -> None:
    """Welcome screen after successful stack up."""
    from .prompt import TEAL

    name = result.get("name", result.get("stacklet", ""))
    sid = result.get("stacklet", "")
    port = result.get("port")
    description = result.get("description", "")

    w = 60
    print("  " + "\u2500" * w)
    print(f"  {GREEN}\u2713{RESET}  {BOLD}{name}{RESET} is running")
    if description:
        print(f"       {DIM}{description}{RESET}")
    print()

    if port:
        url = stck._public_url(sid, port)
        print(f"  {'URL':<14}  {TEAL}{url}{RESET}")

    # Login credentials for the admin user
    manifest = result.get("manifest", {})
    login_field = manifest.get("login_field")
    if login_field:
        from .users import get_admin_user, user_id, get_user_password
        admin = get_admin_user(stck.root)
        if admin:
            login = admin.get("email", "") if login_field == "email" else user_id(admin)
            password = get_user_password(admin, stck.secrets) or ""
            print(f"  {'Login':<14}  {TEAL}{login}{RESET} / {TEAL}{password}{RESET}")

    for svc_name, svc_status in result.get("native_services", []):
        print(f"  {svc_name:<14}  {svc_status}")

    data_dir = str(stck.data / sid) + "/"
    home = str(Path.home())
    display = data_dir.replace(home, "~", 1) if data_dir.startswith(home) else data_dir
    print(f"  {'Your data':<14}  {DIM}{display}{RESET}")
    print()

    for w_msg in result.get("warnings", []):
        print(f"  {ORANGE}\u26a0{RESET}  {w_msg}")

    # Next steps — rendered from manifest hints with credentials
    hints = result.get("hints", [])
    if hints:
        print(f"  {BOLD}Next steps{RESET}")
        for hint in hints:
            print(f"  {DIM}\u2022{RESET} {hint}")
        print()

    print("  " + "\u2500" * w)
    print()


def print_list(result: dict, stck=None) -> None:
    """Stacklet list with status colors."""
    from .prompt import DIM, RESET, status_list
    stacklets = result.get("stacklets", [])
    if not stacklets:
        print("\n  No stacklets found.\n")
        return
    status_list(stacklets)
    sha = stck._git_commit() if stck else ""
    version_info = f"  {DIM}{VERSION} ({sha}){RESET}" if sha else ""
    print(f"  {result.get('online', 0)}/{result.get('total', 0)} online{version_info}")
    print(f"  {DIM}stack up <id> to start a stacklet{RESET}\n")


def print_status(result: dict) -> None:
    """Rich status overview: system info + stacklet list."""
    from .prompt import TEAL, status_list

    name = result.get("name", "stack")
    version = result.get("version", "?")
    commit = result.get("commit", "")
    runtime = result.get("runtime", "?")
    docker_v = result.get("docker_version", "?")
    host = result.get("host", {})
    config = result.get("config", {})
    data_dir = result.get("data_dir", "")

    # Shorten data_dir for display
    home = str(Path.home())
    display_data = data_dir.replace(home, "~", 1) if data_dir.startswith(home) else data_dir

    col = 14
    print()
    print(f"  {ORANGE}{BOLD}{name}{RESET} {DIM}{version} ({commit}){RESET}")
    print()

    print(f"  {BOLD}System{RESET}")
    print(f"    {DIM}{'Runtime':<{col}}{RESET}{runtime} (Docker {docker_v})")
    mem_total = host.get("memory_total_gb")
    mem_used = host.get("memory_used_gb")
    if mem_total and mem_used:
        print(f"    {DIM}{'RAM':<{col}}{RESET}{mem_used} / {mem_total} GB")
    disk_free = host.get("disk_free_gb")
    disk_pct = host.get("disk_used_pct")
    if disk_free is not None:
        color = RED if disk_pct > 90 else ORANGE if disk_pct > 80 else ""
        reset = RESET if color else ""
        print(f"    {DIM}{'Disk':<{col}}{RESET}{color}{disk_pct}% used{reset}, {disk_free} GB free")
    domain = config.get("domain", "")
    if domain:
        print(f"    {DIM}{'Domain':<{col}}{RESET}{domain}")
    print(f"    {DIM}{'Data':<{col}}{RESET}{display_data}")
    print()

    # Stacklet list
    stacklets = result.get("stacklets", [])
    if stacklets:
        print(f"  {BOLD}Stacklets{RESET}")
        status_list(stacklets)
        online = result.get("online", 0)
        total = result.get("total", 0)
        print(f"  {online}/{total} online")
        print(f"  {DIM}stack up <id> to start a stacklet{RESET}")

    print(f"\n  {DIM}Run ./stack help for commands{RESET}\n")


def print_env(result: dict) -> None:
    """Rendered environment variables."""
    if "error" in result:
        print_error(result)
        return
    print()
    for k, v in sorted(result.get("env", {}).items()):
        print(f"  {k}={v}")
    print()


# ── Command handlers ──────────────────────────────────────────────────────

def handle_up(stck, args):
    from .prompt import TEAL
    cli = CLI(stck)
    stacklet = stck._find_stacklet(args.stacklet)
    name = stacklet.get("name", args.stacklet) if stacklet else args.stacklet
    print(f"\n  Bringing up {TEAL}{name}{RESET}...\n", file=sys.stderr)
    result = cli.up(args.stacklet)
    if result.get("ok"):
        print_up_success(result, stck)
        _notify_up(stck, result)
        _refresh_core(stck, args.stacklet)
    else:
        print_error(result)
        if args.stacklet != "messages":
            _notify(stck, f"{name} failed to start.")
        sys.exit(1)


def handle_down(stck, args):
    cli = CLI(stck)

    if args.stacklet == "all":
        result = cli.down("all")
        stopped = result.get("stopped", [])
        if not result.get("ok"):
            print_error({"error": "Some stacklets failed to stop",
                         "problems": [f"{e['stacklet']}" for e in result.get("errors", [])]})
            sys.exit(1)
        if not stopped:
            print(f"  {DIM}Nothing was running.{RESET}")
            return
        for sid in stopped:
            print(f"  {GREEN}✓{RESET} {sid}: stopped")
        return

    stacklet = stck._find_stacklet(args.stacklet)
    name = stacklet.get("name", args.stacklet) if stacklet else args.stacklet

    result = cli.down(args.stacklet)
    if result.get("success", result.get("ok")):
        print(f"  {GREEN}✓{RESET} {args.stacklet}: stopped")
        if args.stacklet != "messages":
            _notify(stck, f"{name} was stopped.")
        _refresh_core(stck, args.stacklet)
    else:
        print_error(result)
        sys.exit(1)


def handle_destroy(stck, args):
    stacklet = stck._find_stacklet(args.stacklet)
    if not stacklet:
        print_error({"error": f"Stacklet '{args.stacklet}' not found"})
        sys.exit(1)

    name = stacklet.get("name", args.stacklet)
    data_path = stck.data / args.stacklet

    if not stacklet.get("enabled"):
        if data_path.exists():
            import shutil
            shutil.rmtree(data_path)
            print(f"  {GREEN}✓{RESET} Cleaned up leftover data for {name}")
        else:
            print(f"  {DIM}{name} is not set up — nothing to destroy.{RESET}")
        return

    if not getattr(args, "yes", False):
        if not sys.stdin.isatty():
            print_error({"error": "Pass --yes to confirm (non-interactive)"})
            sys.exit(1)
        print(f"\n  {ORANGE}⚠  Destroy {name}?{RESET}\n")
        print(f"  This will permanently remove:")
        print(f"    · Containers and volumes")
        if data_path.exists():
            print(f"    · All stored data")
        print(f"    · Secrets and config\n")
        print(f"  You may want to back up your data first.\n")
        try:
            answer = input("  Type 'destroy' to confirm: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if answer != "destroy":
            print(f"  {DIM}Aborted{RESET}")
            return

    print(f"\n  Destroying {name}...\n", file=sys.stderr)
    cli = CLI(stck)
    result = cli.destroy(args.stacklet)
    if result.get("ok"):
        print(f"  {GREEN}✓{RESET} {name} destroyed")
        if args.stacklet != "messages":
            _notify(stck, f"{name} was uninstalled.")
    else:
        print_error(result)
        sys.exit(1)


def handle_status(stck, args):
    preferred = stck._cfg("core", "runtime", "orbstack")
    docker.init_runtime(preferred)
    result = stck.status()
    print_status(result)


def handle_list(stck, args):
    print_list(stck.list(), stck)


def handle_config(stck, args):
    """Config subcommands. Bare 'stack config' prints stack.toml."""
    action = getattr(args, "config_action", None)
    if action == "admin":
        _config_admin(stck)
        return
    path = stck.root / "stack.toml"
    if not path.exists():
        print("  No stack.toml found.")
        return
    print(path.read_text())


def _config_admin(stck):
    """Print tech admin credentials."""
    from .users import TECH_ADMIN_USERNAME, TECH_ADMIN_EMAIL, get_admin_password
    password = get_admin_password(stck.secrets)
    if not password:
        print(f"  {RED}✗{RESET}  No admin password found. Run 'stack install' first.", file=sys.stderr)
        sys.exit(1)
    if not sys.stdout.isatty():
        print(password)
        return
    print(f"\n  {BOLD}Tech Admin{RESET}\n")
    print(f"  {"Username:":<12}{TEAL}{TECH_ADMIN_USERNAME}{RESET}")
    print(f"  {"Email:":<12}{TEAL}{TECH_ADMIN_EMAIL}{RESET}")
    print(f"  {"Password:":<12}{TEAL}{password}{RESET}\n")


def handle_env(stck, args):
    print_env(COMMANDS["env"].execute(stck, stacklet=args.stacklet))


def _init_runtime(stck):
    """Initialize Docker runtime context. Called once at startup."""
    preferred = stck._cfg("core", "runtime", "orbstack")
    status, warning = docker.init_runtime(preferred)
    if status is None:
        print_error({"error": warning}); sys.exit(1)
    if warning:
        print(f"  {ORANGE}⚠{RESET}  {status}")
        for line in warning.splitlines():
            print(f"     {line}")
        print()
    else:
        print(f"  {GREEN}✓{RESET} {status}")


def handle_init(stck, args):
    ok, err = docker.check_docker()
    if err:
        # No Docker at all — guide to OrbStack
        print(f"\n  {ORANGE}Docker is not running.{RESET}\n")
        print(f"  famstack uses OrbStack as its container runtime.")
        print(f"  It's fast, lightweight, and built for macOS.\n")
        print(f"  Install it from {TEAL}https://orbstack.dev{RESET}")
        print(f"  or run: {TEAL}brew install orbstack{RESET}\n")
        print(f"  Then run {TEAL}./stack init{RESET} again.\n")
        sys.exit(1)

    print(f"  {GREEN}✓{RESET} {ok}")

    preferred = stck._cfg("core", "runtime", "orbstack")
    status, warning = docker.init_runtime(preferred)
    if status is None:
        print_error({"error": warning}); sys.exit(1)
    if warning:
        # Docker works but OrbStack not available
        print(f"  {ORANGE}⚠{RESET}  {status}")
        print()
        print(f"  famstack is tested with OrbStack only.")
        print(f"  Docker Desktop can cause high CPU usage and a sluggish system.\n")
        print(f"  Install OrbStack from {TEAL}https://orbstack.dev{RESET}")
        print(f"  or run: {TEAL}brew install orbstack{RESET}\n")
        print(f"  famstack will use it automatically once installed.\n")
    else:
        print(f"  {GREEN}✓{RESET} {status}")

    ok, err = docker.ensure_network()
    if err:
        print_error({"error": err}); sys.exit(1)
    print(f"  {GREEN}✓{RESET} {ok}")

    stck.data.mkdir(parents=True, exist_ok=True)
    print(f"  {GREEN}✓{RESET} Data directory: {stck.data}")

    (stck.root / ".stack").mkdir(exist_ok=True)
    print(f"  {GREEN}✓{RESET} Runtime state ready")


def handle_logs(stck, args):
    stacklet = stck._find_stacklet(args.stacklet)
    if not stacklet:
        print_error({"error": f"'{args.stacklet}' not found"}); sys.exit(1)
    compose_file = docker.find_compose_file(Path(stacklet["path"]))
    if not compose_file:
        print_error({"error": f"No compose file for {args.stacklet}"}); sys.exit(1)
    code, stdout, stderr = docker.compose(
        compose_file, "logs", "--tail", str(args.tail), "--no-color")
    print(stdout or stderr)


def handle_restart(stck, args):
    cli = CLI(stck)
    cli.down(args.stacklet)
    result = cli.up(args.stacklet)
    if "error" in result:
        print_error(result); sys.exit(1)
    else:
        print_up_success(result, stck)


def handle_setup(stck, args):
    stacklet = stck._find_stacklet(args.stacklet)
    if not stacklet:
        print_error({"error": f"'{args.stacklet}' not found"}); sys.exit(1)

    # Check stacklet is running
    running = docker.running_project_ids()
    if args.stacklet not in running:
        print_error({"error": f"'{args.stacklet}' is not running. Run './stack up {args.stacklet}' first."})
        sys.exit(1)

    print(f"\n  Re-running setup for {args.stacklet}...\n", file=sys.stderr)
    ok = stck.run_on_install_success(args.stacklet, step_fn=stck.output.step)
    if not ok:
        print_error({"error": f"Setup hook failed for '{args.stacklet}'"})
        sys.exit(1)
    print(f"\n  {GREEN}✓{RESET}  Setup complete", file=sys.stderr)


def handle_install(stck, args):
    full = getattr(args, "full", False)
    if full:
        from .installer import wizard
    else:
        from .installer_v2 import wizard

    # Save/restore terminal in case a crash leaves it in raw mode
    saved_term = None
    try:
        import termios
        saved_term = termios.tcgetattr(sys.stdin.fileno())
    except Exception:
        pass

    def restore():
        if saved_term:
            try:
                import termios
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, saved_term)
            except Exception:
                pass

    try:
        wizard()
    except KeyboardInterrupt:
        restore()
        print(f"\n\n  {DIM}Cancelled — nothing was changed.{RESET}\n")
    finally:
        restore()


def handle_uninstall(stck, args):
    # 'stack uninstall chatai' is a common mistake — catch it early
    remaining = [a for a in sys.argv[2:] if not a.startswith("-")]
    if remaining:
        sid = remaining[0]
        print(f"\n  {RED}✗{RESET}  'uninstall' removes the entire stack, not a single stacklet.")
        print(f"  To remove {sid}, use: {TEAL}stack destroy {sid}{RESET}\n")
        sys.exit(1)

    name = stck.product_name()
    config_exists = (stck.root / "stack.toml").exists()
    state_exists = (stck.root / ".stack").exists()
    has_containers = bool(docker.all_project_ids())
    if not config_exists and not state_exists and not stck.data.exists() and not has_containers:
        print(f"\n  {DIM}{name} is not set up. Run 'stack install' to get started.{RESET}\n")
        return

    if not sys.stdin.isatty() and not getattr(args, "yes", False):
        print_error({"error": "Pass --yes to confirm (non-interactive)"})
        sys.exit(1)

    if not getattr(args, "yes", False):
        print(f"\n  {ORANGE}\u26a0  Uninstall {name}?{RESET}\n")
        print(f"  This will:")
        print(f"    \u2022 Destroy all running services and their containers")
        print(f"    \u2022 Remove stack.toml and users.toml")
        print(f"    \u2022 Remove all runtime state (.stack/)")
        print(f"    \u2022 Optionally remove all service data\n")
        try:
            answer = input("  Type 'uninstall' to confirm: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if answer != "uninstall":
            print(f"  {DIM}Aborted{RESET}")
            return

    # Only destroy stacklets that actually have state
    cli = CLI(stck)
    container_ids = docker.all_project_ids()
    for s in stck.discover():
        sid = s["id"]
        has_data = (stck.data / sid).exists()
        has_marker = s.get("enabled")
        has_container = sid in container_ids
        if not has_data and not has_marker and not has_container:
            continue
        print(f"  {ORANGE}Uninstalling {s['name']}...{RESET}", file=sys.stderr)
        cli.destroy(sid)
        print(f"  {GREEN}\u2713{RESET} {s['name']} uninstalled")

    # Remove config files
    for name in ("stack.toml", "users.toml"):
        path = stck.root / name
        if path.exists():
            path.unlink()
            print(f"  {GREEN}\u2713{RESET} Removed {name}")

    # Remove runtime state
    state_dir = stck.root / ".stack"
    if state_dir.exists():
        import shutil
        shutil.rmtree(state_dir)
        print(f"  {GREEN}\u2713{RESET} Removed .stack/")

    # Offer to remove data
    if stck.data.exists():
        home = str(Path.home())
        display = str(stck.data).replace(home, "~", 1)
        print(f"\n  {RED}Data directory: {display}{RESET}")
        print(f"  {RED}This contains all your photos, messages, documents, etc.{RESET}")
        print(f"  {RED}This action is irreversible.{RESET}\n")
        try:
            rm_data = input("  Type 'delete' to remove all data: ").strip()
        except (EOFError, KeyboardInterrupt):
            rm_data = ""
        if rm_data == "delete":
            import shutil
            shutil.rmtree(stck.data)
            print(f"  {GREEN}\u2713{RESET} Removed {display}")
        else:
            print(f"  {DIM}Data kept at {display}{RESET}")

    print(f"\n  {GREEN}\u2713{RESET} {name} uninstalled")
    print(f"  {DIM}Run 'stack install' to start fresh.{RESET}\n")


def handle_version(stck, args):
    sha = stck._git_commit()
    print(f"{stck.product_name()} {VERSION} ({sha})")


# ── Plugin loader ─────────────────────────────────────────────────────────

def _load_stacklet_commands(stck: Stack) -> dict:
    """Discover CLI plugins from stacklets/{id}/cli/*.py."""
    commands = {}
    for s in stck.discover():
        cli_dir = Path(s["path"]) / "cli"
        if not cli_dir.exists():
            continue
        for py in sorted(cli_dir.glob("*.py")):
            if py.name.startswith("_"):
                continue
            commands.setdefault(s["id"], {})[py.stem] = str(py)
    return commands


# ── Main ──────────────────────────────────────────────────────────────────

DISPATCH = {
    "install": handle_install,
    "uninstall": handle_uninstall,
    "init": handle_init,
    "up": handle_up,
    "down": handle_down,
    "destroy": handle_destroy,
    "status": handle_status,
    "list": handle_list,
    "config": handle_config,
    "env": handle_env,
    "restart": handle_restart,
    "setup": handle_setup,
    "logs": handle_logs,
    "version": handle_version,
}

# ── Help ─────────────────────────────────────────────────────────────────

_HELP_COMMANDS = [
    ("Lifecycle", [
        ("up <stacklet>",      "Start a stacklet and its containers"),
        ("down <stacklet>",    "Stop a running stacklet"),
        ("restart <stacklet>", "Restart stacklet (includes env update)"),
        ("setup <stacklet>",   "Re-run first-time setup (backend detection, accounts, etc.)"),
        ("destroy <stacklet>", "Remove containers, data, and secrets (Destructive operation)"),
    ]),
    ("Info", [
        ("list",               "Show all stacklets and their status"),
        ("config",             "Print stack.toml configuration"),
        ("config admin",       "Print tech admin credentials"),
        ("env <stacklet>",     "Print rendered environment variables"),
        ("logs <stacklet>",    "Tail container logs"),
    ]),
    ("Setup", [
        ("install",            "Interactive setup wizard"),
        ("uninstall",          "Remove all services, config, and data"),
        ("init",               "Create Docker network and data directories"),
    ]),
]


def print_help(name="stack", plugin_cmds=None):
    """Colored help screen with grouped commands."""
    print(f"\n  {ORANGE}{BOLD}{name}{RESET} {DIM}— manage your stacklets{RESET}\n")
    print(f"  {BOLD}Usage:{RESET}  {DIM}stack <command> [options]{RESET}\n")

    col = 22
    for group, cmds in _HELP_COMMANDS:
        print(f"  {BOLD}{group}{RESET}")
        for cmd, desc in cmds:
            print(f"    {TEAL}{cmd:<{col}}{RESET}{desc}")
        print()

    if plugin_cmds:
        print(f"  {BOLD}Stacklet Specific{RESET}")
        for sid, cmds in plugin_cmds.items():
            subcmds = ", ".join(cmds)
            print(f"    {TEAL}{sid:<{col}}{RESET}{DIM}{subcmds}{RESET}")
        print()

    print(f"    {TEAL}{'version':<{col}}{RESET}Print version")
    print(f"    {TEAL}{'--help':<{col}}{RESET}Show this help\n")


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    repo_root = find_repo_root()
    instance_dir = find_instance_dir()

    # Fail loudly when STACK_DIR points at a non-existent path. Silently
    # falling back to the default would hide config mistakes.
    if os.environ.get("STACK_DIR") and instance_dir is None:
        print(
            f"  {RED}✗{RESET}  STACK_DIR={os.environ['STACK_DIR']!r} is not a directory",
            file=sys.stderr,
        )
        sys.exit(1)

    # Fresh install — launch the wizard only when no command given
    has_config = instance_dir and (instance_dir / "stack.toml").exists()
    has_command = len(sys.argv) > 1
    if repo_root and not has_config and not has_command and sys.stdin.isatty():
        stck = create_stack(repo_root, instance_dir)
        handle_install(stck, None)
        return

    # --json: machine-readable output for read-only queries (list, env)
    json_mode = "--json" in sys.argv
    while "--json" in sys.argv:
        sys.argv.remove("--json")

    parser = argparse.ArgumentParser(
        prog="stack", add_help=False,
    )
    parser.add_argument("-h", "--help", action="store_true")
    parser.add_argument("--version", action="store_true")

    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("install"); p.add_argument("--full", action="store_true")
    p = sub.add_parser("uninstall"); p.add_argument("--yes", action="store_true")
    sub.add_parser("init")
    sub.add_parser("status")
    sub.add_parser("list")
    p = sub.add_parser("config")
    config_sub = p.add_subparsers(dest="config_action")
    config_sub.add_parser("admin")
    sub.add_parser("help")
    sub.add_parser("version")

    p = sub.add_parser("up"); p.add_argument("stacklet")
    p = sub.add_parser("down"); p.add_argument("stacklet")
    p = sub.add_parser("destroy"); p.add_argument("stacklet"); p.add_argument("--yes", action="store_true")
    p = sub.add_parser("restart"); p.add_argument("stacklet")
    p = sub.add_parser("setup"); p.add_argument("stacklet")
    p = sub.add_parser("env"); p.add_argument("stacklet")
    p = sub.add_parser("logs"); p.add_argument("stacklet"); p.add_argument("--tail", default=50, type=int)

    # Stacklet CLI plugins
    stacklet_cmds = {}
    if repo_root:
        stck = create_stack(repo_root, instance_dir)
        stacklet_cmds = _load_stacklet_commands(stck)
        for sid, cmds in stacklet_cmds.items():
            sp = sub.add_parser(sid)
            sp_sub = sp.add_subparsers(dest="action")
            for cmd_name, mod_path in cmds.items():
                sp_sub.add_parser(cmd_name)

    args, _remaining = parser.parse_known_args()

    if args.version:
        name = stck.product_name() if repo_root else "stack"
        sha = stck._git_commit() if repo_root else "unknown"
        print(f"{name} {VERSION} ({sha})")
        return

    if args.help:
        name = stck.product_name() if repo_root else "stack"
        print_help(name, stacklet_cmds or None)
        sys.exit(0)

    if not args.command:
        if repo_root and has_config:
            stck = create_stack(repo_root, instance_dir)
            preferred = stck._cfg("core", "runtime", "orbstack")
            docker.init_runtime(preferred)
            result = stck.status()
            if json_mode:
                json.dump(result, sys.stdout, indent=2, default=str)
                print()
            else:
                print_status(result)
            return
        name = stck.product_name() if repo_root else "stack"
        print_help(name, stacklet_cmds or None)
        sys.exit(0)

    if not repo_root:
        print(f"  {RED}✗{RESET}  Can't find stack directory", file=sys.stderr)
        sys.exit(1)

    stck = create_stack(repo_root, instance_dir)

    # Pin all docker commands to the configured runtime context
    preferred = stck._cfg("core", "runtime", "orbstack")
    docker.init_runtime(preferred)

    # JSON mode — read-only queries only
    _JSON_COMMANDS = {"status", "list", "env"}
    if json_mode and args.command in _JSON_COMMANDS:
        if args.command == "status":
            json.dump(stck.status(), sys.stdout, indent=2, default=str)
            print()
            return
        cmd = COMMANDS.get(args.command)
        if cmd:
            kw = {"stacklet": args.stacklet} if hasattr(args, "stacklet") else {}
            json.dump(cmd.execute(stck, **kw), sys.stdout, indent=2, default=str)
            print()
            return

    # Dispatch
    handler = DISPATCH.get(args.command)
    if handler:
        handler(stck, args)
        return

    # Stacklet CLI plugins
    if args.command in stacklet_cmds:
        action = getattr(args, "action", None)
        if not action:
            parser.parse_args([args.command, "--help"]); return
        result = stck.run_cli_command(args.command, action, _remaining)
        if result and "error" in result:
            print_error(result); sys.exit(1)
        return

    print_help(stck.product_name(), stacklet_cmds or None)
    sys.exit(1)
