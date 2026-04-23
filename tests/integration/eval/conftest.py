"""Eval-only fixtures — real classifier wired to a live AI stacklet.

Lives one level deeper than `tests/integration/conftest.py` so it
inherits all the test-rig plumbing (paperless, paperless_scope, scope,
bdd) and only adds what the eval needs:

  - `eval_ai_config` — pulls model + endpoint config out of env, fails
    fast when the user hasn't picked a model.
  - `bot_paperless`  — the bot's async PaperlessAPI client (upload +
    OCR-task polling lives there, not on the test client).
  - `ai_classifier`  — a real `Classifier` aimed at the AI stacklet,
    with `stack.models._DEFAULT_MODEL` patched to the chosen model so
    `resolve_model("archivist-bot/classifier")` returns it.
  - `eval_upload`    — uploads a file, waits for OCR, returns the doc.

Why bypass the test stack.toml's `[ai]` block: the test instance
points OPENAI_URL at a pytest-httpserver mock for determinism. The
eval is the one place we *want* the real model, so its fixtures
construct a Classifier directly with a real URL rather than mutating
stack.toml.
"""

from __future__ import annotations

import asyncio
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import aiohttp
import pytest

# Bot package lives outside lib/ — has to be on sys.path before we can
# import `pipeline`. Mirrors what the bot-runner container does at boot.
_BOT_DIR = Path(__file__).resolve().parents[3] / "stacklets" / "docs" / "bot"
if str(_BOT_DIR) not in sys.path:
    sys.path.insert(0, str(_BOT_DIR))

from tests.integration.eval._artifacts import RunArtifacts


def _keep_docs() -> bool:
    """Whether the user passed `EVAL_KEEP_DOCS=1` to inspect docs in
    Paperless after the run. The artifact dir under `runs/<stamp>/` is
    always written; this flag controls whether the uploaded docs also
    survive in Paperless."""
    return os.environ.get("EVAL_KEEP_DOCS", "").lower() in ("1", "true", "yes")


# ── Configuration ────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def eval_ai_config() -> dict[str, str]:
    """Resolve which model + endpoint the eval will hit.

    `AI_DEFAULT_MODEL` is required: the eval is a quality measurement,
    auto-picking a model would defeat the purpose. Endpoint defaults to
    the AI stacklet's local oMLX port; key defaults empty (local AI is
    typically unauthenticated).
    """
    model = os.environ.get("AI_DEFAULT_MODEL", "").strip()
    if not model:
        pytest.fail(
            "AI_DEFAULT_MODEL is not set. The eval needs a concrete model "
            "to measure — pick one from your AI stacklet, e.g.\n\n"
            "    AI_DEFAULT_MODEL=qwen3-14b stacktests eval\n\n"
            "List available models: `stack ai models`."
        )
    return {
        "model": model,
        "url":   os.environ.get("EVAL_AI_URL", "http://localhost:42060/v1"),
        "key":   os.environ.get("EVAL_AI_KEY", ""),
    }


@pytest.fixture(scope="session", autouse=True)
def _patch_model_resolver(eval_ai_config) -> None:
    """Make `resolve_model("archivist-bot/...")` return the eval's model.

    `stack.models` reads env at import time; the bot module has already
    imported `resolve_model` by the time our fixtures run. Setting the
    module globals directly works because `resolve_model` reads them at
    call time, not at import time.
    """
    import stack.models as _models
    _models._DEFAULT_MODEL = eval_ai_config["model"]
    _models._MODELS = {}


@pytest.fixture(scope="session", autouse=True)
def _ai_endpoint_reachable(eval_ai_config) -> None:
    """Pre-flight: make sure the AI stacklet is up before the first case.

    Without this every case would burn 5+ minutes of OCR + LLM timeouts
    just to discover `stack up ai` was never run. A single GET /models
    is the cheapest reachability test that proves the endpoint is alive.
    """
    probe = eval_ai_config["url"].rstrip("/") + "/models"
    req = urllib.request.Request(probe)
    if eval_ai_config["key"]:
        req.add_header("Authorization", f"Bearer {eval_ai_config['key']}")
    try:
        with urllib.request.urlopen(req, timeout=5):
            pass
    except (urllib.error.URLError, OSError) as e:
        pytest.fail(
            f"AI endpoint unreachable at {probe}: {e}.\n"
            f"Bring it up with `stack up ai` before running the eval."
        )


# ── Bot's PaperlessAPI (async — has upload + wait_task) ──────────────────

