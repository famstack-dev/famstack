"""The settings recommended for this Mac, and the AI server the stack uses.

    from ..recommended import recommended

    plan = recommended(config)                 # what the setup page shows
    plan["profile"], plan["memory_gb"]         # "small", 16
    plan["speech"]                             # False on a 16 GB Mac
    [m["name"] for m in plan["models"]]        # ["Qwen3.5 9B", "Whisper large-v3 turbo", ...]
    plan["memory"]                             # {"ceiling_gb": 9.0, "idle_minutes": 10} on a small Mac
    plan["provider"]                           # "managed", "external", or "" before a route
    plan["other"]                              # None, or the server elsewhere [ai] names

The setup page's AI step (`/setup?step=ai`) reads this through the host
API (`stack ai recommended show`), because `stack.toml` and this Mac's
memory are on the Mac and the page is drawn in a container.

Three models make the AI, one row each, as this Mac's memory profile
(`stack.profile`) recommends them:

    role     what it does                     model                                      when
    chat     reading and answering            the profile's chat model                   always
    listen   understanding voice messages     `speech_on_omlx.SPEECH_MODEL`              speech on
    speak    speaking                         the house voice of the family's language  speech on

A server the admin connected with "Use your own AI server" (`stack ai
switch <address>`) is named in `other`, with the chat model it uses,
whether voice messages have somewhere to go and whether it stays in the
home network, so the step can say so once it is ready.

What it does not do: ask a server, download a model, or change a
setting anywhere. It reads `stack.toml` and this Mac's memory.
"""

from __future__ import annotations

from stack.ai.probe import stays_home
from stack.ai.voice import voice_for, voices
from stack.profile import SPEECH_KEY, memory_gb, profile_of

from .omlx import repo_id_to_model_id
from .speech_on_omlx import SPEECH_MODEL

__all__ = ["ROLES", "recommended"]

# The rows of the plan, in the order the page lists them.
ROLES = ("chat", "listen", "speak")
# What `[ai] provider` can say: the AI the stack runs, or a server it does not manage.
PROVIDERS = ("managed", "external")


# ── The plan ─────────────────────────────────────────────────────────────

def recommended(config: dict) -> dict:
    """What the setup page shows of the AI on this Mac, from the parsed
    `stack.toml`.

    Returns `provider`, what `[ai] provider` says ("managed" for the AI
    the stack runs, "external" for a server it does not manage, "" while
    no route was taken), `memory_gb` and `profile` (this Mac's memory and memory
    profile), `speech` (the stored `[ai] speech`, else the profile's
    choice for a fresh install), `models`, one row per role of `ROLES`
    with `role`, `repo` and `name`, `memory`, the oMLX memory settings
    the profile asks for (`ceiling_gb` and `idle_minutes`, `omlx_fit.py`;
    None on a Mac large enough for oMLX's own), and `other`, the AI server `[ai]`
    names when the stack does not manage it (`_other`), else None. Writes
    nothing and asks no server.

        recommended({})       # a 16 GB Mac, nothing set up yet
        # {"provider": "", "memory_gb": 16, "profile": "small", "speech": False,
        #  "models": [{"role": "chat", "repo": "mlx-community/Qwen3.5-9B-MLX-4bit",
        #              "name": "Qwen3.5 9B"}, ...],
        #  "memory": {"ceiling_gb": 9.0, "idle_minutes": 10}, "other": None}
    """
    profile = profile_of(config)
    ai = config.get("ai", {})
    stored = ai.get(SPEECH_KEY)
    repos = _repos(config, profile.chat_model)
    provider = ai.get("provider")
    return {"provider": provider if provider in PROVIDERS else "",
            "memory_gb": _memory(), "profile": profile.name,
            "speech": stored if isinstance(stored, bool) else profile.speech_at_install,
            "models": [{"role": role, "repo": repos[role], "name": _name(repos[role])}
                       for role in ROLES],
            "memory": _memory_settings(profile), "other": _other(ai)}


# ── A server elsewhere ───────────────────────────────────────────────────

def _other(ai: dict) -> dict | None:
    """The AI server `[ai]` names when the stack does not manage it, as the
    setup page shows it; None otherwise. `voice` says whether voice
    messages have somewhere to go (a speech server, or a speech-to-text
    model on the AI server), as `stack ai switch` records it; `home`
    whether the server stays in the home network.

        _other({"provider": "external", "openai_url": "http://192.168.1.30:11434/v1",
                "default": "qwen3:8b"})
        # {"address": "http://192.168.1.30:11434", "model": "qwen3:8b",
        #  "voice": False, "home": True}
    """
    url = str(ai.get("openai_url", "")).rstrip("/")
    if ai.get("provider") != "external" or not url:
        return None
    return {"address": url.removesuffix("/v1"), "model": str(ai.get("default", "")),
            "voice": bool(ai.get("whisper_url") or ai.get("whisper_model")),
            "home": stays_home(url)}


# ── oMLX's memory settings ───────────────────────────────────────────────

def _memory_settings(profile) -> dict | None:
    """The oMLX memory settings the profile asks for, as the setup page
    names them: the fixed ceiling in GB and the idle unload in minutes.
    The same values `omlx_fit` writes for the oMLX the stack installs."""
    if profile.omlx_ceiling_gb is None:
        return None
    return {"ceiling_gb": profile.omlx_ceiling_gb,
            "idle_minutes": (profile.omlx_idle_timeout_s or 0) // 60}


# ── Small helpers ────────────────────────────────────────────────────────

def _repos(config: dict, chat_model: str) -> dict[str, str]:
    """The repo of each role: the chat model, the speech model, the house voice."""
    ai = config.get("ai", {})
    language = ai.get("language") or config.get("core", {}).get("language") or "en"
    return {"chat": chat_model, "listen": SPEECH_MODEL,
            "speak": voice_for(voices(ai.get("voices")), language).repo}


# The names a person reads for the models the stack recommends; any other
# model is named by its folder.
NAMES = {
    "mlx-community/Qwen3.5-9B-MLX-4bit": "Qwen3.5 9B",
    "unsloth/Qwen3.6-35B-A3B-UD-MLX-4bit": "Qwen3.6 35B-A3B",
    SPEECH_MODEL: "Whisper large-v3 turbo",
    "mlx-community/Kokoro-82M-bf16": "Kokoro 82M",
    "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit": "Qwen3-TTS 0.6B",
}


def _name(repo: str) -> str:
    """`NAMES`, else the model's folder: "Kokoro-82M-bf16"."""
    return NAMES.get(repo) or repo_id_to_model_id(repo)


def _memory() -> int:
    """This Mac's memory in whole GB, 0 when macOS does not say."""
    memory = memory_gb()
    return round(memory) if memory else 0
