"""AI backend — verify the configured LLM endpoint is reachable.

The provider is chosen at configure time (on_configure.py):
  - "managed" → oMLX on localhost:42060, installed by prepare.py
  - "external" → user-provided endpoint, URL and key in stack.toml

This module just probes the configured endpoint. No detection waterfall,
no scanning, no interactive prompts for URLs.
"""

import tomllib
from pathlib import Path

# Reachability probe lives in the framework so other stacklets (and the host
# CLI's setup flow) can reuse it without rewriting the urllib dance.
from stack.ai.probe import probe as _probe


def normalize_url(url: str) -> str:
    """What people type, as the OpenAI clients expect it.

    `192.168.1.20:11434` and `https://api.example.com` are both how an
    address gets written down; the clients need a scheme and the `/v1`
    base path.
    """
    url = url.strip().rstrip("/")
    if not url.startswith("http"):
        url = f"http://{url}"
    if not url.endswith("/v1"):
        url = f"{url}/v1"
    return url


def _load_ai_config(repo_root: Path) -> dict:
    """Read [ai] section from stack.toml."""
    toml_path = repo_root / "stack.toml"
    if not toml_path.exists():
        return {}
    try:
        with open(toml_path, "rb") as f:
            return tomllib.load(f).get("ai", {})
    except Exception:
        return {}


def ensure_backend(repo_root: Path, interactive=True) -> dict:
    """Verify the configured LLM endpoint is reachable.

    Returns {"url": str, "key": str} or {"error": str}.
    """
    from stack.prompt import done, dim

    ai_cfg = _load_ai_config(repo_root)
    url = ai_cfg.get("openai_url", "")
    key = ai_cfg.get("openai_key", "")
    provider = ai_cfg.get("provider", "managed")

    if not url:
        return {"error": "No LLM endpoint configured in stack.toml [ai] openai_url."}

    probe = _probe(url, key)

    if probe.reachable:
        done(f"LLM endpoint reachable: {url}")
        if probe.models:
            dim(f"  {len(probe.models)} model{'s' if len(probe.models) != 1 else ''} loaded")
        return {"url": url, "key": key}

    if probe.needs_auth and not key:
        return {"error": f"LLM endpoint at {url} requires an API key. Set [ai] openai_key in stack.toml."}

    label = "external endpoint" if provider == "external" else "oMLX"
    return {"error": f"Cannot reach {label} at {url}. Make sure the server is running."}


# ── Model availability ───────────────────────────────────────────────────────


def ensure_model(repo_root: Path, model_id: str, interactive: bool = True,
                  ai_cfg: dict | None = None) -> dict:
    """Ensure a model is loaded on the AI server, downloading it when the
    server is the engine the stack runs (`model_server.py`).

    The engine the stack runs downloads it without a question, because
    choosing that engine was the consent, through the preparation's
    downloader (`model_download.fetch`). A server the stack does not manage, the oMLX app
    included, gets a warning: its admin loads models there, and famstack
    downloads nothing onto it.

    Returns:
        {"loaded": True, "model": str} on success
        {"warning": str} if the model is missing on a server that is not an oMLX
        {"skipped": True, "model": str, "downloaded": True} when the admin did not load it
        {"error": str} on failure
    """
    from stack.prompt import nl, out, dim, done, warn, confirm

    if ai_cfg is None:
        ai_cfg = _load_ai_config(repo_root)
    base_url = ai_cfg.get("openai_url", "")
    api_key = ai_cfg.get("openai_key", "")
    provider = ai_cfg.get("provider", "managed")

    if not base_url:
        return {"error": "No LLM endpoint configured."}

    # Strip to model name for matching
    model_name = model_id.split("/")[-1] if "/" in model_id else model_id

    from stack.prompt import ORANGE, RESET

    probe = _probe(base_url, api_key)
    if not probe.reachable:
        return {"error": f"LLM endpoint not reachable at {base_url}"}

    # Check if loaded
    for loaded in probe.models:
        if model_name in loaded or loaded in model_name:
            done(f"Model ready: {ORANGE}{loaded}{RESET}")
            return {"loaded": True, "model": loaded}

    from .model_server import model_server

    # ── A server the stack does not manage: just warn ───────────────
    # Its admin loads models there; famstack downloads and loads nothing
    # on it (route 2), whether it is an oMLX or not.
    if provider != "managed":
        available = ", ".join(probe.models[:5]) if probe.models else "none"
        warn(f"Default model '{model_name}' not found on endpoint.")
        dim(f"  Available: {available}")
        dim("  Load it on your server, or change [ai] default in stack.toml.")
        return {"warning": f"Model '{model_name}' not found. Available: {available}"}

    server = model_server(provider, base_url, api_key)

    # ── The engine the stack runs: offer to download ────────────────
    client = server.client()
    if not client.login():
        return {"error": "Cannot authenticate to oMLX admin API. Check [ai] openai_key."}

    # Check if downloaded but not loaded

    downloaded = client.list_downloaded()
    for m in downloaded:
        m_id = m.get("id", "")
        if model_name in m_id or m_id in model_name:
            if interactive:
                nl()
                out(f"Model {ORANGE}{m_id}{RESET} is downloaded but not loaded.")
                if confirm("Load it now?", default=True):
                    return _load_model(client, m_id)
                dim(f"Load skipped. Run './stack ai download {m_id}' to load later.")
                return {"skipped": True, "model": m_id, "downloaded": True}
            return {"error": f"Model '{m_id}' is downloaded but not loaded. Load it in oMLX."}

    # Not downloaded: from Hugging Face, by its repo id
    if "/" not in model_id:
        return {
            "error": f"Model '{model_id}' not found.\n"
                     f"Use full HuggingFace repo ID (e.g. mlx-community/{model_id})."
        }

    info = client.get_model_info(model_id)
    if info is None:
        return {"error": f"Model '{model_id}' not found on HuggingFace."}

    if not interactive:
        return {
            "error": f"Model '{model_id}' needs to be downloaded ({info.size_formatted}).\n"
                     f"Run interactively or download manually in oMLX."
        }

    return _download_and_load(client, model_id)


def _load_model(client, model_id: str) -> dict:
    """Load a downloaded model."""
    from stack.prompt import Spinner

    with Spinner(f"Loading {model_id}") as spinner:
        if client.load_model(model_id):
            return {"loaded": True, "model": model_id}
        spinner.fail()
    return {"error": f"Failed to load model '{model_id}'."}


def _download_and_load(client, repo_id: str) -> dict:
    """Download a model onto the engine the stack runs, as the preparation
    does (`model_download.fetch`: the space check, a running download
    followed, its percent), then load it."""
    from stack.output import TerminalOutput
    from .model_download import NoSpace, fetch
    from .omlx import repo_id_to_model_id

    try:
        task = fetch(TerminalOutput(), client, repo_id, repo_id)
    except (NoSpace, RuntimeError) as e:
        return {"error": str(e)}
    if task is not None and task.status != "completed":
        return {"error": f"Download failed: {task.error or task.status}"}
    return _load_model(client, repo_id_to_model_id(repo_id))
