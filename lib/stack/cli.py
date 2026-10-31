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

Stacklet commands (`stacklets/<id>/cli/<cmd>.py`) follow the command
rules in docs/stack-reference.md § CLI Commands: named for what the
admin wants, nouns with verbs, one meaning per flag, apply by default,
wait with progress, return a dict.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import subprocess
import sys
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import NoReturn

from . import app as menubar_app
from . import caddy
from . import docker
from . import doctor
from . import global_command
from . import logscan
from .plugins import plugin_help, stacklet_commands
from .commands import COMMANDS
from .migrations import MigrationError
from .prompt import ORANGE, TEAL, GREEN, RED, DIM, BOLD, RESET
from .stack import Stack, configured_data_dir


def _refresh_core(stck, stacklet_id):
    """Re-render core's env and recreate its containers.

    Core's env references secrets from other stacklets (API tokens etc.)
    that only exist after those stacklets run on_install_success.
    `compose_up` force-recreates unconditionally, so the bot-runner
    picks up a freshly-written token even though compose's service-
    config hash doesn't cover env_file contents.

    Failures here used to be swallowed by a bare-except, which masked
    real bugs (a render error in core's env left the bot-runner stale
    and the user with no signal). We now surface failures via the
    output channel — the parent `up` succeeded, so we don't abort, but
    we don't pretend nothing went wrong either.
    """
    if stacklet_id == "core":
        return
    # A "core" compose project may be running on the same Docker daemon yet
    # belong to a different stack — tests run against the host daemon, and a
    # host can carry a second install. Only refresh when THIS stack actually
    # ships a core stacklet; otherwise refresh_env("core") raises on a stack
    # that has none.
    if not (stck.root / "stacklets" / "core").is_dir():
        return
    from .docker import running_project_ids
    if "core" not in running_project_ids():
        return
    stck.refresh_env("core")
    core_compose = docker.find_compose_file(stck.root / "stacklets" / "core")
    if core_compose:
        code, err = docker.compose_up(core_compose)
        if code != 0 and err:
            stck.output.step(f"core refresh failed: {err.strip().splitlines()[-1] if err.strip() else 'unknown error'}")


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

VERSION = "0.5.0-beta.1"


# ── Stack + Docker orchestration ──────────────────────────────────────────

class CLI:
    """Orchestrates Stack lifecycle with Docker container management.

    Stack.up() handles framework logic (env, hooks, secrets).
    CLI.up() adds Docker operations (network, pull, compose, health).
    """

    def __init__(self, stack: Stack):
        self.stack = stack

    def up(self, stacklet_id: str, answers: dict | None = None) -> dict:
        """Full up: Stack.up() + Docker compose + health check.

        Special case: stacklet_id == "all" brings up every installed
        stacklet in forward dependency order (deps before dependents).
        `answers` settle a first run's setup questions (`stack.configure`).
        """
        if stacklet_id == "all":
            return self._up_all()

        self._say_bots_wait()
        result = self._start(stacklet_id, answers)
        if "error" in result or "cancelled" in result:
            return result
        result = self._migrate(stacklet_id, result)
        if "migrations" in result:
            self._say_bots_wait(still=True)
        return result

    def _say_bots_wait(self, still: bool = False) -> None:
        """Say which migrations the bots wait for, if any.

        Before anything starts, because the bot runner holds its bots from
        its start until no installed stacklet has a migration left to run
        (stacklets/core/bot-runner/main.py), and so do the memory curator
        and the agent's commands. `still` is after the run: one that failed
        keeps them waiting until a `stack up` gets it through. With nothing
        pending it says nothing.
        """
        keys = self.stack.unmigrated()
        if not keys:
            return
        if still:
            self.stack.output.warn(
                f"Bots keep waiting until these migrations have run: {', '.join(keys)}")
        else:
            self.stack.output.step(f"Bots wait until these migrations have run: {', '.join(keys)}")

    def _start(self, stacklet_id: str, answers: dict | None = None) -> dict:
        """Everything `up` does short of the migrations: framework, Docker,
        health, `on_install_success` on a first run, `on_start_ready`.

        `healthy` in the result says whether every health check passed,
        which decides whether the migrations may run.
        """
        result = self.stack.up(stacklet_id, answers)
        if "error" in result or "cancelled" in result:
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

            # On every up, not only the first: a release that moves a
            # service to a new image tag leaves that image missing here,
            # and `compose up` would download it with its output captured,
            # a silent wait of minutes behind the start spinner.
            with self.stack.output.spinner("Pulling images"):
                docker.compose_pull(compose_file, env=compose_env)
            if manifest.get("build"):
                with self.stack.output.spinner("Building service"):
                    docker.compose_build(compose_file, env=compose_env)

            with self.stack.output.spinner("Starting containers"):
                code, err = docker.compose_up(compose_file, env=compose_env)
            if code != 0:
                return {"error": "Failed to start services", "output": err}

        self._reload_proxy()

        # Health checks run first, so hooks can assume the service is
        # healthy. Also without containers: on_start started the services.
        healthy = True
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
                # "auth" is up but gating credentials, which is what
                # `Stack.is_healthy` counts as healthy too.
                healthy = healthy and status in ("ready", "auth")
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
            # Rendered again, not the env from the start of this run:
            # on_install_success may have written secrets since (tokens,
            # seeds) that templates reference. The containers keep the
            # env they were started with; this hook reads the current one.
            ready_ctx = build_hook_ctx(
                stacklet_id, env=self.stack.env(stacklet_id),
                step_fn=self.stack.output.step, stack=self.stack,
            )
            ready_resolver.run("on_start_ready", ready_ctx)

        result["name"] = stacklet.get("name", stacklet_id)
        result["port"] = stacklet.get("port")
        result["description"] = stacklet.get("description", "")
        result["manifest"] = manifest
        result["healthy"] = healthy
        return result

    def _migrate(self, stacklet_id: str, result: dict) -> dict:
        """Run the stacklet's pending migrations once it is up and healthy.

        A failure turns `result` into an error, with the containers left
        running: new code on unmigrated data is not a success, and the
        exit code is what scripts and agents read. With nothing pending
        nothing is printed and `result` comes back as it was.
        """
        if not result.get("healthy", True):
            migrations = self.stack.migrations(stacklet_id)
            if migrations and migrations.status().pending:
                self.stack.output.warn(
                    f"Migrations of {stacklet_id} wait until its health checks pass")
            return result

        outcome = self.stack.migrate(stacklet_id)
        if not outcome:
            return result
        result["migrations"] = outcome
        if "error" in outcome:
            result.update(ok=False, error=outcome["error"])
        elif outcome.get("failed"):
            key = next(iter(outcome["failed"]))
            result.update(ok=False, error=f"Migration {key} failed",
                          problems=[f"Fix the cause, then: stack up {stacklet_id}"])
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
            # Always re-render .env first, exactly as up does. compose reads
            # the whole file before it acts, so a stale .env that is missing a
            # newly-added variable (a merge that adds a mount, for example)
            # makes every command invalid, stop included, and the stacklet
            # becomes unstoppable until env is regenerated by hand. Refreshing
            # only when .env is absent left that gap.
            try:
                self.stack.refresh_env(stacklet_id)
            except ValueError:
                pass
            code, output = docker.compose_stop(compose_file)
            self._reload_proxy()
            result = {"stacklet": stacklet_id, "action": "down",
                      "success": code == 0, "output": output}
            if code != 0:
                # Surface the compose error instead of the caller's generic
                # "unknown error", which hid the real cause.
                result["error"] = output or "docker compose stop failed"
            return result
        self._reload_proxy()
        return {"stacklet": stacklet_id, "action": "down", "success": True}

    def _down_all(self) -> dict:
        """Stop every running stacklet in reverse dependency order.

        Running is what `Stack.running_ids` says: containers, or up without
        them. Stopped/available stacklets are untouched. Dependents shut
        down before their deps so services making outbound calls don't
        error on a disappearing backend.
        """
        return self._down_ordered(self.stack.running_ids())

    def down_many(self, stacklet_ids: list[str]) -> dict:
        """Stop the named stacklets, dependents first.

        Every id is checked before anything stops, so a typo in one
        leaves the rest running rather than half the list stopped.
        """
        unknown = self._unknown(stacklet_ids)
        if unknown:
            return {"ok": False, "error": f"Unknown stacklet: {', '.join(unknown)}",
                    "stopped": [], "errors": []}
        return self._down_ordered(set(stacklet_ids))

    def _down_ordered(self, include: set[str]) -> dict:
        order = _reverse_dependency_order(self.stack.discover(), include)

        stopped: list[str] = []
        errors: list[dict] = []
        for sid in order:
            result = self.down(sid)
            if result.get("success", result.get("ok")):
                stopped.append(sid)
            else:
                errors.append({"stacklet": sid, "result": result})

        return {"ok": not errors, "stopped": stopped, "errors": errors}

    def _up_all(self) -> dict:
        """Bring up every installed stacklet in forward dependency order.

        Symmetric peer to `_down_all`. Operates on *installed* stacklets
        (those past on_install_success); a stacklet that has never been
        set up isn't auto-installed here — `stack up all` is a
        refresh-everything verb, not a first-time installer.

        Deps come up before their dependents so the framework's health
        probes have something to talk to.
        """
        installed = {
            s["id"] for s in self.stack.discover()
            if self.stack.is_installed(s["id"])
        }
        return self._up_ordered(installed)

    def up_many(self, stacklet_ids: list[str], answers: dict | None = None) -> dict:
        """Bring up the named stacklets, dependencies first.

        Checked the same way as `down_many`: an unknown id starts nothing.
        """
        unknown = self._unknown(stacklet_ids)
        if unknown:
            return {"ok": False, "error": f"Unknown stacklet: {', '.join(unknown)}",
                    "started": [], "errors": []}
        return self._up_ordered(set(stacklet_ids), answers)

    def _up_ordered(self, include: set[str], answers: dict | None = None) -> dict:
        """Start every stacklet first, then run their migrations, both in
        dependency order. A migration may need another stacklet's service
        (a bot-runner command needs core), and one that was started later
        in the same command is then already up. Whatever acts on the data
        waits for them meanwhile (`_say_bots_wait`)."""
        order = _dependency_order(self.stack.discover(), include)
        self._say_bots_wait()

        started: list[str] = []
        errors: list[dict] = []
        results: dict[str, dict] = {}
        for sid in order:
            result = self._start(sid, answers)
            if "error" in result or "cancelled" in result:
                errors.append({"stacklet": sid, "result": result})
            else:
                started.append(sid)
                results[sid] = result

        migrated = False
        for sid in started:
            result = self._migrate(sid, results[sid])
            migrated = migrated or "migrations" in result
            if "error" in result:
                errors.append({"stacklet": sid, "result": result})
        if migrated:
            self._say_bots_wait(still=True)

        return {"ok": not errors, "started": started, "errors": errors}

    def _unknown(self, stacklet_ids: list[str]) -> list[str]:
        known = {s["id"] for s in self.stack.discover()}
        return [sid for sid in stacklet_ids if sid not in known]

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

        result = self.stack.destroy(stacklet_id)
        self._reload_proxy()
        return result

    def _reload_proxy(self) -> None:
        """Have the running proxy load the Caddyfile the framework just wrote.

        Called after every change to which stacklets are up. Port mode has
        no proxy, and a proxy that is not up reads the file when it starts.
        A rejected reload leaves Caddy serving its previous config, so the
        step that asked for it still succeeded; the admin is told the
        routes did not change.
        """
        if not self.stack._cfg("core", "domain"):
            return
        if caddy.STACKLET not in self.stack.serving_ids():
            return
        code, err = docker.exec_in(caddy.CONTAINER, *caddy.RELOAD_COMMAND)
        if code != 0:
            reason = err.strip().splitlines()[-1] if err.strip() else f"exit {code}"
            self.stack.output.warn(f"Caddy kept its previous routes: {reason}")


# ── Stacklet id arguments ─────────────────────────────────────────────────

