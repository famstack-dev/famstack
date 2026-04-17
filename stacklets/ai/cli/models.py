"""stack ai models — list available AI models."""

HELP = "Available models"

import sys
from pathlib import Path


def run(args, stacklet, config):
    # backend.py lives next to cli/ in the stacklet dir
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from backend import ensure_backend, _probe

    repo_root = Path(config.get("repo_root", "."))
    result = ensure_backend(repo_root, interactive=sys.stdin.isatty())
    if "error" in result:
        return result

    base_url = result["url"]
    api_key = result.get("key", "")

    # Probe models from the detected endpoint
    probe = _probe(base_url, api_key)
    if not probe.reachable:
        return {"error": f"AI endpoint unavailable at {base_url}"}

    # Default model from the hook contract — framework has already
    # parsed stack.toml, no need to re-read it from disk.
    default_model = config.get("stack", {}).get("ai", {}).get("default", "")

    models = [{"id": m} for m in probe.models]
    default_name = default_model.split("/")[-1] if "/" in default_model else default_model

    if sys.stderr.isatty():
        from stack.prompt import nl, out, dim, done, warn, GREEN, ORANGE, RESET

        nl()
        # Check if default is loaded
        default_loaded = any(default_name in m for m in probe.models) if default_name else False

        for m in probe.models:
            is_default = default_name and default_name in m
            marker = f" {GREEN}(default){RESET}" if is_default else ""
            out(f"  {ORANGE}{m}{RESET}{marker}")

        if default_model and not default_loaded:
            nl()
            warn(f"Default model not loaded: {default_model}")
            dim(f"  Run: stack ai download {default_model}")

        nl()

    return {"models": models, "count": len(models), "default": default_model}
