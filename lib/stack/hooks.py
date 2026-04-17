from __future__ import annotations

"""Lifecycle hook resolution and execution.

Hooks are the extension points of the stacklet lifecycle. Each transition
(install, start, stop, destroy) can trigger stacklet-specific code via
a hook file in the stacklet's hooks/ directory.

Resolution rules:
  1. Look for hooks/{name}.py first (preferred — gets full ctx)
  2. Fall back to hooks/{name}.sh (gets env vars only)
  3. Return None if neither exists — framework skips the step

Python hooks implement: def run(ctx): ...

The ctx object provides:
  ctx.stack   — the Stack instance (full framework access)
  ctx.env     — rendered environment variables
  ctx.step()  — report progress
  ctx.shell() — run a system command
"""

import importlib.util
import os
import subprocess
from pathlib import Path


class StackContext:
    """Everything a hook needs. No artificial restrictions.

    Hook authors get the full Stack instance. They can read config,
    manage secrets, run CLI commands, discover other stacklets —
    whatever makes sense for their use case.
    """

    def __init__(self, stack, stacklet_id: str, env: dict, step_fn=None):
        self.stack = stack
        self.stacklet_id = stacklet_id
        self.env = env
        self._step_fn = step_fn or (lambda msg: None)

    def step(self, msg: str):
        """Report progress to the user."""
        self._step_fn(msg)

    def warn(self, msg: str):
        """Report a warning to the user."""
        if self.stack and hasattr(self.stack, "output"):
            self.stack.output.warn(msg)
        else:
            self._step_fn(msg)

    def shell(self, cmd: str) -> str:
        """Run a shell command. Returns stdout. Raises on failure.

        Inherits env vars from self.env so hooks don't need to manage
        environment manually.
        """
        merged_env = {**os.environ, **self.env}
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            env=merged_env, timeout=1200,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            raise RuntimeError(
                f"Command failed (exit {result.returncode}): {cmd}"
                + (f"\n{stderr}" if stderr else "")
            )
        return result.stdout

    def shell_live(self, cmd: str):
        """Run a shell command with live output to the terminal.

        Use for long-running commands (builds, downloads) where the user
        needs to see progress. Raises on failure.
        """
        merged_env = {**os.environ, **self.env}
        if hasattr(self.stack, "output") and hasattr(self.stack.output, "flush"):
            self.stack.output.flush()
        result = subprocess.run(
            cmd, shell=True, env=merged_env, timeout=1200,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Command failed (exit {result.returncode}): {cmd}")

    @property
    def secret(self):
        """Read/write secrets scoped to the current stacklet.

        Usage: ctx.secret("DB_PASSWORD") to read, ctx.secret("KEY", "value") to write.
        """
        stack = self.stack
        sid = self.stacklet_id
        def _secret(name, value=None):
            if value is not None:
                stack.secrets.set(sid, name, value)
                return value
            return stack.secrets.get(sid, name)
        return _secret

    @property
    def users(self):
        """Load users from users.toml."""
        from .users import load_users
        return load_users(self.stack.instance_dir)

    def cfg(self, key: str, value=None, default: str = "") -> str:
        """Read/write stack.toml config scoped to the current stacklet's section.

        Usage: ctx.cfg("provider") to read, ctx.cfg("provider", "managed") to write.
        """
        if value is not None:
            self.stack._set_cfg(self.stacklet_id, key, value)
            return value
        return self.stack._cfg(self.stacklet_id, key, default)

    @property
    def http_get(self):
        return self._http_get

    @property
    def http_post(self):
        return self._http_post

    @property
    def http_put(self):
        return self._http_put

    def _http(self, method, url, body=None, content_type=None, headers=None):
        import json
        import ssl
        import urllib.request
        h = {}
        if content_type:
            h["Content-Type"] = content_type
        h.update(headers or {})
        data = body.encode() if isinstance(body, str) else body
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, data=data, headers=h, method=method)
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            return json.loads(resp.read().decode())

    def _http_get(self, url, headers=None):
        return self._http("GET", url, headers=headers)

    def _http_post(self, url, body, content_type="application/x-www-form-urlencoded", headers=None):
        return self._http("POST", url, body, content_type, headers)

    def _http_put(self, url, body, content_type="application/json", headers=None):
        return self._http("PUT", url, body, content_type, headers)


