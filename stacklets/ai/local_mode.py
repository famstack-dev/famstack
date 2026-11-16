"""`[ai]` for the engine the stack manages on this Mac, and its run.

    from ..local_mode import MANAGED_ENGINE, bring_up, managed_engine

    managed_engine(data_dir)["openai_url"]    # "http://localhost:42060/v1"
    bring_up(config)                  # 0 once `stack up ai` brought it up

`stack ai switch managed` writes `[ai]` before it brings the engine up
with `stack up ai`, the run that installs what is missing and ends by
asking the AI one question. Its key is famstack's oMLX's own, from its
settings file (`omlx.own_key`), never another server's. Its model is one
famstack's oMLX holds, or one it downloads (`engine_model`): a server
elsewhere names its models by its own ids (`llama3.1:8b`,
`openai/gpt-4o`), which famstack's oMLX neither holds nor can download.
"""

import os
import subprocess
import sys
from pathlib import Path

from stack.profile import profile_of
from stack.stack import AI_MANAGED

from .omlx import own_key
from .omlx_service import holds

__all__ = ["MANAGED_ENGINE", "managed_engine", "engine_model", "replaced", "bring_up"]

# `[ai]` for the engine the stack manages on this Mac, and its Whisper.
# Its key is oMLX's own (`managed_engine`), never a fixed word.
MANAGED_ENGINE = {
    "provider": AI_MANAGED,
    "openai_url": "http://localhost:42060/v1",
    "whisper_url": "http://localhost:42062/v1",
    "whisper_key": "",
}


def managed_engine(data_dir: str | Path) -> dict:
    """`[ai]` for the engine the stack manages, with the key of famstack's
    oMLX from its settings file (`omlx.own_key`, which makes and writes
    one when the file holds none). Never the key of another server.

        managed_engine("/Users/homer/famstack-data")["openai_key"]    # "q8re-4kxm-…"
    """
    return {**MANAGED_ENGINE, "openai_key": own_key(data_dir)}


def engine_model(stack_config: dict, chosen: str = "") -> str:
    """`[ai] default` for the engine the stack manages, from the parsed
    `stack.toml`: `chosen` (a `--model`) when given, else the current
    default when famstack's oMLX holds it (`omlx_service.holds`, a bare id
    or a repo), else the chat model this Mac's memory profile recommends,
    which the start downloads. The owner of the models decides, not the
    shape of the id: `openai/gpt-4o` looks like a repo and is another
    server's model. Raises ValueError for a `[core] profile` that names no
    profile.

        engine_model({"ai": {"default": "llama3.1:8b"}})
        # "mlx-community/Qwen3.5-9B-MLX-4bit" on a 16 GB Mac
        engine_model({"ai": {"default": "Qwen3.5-9B-MLX-4bit"}})
        # "Qwen3.5-9B-MLX-4bit" while famstack's oMLX holds it
    """
    if chosen:
        return chosen
    current = str(stack_config.get("ai", {}).get("default", ""))
    return current if holds(current) else profile_of(stack_config).chat_model


def replaced(stack_config: dict, model: str) -> str:
    """The note that `model` replaces the current `[ai] default`, and why;
    "" when nothing is replaced.

        replaced({"ai": {"default": "llama3.1:8b"}}, "mlx-community/Qwen3.5-9B-MLX-4bit")
        # "famstack's oMLX does not hold llama3.1:8b; it downloads mlx-community/Qwen3.5-9B-MLX-4bit"
    """
    current = str(stack_config.get("ai", {}).get("default", ""))
    if not current or current == model:
        return ""
    return f"famstack's oMLX does not hold {current}; it downloads {model}"


def bring_up(config: dict) -> int:
    """Run `stack up ai` on this instance and return its exit code. Its
    output goes to stderr, so that a `--json` answer stays the only thing
    on stdout. `config` is a CLI command's config (`repo_root`,
    `instance_dir`).

        bring_up(config)    # 0, or the run's exit code when it failed
    """
    stack = Path(config["repo_root"]) / "stack"
    env = {**os.environ, "STACK_DIR": config["instance_dir"]}
    return subprocess.run([str(stack), "up", "ai"], env=env, stdout=sys.stderr).returncode