def _stacklet_ids(values: list[str]) -> list[str]:
    """Flatten `a b`, `a,b` and any mix of the two into one id list.

    Order is kept and repeats are dropped, so `down a,b a` stops a once.
    """
    ids: list[str] = []
    for value in values:
        for sid in value.split(","):
            sid = sid.strip()
            if sid and sid not in ids:
                ids.append(sid)
    return ids


# ── Topological helpers ───────────────────────────────────────────────────

def _dependency_order(stacklets: list[dict], include: set[str]) -> list[str]:
    """Return the subset of stacklet IDs in `include`, ordered so that
    dependencies come before their dependents.

    Mirror of `_reverse_dependency_order` for `up all` — Kahn's algorithm
    on the forward graph. Stacklets with `requires` on entries NOT in
    `include` have those edges dropped; we only order among the ones
    we're actually bringing up.

    An OIDC client also waits for the provider when both are in
    `include`, as if it listed it in `requires`. A client reads its
    credentials when its env is rendered, at the start of its own `up`,
    and the provider stores them during its own. Brought up first, the
    client would start without single sign-on until its next `up`. It
    is not a real dependency: a client brought up alone does not pull
    the provider in, and keeps its own login.
    """
    by_id = {s["id"]: s for s in stacklets if s["id"] in include}
    if not by_id:
        return []

    # The same provider Stack.oidc_provider() picks: the first declared.
    provider = next((s["id"] for s in stacklets
                     if "oidc_provider" in s.get("manifest", {})), None)

    remaining_deps = {
        sid: {d for d in s.get("manifest", {}).get("requires", []) if d in by_id}
             | ({provider} if provider in by_id and provider != sid
                and "oidc" in s.get("manifest", {}) else set())
        for sid, s in by_id.items()
    }

    ready = sorted([sid for sid, d in remaining_deps.items() if not d])
    order: list[str] = []
    while ready:
        sid = ready.pop(0)
        order.append(sid)
        for other, deps in remaining_deps.items():
            if sid in deps:
                deps.discard(sid)
                if not deps and other not in order and other not in ready:
                    ready.append(other)
        ready.sort()

    # Cycle fallback: any leftovers are appended so we still try to bring
    # them up. A cycle is a manifest bug; we surface it by not silently
    # dropping work.
    for sid in by_id:
        if sid not in order:
            order.append(sid)
    return order


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
    """Walk up from CWD to find the repo root (has stacklets/ dir).

    Outside any checkout, the checkout this code runs from, so a global
    command started from another directory still finds its stacklets.
    """
    here = Path.cwd()
    for candidate in [here, *here.parents]:
        if (candidate / "stacklets").is_dir():
            return candidate
    own = Path(__file__).resolve().parents[2]
    return own if (own / "stacklets").is_dir() else None


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


def _scheduler_findings(stck) -> list:
    """The scheduler's heartbeat and the runs it missed in the last day, as doctor findings."""
    from datetime import datetime, timedelta, timezone

    from .schedule import Schedule

    schedule = Schedule(stck.data / "core" / "schedule")
    now = datetime.now(timezone.utc)
    missed = [r["name"] for r in schedule.runs(limit=500) if r.get("status") == "missed"
              and datetime.fromisoformat(r["due"]) > now - timedelta(days=1)]
    return doctor.check_scheduler(schedule.heartbeat(), now,
                                  core_running=bool(docker.containers_for("core")), missed=missed)


