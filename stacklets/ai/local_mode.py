"""`[ai]` for the engine the stack manages on this Mac, and its run.

    from ..local_mode import MANAGED_ENGINE, bring_up, managed_engine

    managed_engine(data_dir)["openai_url"]    # "http://localhost:42060/v1"
    bring_up(config)                  # 0 once `stack up ai` brought it up

`stack ai switch managed` writes `[ai]` before it brings the engine up
with `stack up ai`, the run that installs what is missing and ends by
asking the AI one question. Its key is oMLX's own (`omlx.managed_key`),
never a fixed word.
"""

import os
import subprocess
import sys
from pathlib import Path

from .omlx import managed_key

__all__ = ["MANAGED_ENGINE", "managed_engine", "bring_up"]

# `[ai]` for the engine the stack manages on this Mac, and its Whisper.
# Its key is oMLX's own (`managed_engine`), never a fixed word.
MANAGED_ENGINE = {
    "provider": "managed",
    "openai_url": "http://localhost:42060/v1",
    "whisper_url": "http://localhost:42062/v1",
    "whisper_key": "",
}


def managed_engine(data_dir: str | Path, stack_key: str = "") -> dict:
    """`[ai]` for the engine the stack manages, with the key famstack's
    oMLX in `data_dir` takes (`omlx.managed_key`): its own key, else the
    real key `stack_key` (`[ai] openai_key`) that its first start carries
    over, else the placeholder before oMLX is installed, whose install then
    gives both a generated key.

        managed_engine("/Users/homer/famstack-data")["openai_key"]    # "local" before the install
    """
    return {**MANAGED_ENGINE, "openai_key": managed_key(data_dir, stack_key)}


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
