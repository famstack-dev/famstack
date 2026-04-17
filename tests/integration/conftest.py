"""Integration test fixtures.

The rig is a dedicated stack instance at tests/integration/instance/.
Stacklets are spun up on demand by the fixtures below — `paperless`
brings up `docs`, `matrix` brings up `messages`. They stay running
across pytest invocations; stop them between coding sessions with
`tests/integration/test-env-down.sh`.

Per-test isolation is by prefix: every entity a test creates in a
backend carries its scope uid, and teardown deletes only what matches.
Tests run in parallel as long as they each ask for the same scope.

External services exercised for real: Paperless, Postgres, Redis,
Synapse. Only OpenAI is mocked (determinism trumps realism for
classification output).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTANCE_DIR = REPO_ROOT / "tests" / "integration" / "instance"

sys.path.insert(0, str(REPO_ROOT / "lib"))

from tests.integration.paperless import PaperlessAPI, cleanup_prefix
from tests.integration.matrix import MatrixCreds, login
from tests.integration.bdd import BDDLog
from tests.integration._seed_secrets import seed as _seed_test_instance_secrets


# ── Pin the OpenAI mock to the port baked into stack.toml ────────────────

@pytest.fixture(scope="session")
def httpserver_listen_address():
    """pytest-httpserver binds to 127.0.0.1:42199 — the `openai_url` in
    tests/integration/instance/stack.toml points here."""
    return ("127.0.0.1", 42199)


@pytest.fixture
def openai(httpserver):
    """OpenAI-compatible endpoint backed by pytest-httpserver.

    Register responses before the archivist call that triggers them:

        from tests.integration.openai_stub import stub_classify, stub_reformat
        stub_classify(openai, {"title": "...", "topics": [...], ...})
        stub_reformat(openai, "# clean markdown")

    pytest-httpserver asserts all ordered expectations were consumed at
    teardown — tests that register a stub but don't trigger it will fail,
    keeping the rig honest.
    """
    return httpserver


# ── Test stack handle ────────────────────────────────────────────────────

@dataclass
class TestStack:
    """Thin wrapper around the CLI pointed at the test instance."""

    instance_dir: Path = INSTANCE_DIR

    def _env(self) -> dict:
        return {
            **os.environ,
            "STACK_DIR": str(self.instance_dir),
            "PYTHONPATH": str(REPO_ROOT / "lib"),
        }

    def run(self, *args: str, timeout: int = 240) -> dict:
        cmd = [sys.executable, "-m", "stack", *args]
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, cwd=str(REPO_ROOT), env=self._env(),
        )
        for stream in (result.stdout, result.stderr):
            try:
                data = json.loads(stream)
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, ValueError):
                continue
        return {
            "ok": result.returncode == 0,
            "_stdout": result.stdout,
            "_stderr": result.stderr,
            "_code": result.returncode,
        }


@pytest.fixture(scope="session")
def test_stack() -> TestStack:
    return TestStack()


@pytest.fixture(scope="session")
def stack():
    """A Stack instance pointed at the test instance — for tests that
    need to exercise the framework API directly (is_healthy, etc.)."""
    from stack.cli import create_stack
    return create_stack(REPO_ROOT, INSTANCE_DIR)


# ── Per-test prefix + cleanup ────────────────────────────────────────────

@dataclass
class Scope:
    uid: str
    on_cleanup: list = field(default_factory=list)

    def tag(self, base: str) -> str:
        return f"{self.uid}-{base}"

    def cleanup(self) -> None:
        for fn in self.on_cleanup:
            try:
                fn(self.uid)
            except Exception as e:
                print(f"[scope {self.uid}] cleanup error: {e}", file=sys.stderr)


@pytest.fixture
def scope() -> Scope:
    s = Scope(uid=f"t-{uuid.uuid4().hex[:8]}")
    yield s
    s.cleanup()


# ── BDD logger ───────────────────────────────────────────────────────────

@pytest.fixture
def bdd() -> BDDLog:
    """A narrator for the test. Call bdd.given/when/then/and_ to emit
    timestamped protocol lines. Run pytest with `-s` to stream live."""
    return BDDLog()


# ── Sample files for upload ──────────────────────────────────────────────

@pytest.fixture(scope="session")
def sample_invoice_pdf() -> bytes:
    """A minimal single-page PDF with extractable text — enough for
    Paperless OCR to produce recognizable content the LLM can classify."""
    from PIL import Image, ImageDraw
    import io as _io

    img = Image.new("RGB", (1200, 1600), "white")
    draw = ImageDraw.Draw(img)
    draw.text((80, 80),
              "ADAC Autoversicherung\n\n"
              "Kfz-Versicherung 2026\n"
              "Jahresbeitrag: EUR 340,00\n"
              "Versicherungsnehmer: Homer Simpson\n"
              "Vertragsnummer: KFZ-2026-000123\n"
              "Zahlungsziel: 15.03.2026",
              fill="black")
    buf = _io.BytesIO()
    img.save(buf, format="PDF")
    return buf.getvalue()


# ── Paperless (shared, session-scoped) ───────────────────────────────────

@pytest.fixture(scope="session")
def paperless(test_stack) -> PaperlessAPI:
    """Brings up the docs stacklet and returns an API client.

    First call per coding session: ~30s (container boot + Celery warmup).
    Subsequent: no-op. Not torn down at session end — stop the test stack
    manually with tests/integration/test-env-down.sh.
    """
    _seed_test_instance_secrets()
    result = test_stack.run("up", "docs", timeout=240)
    if "_stderr" in result and not result.get("ok"):
        pytest.fail(
            f"`stack up docs` failed (code {result.get('_code')}):\n"
            f"{result.get('_stderr', '')}\n{result.get('_stdout', '')}"
        )

    from stack.secrets import TomlSecretStore
    store = TomlSecretStore(INSTANCE_DIR / ".stack" / "secrets.toml")
    token = store.get("docs", "API_TOKEN")
    if not token:
        pytest.fail(
            "No API_TOKEN in test instance secrets after `stack up docs`."
        )

    return PaperlessAPI(url="http://localhost:42020", token=token)


@pytest.fixture
def paperless_scope(paperless, scope) -> Scope:
    """Scope bound to Paperless cleanup — on teardown, every tag, doc
    type, correspondent, and document whose name starts with scope.uid
    is deleted."""
    scope.on_cleanup.append(lambda uid: cleanup_prefix(paperless, uid))
    return scope


# ── Matrix (shared, session-scoped) ──────────────────────────────────────

@pytest.fixture(scope="session")
def matrix(test_stack) -> dict:
    """Brings up the messages stacklet and logs in the Simpsons family.

    Returns a dict of MatrixCreds keyed by username. Session-scoped so
    the first test pays the ~40s Synapse boot and the rest pay nothing.
    Not torn down — stop with tests/integration/test-env-down.sh.
    """
    _seed_test_instance_secrets()
    result = test_stack.run("up", "messages", timeout=240)
    if "_stderr" in result and not result.get("ok"):
        pytest.fail(
            f"`stack up messages` failed (code {result.get('_code')}):\n"
            f"{result.get('_stderr', '')}\n{result.get('_stdout', '')}"
        )

    # The messages stacklet's setup CLI creates family accounts using
    # the seeded USER_<NAME>_PASSWORD values. Log them in once to capture
    # access tokens for use by tests.
    creds = {}
    for username in ("homer", "marge", "bart", "lisa"):
        creds[username] = login(
            server_name="test.local",
            username=username,
            password=username,  # seeded in _seed_test_instance_secrets
        )
    return creds


@pytest.fixture
async def homer(matrix):
    """An nio AsyncClient logged in as Homer. Function-scoped so every
    test gets a fresh client with clean sync state."""
    from nio import AsyncClient
    c = matrix["homer"]
    client = AsyncClient(c.homeserver, c.user_id)
    client.access_token = c.access_token
    client.device_id = c.device_id
    client.user_id = c.user_id
    try:
        yield client
    finally:
        await client.close()
