"""Integration test fixtures.

The rig now runs against the *repo root* as a test instance — same
layout as a real famstack (`stack.toml`, `users.toml`, `.stack/` at
the repo root). A sentinel file `.stack/.test-instance` marks the
repo as test-owned; `_seed_secrets.seed()` refuses to clobber a
non-test setup, so running the rig over a real user's stack errors
out with a cleanup hint instead of silently overwriting it.

Stacklets are spun up on demand by the fixtures below — `paperless`
brings up `docs`, `matrix` brings up `messages`, `code` brings up
Forgejo. They stay running across pytest invocations; tear them down
between coding sessions with `tests/integration/stacktests cleanup`.

Per-test isolation is by prefix: every entity a test creates in a
backend carries its scope uid, and teardown deletes only what matches.
Tests run in parallel as long as they each ask for the same scope.

External services exercised for real: Paperless, Postgres, Redis,
Synapse, Forgejo. Only OpenAI is mocked (determinism trumps realism
for classification output).
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
# The test instance IS the repo root. Kept as a distinct name so
# `.stack/` / `stack.toml` / `users.toml` references stay readable.
INSTANCE_DIR = REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))

from tests.integration.paperless import PaperlessAPI, cleanup_prefix
from tests.integration.matrix import MatrixCreds, login
from tests.integration.forgejo import ForgejoAPI, cleanup_mirror_files
from tests.integration.bdd import BDDLog
from tests.integration._seed_secrets import (
    seed as _seed_test_instance_secrets,
    TestInstanceConflict,
)


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
    """Thin wrapper around the CLI pointed at the test instance.

    The test instance IS the repo root, so we don't set STACK_DIR.
    `_seed_test_instance_secrets()` installs the test-owned stack.toml
    and sentinel marker up front.
    """

    instance_dir: Path = INSTANCE_DIR

    def _env(self) -> dict:
        return {
            **os.environ,
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
    need to exercise the framework API directly (is_healthy, etc.).
    Repo root and instance dir are the same in the repo-root rig."""
    from stack.cli import create_stack
    return create_stack(REPO_ROOT, REPO_ROOT)


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

@pytest.fixture
def sample_invoice_pdf(scope) -> bytes:
    """A minimal single-page PDF with extractable text — enough for
    Paperless OCR to produce recognizable content the LLM can classify.

    Function-scoped with the test's scope uid baked into the rendered
    text. Different bytes per run → Paperless's content-hash duplicate
    check doesn't fire on re-runs against a retained instance (where
    the prior doc is sitting in the trash)."""
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
              "Zahlungsziel: 15.03.2026\n\n"
              f"Ref: {scope.uid}",
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
    try:
        _seed_test_instance_secrets()
    except TestInstanceConflict as e:
        pytest.fail(str(e))
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
    try:
        _seed_test_instance_secrets()
    except TestInstanceConflict as e:
        pytest.fail(str(e))
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


# ── Forgejo / code stacklet (shared, session-scoped) ─────────────────────

# The documents repo now lives under the Forgejo org the archivist
# provisions (`mirror_org` in `stacklets/docs/bot/bot.toml`, default
# "family"). The bot keeps its own Forgejo user identity for commit
# authorship, but the repo owner is the org so admins see it in their
# dashboards.
FORGEJO_DOCS_OWNER = "family"
FORGEJO_DOCS_REPO = "documents"


@pytest.fixture(scope="session")
def code(test_stack) -> ForgejoAPI:
    """Brings up the code stacklet (Forgejo) and returns an admin API client.

    `up core` first so any new core env (e.g. CODE_URL for the bot
    runner) is rendered into the .env file and the bot-runner restarts
    with it. `up code` then boots Forgejo itself.

    First call per coding session: ~40s. Subsequent: no-op.
    """
    try:
        _seed_test_instance_secrets()
    except TestInstanceConflict as e:
        pytest.fail(str(e))
    for target, timeout in (("core", 60), ("code", 240)):
        result = test_stack.run("up", target, timeout=timeout)
        if "_stderr" in result and not result.get("ok"):
            pytest.fail(
                f"`stack up {target}` failed (code {result.get('_code')}):\n"
                f"{result.get('_stderr', '')}\n{result.get('_stdout', '')}"
            )

    from stack.secrets import TomlSecretStore
    store = TomlSecretStore(INSTANCE_DIR / ".stack" / "secrets.toml")
    admin_password = store.get("_", "ADMIN_PASSWORD") or store.get("global", "ADMIN_PASSWORD")
    if not admin_password:
        pytest.fail("No ADMIN_PASSWORD in test instance secrets for Forgejo.")

    return ForgejoAPI(
        url="http://localhost:42040",
        admin_user="stackadmin",
        admin_password=admin_password,
    )


@pytest.fixture
def mirror_scope(code, scope) -> Scope:
    """Scope bound to mirror cleanup — on teardown, every file in the
    `documents` repo whose frontmatter title starts with scope.uid is
    deleted. The repo + bot user + README survive between tests."""
    scope.on_cleanup.append(
        lambda uid: cleanup_mirror_files(code, FORGEJO_DOCS_OWNER, FORGEJO_DOCS_REPO, uid)
    )
    return scope