def build_hook_ctx(stacklet_id: str, env=None, step_fn=None, **kwargs):
    """Build a StackContext for a hook invocation."""
    return StackContext(
        stack=kwargs.get("stack"),
        stacklet_id=stacklet_id,
        env=env or {},
        step_fn=step_fn,
    )


class HookResolver:
    """Finds and runs lifecycle hooks for a stacklet.

    One resolver per stacklet. Stateless — reads filesystem on every call.
    """

    def __init__(self, stacklet_dir: Path):
        self._stacklet_dir = Path(stacklet_dir)
        self._hooks_dir = self._stacklet_dir / "hooks"

    def resolve(self, name: str) -> Path | None:
        """Find a hook by name. .py preferred, .sh fallback."""
        if not self._hooks_dir.exists():
            return None
        py = self._hooks_dir / f"{name}.py"
        if py.exists():
            return py
        sh = self._hooks_dir / f"{name}.sh"
        if sh.exists():
            return sh
        return None

    def available(self) -> list[str]:
        """List all hook names that have files."""
        if not self._hooks_dir.exists():
            return []
        names = set()
        for f in self._hooks_dir.iterdir():
            if f.suffix in (".py", ".sh") and f.stem.startswith("on_"):
                names.add(f.stem)
        return sorted(names)

    def run(self, name: str, ctx) -> bool:
        """Execute a hook. Returns True on success, False on failure.

        Missing hook = True (no-op). Hook raises or exits non-zero = False.
        """
        hook = self.resolve(name)
        if hook is None:
            return True

        if hook.suffix == ".py":
            return self._run_python(hook, ctx)
        elif hook.suffix == ".sh":
            return self._run_shell(hook, ctx)
        return True

    def _run_python(self, path: Path, ctx) -> bool:
        """Load and run a Python hook."""
        try:
            spec = importlib.util.spec_from_file_location(
                f"hook.{path.stem}", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, "run"):
                mod.run(ctx)
            return True
        except Exception as e:
            if hasattr(ctx, "stack") and hasattr(ctx.stack, "output"):
                ctx.stack.output.error(f"Hook {path.name} error: {e}")
            else:
                step_fn = ctx.step if hasattr(ctx, "step") else ctx.get("step", lambda m: None)
                step_fn(f"Hook {path.name} error: {e}")
            return False

    def _run_shell(self, path: Path, ctx) -> bool:
        """Run a shell hook with env vars from ctx.

        Shell hooks stream output directly to the terminal — they handle
        their own progress reporting (sections, steps, spinners).

        FAMSTACK_DATA_DIR and FAMSTACK_DOMAIN are injected from the stack
        so hooks reading them (e.g. on_install.sh for bind-mount paths)
        see the *configured* values, not the hardcoded `$HOME/famstack-data`
        fallback. Stacklet-declared env vars still override, because they
        come second in the merge order.
        """
        env_dict = ctx.env if hasattr(ctx, "env") else ctx.get("env", {})
        stack = getattr(ctx, "stack", None) if hasattr(ctx, "stack") else None

        framework_env = {}
        if stack is not None:
            framework_env["FAMSTACK_DATA_DIR"] = str(stack.data)
            framework_env["FAMSTACK_DOMAIN"] = stack._cfg("core", "domain")

        env = {**os.environ, **framework_env, **env_dict}

        # Flush any active spinner so shell output isn't clobbered
        if hasattr(ctx, "stack") and hasattr(ctx.stack, "output"):
            output = ctx.stack.output
            if hasattr(output, "flush"):
                output.flush()

        try:
            result = subprocess.run(
                ["bash", str(path)],
                env=env, timeout=1200,
            )
            return result.returncode == 0
        except Exception as e:
            if hasattr(ctx, "warn"):
                ctx.warn(f"Hook {path.name} error: {e}")
            else:
                step_fn = ctx.step if hasattr(ctx, "step") else ctx.get("step", lambda m: None)
                step_fn(f"Hook {path.name} error: {e}")
            return False