def _pmset() -> str:
    """`pmset -g`, the Mac's power settings; empty where there is none."""
    try:
        return subprocess.run(["pmset", "-g"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""

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

    stck = Stack(
        root=repo_root,
        data=configured_data_dir(cfg),
        instance_dir=instance,
        output=TerminalOutput(),
    )
    if adopted := stck.adopt_product_name():
        stck.output.step(f"stack.toml: set [core] name = \"{adopted}\", "
                         f"the name of the data dir {stck.data}")
    return stck


# ── Output formatting ────────────────────────────────────────────────────

def print_error(result: dict) -> None:
    """Print an error with stack colors."""
    error = result.get("error", "unknown error")
    print(f"\n  {RED}✗{RESET}  {error}\n", file=sys.stderr)
    # The tool's own output says why (compose names the port, the mount,
    # the image). Its last lines carry the error; the rest is progress.
    output = (result.get("output") or "").strip()
    if output and output != str(error).strip():
        for line in [ln for ln in output.splitlines() if ln.strip()][-5:]:
            print(f"      {DIM}{line.strip()}{RESET}", file=sys.stderr)
        print(file=sys.stderr)
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

    # Warnings are streamed by the output adapter as they happen, which
    # is before the containers start and also covers `up all`, where no
    # banner is printed. The result still carries them for API callers.

    # Next steps — rendered from manifest hints with credentials
    hints = result.get("hints", [])
    if hints:
        print(f"  {BOLD}Next steps{RESET}")
        for hint in hints:
            print(f"  {DIM}\u2022{RESET} {hint}")
        print()

    print("  " + "\u2500" * w)
    print()


def running_version_of(stck) -> str:
    """The version to show a human: the tag, plus how far past it we are.

    Every surface that prints a version goes through here, so `version`,
    `doctor`, `update` and `list` cannot disagree about what is running.
    """
    from .updater import Checkout, running_version

    checkout = Checkout(stck.root)
    return running_version(checkout.describe() if checkout.is_git() else "", VERSION)


def _short_path(path) -> str:
    """Home-relative path for display."""
    home = str(Path.home())
    text = str(path)
    return text.replace(home, "~", 1) if text.startswith(home) else text


def _split_by_origin(stacklets: list[dict]) -> tuple[list[dict], list[dict]]:
    """Stacklets the release ships, then the ones loaded from elsewhere.

    Two lists rather than one, because where a stacklet came from decides
    who supports it. Mixing them into a single column asks the reader to
    remember which of eleven names is not ours.
    """
    shipped = [s for s in stacklets if s.get("source") != "extension"]
    extensions = [s for s in stacklets if s.get("source") == "extension"]
    return shipped, extensions


def print_extensions_section(extensions: list[dict], dirs: list) -> None:
    """The extensions half of `list` and `status`. Silent when empty."""
    from .prompt import status_list
    if not extensions:
        return
    where = ", ".join(_short_path(d) for d in dirs)
    print(f"  {BOLD}Extensions{RESET}  {DIM}{where}{RESET}" if where
          else f"  {BOLD}Extensions{RESET}")
    status_list(extensions)


def print_list(result: dict, stck=None) -> None:
    """Stacklet list with status colors."""
    from .prompt import DIM, RESET, status_list
    stacklets = result.get("stacklets", [])
    if not stacklets:
        print("\n  No stacklets found.\n")
        return
    shipped, extensions = _split_by_origin(stacklets)
    status_list(shipped)
    print_extensions_section(extensions, result.get("extension_dirs", []))
    version_info = f"  {DIM}{running_version_of(stck)}{RESET}" if stck else ""
    print(f"  {result.get('online', 0)}/{result.get('total', 0)} online{version_info}")
    stale = result.get("stale", [])
    if stale:
        print_restart_call_to_action(stale)
        print()
    else:
        print(f"  {DIM}stack up <id> to start a stacklet{RESET}\n")


def print_status(result: dict) -> None:
    """Rich status overview: system info + stacklet list."""
    from .prompt import status_list

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
    # The version already carries the commit when the checkout is past a
    # tag, so repeating it would read as two different answers.
    suffix = "" if commit and commit in version else f" ({commit})"
    print(f"  {ORANGE}{BOLD}{name}{RESET} {DIM}{version}{suffix}{RESET}")
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
        shipped, extensions = _split_by_origin(stacklets)
        print(f"  {BOLD}Stacklets{RESET}")
        status_list(shipped)
        print_extensions_section(extensions, result.get("extension_dirs", []))
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
    """`stack up`: start what the command line names and say how it went.

    With `--events` the run is also reported on stdout as it happens, one
    JSON object per line, ending with one `result` (ADR-020); everything
    people read goes to stderr then. The exit code is the same either way.
    """
    up = _up_reporting_events if getattr(args, "events", False) else _up
    _, code = up(stck, args)
    if code:
        sys.exit(code)


def _up_reporting_events(stck, args) -> tuple[dict, int]:
    """`_up`, with each step as an event on stdout and the result last.

    A crash still writes a result, before its traceback, so a caller
    reading the stream learns the run is over.
    """
    from .output import events_on_stdout
    terminal = stck.output
    with events_on_stdout(terminal) as events:
        stck.output = events
        try:
            result, code = _up(stck, args)
        except Exception as e:
            events.result({"error": str(e) or type(e).__name__})
            raise
        finally:
            stck.output = terminal
        events.result(result)
    return result, code


def _up(stck, args) -> tuple[dict, int]:
    """Bring up what `args` names and tell the terminal: (result, exit code)."""
    from .prompt import TEAL
    cli = CLI(stck)

    # --no-voice is the flag form of STACK_AI_NO_VOICE=1. The ai stacklet's
    # on_start reads this env var to drop the Piper TTS container (and
    # on_install skips the Whisper build). Other stacklets don't read it, so
    # setting it here for `up all` is harmless.
    if getattr(args, "no_voice", False):
        os.environ["STACK_AI_NO_VOICE"] = "1"

    answers, problem = given_answers(args)
    if problem:
        print_error({"error": problem})
        return {"error": problem}, 1

    ids = _many_ids(args)
    if ids:
        result = cli.up_many(ids, answers)
        if not result.get("ok"):
            print_error({"error": result.get("error") or "Some stacklets failed to start",
                         "problems": [e["stacklet"] for e in result.get("errors", [])]})
            return result, 1
        for sid in result["started"]:
            print(f"  {GREEN}✓{RESET} {sid}: started")
        if "core" not in ids:
            _refresh_core(stck, ids[0])
        return result, 0

    if args.stacklet == "all":
        print(f"\n  Bringing up {TEAL}all installed stacklets{RESET}...\n",
              file=sys.stderr)
        result = cli.up("all")
        started = result.get("started", [])
        if not result.get("ok"):
            print_error({"error": "Some stacklets failed to start",
                         "problems": [e["stacklet"] for e in result.get("errors", [])]})
            return result, 1
        if not started:
            print(f"  {DIM}Nothing is installed yet — run `stack install` first.{RESET}")
            return result, 0
        for sid in started:
            print(f"  {GREEN}✓{RESET} {sid}: started")
        _refresh_core(stck, "all")
        return result, 0

    stacklet = stck._find_stacklet(args.stacklet)
    name = stacklet.get("name", args.stacklet) if stacklet else args.stacklet
    print(f"\n  Bringing up {TEAL}{name}{RESET}...\n", file=sys.stderr)
    result = cli.up(args.stacklet, answers)
    if "cancelled" in result:
        print(f"  {DIM}{result['cancelled']}{RESET}", file=sys.stderr)
        return result, 1
    if result.get("ok"):
        print_up_success(result, stck)
        _notify_up(stck, result)
        _refresh_core(stck, args.stacklet)
        return result, 0
    print_error(result)
    # A failed migration leaves the stacklet running, so it did not
    # fail to start. A setup question left open is not a failure either:
    # nothing started, and the answer is what is missing.
    if args.stacklet != "messages" and "migrations" not in result and "form" not in result:
        _notify(stck, f"{name} failed to start.")
    return result, 1


def _many_ids(args):
    """The ids named on the command line, or None for one id or `all`.

    One id keeps the single-stacklet path and its output. `all` cannot
    be combined with other ids, since it already names every one.
    """
    ids = _stacklet_ids(args.stacklet)
    if len(ids) == 1:
        args.stacklet = ids[0]
        return None
    if "all" in ids:
        print_error({"error": "'all' already names every stacklet; pass it alone"})
        sys.exit(1)
    return ids


def handle_down(stck, args):
    cli = CLI(stck)

    ids = _many_ids(args)
    if ids:
        result = cli.down_many(ids)
        if not result.get("ok"):
            print_error({"error": result.get("error") or "Some stacklets failed to stop",
                         "problems": [e["stacklet"] for e in result.get("errors", [])]})
            sys.exit(1)
        for sid in result["stopped"]:
            print(f"  {GREEN}✓{RESET} {sid}: stopped")
        if "core" not in ids:
            _refresh_core(stck, ids[0])
        return

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
    ids = _many_ids(args)
    if not ids:
        _destroy_one(stck, args)
        return

    unknown = [sid for sid in ids if not stck._find_stacklet(sid)]
    if unknown:
        print_error({"error": f"Unknown stacklet: {', '.join(unknown)}"})
        sys.exit(1)
    # Dependents first, as down does. Each one keeps its own confirmation.
    for sid in _reverse_dependency_order(stck.discover(), set(ids)):
        args.stacklet = sid
        _destroy_one(stck, args)


def _destroy_one(stck, args):
    stacklet = stck._find_stacklet(args.stacklet)
    if not stacklet:
        print_error({"error": f"Stacklet '{args.stacklet}' not found"})
        sys.exit(1)

    name = stacklet.get("name", args.stacklet)
    data_path = stck.data / args.stacklet

    # Not installed and no containers: only data a failed setup left behind.
    # A first `up` that failed after starting containers leaves those too,
    # holding their names and ports, so that case takes the full path.
    if not stck.is_installed(args.stacklet) and args.stacklet not in docker.all_project_ids():
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
        print("  This will permanently remove:")
        print("    · Containers and volumes")
        if data_path.exists():
            print("    · All stored data")
        print("    · Secrets and config\n")
        print("  You may want to back up your data first.\n")
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


def handle_doctor(stck, args):
    """Diagnose the instance: what is wrong, and what to type to fix it.

    `status` answers "is it up?". When it isn't, this answers "why?" —
    the checks live in doctor.py as pure rules; everything here is the
    I/O they need.
    """
    preferred = stck._cfg("core", "runtime", "orbstack")
    docker.init_runtime(preferred)

    discovered = stck.discover()
    stacklets = sorted(s["id"] for s in discovered)
    manifests = {s["id"]: s["manifest"] for s in discovered}

    def missing_secrets(stacklet_id):
        """Declared credentials the secret store cannot produce.

        `required_secrets` names them the way a stacklet's own hooks do,
        without the namespace prefix, so the manifest reads the same as
        the `ctx.secret("API_TOKEN")` call that consumes it.
        """
        required = manifests.get(stacklet_id, {}).get("required_secrets", [])
        return [name for name in required if not stck.secret(stacklet_id, name)]

    # What compose would give each service now, keyed by container name.
    # Comparing a container against this, rather than against the
    # stacklet's rendered env, is what keeps a deliberate compose
    # override from reading as drift forever.
    expected = {}
    for s in discovered:
        compose_file = docker.find_compose_file(Path(s["path"]))
        if compose_file:
            expected.update(docker.compose_service_env(compose_file))

    def migrations(stacklet_id):
        """Pending, failed and newer-than-checkout migrations, as data."""
        try:
            status = stck.migrations(stacklet_id).status()
        except MigrationError as e:
            return [], {f"{stacklet_id}/migrations": str(e)}, []
        return ([(m.key, m.summary) for m in status.pending], status.failed, status.newer)

    findings = doctor.diagnose(
        stacklets,
        lambda container: expected.get(container, {}),
        docker.containers_for,
        docker.container_env,
        missing_secrets=missing_secrets,
        stale=stck.list().get("stale", []),
        migrations=migrations,
    )

    findings += doctor.check_oidc(
        [s["id"] for s in discovered if "oidc_provider" in s["manifest"]],
        {c["stacklet"]: bool(c["client_id"] and c["client_secret"])
         for c in stck.oidc_clients()},
        running={sid for sid in stacklets if docker.containers_for(sid)},
    )

    language = doctor.check_language(stck._cfg("core", "language", ""),
                                     stck._cfg("ai", "language", ""))
    if language:
        findings.append(language)

    findings += _scheduler_findings(stck)
    sleep = doctor.check_sleep(_pmset())
    if sleep:
        findings.append(sleep)

    # What this instance is running, and whether a release has passed it.
    # No fetch: doctor is run often, and often when something is
    # unreachable, so it answers from the tags this clone already has.
    # The same rule as `stack update`: a pre-release only to a checkout
    # already on one.
    from .updater import Checkout, offered_tag

    checkout = Checkout(stck.root)
    position = checkout.describe() if checkout.is_git() else ""
    latest = (offered_tag(checkout.tags(), position) or "") if position else ""
    release = doctor.check_release(
        position, latest, up_to_date=bool(latest) and checkout.contains(latest))
    if release:
        findings.insert(0, release)

    if args.json:
        json.dump({
            "version": running_version_of(stck),
            "summary": doctor.summarise(findings),
            "findings": [asdict(f) for f in findings],
        }, sys.stdout, indent=2)
        print()
        if any(f.is_error for f in findings):
            sys.exit(1)
        return

    print()
    if position:
        print(f"  {ORANGE}{BOLD}{stck.product_name()}{RESET} "
              f"{DIM}{running_version_of(stck)}{RESET}\n")
    if not findings:
        print(f"  {GREEN}✓{RESET}  {doctor.summarise(findings)}\n")
        return

    for finding in findings:
        mark = f"{RED}✗{RESET}" if finding.is_error else f"{ORANGE}⚠{RESET}"
        print(f"  {mark}  {BOLD}{finding.title}{RESET}")
        print(f"     {DIM}{finding.detail}{RESET}")
        # Labelled and copy-pasteable. The `./` goes on here rather than
        # in the finding, so the data stays a bare command for anything
        # reading the JSON, and a fix that is not a stack command (the
        # stacktests path) is left exactly as written.
        command = (f"./{finding.fix}" if finding.fix.startswith("stack ")
                   else finding.fix)
        print(f"     {DIM}to fix:{RESET} {TEAL}{command}{RESET}\n")

    print(f"  {doctor.summarise(findings)}\n")
    # Exit non-zero on errors so an agent or script can gate on it.
    if any(f.is_error for f in findings):
        sys.exit(1)


def handle_list(stck, args):
    print_list(stck.list(), stck)


# A key named like a credential. Matched on the name, not the value, so a
# secret added to stack.toml or users.toml later is hidden without a change here.
_SECRET_KEY = re.compile(r"(key|password|passwd|secret|token)$", re.IGNORECASE)
_HIDDEN = "(hidden)"


def _redact(value):
    """`value` with every credential replaced by a marker. An empty one stays
    empty, so the reader can still tell set from unset."""
    if isinstance(value, dict):
        return {k: (_HIDDEN if _SECRET_KEY.search(k) and v not in ("", None) else _redact(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def _read_toml(path: Path) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def config_report(instance_dir: Path) -> dict:
    """stack.toml and users.toml as written, without their credentials.

    The raw files, not resolved values: a key the admin left out is absent
    here and its default applies. Both paths are included so a reader can
    open the file to change it.
    """
    stack_toml = instance_dir / "stack.toml"
    users_toml = instance_dir / "users.toml"
    return {
        "stack_toml": str(stack_toml),
        "users_toml": str(users_toml),
        "config": _redact(_read_toml(stack_toml)),
        "users": _redact(_read_toml(users_toml).get("users", [])),
    }


def handle_config(stck, args):
    """Config subcommands. Bare 'stack config' prints stack.toml."""
    action = getattr(args, "config_action", None)
    if action == "admin":
        _config_admin(stck)
        return
    if getattr(args, "json", False):
        json.dump(config_report(stck.instance_dir), sys.stdout, indent=2, default=str)
        print()
        return
    path = stck.instance_dir / "stack.toml"
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
    print(f"  {'Username:':<12}{TEAL}{TECH_ADMIN_USERNAME}{RESET}")
    print(f"  {'Email:':<12}{TEAL}{TECH_ADMIN_EMAIL}{RESET}")
    print(f"  {'Password:':<12}{TEAL}{password}{RESET}\n")


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
        print("  famstack uses OrbStack as its container runtime.")
        print("  It's fast, lightweight, and built for macOS.\n")
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
        print("  famstack is tested with OrbStack only.")
        print("  Docker Desktop can cause high CPU usage and a sluggish system.\n")
        print(f"  Install OrbStack from {TEAL}https://orbstack.dev{RESET}")
        print(f"  or run: {TEAL}brew install orbstack{RESET}\n")
        print("  famstack will use it automatically once installed.\n")
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

    output = stdout or stderr

    if args.grep:
        output = "\n".join(
            line for line in output.splitlines()
            if args.grep in line
        )

    if args.json:
        print(json.dumps({"ok": True, "lines": output.splitlines() if output else [], "count": len(output.splitlines()) if output else 0}))
    else:
        print(output)


# ── errors, host: what an admin checks first ──────────────────────────────

_DURATION = re.compile(r"^\d+[smh]$")


def handle_errors(stck, args):
    """Error lines from every stacklet's containers over a recent window.

    One question across the whole stack, so a failure in a sidecar nobody
    watches is seen without opening each stacklet's log. What counts as an
    error lives in logscan.py; this is the I/O around it.
    """
    since = args.since
    if not _DURATION.match(since):
        print_error({"error": f"--since takes a duration such as 30m or 24h, not '{since}'"})
        sys.exit(1)

    known = {s["id"] for s in stck.discover()}
    containers = [c for c in docker.stack_containers() if c["stacklet"] in known]
    with ThreadPoolExecutor(max_workers=8) as pool:
        scans = list(pool.map(
            lambda c: logscan.scan(docker.container_logs(c["name"], since)), containers))

    found = [{"stacklet": c["stacklet"], "container": c["name"], **scan}
             for c, scan in zip(containers, scans) if scan["count"]]
    found.sort(key=lambda e: e["last_at"] or "", reverse=True)
    report = {"since": since, "scanned": len(containers), "containers": found}

    if args.json:
        json.dump(report, sys.stdout, indent=2)
        print()
        return

    print()
    if not found:
        print(f"  {GREEN}✓{RESET}  No errors in the last {since} "
              f"across {len(containers)} containers.\n")
        return
    for entry in found:
        noun = "error" if entry["count"] == 1 else "errors"
        print(f"  {RED}✗{RESET}  {BOLD}{entry['container']}{RESET}  "
              f"{entry['count']} {noun} in the last {since}")
        for line in entry["lines"][-3:]:
            print(f"     {DIM}{line['text'][:160]}{RESET}")
        print(f"     {DIM}more:{RESET} {TEAL}./stack logs {entry['stacklet']}{RESET}\n")


APP_VERBS = {"install": "build the menu bar app from this checkout and put it in Applications"}


def handle_app(stck, args):
    """The menu bar app. `install` builds it here and installs it; running
    it again after `stack update` updates it. Without a verb, the verbs."""
    if not args.verb:
        report = {"commands": APP_VERBS}
        if args.json:
            json.dump(report, sys.stdout, indent=2)
            print()
            return
        print()
        for verb, what in APP_VERBS.items():
            print(f"  {TEAL}./stack app {verb}{RESET}  {DIM}{what}{RESET}")
        print()
        return
    if args.verb not in APP_VERBS:
        print_error({"error": f"Unknown verb '{args.verb}'. Use: {', '.join(APP_VERBS)}"})
        sys.exit(1)

    if not args.json:
        print("\n  Building the menu bar app (about a minute)...")
    result = menubar_app.install(stck.root, open_after=not args.json)
    if "error" in result:
        print_error(result)
        sys.exit(1)
    if args.json:
        json.dump(result, sys.stdout, indent=2)
        print()
        return
    print(f"  {GREEN}\u2713{RESET}  Installed {TEAL}{result['installed']}{RESET}")
    print(f"  {DIM}It sits in the menu bar. Setup \u2192 Connection picks this Mac "
          f"or another one over SSH.{RESET}\n")


def _uptime_seconds() -> int | None:
    """Seconds since boot, from `sysctl kern.boottime` (`{ sec = 1727..., ...}`)."""
    try:
        r = subprocess.run(["sysctl", "-n", "kern.boottime"],
                           capture_output=True, text=True, timeout=5)
        match = re.search(r"sec = (\d+)", r.stdout)
        return int(time.time()) - int(match.group(1)) if match else None
    except Exception:
        return None


def handle_host(stck, args):
    """The machine: memory, disk, uptime, and what each stacklet uses.

    Stacklet figures come from `docker stats`, so a stacklet that runs on
    the host itself (the AI server) has none: its processes are not
    containers.
    """
    known = {s["id"] for s in stck.discover()}
    stats = docker.container_stats()
    per_stacklet: dict[str, dict] = {}
    for c in docker.stack_containers():
        if c["stacklet"] not in known or c["name"] not in stats:
            continue
        entry = per_stacklet.setdefault(
            c["stacklet"], {"id": c["stacklet"], "memory_bytes": 0, "cpu_pct": 0.0, "containers": 0})
        entry["memory_bytes"] += stats[c["name"]]["memory_bytes"]
        entry["cpu_pct"] = round(entry["cpu_pct"] + stats[c["name"]]["cpu_pct"], 1)
        entry["containers"] += 1

    report = {
        **stck._host_stats(),
        "uptime_seconds": _uptime_seconds(),
        "stacklets": sorted(per_stacklet.values(), key=lambda e: e["memory_bytes"], reverse=True),
    }

    if args.json:
        json.dump(report, sys.stdout, indent=2)
        print()
        return

    print(f"\n  {BOLD}Host{RESET}")
    if "memory_total_gb" in report:
        print(f"    Memory   {report.get('memory_used_gb', '?')} of {report['memory_total_gb']} GB used")
    if "disk_total_gb" in report:
        print(f"    Disk     {report['disk_free_gb']} GB free of {report['disk_total_gb']} GB "
              f"({report['disk_used_pct']}% used)")
    if report["uptime_seconds"] is not None:
        days, rest = divmod(report["uptime_seconds"], 86400)
        print(f"    Up       {days} days, {rest // 3600} hours")
    if report["stacklets"]:
        print(f"\n  {BOLD}Stacklets by memory{RESET}")
        for e in report["stacklets"]:
            print(f"    {e['id']:<12}{e['memory_bytes'] / 1024 ** 3:>6.2f} GB"
                  f"   {e['cpu_pct']:>5.1f}% CPU   {DIM}{e['containers']} containers{RESET}")
    print()


def handle_restart(stck, args):
    cli = CLI(stck)

    # No argument: restart exactly what is running code the checkout has
    # moved past. `all` stays the sledgehammer for when you want the lot.
    if not args.stacklet:
        _restart_stale(stck, cli, yes=getattr(args, "yes", False))
        return

    ids = _many_ids(args)
    if ids:
        # Stop the lot before starting any, as `all` does, so nothing comes
        # back up against a dependency that is about to go down.
        down_result = cli.down_many(ids)
        if not down_result.get("ok"):
            print_error({"error": down_result.get("error") or "Some stacklets failed to stop",
                         "problems": [e["stacklet"] for e in down_result.get("errors", [])]})
            sys.exit(1)
        up_result = cli.up_many(ids)
        if not up_result.get("ok"):
            print_error({"error": "Some stacklets failed to start",
                         "problems": [e["stacklet"] for e in up_result.get("errors", [])]})
            sys.exit(1)
        for sid in up_result["started"]:
            print(f"  {GREEN}✓{RESET} {sid}: restarted")
        return

    if args.stacklet == "all":
        # Whole-stack restart: stop everything running, then bring up
        # every installed stacklet. The two phases use their own
        # dependency orderings (reverse for down, forward for up) so
        # services dance off and back on without talking to dead deps.
        from .prompt import TEAL
        print(f"\n  Restarting {TEAL}the whole stack{RESET}...\n",
              file=sys.stderr)
        down_result = cli.down("all")
        if not down_result.get("ok"):
            print_error({"error": "Some stacklets failed to stop",
                         "problems": [e["stacklet"] for e in down_result.get("errors", [])]})
            sys.exit(1)
        up_result = cli.up("all")
        if not up_result.get("ok"):
            print_error({"error": "Some stacklets failed to start",
                         "problems": [e["stacklet"] for e in up_result.get("errors", [])]})
            sys.exit(1)
        for sid in up_result.get("started", []):
            print(f"  {GREEN}✓{RESET} {sid}: restarted")
        return

    cli.down(args.stacklet)
    result = cli.up(args.stacklet)
    if "error" in result:
        print_error(result); sys.exit(1)
    else:
        print_up_success(result, stck)


def _restart_stale(stck, cli, yes=False):
    """Restart the stacklets whose containers predate the code on disk.

    Only the ones it can prove: a container stamped with the commit it
    was started from. A stacklet whose containers carry no stamp is left
    running and named, because silently skipping it is how someone
    concludes the update was applied when it was not.
    """
    from .prompt import TEAL, confirm

    listing = stck.list()
    stale = listing.get("stale", [])
    running = {s["id"] for s in listing["stacklets"] if s.get("online") or s.get("starting")}
    unknown = sorted(running - set(stale) - set(stck.started_commits()))

    if not stale:
        print(f"\n  {GREEN}\u2713{RESET}  Everything running is on the current code.")
        if unknown:
            print(f"  {DIM}Cannot tell for: {', '.join(unknown)}. "
                  f"Run stack up <id> if you changed its config.{RESET}")
        print()
        return

    print(f"\n  {BOLD}Restarting{RESET} {TEAL}{', '.join(stale)}{RESET} "
          f"{DIM}(running code the checkout has moved past){RESET}")
    if unknown:
        print(f"  {DIM}Not touched, no commit stamp to compare: "
              f"{', '.join(unknown)}{RESET}")
    print()

    if not yes and sys.stdin.isatty():
        if not confirm(f"Restart {len(stale)} stacklet(s)?"):
            print(f"  {DIM}Aborted{RESET}\n")
            return

    # Stopped together and started together, as `restart a b` does, so
    # the migrations run after every stale stacklet is back, in
    # dependency order.
    cli.down_many(stale)
    result = cli.up_many(stale)
    failed = {e["stacklet"]: e["result"].get("error", "") for e in result["errors"]}
    for sid in stale:
        if sid in failed:
            print(f"  {RED}\u2717{RESET} {sid}: {failed[sid]}")
        else:
            print(f"  {GREEN}\u2713{RESET} {sid}: restarted")

    print()
    if failed:
        sys.exit(1)


def handle_setup(stck, args):
    stacklet = stck._find_stacklet(args.stacklet)
    if not stacklet:
        print_error({"error": f"'{args.stacklet}' not found"}); sys.exit(1)

    if not stck.is_running(args.stacklet):
        print_error({"error": f"'{args.stacklet}' is not running. Run './stack up {args.stacklet}' first."})
        sys.exit(1)

    print(f"\n  Re-running setup for {args.stacklet}...\n", file=sys.stderr)
    ok = stck.run_on_install_success(args.stacklet, step_fn=stck.output.step)
    if not ok:
        print_error({"error": f"Setup hook failed for '{args.stacklet}'"})
        sys.exit(1)
    print(f"\n  {GREEN}✓{RESET}  Setup complete", file=sys.stderr)


def given_answers(args) -> tuple[dict, str]:
    """The setup answers given on the command line, and what is wrong with
    them ("" when nothing is).

    `--answers <file>` (`-` reads stdin) is a JSON object of field name to
    answer; each `--answer key=value` is one more, and wins over the file.
    A secret belongs in the file or on stdin: arguments are visible to
    every process on the Mac.
    """
    answers: dict = {}
    source = getattr(args, "answers", None)
    if source:
        try:
            text = sys.stdin.read() if source == "-" else Path(source).read_text()
            answers = json.loads(text)
        except (OSError, json.JSONDecodeError) as e:
            return {}, f"Could not read the answers in {source}: {e}"
        if not isinstance(answers, dict):
            return {}, f"The answers in {source} are not a JSON object of field to answer"
    for pair in getattr(args, "answer", None) or []:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip():
            return {}, f"--answer takes key=value, not {pair.split('=')[0]!r}"
        answers[key.strip()] = value
    return answers, ""


def handle_configure(stck, args):
    """`stack configure <id>`: ask, or answer, a stacklet's setup questions.

    The same walk as a first `stack up` (`stack.configure`), and nothing
    else: no install, no start. With `--json` nothing is asked: stdout is
    the next form, `{"form": ...}`, or `{"ready": true}` once the answers
    are applied, and both exit 0. Without it, a terminal is asked and a
    configured stacklet says what is set.
    """
    from .configure import nobody

    as_json = getattr(args, "json", False)
    answers, problem = given_answers(args)
    if problem:
        _configure_failed(as_json, {"error": problem})
    with contextlib.redirect_stdout(sys.stderr) if as_json else contextlib.nullcontext():
        result = stck.configure(args.stacklet, answers, ask=nobody if as_json else None,
                                forms_only=as_json)

    if as_json and ("form" in result or result.get("ready")):
        shown = {"form": result["form"]} if "form" in result else {"ready": True}
        json.dump(shown, sys.stdout, indent=2)
        print()
        return
    if "cancelled" in result:
        print(f"  {DIM}{result['cancelled']}{RESET}", file=sys.stderr)
        sys.exit(1)
    if "error" in result:
        _configure_failed(as_json, result)
    stacklet = stck._find_stacklet(args.stacklet) or {}
    name = stacklet.get("name", args.stacklet)
    if result.get("applied"):
        print(f"  {GREEN}\u2713{RESET}  {name} is configured.")
        return
    _print_settings(name, stck.config.get(args.stacklet, {}))


def _configure_failed(as_json: bool, result: dict) -> None:
    if as_json:
        json.dump({"error": result["error"]}, sys.stdout, indent=2)
        print()
    else:
        print_error(result)
    sys.exit(1)


def _print_settings(name: str, section: dict) -> None:
    """What a configured stacklet has in stack.toml, credentials hidden."""
    print(f"  {name} is configured. Nothing to ask.")
    rows = _flatten(_redact(section))
    if not rows:
        print(f"  {DIM}It has no settings in stack.toml.{RESET}")
        return
    print()
    for key, value in rows:
        print(f"  {DIM}{key:<24}{RESET}  {value}")


def _flatten(section, prefix: str = "") -> list[tuple[str, str]]:
    """`{"targets": {"vault": {"disk": "x"}}}` as `[("targets.vault.disk", "x")]`."""
    rows = []
    for key, value in section.items():
        if isinstance(value, dict):
            rows += _flatten(value, f"{prefix}{key}.")
        else:
            rows.append((f"{prefix}{key}", str(value)))
    return rows


def handle_install(stck, args):
    """The wizard, or with `--family <file>` the install that asks nothing."""
    if args is not None and (args.family or args.yes):
        if not args.family:
            print_error({"error": "--yes installs from a family file: add --family <file>"})
            sys.exit(1)
        from .installer import install_from_file
        if code := install_from_file(stck, Path(args.family), yes=args.yes):
            sys.exit(code)
        return

    from .installer import install

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
        install()
    except KeyboardInterrupt:
        restore()
        print(f"\n\n  {DIM}Cancelled — nothing was changed.{RESET}\n")
    finally:
        restore()


def _belongs_to_stacklet(entry_name: str, sids: list[str]) -> bool:
    """True when a .stack/ entry belongs to one of the named stacklets.

    Setup markers look like `<sid>.setup-done`; any future per-stacklet
    state under .stack/ is expected to use the same `<sid>.` prefix so
    it gets preserved automatically when the user excludes that sid.
    The .test-instance sentinel and global secrets (ADMIN_PASSWORD,
    user passwords) don't carry a stacklet prefix and therefore get
    wiped as part of the full-stack teardown, which is correct.
    """
    return any(entry_name.startswith(f"{sid}.") for sid in sids)


def handle_uninstall(stck, args):
    # `--not X [Y ...]` excludes one or more stacklets from the uninstall.
    # The state of excluded stacklets (container, setup marker, data dir)
    # is preserved across this call so expensive state — most notably the
    # ai stacklet's downloaded model weights — can survive a teardown
    # without a multi-gigabyte re-download on the next install.
    exclude = list(getattr(args, "exclude", None) or [])

    # 'stack uninstall chatai' is a common mistake — catch it early. The
    # values consumed by `--not` are intentional and must be filtered out
    # before we treat a bare token as a "did you mean destroy?" signal.
    remaining = [a for a in sys.argv[2:] if not a.startswith("-")]
    remaining = [a for a in remaining if a not in exclude]
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
        print("  This will:")
        print("    \u2022 Destroy all running services and their containers")
        print("    \u2022 Remove stack.toml and users.toml")
        print("    \u2022 Remove all runtime state (.stack/)")
        print("    \u2022 Optionally remove all service data\n")
        try:
            answer = input("  Type 'uninstall' to confirm: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if answer != "uninstall":
            print(f"  {DIM}Aborted{RESET}")
            return

    # Only destroy stacklets that actually have state, and never touch
    # the excluded set. An excluded stacklet keeps its container, data
    # dir, and setup marker: everything needed to survive the uninstall
    # and resume on the next `stack up`.
    cli = CLI(stck)
    container_ids = docker.all_project_ids()
    for s in stck.discover():
        sid = s["id"]
        if sid in exclude:
            print(f"  {DIM}Preserving {s['name']} (excluded via --not){RESET}", file=sys.stderr)
            continue
        has_data = (stck.data / sid).exists()
        has_marker = s.get("enabled")
        has_container = sid in container_ids
        if not has_data and not has_marker and not has_container:
            continue
        print(f"  {ORANGE}Uninstalling {s['name']}...{RESET}", file=sys.stderr)
        cli.destroy(sid)
        print(f"  {GREEN}\u2713{RESET} {s['name']} uninstalled")

    # Remove config files
    for filename in ("stack.toml", "users.toml"):
        path = stck.root / filename
        if path.exists():
            path.unlink()
            print(f"  {GREEN}\u2713{RESET} Removed {filename}")

    # Remove runtime state. With exclusions, wipe entry by entry so the
    # setup markers of the preserved stacklets (and anything else they
    # own under .stack/) survive. Without exclusions, the whole directory
    # goes as before.
    state_dir = stck.root / ".stack"
    if state_dir.exists():
        import shutil
        if exclude:
            for item in state_dir.iterdir():
                if _belongs_to_stacklet(item.name, exclude):
                    continue
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
            print(f"  {GREEN}\u2713{RESET} Cleaned .stack/ (kept: {', '.join(exclude)})")
        else:
            shutil.rmtree(state_dir)
            print(f"  {GREEN}\u2713{RESET} Removed .stack/")

    # The installer puts the global command in place for the checkout's
    # own instance, so only uninstalling that instance takes it away. A
    # STACK_DIR instance (a test run, a sandbox) leaves it for the others.
    if stck.instance_dir == stck.root and (bin_dir := global_command.bin_dir()):
        if global_command.uninstall(stck.root, bin_dir):
            print(f"  {GREEN}\u2713{RESET} Removed the {global_command.NAME} command")

    # Offer to remove data
    if stck.data.exists():
        home = str(Path.home())
        display = str(stck.data).replace(home, "~", 1)
        print(f"\n  {RED}Data directory: {display}{RESET}")
        print(f"  {RED}This contains all your photos, messages, documents, etc.{RESET}")
        # Extension stacklets are source code, not data, and they live in
        # the data dir by default. Say so before the wipe, not after.
        doomed = [d for d in stck.extension_dirs
                  if d.exists() and d.is_relative_to(stck.data)]
        for d in doomed:
            print(f"  {RED}It also contains your extension stacklets "
                  f"({_short_path(d)}).{RESET}")
        print(f"  {RED}This action is irreversible.{RESET}\n")
        try:
            rm_data = input("  Type 'delete' to remove all data: ").strip()
        except (EOFError, KeyboardInterrupt):
            rm_data = ""
        if rm_data == "delete":
            import shutil
            if exclude:
                for item in stck.data.iterdir():
                    if item.name in exclude:
                        continue
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
                print(f"  {GREEN}\u2713{RESET} Removed {display} (kept: {', '.join(exclude)})")
            else:
                shutil.rmtree(stck.data)
                print(f"  {GREEN}\u2713{RESET} Removed {display}")
        else:
            print(f"  {DIM}Data kept at {display}{RESET}")

    print(f"\n  {GREEN}\u2713{RESET} {name} uninstalled")
    print(f"  {DIM}Run 'stack install' to start fresh.{RESET}\n")


def handle_update(stck, args):
    """Move the checkout to a release. Restarting stays the admin's call.

    Sources only: nothing here stops or starts a service. A family is
    using these, so when they go down is a decision for the person at the
    keyboard, not a side effect of updating. The command works out which
    restarts the release actually earns and prints them.

    The move is a transaction. Local edits are set aside and put back,
    and if they collide with the release the whole thing winds back to
    where it started, because a half-applied update leaves conflict
    markers inside files that have to parse.
    """
    from .prompt import TEAL, confirm
    from .updater import (
        Checkout, latest_tag, migrations_in, migrations_left_behind, offered_tag,
        restart_targets, touches_framework, touched_stacklets, version_key,
    )

    checkout = Checkout(stck.root)
    if not checkout.is_git():
        print_error({"error": "This is not a git checkout, so there is no release to move to",
                     "hint": "git clone https://github.com/famstack-dev/famstack.git"})
        sys.exit(1)

    # The same line doctor opens with, so both commands answer "what am I
    # running" identically. The positions themselves are in the plan below.
    # flush: the progress lines below go to stderr unbuffered, and this
    # one would otherwise arrive after them whenever output is piped.
    print(f"\n  {ORANGE}{BOLD}{stck.product_name()}{RESET} "
          f"{DIM}{running_version_of(stck)}{RESET}", flush=True)

    print("  Fetching releases...", file=sys.stderr)
    fetched, fetch_err = checkout.fetch_tags()
    if not fetched:
        print(f"  {ORANGE}\u26a0{RESET}  Could not reach the remote. Working from the tags already here.",
              file=sys.stderr)
        if fetch_err:
            print(f"      {DIM}{fetch_err.splitlines()[-1]}{RESET}", file=sys.stderr)

    # Without a tag argument, a pre-release is offered only to a checkout
    # already on one; `latest is` below still names the newest of all,
    # since whoever types a tag is choosing among all of them.
    tags = checkout.tags()
    target = args.tag or offered_tag(tags, checkout.describe())
    if not target:
        print_error({
            "error": "No releases found in this checkout",
            "problems": [
                f"tags come from: {', '.join(checkout.remotes()) or 'no remotes'}",
                "on a fork, add the project as a remote: "
                "git remote add upstream <url>",
            ],
        })
        sys.exit(1)
    if target not in tags:
        print_error({"error": f"No such release: {target}",
                     "problems": [f"latest is {latest_tag(tags)}"]})
        sys.exit(1)

    current = checkout.current_tag()
    if current == target:
        print(f"\n  {GREEN}\u2713{RESET}  Already on {TEAL}{target}{RESET}\n")
        return

    # Working past the newest release: a branch, main, or a tag with
    # commits after it. Moving "up" to that release would move backwards
    # and stash whatever is in progress to do it.
    branch = checkout.branch()
    if not args.tag and checkout.contains(target):
        ahead = checkout.commits_ahead_of(target)
        distance = f"{ahead} commit" + ("s" if ahead != 1 else "")
        print(f"\n  {GREEN}\u2713{RESET}  Your checkout is {TEAL}{distance} "
              f"ahead{RESET} of {target}, the newest release.")
        if branch:
            print(f"  {DIM}You are on {branch}, which is development.{RESET}")
        print(f"  {DIM}To move onto that release anyway: "
              f"stack update {target}{RESET}")
        if len(checkout.remotes()) > 1:
            print(f"  {DIM}Tags came from: {', '.join(checkout.remotes())}{RESET}")
        print()
        return

    # ── What this jump would do ───────────────────────────────────
    base = current or "HEAD"
    changed = checkout.changed_paths(base, target)
    subjects = checkout.log_subjects(base, target)
    running = stck.running_ids()
    targets = restart_targets(changed, running)
    dirty = checkout.dirty_paths()
    collisions = sorted(set(dirty) & set(changed))
    arriving = migrations_in(checkout.added_paths(base, target))
    left_behind = migrations_left_behind(
        checkout.removed_paths(base, target), lambda sid: _applied_migrations(stck, sid))

    print(f"\n  {BOLD}Update{RESET}  {DIM}{checkout.describe()}{RESET} \u2192 {TEAL}{target}{RESET}\n")
    if current and version_key(target) < version_key(current):
        print(f"  {ORANGE}\u26a0{RESET}  {target} is older than {current}. Data does not downgrade.")
    if left_behind:
        print(f"  {ORANGE}\u26a0{RESET}  {target} predates data migrations this instance has had:")
        for sid, number, name in left_behind:
            print(f"     {sid}/{number} {DIM}{name}{RESET}")
        print(f"  {DIM}Its code reads the migrated data as it finds it. The way back is "
              f"restoring {stck.data} from a backup made before they ran.{RESET}")
    if subjects:
        commits = f"{len(subjects)} commit" + ("s" if len(subjects) != 1 else "")
        files = f"{len(changed)} file" + ("s" if len(changed) != 1 else "")
        print(f"  {commits}, {files}")
        for line in subjects[:5]:
            print(f"  {DIM}\u2022 {line}{RESET}")
        if len(subjects) > 5:
            print(f"  {DIM}  ... and {len(subjects) - 5} more{RESET}")
    if dirty:
        count = f"{len(dirty)} file" + ("s" if len(dirty) != 1 else "")
        print(f"\n  Your edits to {count} are set aside for the move, then put back:")
        for path in dirty[:5]:
            print(f"  {DIM}\u2022 {path}{RESET}")
        for path in collisions:
            print(f"  {ORANGE}\u26a0{RESET}  {path} is also changed by this release. "
                  f"If they collide, the update winds back and changes nothing.")
    if branch:
        print(f"\n  {DIM}This leaves {branch} for the tag, which detaches HEAD. "
              f"Your work stays on {branch}; git switch {branch} goes back.{RESET}")
    if targets:
        scope = ("every running stacklet" if touches_framework(changed)
                 else ", ".join(targets))
        print(f"\n  Needs a restart afterwards: {scope}")
    if arriving:
        # Never run from here: a migration needs the new code running.
        print(f"  Data migrations that run when their stacklet next starts: "
              f"{', '.join(f'{sid}/{number} {name}' for sid, number, name in arriving)}")
    print(f"  {DIM}Release notes: "
          f"https://github.com/famstack-dev/famstack/releases/tag/{target}{RESET}")

    if getattr(args, "dry_run", False):
        _print_restart_advice(targets, changed, running, touches_framework, touched_stacklets)
        return
    if not getattr(args, "yes", False):
        if not sys.stdin.isatty():
            print_error({"error": "Pass --yes to confirm (non-interactive)"})
            sys.exit(1)
        if not confirm(f"Update to {target}?"):
            print(f"  {DIM}Aborted{RESET}\n")
            return

    # ── Move ──────────────────────────────────────────────────────
    was = checkout.position()
    stashed = checkout.stash() if dirty else False
    if stashed:
        print(f"  {GREEN}\u2713{RESET}  Your edits set aside")

    moved, err = checkout.checkout(target)
    if not moved:
        if stashed:
            checkout.stash_pop()
        print_error({"error": f"Could not move to {target}",
                     "problems": err.splitlines()[:4]})
        sys.exit(1)

    # Announced only once the edits are back, because a failed restore
    # winds the move back and "now on <tag>" would be a lie by then.
    if stashed and not _restore_edits(checkout, was, target, collisions):
        sys.exit(1)
    print(f"  {GREEN}\u2713{RESET}  Now on {target}")

    print(f"\n  {GREEN}\u2713{RESET}  Updated to {TEAL}{target}{RESET}")
    _print_restart_advice(targets, changed, running, touches_framework, touched_stacklets)
    # The app is built from the checkout, so moving the checkout leaves the
    # installed one behind until it is built again.
    if menubar_app.installed():
        print(f"  {DIM}The menu bar app is built from the checkout:{RESET} "
              f"{TEAL}./stack app install{RESET} {DIM}updates it.{RESET}\n")


def _applied_migrations(stck, stacklet_id) -> set[str]:
    """The migration numbers recorded as applied in a stacklet's data."""
    migrations = stck.migrations(stacklet_id)
    return set(migrations.record().applied) if migrations else set()


def print_restart_call_to_action(targets, framework=False) -> None:
    """The one loud line that says the code moved and nothing applied it.

    Printed wherever the gap is visible: after an update, under a list
    that shows stale stacklets. Same wording every time, because an
    operator should recognise it rather than read it twice.
    """
    from .prompt import TEAL

    print(f"\n  {ORANGE}{BOLD}\u26a0  Code updated. A RESTART is required "
          f"to apply it.{RESET}")
    if framework:
        print(f"     {DIM}The framework changed, so this is everything running.{RESET}")
        print(f"     {TEAL}./stack restart{RESET}")
        return
    print(f"     {TEAL}./stack restart{RESET}  "
          f"{DIM}or: {', '.join('./stack restart ' + t for t in targets[:3])}{RESET}")


def _print_restart_advice(targets, changed, running, touches_framework, touched_stacklets):
    """What the admin has to run for the new code to be the running code.

    Deliberately advice and not action: a stacklet only picks up a
    release when its containers are recreated, and that is a decision
    about when the family loses the service.
    """
    if not targets:
        idle = sorted(touched_stacklets(changed) - set(running))
        if idle:
            print(f"\n  {DIM}Nothing to restart. {', '.join(idle)} changed but "
                  f"is not running, and will pick this up on the next stack up.{RESET}\n")
        else:
            print(f"\n  {DIM}Nothing running was changed by this release.{RESET}\n")
        return

    print_restart_call_to_action(targets, framework=touches_framework(changed))
    print(f"     {TEAL}./stack doctor{RESET}\n")


def _restore_edits(checkout, was, target, collisions) -> bool:
    """Put the admin's edits back, or wind the whole update back.

    git keeps the stash entry when a pop conflicts, so both sides of the
    collision still exist: the release is the tag, the edits are the
    stash. That makes undoing safe, and undoing is the right default. A
    tree carrying conflict markers in a compose file is not something to
    hand back to someone who typed one command.
    """
    from .prompt import TEAL

    restored, _ = checkout.stash_pop()
    if restored:
        print(f"  {GREEN}\u2713{RESET}  Your edits put back")
        return True

    checkout.force_checkout(was)
    recovered, err = checkout.stash_pop()
    print(f"\n  {ORANGE}\u26a0{RESET}  Your edits collide with {target}. Nothing changed.")

    if not recovered:
        print(f"      {RED}The wind-back did not finish.{RESET} Your edits are still "
              f"in the stash:")
        print(f"      {DIM}git stash list && git stash pop{RESET}")
        if err:
            print(f"      {DIM}{err.splitlines()[-1]}{RESET}\n")
        return False

    print(f"      Back on {TEAL}{checkout.describe()}{RESET} with your edits where they were.")
    if collisions:
        print("      The collision is in:")
        for path in collisions:
            print(f"      {DIM}\u2022 {path}{RESET}")
    print("      To take the release anyway, deal with that file first:")
    print(f"      {DIM}git checkout -- <file>   drop your version for the release's{RESET}")
    print(f"      {DIM}git stash                keep it, then pop and merge by hand{RESET}")
    print(f"      then run {TEAL}./stack update{RESET} again.\n")
    return False


def handle_family(stck, args):
    """`stack family` shows who the instance is for; `stack family set --from
    <file>` writes it, from a TOML or JSON file or from stdin (`-`). The
    rules are in stack.family."""
    from . import family

    if not args.verb:
        report = family.show(stck)
        if args.json:
            json.dump(report, sys.stdout, indent=2, ensure_ascii=False)
            print()
            return
        print_family(report)
        return
    if args.verb != "set":
        print_error({"error": f"Unknown verb '{args.verb}'. Use: set"})
        sys.exit(1)
    if not args.source:
        print_error({"error": "stack family set needs --from <file>, or --from - to read stdin"})
        sys.exit(1)
    try:
        text = sys.stdin.read() if args.source == "-" else Path(args.source).read_text()
    except OSError as e:
        print_error({"error": f"Could not read {args.source}: {e.strerror}"})
        sys.exit(1)

    data, found = family.load(text, args.source)
    result = family.refusal(found) if found else family.set_family(stck, data)
    if args.json:
        json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
        print()
    elif "error" in result:
        print_error({**result, "problems": family.describe(result["problems"], data)})
    else:
        written = ", ".join(result["changed"]) or "nothing, it was already so"
        print(f"\n  {GREEN}\u2713{RESET}  Saved. Written: {written}")
        print_family(result)
    if "error" in result:
        sys.exit(1)


def print_family(report: dict) -> None:
    """`stack family` for people: the name, the web address, one line each."""
    from .users import family_display_name

    print()
    if not report["people"] and not report["name"]:
        print(f"  {DIM}Nobody yet. Write the family with "
              f"{TEAL}stack family set --from <file>{RESET}{DIM}.{RESET}\n")
        return
    print(f"  {BOLD}{family_display_name(report['name']) or 'Unnamed'}{RESET}")
    if report["domain"]:
        print(f"  {DIM}Web address{RESET}  {report['domain']}")
    print()
    width = max((len(p["name"]) for p in report["people"]), default=0) + 2
    for p in report["people"]:
        about = ", ".join(x for x in [
            p["relation"], p["words"], "looks after the server" if p["role"] == "admin" else ""] if x)
        print(f"  {p['name']:<{width}}{TEAL}{p['id']:<10}{RESET}{about}")
        print(f"  {'':<{width}}{DIM}{p['email']}{RESET}")
    print(f"\n  {DIM}Change it: {TEAL}stack family set --from <file>{RESET}"
          f"{DIM} (see docs/stack-reference.md, Users){RESET}\n")


# ── Extensions ────────────────────────────────────────────────────────────

def handle_extensions(stck, args):
    """`stack extensions list|add|update`: the catalog, installing from a
    repo, and updating from it.

    `add` copies one stacklet out of a git repo into the first extension
    dir and starts nothing; `stack up <id>` does that, as for any other.
    `update` replaces installed ones with the newest version of their repo
    and restarts nothing. The mechanics are in `extensions.py`, the
    decisions in ADR-022.
    """
    if args.extensions_action == "list":
        _extensions_list(stck)
    elif args.extensions_action == "add":
        _extensions_add(stck, args)
    elif args.extensions_action == "update":
        _extensions_update(stck, args)
    else:
        print(f"\n  {BOLD}stack extensions{RESET}\n")
        print(f"    {TEAL}{'list':<25}{RESET}What the catalog offers and what is installed")
        print(f"    {TEAL}{'add <id>':<25}{RESET}Install an extension from the catalog")
        print(f"    {TEAL}{'add <git-url> [<id>]':<25}{RESET}Install one from any git repo")
        print(f"    {TEAL}{'update [<id>...]':<25}{RESET}Update installed extensions from their repo\n")


def _refuse(error: str, problems: list[str] | None = None) -> NoReturn:
    # stdout first: a refusal after a warning must not arrive before it
    # when both streams go to a pipe.
    sys.stdout.flush()
    print_error({"error": error, "problems": problems or []})
    sys.exit(1)


def _extensions_list(stck) -> None:
    """The catalog's extensions, then what is installed from elsewhere or by hand.

    An installed extension is matched to the catalog by its `.source.toml`,
    and is out of date when the tree of its path there has moved on. What
    is installed is listed even when the catalog cannot be reached: it is
    on this Mac.
    """
    from .extensions import catalog_url, read_source, same_repo, short_url

    url = catalog_url(stck.config)
    catalog, failure = _read_catalog(stck, url)
    installed = [(s, read_source(Path(s["path"])))
                 for s in stck.discover() if s.get("source") == "extension"]
    ours = {src.path: (s, src) for s, src in installed if src and same_repo(src.url, url)}

    def row(sid, name, stage, detail=""):
        print(f"    {sid:<12} {name:<28} {DIM}{stage:<12}{RESET}{detail}".rstrip())

    print(f"\n  {BOLD}Catalog{RESET}  {DIM}{short_url(url)}{RESET}")
    if failure:
        print(f"  {ORANGE}\u26a0{RESET}  Could not reach the catalog: {failure}")
        if catalog:
            print(f"     {DIM}As fetched on {catalog['fetched']}:{RESET}")
    outdated = []
    for entry in (catalog or {}).get("extensions", []):
        detail = ""
        if mine := ours.pop(entry["path"], None):
            detail = "installed"
            if mine[1].tree != entry["tree"]:
                detail = f"{ORANGE}installed, update available{RESET}"
                outdated.append(mine[0]["id"])
        row(entry["id"], entry["name"], entry["stage"], detail)
    for s, _ in ours.values():
        row(s["id"], s["name"], s["stage"], "installed")

    elsewhere = [(s, src) for s, src in installed if src and not same_repo(src.url, url)]
    if elsewhere:
        print(f"\n  {BOLD}Installed from elsewhere{RESET}")
        for s, src in elsewhere:
            row(s["id"], s["name"], s["stage"], f"{short_url(src.url)} @ {src.commit[:7]}")
    by_hand = [s for s, src in installed if not src]
    if by_hand:
        print(f"\n  {BOLD}Installed by hand{RESET}")
        for s in by_hand:
            row(s["id"], s["name"], s["stage"], _short_path(s["path"]))
    print()
    if outdated:
        print(f"  {DIM}Update:{RESET}   stack extensions update {' '.join(outdated)}")
    print(f"  {DIM}Install:{RESET}  stack extensions add <id | git-url>\n")


def _read_catalog(stck, url: str) -> tuple[dict | None, str | None]:
    """The catalog fetched now, or as last fetched; and why it could not
    be fetched, if it could not. Kept in the instance's `.stack/`, so
    listing never creates an extension dir."""
    from .extensions import CATALOG_CACHE, FetchFailed, fetch, load_catalog, save_catalog

    cache = stck.instance_dir / ".stack" / CATALOG_CACHE
    print("  Fetching the catalog...", file=sys.stderr)
    failure = None
    try:
        with fetch(url) as repo:
            save_catalog(cache, repo)
    except FetchFailed as e:
        failure = str(e)
    return load_catalog(cache, url), failure


def _extensions_add(stck, args) -> None:
    """Install one extension, from the catalog by id or from any git URL."""
    from .extensions import FetchFailed, catalog_url, fetch, is_url, same_repo

    catalog = catalog_url(stck.config)
    if is_url(args.source):
        url, wanted = _recordable(args.source), args.id
    elif args.id:
        _refuse("Name one extension: stack extensions add <id>, "
                "or stack extensions add <git-url> <id>")
    else:
        url, wanted = catalog, args.source

    print(f"  Fetching {url}...", file=sys.stderr)
    try:
        with fetch(url) as repo:
            installed = _install_from(stck, repo, wanted, same_repo(url, catalog), args.yes)
    except FetchFailed as e:
        _refuse(f"Could not fetch {url}", [str(e)])
    if installed:
        offer, target = installed
        print(f"\n  {GREEN}✓{RESET}  {offer.name} installed in {_short_path(target)}")
        # The extension knows its next step: hue pairs with a bridge and has
        # nothing to start. `stack up` is named when it runs something, or
        # when there is no README to say what comes next.
        readme = target / "README.md"
        if readme.is_file():
            print(f"  Next: read {_short_path(readme)}")
        if _runs_on_up(target) or not readme.is_file():
            print(f"  Start it with: {TEAL}stack up {offer.id}{RESET}")
        print()


def _recordable(source: str) -> str:
    """The URL to fetch and record: a local directory as its absolute
    path, so `update` finds it from wherever it runs."""
    local = Path(source).expanduser()
    # absolute, not resolve: a catalog configured through a symlinked
    # path (/tmp on macOS) must still match the URL recorded here.
    return os.path.abspath(local) if local.is_dir() else source


def _runs_on_up(folder: Path) -> bool:
    """Whether `stack up` runs anything of this stacklet: containers, or
    a lifecycle hook (`hooks/on_*`)."""
    hooks = folder / "hooks"
    return (docker.find_compose_file(folder) is not None
            or (hooks.is_dir() and any(hooks.glob("on_*"))))


def _install_from(stck, repo, wanted, trusted, yes):
    """Pick, check, warn and copy. The offer and its folder, or None when
    the admin said no; a refusal exits. `trusted` is the catalog: its
    extensions were reviewed, so they install without the warning."""
    from .extensions import CopyFailed, install

    offer = _pick_offer(repo, wanted, trusted)
    if offer is None:
        return None
    if problems := offer.problems():
        _refuse(f"{offer.id or repo.url} cannot be installed", problems)
    _refuse_if_taken(stck, offer.id)

    consent = trusted or _foreign_consent(stck, repo, offer, f"Install {offer.id}?", yes)
    if consent is None:
        _refuse(_NEEDS_YES)
    if not consent:
        print(f"  {DIM}Nothing installed{RESET}\n")
        return None
    try:
        return offer, install(repo, offer, stck.mounted_extension_dir)
    except CopyFailed as e:
        _refuse(f"Could not copy {offer.id} into {stck.mounted_extension_dir}", [str(e)])


def _pick_offer(repo, wanted, trusted):
    """The stacklet to install: the one named, the only one, or the one
    the admin picks. Never all of them: each is code to trust on its own."""
    from .prompt import ask

    offers = repo.offers
    if not offers:
        _refuse(f"No stacklet in {repo.url}",
                ["Looked for stacklet.toml at its root, then in each folder one level down."])
    if wanted:
        if offer := repo.offer(wanted):
            return offer
        _refuse(f"{'The catalog' if trusted else repo.url} has no extension {wanted}",
                [f"It has: {', '.join(o.id for o in offers)}"])
    if len(offers) == 1:
        return offers[0]
    if not sys.stdin.isatty():
        _refuse(f"{repo.url} holds {len(offers)} extensions. Name the one to install:",
                [f"stack extensions add {repo.url} {o.id}" for o in offers])

    print(f"\n  {repo.url} holds {len(offers)} extensions:\n")
    for number, o in enumerate(offers, 1):
        print(f"    {number}  {o.id:<12} {o.name}")
    print()
    count = len(offers)
    answer = ask(f"Which one? [1-{count}]", validate=lambda v: None if (
        v.isdigit() and 1 <= int(v) <= count) else f"A number from 1 to {count}")
    return offers[int(answer) - 1] if answer else None


def _refuse_if_taken(stck, sid: str) -> None:
    """Refuse an id something else already has: the release, an installed
    extension in any extension dir, or a folder of that name."""
    for s in stck.discover():
        if s["id"] != sid:
            continue
        if s.get("source") == "repo":
            _refuse(f"{sid} ships with {stck.product_name()}",
                    ["The release wins a clash of ids, so this one would never load."])
        _refuse(f"{sid} is already installed in {s['path']}")
    if (target := stck.mounted_extension_dir / sid).exists():
        _refuse(f"{target} already exists")


_NEEDS_YES = "Pass --yes to confirm (non-interactive)"


def _foreign_consent(stck, repo, offer, question: str, yes: bool) -> bool | None:
    """The warning for code from elsewhere, then `question`, default no.
    True to go ahead, False when the admin said no, None when there is no
    terminal to ask at and no `--yes`. `--yes` skips only the question."""
    from .prompt import confirm

    _print_foreign_warning(stck.product_name(), repo, offer)
    if yes:
        return True
    if not sys.stdin.isatty():
        return None
    return confirm(question, default=False)


def _print_foreign_warning(product: str, repo, offer) -> None:
    """What an admin needs before running code nobody reviewed: where it
    is from, which commit, and what of it runs where."""
    brings = repo.brings(offer)
    commands = f"stack {offer.id} {', '.join(brings.commands)}" if brings.commands else ""
    runs = [(label, value) for label, value in (
        ("containers", ", ".join(brings.containers)),
        ("host hooks", ", ".join(brings.hooks)),
        ("migrations", ", ".join(brings.migrations)),
        ("CLI plugin", commands),
        ("bot", ", ".join(brings.bots)),
    ) if value] or [("only files", "no containers, hooks, commands or bot")]

    print(f"\n  {ORANGE}!{RESET} {BOLD}{offer.id} is not from the {product} catalog.{RESET} "
          f"Nobody at {product}\n    has reviewed this code.\n")
    print(f"    {'Source':<9}{repo.url}")
    print(f"    {'Commit':<9}{repo.short}  {repo.date}  \"{repo.subject}\"")
    for number, (label, value) in enumerate(runs):
        print(f"    {'Brings' if number == 0 else '':<9}{label:<13}{value}")
    print("\n    Its hooks and CLI plugin run on this Mac as you, with access to\n"
          "    the stack's secrets. Install only code you trust.\n")


# ── Extensions: update ──────────────────────────────────────────────────

def _extensions_update(stck, args) -> None:
    """Update the named extensions, or every one that records its source.

    Each extension gets its own line, and a refusal of one does not stop
    the others; the exit code is 1 when any was refused. Extensions from
    one repo share one fetch of its default branch, the one `list` reads.
    Nothing is started: the new code runs after `stack up <id>`.
    """
    from .extensions import catalog_url, read_source, same_repo

    installed = {s["id"]: (Path(s["path"]), read_source(Path(s["path"])))
                 for s in stck.discover() if s.get("source") == "extension"}
    if args.ids:
        wanted, refused = _named_for_update(installed, list(dict.fromkeys(args.ids)))
    else:
        wanted, refused = _all_for_update(installed)
    catalog = catalog_url(stck.config)
    for url, members in _by_repo(installed, wanted):
        if not _update_from(stck, url, members, same_repo(url, catalog), args.yes):
            refused = True
    if refused:
        sys.exit(1)


# Why an installed extension cannot be updated before anything is fetched.
_BY_HAND = "installed by hand: stack extensions does not change it"
_NO_FILES = ("its record lists no files, so its files cannot be told from yours: "
             "move the folder away and add it again")


def _named_for_update(installed: dict, ids: list[str]) -> tuple[list[str], bool]:
    """The ids that can be updated, and whether any was refused by name."""
    wanted, refused = [], False
    for sid in ids:
        _, src = installed.get(sid, (None, None))
        reason = ("not installed" if sid not in installed else _BY_HAND if src is None
                  else _NO_FILES if src.files is None else None)
        if reason:
            _update_refused(sid, reason)
            refused = True
        else:
            wanted.append(sid)
    return wanted, refused


def _all_for_update(installed: dict) -> tuple[list[str], bool]:
    """Every id that records its source and its files, and whether one
    was refused. One put there by hand is named once and is no refusal."""
    for sid in (sid for sid, (_, src) in installed.items() if src is None):
        _update_line(sid, "installed by hand, not updated")
    no_files = [sid for sid, (_, src) in installed.items() if src is not None and src.files is None]
    for sid in no_files:
        _update_refused(sid, _NO_FILES)
    wanted = [sid for sid, (_, src) in installed.items() if src is not None and src.files is not None]
    if not wanted and not no_files:
        print("  No extensions installed with stack extensions add")
    return wanted, bool(no_files)


def _by_repo(installed: dict, wanted: list[str]) -> list[tuple[str, list]]:
    """The wanted extensions grouped by repo, in order, for one fetch each."""
    from .extensions import same_repo

    groups: list[tuple[str, list]] = []
    for sid in wanted:
        folder, src = installed[sid]
        group = next((g for g in groups if same_repo(g[0], src.url)), None)
        if group is None:
            group = (src.url, [])
            groups.append(group)
        group[1].append((sid, folder, src))
    return groups


def _update_line(sid: str, text: str, mark: str = " ") -> None:
    print(f"  {mark}  {sid:<12} {text}")


def _update_refused(sid: str, reason: str, problems: list[str] | None = None) -> None:
    """One extension's refusal on stderr."""
    sys.stdout.flush()
    print(f"  {RED}✗{RESET}  {sid:<12} {reason}", file=sys.stderr)
    for problem in problems or []:
        print(f"      {problem}", file=sys.stderr)


def _update_from(stck, url: str, members: list, trusted: bool, yes: bool) -> bool:
    """Update the extensions of one repo with one fetch; False when any of
    them was refused."""
    from .extensions import FetchFailed, fetch

    sys.stdout.flush()
    print(f"  Fetching {url}...", file=sys.stderr)
    with contextlib.ExitStack() as fetched:
        try:
            repo = fetched.enter_context(fetch(url))
        except FetchFailed as e:
            for sid, _, _ in members:
                _update_refused(sid, f"could not fetch {url}", [str(e)])
            return False
        results = [_update_one(stck, repo, sid, folder, src, trusted, yes)
                   for sid, folder, src in members]
    return all(results)


def _update_one(stck, repo, sid: str, folder: Path, src, trusted: bool, yes: bool) -> bool:
    """Update one extension; False when it was refused. A file it cannot
    read refuses this extension, not the command."""
    from .extensions import CopyFailed, FolderEdited

    try:
        return _check_and_replace(stck, repo, sid, folder, src, trusted, yes)
    except FolderEdited as e:
        _update_refused(sid, "not updated", _edited_problems(folder, e.files))
    except CopyFailed as e:
        _update_refused(sid, "not updated", [str(e)])
    return False


def _check_and_replace(stck, repo, sid: str, folder: Path, src, trusted: bool, yes: bool) -> bool:
    """Check, warn and replace one extension; False when it was refused.
    `trusted` is the catalog: no warning and no question, as for `add`."""
    from .extensions import FolderEdited, edited_files, replace

    offer = repo.offer_at(src.path)
    if offer is None:
        where = "a stacklet at its root" if src.path == "." else src.path
        _update_refused(sid, "not updated", [f"{repo.url} no longer has {where}"])
        return False
    if offer.tree == src.tree:
        _update_line(sid, "up to date")
        return True
    if problems := _update_problems(offer, sid):
        _update_refused(sid, "not updated", problems)
        return False
    # Checked before the question, so an edit is refused without asking;
    # `replace` checks again, after the answer.
    if edited := edited_files(folder, src):
        raise FolderEdited(edited)

    consent = trusted or _foreign_consent(stck, repo, offer, f"Update {sid}?", yes)
    if consent is None:
        _update_refused(sid, _NEEDS_YES)
        return False
    if not consent:
        _update_line(sid, "not updated")
        return True
    replace(repo, offer, folder, src)
    # Only the commit: the clone is shallow, so the last commit that changed
    # this extension is not known, and the newest one may be another's.
    _update_line(sid, f"updated to {repo.short}", f"{GREEN}✓{RESET}")
    if stck._is_set_up(sid):
        print(f"     Apply the new code with: {TEAL}stack up {sid}{RESET}")
    return True


def _update_problems(offer, sid: str) -> list[str]:
    """Why the new version cannot replace the extension; none if it can."""
    if problems := offer.problems():
        return [f"The new version cannot be installed: {p}" for p in problems]
    if offer.id != sid:
        return [f"The new version is called {offer.id}, not {sid}. Add it as a new extension."]
    return []


# The most edited files a refusal names; the rest are counted.
_EDITS_SHOWN = 5


def _edited_problems(folder: Path, edited: list[str]) -> list[str]:
    more = len(edited) - _EDITS_SHOWN
    names = ", ".join(edited[:_EDITS_SHOWN]) + (f" and {more} more" if more > 0 else "")
    return [f"{_short_path(folder)} was changed since it was installed: {names}",
            "Undo the change, or move the folder away and add the extension again."]


def handle_version(stck, args):
    print(f"{stck.product_name()} {running_version_of(stck)}")


# ── Main ──────────────────────────────────────────────────────────────────

DISPATCH = {
    "install": handle_install,
    "uninstall": handle_uninstall,
    "family": handle_family,
    "init": handle_init,
    "up": handle_up,
    "down": handle_down,
    "destroy": handle_destroy,
    "status": handle_status,
    "doctor": handle_doctor,
    "list": handle_list,
    "config": handle_config,
    "env": handle_env,
    "restart": handle_restart,
    "setup": handle_setup,
    "configure": handle_configure,
    "logs": handle_logs,
    "errors": handle_errors,
    "host": handle_host,
    "app": handle_app,
    "version": handle_version,
    "update": handle_update,
    "extensions": handle_extensions,
}

# ── Help ─────────────────────────────────────────────────────────────────

_HELP_COMMANDS = [
    ("Lifecycle", [
        ("up <stacklet>|all",      "Start a stacklet (or 'all' to bring up every installed stacklet)"),
        ("down <stacklet>|all",    "Stop a running stacklet (or 'all' to stop everything running)"),
        ("restart [<stacklet>]",   "Restart what is running stale code (or one stacklet, or 'all')"),
        ("setup <stacklet>",   "Re-run first-time setup (backend detection, accounts, etc.)"),
        ("configure <stacklet>", "Ask or answer a stacklet's setup questions (--answer, --json)"),
        ("destroy <stacklet>", "Remove containers, data, and secrets (Destructive operation)"),
    ]),
    ("Info", [
        ("list",               "Show all stacklets and their status"),
        ("doctor",             "Diagnose problems and print how to fix them"),
        ("config",             "Print stack.toml configuration"),
        ("config admin",       "Print tech admin credentials"),
        ("family",             "Show the family; 'family set --from <file>' writes it"),
        ("env <stacklet>",     "Print rendered environment variables"),
        ("logs <stacklet>",    "Tail container logs"),
        ("errors [--since 24h]", "Recent error lines across every stacklet"),
        ("host",               "Memory, disk, uptime, and what each stacklet uses"),
    ]),
    ("Setup", [
        ("update [<tag>]",     "Move the checkout to a release (says what to restart)"),
        ("extensions list",    "What the extension catalog offers and what is installed"),
        ("extensions add <id|url>", "Install an extension from the catalog or a git repo"),
        ("extensions update [<id>]", "Update installed extensions from their repo"),
        ("app install",        "Build the menu bar app and put it in Applications"),
        ("install",            "Install: dependency checks, core, then the dashboard"),
        ("install --family <file>", "Install from a family file; with --yes it asks nothing"),
        ("uninstall",          "Remove all services, config, and data"),
        ("init",               "Create Docker network and data directories"),
    ]),
]


def print_help(name="stack", plugin_cmds=None):
    """Colored help screen with grouped commands."""
    print(f"\n  {ORANGE}{BOLD}{name}{RESET} {DIM}— manage your stacklets{RESET}\n")
    print(f"  {BOLD}Usage:{RESET}  {DIM}stack <command> [options]{RESET}\n")

    col = 25
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

    print(f"  ⚠  {BOLD}Local network only.{RESET} Never install famstack on a machine reachable")
    print("     from the public internet. Remote access belongs behind a VPN.\n")


# ── Main ──────────────────────────────────────────────────────────────────

class _Watched:
    """Stdout that remembers whether a command wrote anything to it."""

    def __init__(self, stream):
        self._stream, self.wrote = stream, False

    def write(self, text):
        self.wrote = self.wrote or bool(text)
        return self._stream.write(text)

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _print_fields(result: dict | None) -> None:
    """A command that printed nothing: its result as `key: value` lines.

    The plugin contract lets a command only return a dict. Without this,
    the terminal and the agent's text surface showed nothing at all.
    """
    for key, value in (result or {}).items():
        if key == "ok" and value is True:
            continue
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
        print(f"{key}: {text}")

def _answer_flags(p) -> None:
    """`--answer` and `--answers`, for the commands that take setup answers."""
    p.add_argument("--answer", action="append", metavar="KEY=VALUE",
                   help="Answer a setup question (repeatable)")
    p.add_argument("--answers", metavar="FILE",
                   help="Setup answers as a JSON object, from a file or - for stdin")


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

    # --json: machine-readable output for the bare `stack` / status / list /
    # env paths. The flag is *detected* here but left in argv — stacklet CLI
    # plugins (stack docs classify --json, ...) are free to define --json
    # as their own option. parser.parse_known_args() below is tolerant of
    # unknown flags, so leaving --json in doesn't disrupt top-level parsing.
    json_mode = "--json" in sys.argv

    parser = argparse.ArgumentParser(
        prog="stack", add_help=False,
    )
    parser.add_argument("-h", "--help", action="store_true")
    parser.add_argument("--version", action="store_true")

    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("install")
    p.add_argument("--family", metavar="FILE",
                   help="Install from a family file (TOML or JSON) instead of asking")
    p.add_argument("--yes", action="store_true", help="Skip the confirmation")
    p = sub.add_parser("uninstall")
    p.add_argument("--yes", action="store_true")
    p.add_argument(
        "--not", dest="exclude", nargs="+", default=[], metavar="STACKLET",
        help="Stacklets to preserve (state, data, setup marker all stay)",
    )
    sub.add_parser("init")
    sub.add_parser("status")
    p = sub.add_parser("doctor")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    sub.add_parser("list")
    p = sub.add_parser("config")
    p.add_argument("--json", action="store_true",
                   help="stack.toml and users.toml as JSON, credentials hidden")
    config_sub = p.add_subparsers(dest="config_action")
    config_sub.add_parser("admin")
    sub.add_parser("help")
    sub.add_parser("version")

    p = sub.add_parser("up")
    p.add_argument("stacklet", nargs="+",
                   help="Stacklets to start, space or comma separated, or 'all'")
    p.add_argument("--no-voice", action="store_true",
                   help="(ai) start without the voice container (TTS + Whisper); sets STACK_AI_NO_VOICE=1")
    _answer_flags(p)
    p.add_argument("--events", action="store_true",
                   help="Report each step as a line of JSON on stdout, then the result")
    p = sub.add_parser("down")
    p.add_argument("stacklet", nargs="+",
                   help="Stacklets to stop, space or comma separated, or 'all'")
    p = sub.add_parser("destroy")
    p.add_argument("stacklet", nargs="+", help="Stacklets to destroy, space or comma separated")
    p.add_argument("--yes", action="store_true")
    p = sub.add_parser("restart")
    p.add_argument("stacklet", nargs="*", default=None,
                   help="Stacklets to restart (space or comma separated), "
                        "'all', or nothing for whatever is running stale code")
    p.add_argument("--yes", action="store_true", help="Skip the confirmation")
    p = sub.add_parser("update")
    p.add_argument("tag", nargs="?", default=None,
                   help="Release to move to (default: the newest release; a pre-release "
                        "only when this checkout already runs one)")
    p.add_argument("--dry-run", action="store_true",
                   help="Show the plan and stop. Still fetches tags, "
                        "or the answer would be as stale as your last fetch")
    p.add_argument("--yes", action="store_true", help="Skip the confirmation")
    p = sub.add_parser("extensions")
    ext_sub = p.add_subparsers(dest="extensions_action")
    ext_sub.add_parser("list")
    p = ext_sub.add_parser("add")
    p.add_argument("source", help="A catalog id, or a git URL")
    p.add_argument("id", nargs="?", default=None,
                   help="Which extension, for a repo that holds several")
    p.add_argument("--yes", action="store_true",
                   help="Install from a repo other than the catalog without asking "
                        "(the warning is still printed)")
    p = ext_sub.add_parser("update")
    p.add_argument("ids", nargs="*", metavar="id",
                   help="Extensions to update (default: every one installed from a repo)")
    p.add_argument("--yes", action="store_true",
                   help="Update from a repo other than the catalog without asking "
                        "(the warning is still printed)")
    p = sub.add_parser("setup"); p.add_argument("stacklet")
    p = sub.add_parser("configure")
    p.add_argument("stacklet")
    p.add_argument("--json", action="store_true",
                   help="Never ask: print the next form, or {\"ready\": true} once applied")
    _answer_flags(p)
    p = sub.add_parser("env"); p.add_argument("stacklet")
    p = sub.add_parser("logs")
    p.add_argument("stacklet")
    p.add_argument("--tail", default=200, type=int)
    p.add_argument("--grep", default=None, help="Filter log lines with grep pattern")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p = sub.add_parser("errors")
    p.add_argument("--since", default="24h", help="How far back to look: 30m, 24h, 168h")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p = sub.add_parser("app")
    p.add_argument("verb", nargs="?", default=None, help="install")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p = sub.add_parser("host")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p = sub.add_parser("family")
    p.add_argument("verb", nargs="?", default=None, help="set")
    p.add_argument("--from", dest="source", metavar="FILE",
                   help="(set) the family as a TOML or JSON file, or - for stdin")
    p.add_argument("--json", action="store_true", help="Output as JSON")

    # Stacklet CLI plugins
    stacklet_cmds = {}
    stck = None
    if repo_root:
        stck = create_stack(repo_root, instance_dir)
        stacklet_cmds = stacklet_commands(stck)
        # Help is read from the plugin files, about 40 ms for all of them, and
        # only shown for `stack <id>` and `--help`: a command that runs reads none.
        argv = sys.argv[1:]
        help_for = argv[0] if argv and ("-h" in argv or "--help" in argv or len(argv) == 1) else None
        for sid, cmds in stacklet_cmds.items():
            sp = sub.add_parser(sid)
            sp_sub = sp.add_subparsers(dest="action")
            for cmd_name, mod_path in cmds.items():
                # Surface each plugin's own HELP + docstring so
                # `stack <id>` lists commands with a one-liner and
                # `stack <id> <cmd> --help` prints its usage + examples.
                short, full = plugin_help(mod_path) if sid == help_for else (None, None)
                sp_sub.add_parser(
                    cmd_name, help=short,
                    description=full,
                    formatter_class=argparse.RawDescriptionHelpFormatter,
                )

    args, _remaining = parser.parse_known_args()

    if args.version:
        name = stck.product_name() if stck else "stack"
        sha = stck._git_commit() if stck else "unknown"
        print(f"{name} {VERSION} ({sha})")
        return

    if args.help:
        name = stck.product_name() if stck else "stack"
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
        name = stck.product_name() if stck else "stack"
        print_help(name, stacklet_cmds or None)
        sys.exit(0)

    if not repo_root:
        print(f"  {RED}✗{RESET}  Can't find stack directory", file=sys.stderr)
        sys.exit(1)

    stck = create_stack(repo_root, instance_dir)

    # Pin all docker commands to the configured runtime context, picked
    # on the first one: most commands never run docker.
    docker.prefer_runtime(stck._cfg("core", "runtime", "orbstack"))

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
        if json_mode:
            # The plugin contract: a command returns a dict and the framework
            # renders it. With --json that dict is the only thing on stdout;
            # what the command prints for people goes to stderr, and the flag
            # is taken out of the arguments before the command reads them.
            _remaining = [a for a in _remaining if a != "--json"]
            sys.argv = [a for a in sys.argv if a != "--json"]
            with contextlib.redirect_stdout(sys.stderr):
                result = stck.run_cli_command(args.command, action, _remaining)
            json.dump(result or {}, sys.stdout, indent=2, default=str)
            print()
            if result and "error" in result:
                sys.exit(1)
            return
        watched = _Watched(sys.stdout)
        with contextlib.redirect_stdout(watched):
            result = stck.run_cli_command(args.command, action, _remaining)
        if result and "error" in result:
            print_error(result); sys.exit(1)
        if not watched.wrote:
            _print_fields(result)
        return

    print_help(stck.product_name(), stacklet_cmds or None)
    sys.exit(1)