@pytest.fixture
async def bot_paperless(paperless):
    """The bot's async PaperlessAPI — exposes upload + wait_task.

    The synchronous `paperless` fixture from the parent conftest is
    enough for entity reads/cleanup, but the upload+OCR cycle wants
    the async client the bot itself uses. Reusing the bot's class
    keeps the eval honest: the same upload code path the production
    archivist hits.
    """
    from pipeline import PaperlessAPI as BotPaperlessAPI

    async with aiohttp.ClientSession() as session:
        yield BotPaperlessAPI(session, paperless.url, paperless.token)


# ── AI Classifier (real, not stubbed) ────────────────────────────────────

@pytest.fixture
async def ai_classifier(eval_ai_config):
    """Real `Classifier` aimed at the live AI stacklet."""
    from pipeline import Classifier

    async with aiohttp.ClientSession() as session:
        yield Classifier(
            http=session,
            url=eval_ai_config["url"],
            key=eval_ai_config["key"],
            bot_name="archivist-bot",
        )


# ── Upload + OCR helper ──────────────────────────────────────────────────

@pytest.fixture
def eval_upload(bot_paperless, paperless_scope):
    """Returns `await upload(path, *, ocr_timeout=120)` → Paperless doc dict.

    Uploads the file, polls Paperless's task queue until OCR finishes,
    then refetches the doc so the caller gets the post-OCR `content`
    field. Title is prefixed with the test scope uid so cleanup nukes
    only this run's docs.

    The returned dict is the same shape `enrich_document` consumes —
    pass it straight in.
    """
    async def _upload(path: Path, *, ocr_timeout: int = 120) -> dict[str, Any]:
        # Prefix with scope uid for cleanup — `paperless_scope` deletes
        # every Paperless entity whose name starts with the uid.
        scoped_name = f"{paperless_scope.uid}-{path.name}"
        data = path.read_bytes()
        content_type = "application/pdf" if path.suffix.lower() == ".pdf" else None

        task_id = await bot_paperless.upload(
            scoped_name, data, content_type=content_type,
        )
        if not task_id:
            pytest.fail(f"Upload failed for {path.name} — see logs above")

        doc_id = await bot_paperless.wait_task(task_id, timeout=ocr_timeout)
        if not doc_id:
            pytest.fail(
                f"OCR did not complete within {ocr_timeout}s for {path.name}"
            )

        # Refetch so `content` (OCR text) is populated.
        doc = await bot_paperless.get_doc(doc_id)
        if not doc:
            pytest.fail(f"Doc #{doc_id} disappeared after OCR")
        return doc

    return _upload


# ── Per-run artifact directory ───────────────────────────────────────────

@pytest.fixture(scope="session")
def eval_run_dir(eval_ai_config) -> RunArtifacts:
    """Open a fresh run directory and yield a writer.

    Always created — artifacts (OCR text, classification JSON, scorecard)
    are the primary diagnostic surface, useful even when EVAL_KEEP_DOCS
    is off. The session-end summary is written by `_finalize_run`.
    """
    return RunArtifacts.open(model=eval_ai_config["model"])


@pytest.fixture(scope="session", autouse=True)
def _finalize_run(eval_run_dir):
    """Write summary.{txt,json} once every case has reported."""
    yield
    if eval_run_dir.cases:
        eval_run_dir.finalize()
        print(f"\n  Run artifacts: {eval_run_dir.root}")
        print(f"  Summary:       {eval_run_dir.root / 'summary.txt'}\n")


# ── Override: keep Paperless docs when EVAL_KEEP_DOCS=1 ──────────────────
#
# The parent conftest's `paperless_scope` registers a teardown that
# nukes every entity prefixed with the scope uid. For diagnostic runs
# (--keep-docs) we want the docs to survive so the user can browse OCR
# text and classification artifacts in Paperless's UI. We override the
# fixture here to skip the cleanup hook.

@pytest.fixture
def paperless_scope(paperless, scope):  # noqa: F811 — intentional override
    """Eval-aware paperless_scope.

    Default: behaves like the parent fixture (cleanup on teardown).
    With EVAL_KEEP_DOCS=1: skips the cleanup hook so uploaded docs
    survive in Paperless for inspection. The scope.uid still prefixes
    titles, so a later `cleanup` sweep (or the next test session) wipes
    them.
    """
    from tests.integration.paperless import cleanup_prefix

    if _keep_docs():
        print(f"\n  EVAL_KEEP_DOCS=1 — uploaded docs will survive "
              f"under prefix '{scope.uid}'")
    else:
        scope.on_cleanup.append(lambda uid: cleanup_prefix(paperless, uid))
    return scope
