"""`[ai]` for the engine the stack manages on this Mac.

    from ..local_mode import MANAGED_ENGINE, managed_engine

    managed_engine()["openai_url"]    # "http://localhost:42060/v1"

`stack ai switch managed` writes it before it runs `stack up ai`. Its key
is oMLX's own (`omlx.managed_key`), never a fixed word.
"""

from .omlx import managed_key

# `[ai]` for the engine the stack manages on this Mac, and its Whisper.
# Its key is oMLX's own (`managed_engine`), never a fixed word.
MANAGED_ENGINE = {
    "provider": "managed",
    "openai_url": "http://localhost:42060/v1",
    "whisper_url": "http://localhost:42062/v1",
    "whisper_key": "",
}


def managed_engine() -> dict:
    """`[ai]` for the engine the stack manages, with the key its oMLX
    takes (`omlx.managed_key`): the placeholder before oMLX is installed,
    whose install then gives both a generated key."""
    return {**MANAGED_ENGINE, "openai_key": managed_key()}
