"""Write the settings nanobot cannot take as a `${VAR}` into the agent's config.

    python3 agent_config.py <config.json>   # entrypoint.sh, after it copies the image's config

The image's `config.json` names most settings as `${VAR}`, which nanobot
fills from the environment when it loads the file. Two settings cannot
work that way, so this script writes them as values at every start:

- **The household language** (`AGENT_LANGUAGE`). nanobot checks
  `transcription.language` against `^[a-z]{2,3}$` before it resolves
  variables, and refuses to start on `${AGENT_LANGUAGE}`. Two letters, as
  the archivist sends it; none at all lets Whisper detect the language.
- **Transcription off without an address** (`AGENT_WHISPER_URL` empty, as
  the stack renders it with speech off or without an AI). nanobot reads an
  empty `api_base` as "the provider's default", and for the `openai`
  provider that is api.openai.com.

SAFETY: without a transcription address, transcription is switched off and
its key removed, so nanobot never builds a client. Otherwise a voice message
and the AI's key would leave the house for a public service.
"""

from __future__ import annotations

import json
import os
import sys

__all__ = ["settle", "main"]


def settle(config: dict, *, language: str, whisper_url: str) -> dict:
    """The agent's `config` with the language and the transcription switch
    written in. Changes `config` in place and returns it.

        settle({"transcription": {"provider": "openai"}},
               language="de", whisper_url="")
        # {"transcription": {"provider": "openai", "language": "de",
        #                    "enabled": False}}
    """
    transcription = config.setdefault("transcription", {})
    language = language.strip().lower()[:2]
    if language.isalpha():
        transcription["language"] = language
    if not whisper_url.strip():
        transcription["enabled"] = False
        provider = transcription.get("provider", "")
        config.get("providers", {}).get(provider, {}).pop("api_key", None)
    return config


def main(argv: list[str]) -> int:
    """Settle the config file at `argv[1]` from the environment, in place."""
    path = argv[1]
    with open(path) as f:
        config = json.load(f)
    settle(config, language=os.environ.get("AGENT_LANGUAGE", ""),
           whisper_url=os.environ.get("AGENT_WHISPER_URL", ""))
    with open(path, "w") as f:
        json.dump(config, f, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
