"""AI model resolution.

Bots request models by role path ("archivist/classifier"). The resolver
walks a fallback chain through [ai.models] in stack.toml to find the
concrete model name:

    resolve_model("archivist/classifier")
      1. ai.models.archivist.classifier — task-specific override
      2. ai.models.archivist            — bot-level default
      3. ai.default                     — global fallback

Config arrives via environment variables because bots run inside Docker
and can't read stack.toml directly:
  AI_DEFAULT_MODEL — the [ai].default value
  AI_MODELS_JSON   — the [ai.models] section serialized as JSON
"""

import json
import os

_DEFAULT_MODEL = os.environ.get("AI_DEFAULT_MODEL", "")
_MODELS: dict = {}

_raw = os.environ.get("AI_MODELS_JSON", "")
if _raw:
    try:
        _MODELS = json.loads(_raw)
    except json.JSONDecodeError:
        pass


def resolve_model(path: str) -> str:
    """Resolve a symbolic model path to a concrete model name.

    Fallback chain for resolve_model("archivist/classifier"):
      1. archivist.classifier — task-specific override
      2. archivist            — bot-level default
      3. default              — global fallback

    Raises ValueError when nothing is configured — better to catch it
    here with a clear message than get a cryptic API error downstream.
    """
    parts = path.split("/", 1)

    if len(parts) == 2:
        bot, task = parts
        specific = _MODELS.get(f"{bot}.{task}")
        if specific:
            return specific
        bot_default = _MODELS.get(bot)
        if bot_default:
            return bot_default
    elif len(parts) == 1:
        task_model = _MODELS.get(parts[0])
        if task_model:
            return task_model

    if _DEFAULT_MODEL:
        return _DEFAULT_MODEL

    raise ValueError(
        f"No model configured for '{path}'. "
        f"Set [ai].default in stack.toml or add [ai.models]."
        f"{path.replace('/', '.')} for a specific override."
    )
