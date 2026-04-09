"""stack ai download <model> — download a model from HuggingFace."""

HELP = "Download model from HuggingFace (oMLX only)"

import sys
from pathlib import Path

# oMLX defaults — download works even without stack.toml
_DEFAULT_URL = "http://localhost:42060"
_DEFAULT_KEY = "local"


def run(args, stacklet, config):
    if not args:
        return {
            "error": "Usage: stack ai download <repo_id>\n"
                     "Example: stack ai download mlx-community/gemma-4-26b-a4b-it-4bit"
        }

    model_id = args[0]

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from backend import ensure_model, _load_ai_config

    repo_root = Path(config.get("repo_root", "."))
    ai_cfg = _load_ai_config(repo_root)

    # Fall back to managed oMLX defaults when stack.toml is missing or unconfigured
    if not ai_cfg.get("openai_url"):
        ai_cfg.setdefault("openai_url", f"{_DEFAULT_URL}/v1")
        ai_cfg.setdefault("openai_key", _DEFAULT_KEY)
        ai_cfg.setdefault("provider", "managed")

    return ensure_model(repo_root, model_id, interactive=True, ai_cfg=ai_cfg)
