"""The models recommended for this Mac, on the oMLX app the stack uses.

    from ..recommended import recommended, download

    plan = recommended(config)                 # what the setup page shows
    plan["found"]                              # "http://localhost:8000"
    plan["connected"]                          # True once [ai] names it, with its key
    [m["state"] for m in plan["models"]]       # ["downloading", "on_mac", "to_download"]

    download(config, speech=False)             # [ai] speech and default written,
                                               # the chat model's download started

The setup page's AI step (`/setup?step=ai`) sets up the AI from the oMLX
app: it finds the app, takes its key, shows what this Mac's memory
profile recommends, downloads it with a bar per model and starts the AI.
This module is the AI stacklet's half of that, the part that knows the
models and talks to oMLX's admin API. The page asks for it through the
host API (`stack ai recommended show`, `stack ai recommended download`),
because the key and `stack.toml` stay on this Mac.

Three models make the AI, one row each:

    role     what it does                     model                                      when
    chat     reading and answering            the profile's chat model (`stack.profile`)   always
    listen   understanding voice messages     `speech_on_omlx.SPEECH_MODEL`              speech on
    speak    speaking                         the house voice of the family's language  speech on

Each row says where the model stands on the oMLX app:

    on_mac        oMLX lists it among its downloaded models
    downloading   an oMLX download task for it is pending or running
    failed        its last download task failed, and it is not on the Mac
    to_download   none of these

The downloads are oMLX's own tasks (`/admin/api/hf/download`), so they go
on when the page is closed, and the next look at `hf/tasks` shows where
they are. A worked case on a 16 GB Mac whose oMLX app has the house voice:

    recommended(config)["models"]
    # [{"role": "chat",   "state": "to_download", "size": 6227702579, ...},
    #  {"role": "listen", "state": "to_download", "size": 1618636172, ...},
    #  {"role": "speak",  "state": "on_mac",      "size": 0, ...}]
    download(config, speech=False)
    # [ai] speech = false, [ai] default = "mlx-community/Qwen3.5-9B-MLX-4bit",
    # one download started: the chat model. Whisper stays where it is.

Why the speech choice is written here: `[ai] speech` is what the AI's
first start reads to install speech or not (`speech_setting.py`), and the
default model is what it loads. The page then starts `stack up ai`, which
finds every model on the Mac and downloads nothing more.

What it does not do: download onto an oMLX on another machine (its disk
and memory are someone else's; `model_server.ModelServer.asks_first`),
onto the engine the stack installs from Homebrew (its first start
downloads), or change the app's settings.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from stack.ai.voice import voice_for, voices
from stack.profile import SPEECH_KEY, memory_gb, profile_of

from .model_server import ModelServer, model_server
from .omlx import DownloadTask, OMLXClient, repo_id_to_model_id
from .running_server import running_server
from .speech_on_omlx import SPEECH_MODEL

__all__ = ["ROLES", "STATES", "recommended", "download", "wait"]

# The rows of the plan, in the order the page lists them.
ROLES = ("chat", "listen", "speak")
# Where a model stands on the oMLX app.
ON_MAC, DOWNLOADING, FAILED, TO_DOWNLOAD = "on_mac", "downloading", "failed", "to_download"
STATES = (ON_MAC, DOWNLOADING, FAILED, TO_DOWNLOAD)
# The roles that come with speech.
SPEECH_ROLES = ("listen", "speak")

# How often `wait` asks oMLX where its downloads are.
POLL_SECONDS = 2.0


# ── The plan ─────────────────────────────────────────────────────────────

def recommended(config: dict) -> dict:
    """What the setup page shows of the AI on this Mac, from the parsed
    `stack.toml`.

    Returns `found` (the address of the AI server answering on this Mac,
    "" when none does), `connected` (whether `[ai]` names that server, it
    is an oMLX and its key logs in), `memory_gb` and `profile` (this
    Mac's memory and memory profile), `speech` (the stored `[ai] speech`,
    else the profile's choice for a fresh install) and `models`, one row
    per role of `ROLES`, empty while not connected. Each row has `role`,
    `repo`, `name`, `state` (one of `STATES`), `size` and `done` in
    bytes, and `error` for a failed download. Writes nothing.

        recommended({})       # a Mac where nothing answers on port 8000
        # {"found": "", "connected": False, "memory_gb": 16, "profile": "small",
        #  "speech": False, "models": []}
    """
    profile = profile_of(config)
    ai = config.get("ai", {})
    found = running_server() or ""
    stored = ai.get(SPEECH_KEY)
    plan = {"found": found.removesuffix("/v1"), "connected": False, "memory_gb": _memory(),
            "profile": profile.name,
            "speech": stored if isinstance(stored, bool) else profile.speech_at_install,
            "models": []}
    client = _logged_in(config, found)
    if client is None:
        return plan
    return {**plan, "connected": True, "models": _rows(client, _repos(config, profile.chat_model))}


def download(config: dict, set_cfg: Callable[[str, str, object], None], *, speech: bool) -> dict:
    """Record the speech choice and the chat model, and start oMLX's
    downloads of what the choice needs and the Mac does not have.

    `set_cfg(section, key, value)` writes one line of `stack.toml` (a CLI
    command's `config["set_cfg"]`). Writes `[ai] speech` and
    `[ai] default` = the profile's chat model, then asks oMLX to download
    each needed row that is `to_download` or `failed`; a row on the Mac
    or downloading is left as it is. Returns the plan afterwards, as
    `recommended` reads it. Returns `{"error": ...}` and writes nothing
    while the oMLX app is not connected.

        download(config, set_cfg, speech=True)["models"][0]["state"]   # "downloading"
    """
    plan = recommended(config)
    if not plan["connected"]:
        return {"error": "The stack does not use the oMLX app on this Mac yet. Connect it "
                         "with its API key first: './stack up ai'."}
    set_cfg("ai", SPEECH_KEY, speech)
    set_cfg("ai", "default", plan["models"][0]["repo"])
    client = _logged_in(config, plan["found"] + "/v1")
    assert client is not None, "connected a moment ago"
    for row in plan["models"]:
        if (speech or row["role"] not in SPEECH_ROLES) and row["state"] in (TO_DOWNLOAD, FAILED):
            client.start_download(row["repo"])
    return recommended(config)


def wait(config: dict, say: Callable[[str], None], *, poll: float = POLL_SECONDS) -> dict:
    """Wait until no row is downloading, saying each row's progress as it
    changes; returns the plan at the end.

        wait(config, print)
        # Qwen3.5 9B: 2.1 of 5.8 GB
        # Qwen3.5 9B: on this Mac
    """
    said: dict[str, str] = {}
    while True:
        plan = recommended(config)
        for row in plan["models"]:
            line = _progress(row)
            if said.get(row["role"]) != line:
                said[row["role"]] = line
                say(f"{row['name']}: {line}")
        if not any(row["state"] == DOWNLOADING for row in plan["models"]):
            return plan
        time.sleep(poll)


# ── Reading oMLX ─────────────────────────────────────────────────────────

def _logged_in(config: dict, found: str) -> OMLXClient | None:
    """A logged-in client of the oMLX app when `[ai]` names the server
    found on this Mac and it is an oMLX; None otherwise."""
    ai = config.get("ai", {})
    url = str(ai.get("openai_url", "")).rstrip("/")
    if not found or ai.get("provider") != "external" or url != found.rstrip("/"):
        return None
    server: ModelServer | None = model_server("external", url, str(ai.get("openai_key", "")))
    if server is None or server.asks_first:
        return None
    client = server.client()
    return client if client.login() else None


def _repos(config: dict, chat_model: str) -> dict[str, str]:
    """The repo of each role: the chat model, the speech model, the house voice."""
    ai = config.get("ai", {})
    language = ai.get("language") or config.get("core", {}).get("language") or "en"
    return {"chat": chat_model, "listen": SPEECH_MODEL,
            "speak": voice_for(voices(ai.get("voices")), language).repo}


def _rows(client: OMLXClient, repos: dict[str, str]) -> list[dict]:
    on_mac = {m.get("id", "") for m in client.list_downloaded()}
    tasks = {t.repo_id: t for t in client.get_download_tasks()}
    return [_row(role, repos[role], on_mac, tasks.get(repos[role]), client) for role in ROLES]


def _row(role: str, repo: str, on_mac: set[str], task: DownloadTask | None,
         client: OMLXClient) -> dict:
    """One row of the plan. The size of a model still to download is
    asked from Hugging Face through oMLX; a running task carries its own."""
    row = {"role": role, "repo": repo, "name": _name(repo), "size": 0, "done": 0, "error": ""}
    if repo_id_to_model_id(repo) in on_mac:
        return {**row, "state": ON_MAC}
    if task is not None and task.status in ("pending", "downloading"):
        return {**row, "state": DOWNLOADING, "size": task.total_size,
                "done": task.downloaded_size}
    info = client.get_model_info(repo)
    size = info.size if info else 0
    if task is not None and task.status == "failed":
        return {**row, "state": FAILED, "size": size, "error": task.error}
    return {**row, "state": TO_DOWNLOAD, "size": size}


def _progress(row: dict) -> str:
    if row["state"] == DOWNLOADING:
        return f"{_gb(row['done'])} of {_gb(row['size'])} GB"
    return {ON_MAC: "on this Mac", FAILED: f"failed ({row['error']})"}.get(row["state"],
                                                                         "not downloaded")


# ── Small helpers ────────────────────────────────────────────────────────

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


def _gb(size: int) -> str:
    return f"{size / 1024 ** 3:.1f}"


def _memory() -> int:
    """This Mac's memory in whole GB, 0 when macOS does not say."""
    memory = memory_gb()
    return round(memory) if memory else 0
