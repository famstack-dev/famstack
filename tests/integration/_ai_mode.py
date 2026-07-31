"""Flip the rig's AI backend between mocked, local, and external.

Three ways to answer a model call. `local` is the default you want.

  local     A self-hosted OpenAI-compatible endpoint - a real model,
            giving real answers, on hardware that costs nothing per call.
            Slower than a stub and that is the whole price. It skips the
            `ai` stacklet's install-and-load-weights wait (minutes and
            gigabytes every time) while still exercising the real thing.

  mock      pytest-httpserver, started by the `openai` conftest fixture.
            Deterministic and offline. Correct only where the assertion
            is about *exact* model output and needs ordered stubs.

  external  A hosted provider. Bills per call. For checking behaviour
            against a frontier model, not for looping.

PREFER `local`. A green mock run proves the wiring, not the behaviour:
the stub returns whatever the test told it to, so classification,
extraction, and prompt changes all "pass" while being wrong. Mocking is
the cheap way to make a suite green and the expensive way to ship a bug.
Reach for `mock` when you need determinism, not when you need it to pass.

Only `[ai]` in the instance's stack.toml changes. Everything downstream
(`ai_openai_url`, the container's host.docker.internal rewrite, the
`AI_API_KEY` secret override) already reads from there, so no other
surface needs to know a mode exists.

WHY ENDPOINTS COME FROM THE ENVIRONMENT
    A self-hosted URL is machine-specific and often a personal hostname.
    This repo is public, so `local` and `external` read their endpoint,
    key, and model from env vars rather than carrying anyone's
    infrastructure in version control. `mock` needs none of that - its
    endpoint is a fixture on localhost - so it is what a fresh checkout
    seeds to. That makes it the fallback, not the goal: set the three
    vars below once and work in `local`.

        FAMSTACK_AI_URL     base URL, including /v1
        FAMSTACK_AI_MODEL   model name to send as `default`
        FAMSTACK_AI_KEY     optional; many self-hosted endpoints ignore it
"""

from __future__ import annotations

import os
import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
STACK_TOML = REPO_ROOT / "stack.toml"

# Keep in step with _seed_secrets.TEST_MODEL: the archivist's vision-probe
# cache is pre-seeded under this name, and a mismatch re-fires the probe.
MOCK_URL = "http://localhost:42199/v1"
MOCK_KEY = "test"
MOCK_MODEL = "test-model"

EXTERNAL_URL = "https://api.openai.com/v1"

MODES = ("mock", "local", "external")


class AIModeError(RuntimeError):
    """Configuration the caller has to fix, reported without a traceback."""


def _env(name: str, mode: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise AIModeError(
            f"{mode} mode needs {name}.\n\n"
            f"  export {name}=...\n\n"
            "Endpoints are read from the environment, not committed - see\n"
            "tests/integration/_ai_mode.py for why and for the full list."
        )
    return value


def settings_for(mode: str) -> dict[str, str]:
    """Resolve a mode to the four `[ai]` values, or explain what is missing."""
    if mode == "mock":
        return {"openai_url": MOCK_URL, "openai_key": MOCK_KEY, "default": MOCK_MODEL}
    if mode == "local":
        return {
            "openai_url": _env("FAMSTACK_AI_URL", "local"),
            "openai_key": os.environ.get("FAMSTACK_AI_KEY", "").strip(),
            "default": _env("FAMSTACK_AI_MODEL", "local"),
        }
    if mode == "external":
        return {
            "openai_url": os.environ.get("FAMSTACK_AI_URL", "").strip() or EXTERNAL_URL,
            "openai_key": _env("FAMSTACK_AI_KEY", "external"),
            "default": _env("FAMSTACK_AI_MODEL", "external"),
        }
    raise AIModeError(f"Unknown mode {mode!r}. Pick one of: {', '.join(MODES)}.")


def current() -> tuple[str, dict]:
    """Return (mode, ai_table) for the instance as it stands."""
    if not STACK_TOML.exists():
        raise AIModeError(f"{STACK_TOML} is missing - bring the instance up first.")
    with STACK_TOML.open("rb") as fh:
        ai = tomllib.load(fh).get("ai", {})
    url = ai.get("openai_url", "")
    if url == MOCK_URL:
        return "mock", ai
    if url.startswith(EXTERNAL_URL.rsplit("/", 1)[0]):
        return "external", ai
    return ("local", ai) if url else ("unset", ai)


def _rewrite(text: str, key: str, value: str) -> str:
    """Replace one `key = "..."` inside the [ai] table, comments intact.

    A tomllib round-trip would drop every comment in the file, and the
    comments here are load-bearing (they explain the fixture wiring). So
    edit the one line, scoped to the [ai] table so a same-named key in
    another table is untouched.
    """
    pattern = re.compile(
        r"(^\[ai\]\n(?:(?!^\[).*\n)*?^" + re.escape(key) + r"\s*=\s*)(\".*?\")",
        re.MULTILINE,
    )
    replacement = rf'\g<1>"{value}"'
    new_text, count = pattern.subn(replacement, text, count=1)
    if count == 0:
        raise AIModeError(f"No `{key}` found in the [ai] table of {STACK_TOML}.")
    return new_text


def apply(mode: str) -> dict[str, str]:
    """Write the mode into stack.toml. Returns the values applied."""
    settings = settings_for(mode)  # resolve first: never half-write a mode
    if not STACK_TOML.exists():
        raise AIModeError(f"{STACK_TOML} is missing - bring the instance up first.")
    text = STACK_TOML.read_text(encoding="utf-8")
    for key, value in settings.items():
        text = _rewrite(text, key, value)
    STACK_TOML.write_text(text, encoding="utf-8")
    return settings


def _redact(key: str, value: str) -> str:
    if key == "openai_key" and value and value != MOCK_KEY:
        return "<set>"
    return value or "<empty>"


def main(argv: list[str]) -> int:
    if not argv:
        mode, ai = current()
        print(f"  ai mode: {mode}")
        for key in ("openai_url", "default", "openai_key"):
            print(f"    {key:<12} {_redact(key, ai.get(key, ''))}")
        print(f"\n  switch with: stacktests ai [{'|'.join(MODES)}]")
        return 0

    mode = argv[0]
    settings = apply(mode)
    print(f"  ai mode -> {mode}")
    for key, value in settings.items():
        print(f"    {key:<12} {_redact(key, value)}")
    if mode != "mock":
        print("\n  Restart affected stacklets so containers pick this up:")
        print("    tests/integration/stacktests up docs")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except AIModeError as exc:
        print(f"\n  {exc}\n", file=sys.stderr)
        raise SystemExit(2) from None
