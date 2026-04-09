"""AI backend — verify the configured LLM endpoint is reachable.

The provider is chosen at configure time (on_configure.py):
  - "managed" → oMLX on localhost:42060, installed by on_install.py
  - "external" → user-provided endpoint, URL and key in stack.toml

This module just probes the configured endpoint. No detection waterfall,
no scanning, no interactive prompts for URLs.
"""

import dataclasses
import json
import ssl
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = ssl.CERT_NONE


@dataclasses.dataclass
class ProbeResult:
    reachable: bool
    needs_auth: bool = False
    models: list = dataclasses.field(default_factory=list)


def _probe(url: str, key: str = "") -> ProbeResult:
    """Hit {url}/models and return what we find."""
    models_url = f"{url.rstrip('/')}/models"
    headers = {}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(models_url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=3, context=_SSL) as resp:
            data = json.loads(resp.read().decode())
            model_ids = [m.get("id", "") for m in data.get("data", [])]
            return ProbeResult(reachable=True, models=model_ids)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return ProbeResult(reachable=False, needs_auth=True)
        return ProbeResult(reachable=False)
    except Exception:
        return ProbeResult(reachable=False)


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
    from stack.prompt import done, warn, dim

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


def _memory_warning(model_bytes: int):
    """Warn if system is already swapping heavily."""
    import re
    import subprocess
    try:
        out = subprocess.check_output(["sysctl", "vm.swapusage"], timeout=5,
                                      stderr=subprocess.DEVNULL).decode()
    except Exception:
        return
    m = re.search(r"used\s*=\s*([\d.]+)M", out)
    if not m:
        return
    swap_mb = float(m.group(1))
    if swap_mb > 1024:
        from stack.prompt import warn
        swap_gb = swap_mb / 1024
        warn(f"System is already using {swap_gb:.1f} GB swap. "
             f"Consider a smaller model or close some apps.")


# ── Model availability ───────────────────────────────────────────────────────


def ensure_model(repo_root: Path, model_id: str, interactive: bool = True,
                  ai_cfg: dict | None = None) -> dict:
    """Ensure a model is available. For managed oMLX, offers to download.
    For external endpoints, just checks and warns.

    Returns:
        {"loaded": True, "model": str} on success
        {"warning": str} if model not found on external endpoint
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

    # ── External provider: just warn, don't try to manage models ────
    if provider == "external":
        available = ", ".join(probe.models[:5]) if probe.models else "none"
        warn(f"Default model '{model_name}' not found on endpoint.")
        dim(f"  Available: {available}")
        dim(f"  Load it on your server, or change [ai] default in stack.toml.")
        return {"warning": f"Model '{model_name}' not found. Available: {available}"}

    # ── Managed oMLX: offer to download ─────────────────────────────
    from omlx import OMLXClient, is_omlx, repo_id_to_model_id

    admin_url = base_url.rstrip("/").replace("/v1", "")
    if not is_omlx(admin_url):
        return {
            "error": f"Model '{model_id}' not loaded.\n"
                     f"Load it manually in your LLM app."
        }

    client = OMLXClient(admin_url, api_key)
    if not client.login():
        return {"error": "Cannot authenticate to oMLX admin API. Check [ai] openai_key."}

    # Check if downloaded but not loaded
    from stack.prompt import TEAL

    downloaded = client.list_downloaded()
    for m in downloaded:
        m_id = m.get("id", "")
        if model_name in m_id or m_id in model_name:
            if interactive:
                nl()
                out(f"Model {ORANGE}{m_id}{RESET} is downloaded but not loaded.")
                if confirm("Load it now?"):
                    return _load_model(client, m_id)
                dim(f"Load skipped. Run './stack ai download {m_id}' to load later.")
                return {"skipped": True, "model": m_id, "downloaded": True}
            return {"error": f"Model '{m_id}' is downloaded but not loaded. Load it in oMLX."}

    # Check HuggingFace and offer download
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

    nl()
    out(f"Model {ORANGE}{model_id}{RESET} is not downloaded.")
    out(f"Size: {TEAL}{info.size_formatted}{RESET}")
    _memory_warning(info.size)
    nl()
    warn("Download can take a while depending on your connection.")
    nl()

    if not confirm(f"Download from HuggingFace?"):
        nl()
        dim("Download skipped. You can change the model or download later:")
        dim(f"  • stack.toml [ai] has commented alternatives — uncomment to switch")
        dim(f"  • Run './stack setup ai' to re-run this check")
        return {"skipped": True, "model": model_id}

    return _download_and_load(client, model_id, info)


def _load_model(client, model_id: str) -> dict:
    """Load a downloaded model."""
    from stack.prompt import Spinner

    with Spinner(f"Loading {model_id}") as spinner:
        if client.load_model(model_id):
            return {"loaded": True, "model": model_id}
        spinner.fail()
    return {"error": f"Failed to load model '{model_id}'."}


def _download_and_load(client, repo_id: str, info) -> dict:
    """Download a model from HuggingFace with progress display, then load it."""
    import time
    from stack.prompt import nl, out, dim, done, warn, ORANGE, TEAL, RESET
    from omlx import repo_id_to_model_id

    task = client.start_download(repo_id)
    if task is None:
        return {"error": f"Failed to start download for '{repo_id}'."}

    nl()
    out(f"Downloading {ORANGE}{repo_id}{RESET}...")
    dim("Press Ctrl+C to cancel (download continues in background)")
    nl()

    last_progress = -1
    try:
        while True:
            task = client.get_task(task.task_id)
            if task is None:
                return {"error": "Lost connection to download task."}

            if task.status == "completed":
                _render_progress(100.0, info.size, info.size)
                print()
                done("Download complete")
                break

            if task.status == "failed":
                print()
                return {"error": f"Download failed: {task.error or 'unknown error'}"}

            if task.progress != last_progress:
                _render_progress(task.progress, task.downloaded_size, task.total_size or info.size)
                last_progress = task.progress

            time.sleep(0.5)

    except KeyboardInterrupt:
        print()
        nl()
        dim("Download continues in background.")
        dim("Run 'stack ai models' to check when it's ready.")
        return {"cancelled": True, "model": repo_id, "task_id": task.task_id}

    model_id = repo_id_to_model_id(repo_id)
    return _load_model(client, model_id)


def _render_progress(percent: float, downloaded: int, total: int):
    """Render a progress bar."""
    from stack.prompt import ORANGE, TEAL, DIM, RESET

    width = 30
    filled = int(width * percent / 100)
    bar = "\u2588" * filled + "\u2591" * (width - filled)

    dl_str = _format_size(downloaded)
    total_str = _format_size(total)

    print(f"\r  {ORANGE}{bar}{RESET}  {TEAL}{percent:5.1f}%{RESET}  {DIM}{dl_str} / {total_str}{RESET}",
          end="", flush=True)


def _format_size(size: int) -> str:
    """Format bytes as human-readable size."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"
