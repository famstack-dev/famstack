"""oMLX admin API client for model management.

oMLX is the primary local LLM backend. Its admin API supports:
- Listing downloaded models
- Downloading models from HuggingFace
- Loading/unloading models

This module handles the oMLX-specific protocol. The generic interface
is in backend.py which calls this for oMLX backends.

It also keeps the key of the oMLX the stack installs and manages
(`[ai] provider = "managed"`): oMLX takes a request only with the key in
its settings file (`auth.api_key` in `~/.omlx/settings.json`), and the
stack sends the key in `[ai] openai_key`. The two must be the same.

    key = give_new_key(lambda k: ctx.cfg("openai_key", k))   # "7fk2-pq9w-m3xd-h8ra"
    managed_key()                                            # the same key, from the file

Earlier releases installed oMLX with the key `local`, and in port mode
oMLX listens on every interface, so every device at home could use the AI
and its admin pages with that well-known word. A new install gets a
generated key (`hooks/on_install.py`); an instance that still has `local`
keeps it until the admin makes a new one (`stack ai key new`, or "Make a
new key" on the AI's service card on the Mac). Nothing changes the key
on its own, because apps on other computers may use it. The oMLX app and
servers elsewhere keep the key their admin set: this part never touches
them.
"""

import http.cookiejar
import json
import secrets
from collections.abc import Callable
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


__all__ = ["ModelInfo", "DownloadTask", "OMLXClient", "is_omlx", "omlx_at", "repo_id_to_model_id",
           "PLACEHOLDER_KEY", "settings_file", "managed_key", "new_key", "give_new_key"]

# The key oMLX was installed with before keys were generated, and the
# placeholder for a server that takes any key: the OpenAI clients refuse
# an empty one. Every part of the AI stacklet spells it through this name.
PLACEHOLDER_KEY = "local"


@dataclass
class ModelInfo:
    """Model metadata from HuggingFace."""
    repo_id: str
    size: int
    size_formatted: str
    downloads: int = 0
    likes: int = 0


@dataclass
class DownloadTask:
    """Active download task status."""
    task_id: str
    repo_id: str
    status: str  # pending, downloading, completed, failed
    progress: float
    total_size: int = 0
    downloaded_size: int = 0
    error: str = ""


class OMLXClient:
    """Client for oMLX admin API.

    Usage:
        client = OMLXClient("http://localhost:8000", api_key="7fk2-pq9w-m3xd-h8ra")
        if client.login():
            models = client.list_downloaded()
            info = client.get_model_info("mlx-community/Qwen3.5-9B-MLX-8bit")
    """

    def __init__(self, base_url: str, api_key: str = PLACEHOLDER_KEY):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._cookies = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._cookies)
        )

    def _request(self, method: str, path: str, data: dict | None = None) -> dict | None:
        """Make an authenticated request to the admin API."""
        url = f"{self.base_url}{path}"
        headers = {"Content-Type": "application/json"}

        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(url, data=body, headers=headers, method=method)

        try:
            with self._opener.open(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return None
            try:
                return json.loads(e.read().decode())
            except Exception:
                return None
        except Exception:
            return None

    def login(self) -> bool:
        """Authenticate to the admin API. Returns True on success."""
        result = self._request("POST", "/admin/api/login", {"api_key": self.api_key})
        return result is not None and result.get("success", False)

    def version(self) -> str:
        """The running oMLX's version, "0.6.4", or "" when it does not say.
        oMLX is a FastAPI app, which serves its version in the API
        description without a key (oMLX 0.6.4)."""
        result = self._request("GET", "/openapi.json") or {}
        return str(result.get("info", {}).get("version", ""))

    def list_downloaded(self) -> list[dict]:
        """List models downloaded to the oMLX cache."""
        result = self._request("GET", "/admin/api/models")
        if result is None:
            return []
        return result.get("models", [])

    def get_model_info(self, repo_id: str) -> ModelInfo | None:
        """Get model info from HuggingFace. Returns None if not found."""
        encoded = urllib.parse.quote(repo_id, safe="")
        result = self._request("GET", f"/admin/api/hf/model-info?repo_id={encoded}")
        if result is None or "error" in result:
            return None
        return ModelInfo(
            repo_id=result.get("repo_id", repo_id),
            size=result.get("size", 0),
            size_formatted=result.get("size_formatted", "unknown"),
            downloads=result.get("downloads", 0),
            likes=result.get("likes", 0),
        )

    def start_download(self, repo_id: str) -> DownloadTask | None:
        """Start downloading a model from HuggingFace."""
        result = self._request("POST", "/admin/api/hf/download", {"repo_id": repo_id})
        if result is None or not result.get("success"):
            return None
        task = result.get("task", {})
        return DownloadTask(
            task_id=task.get("task_id", ""),
            repo_id=repo_id,
            status=task.get("status", "pending"),
            progress=task.get("progress", 0.0),
        )

    def get_download_tasks(self) -> list[DownloadTask]:
        """Get all active download tasks."""
        result = self._request("GET", "/admin/api/hf/tasks")
        if result is None:
            return []
        tasks = []
        for t in result.get("tasks", []):
            tasks.append(DownloadTask(
                task_id=t.get("task_id", ""),
                repo_id=t.get("repo_id", ""),
                status=t.get("status", "unknown"),
                progress=t.get("progress", 0.0),
                total_size=t.get("total_size", 0),
                downloaded_size=t.get("downloaded_size", 0),
                error=t.get("error", ""),
            ))
        return tasks

    def get_task(self, task_id: str) -> DownloadTask | None:
        """Get a specific download task by ID."""
        tasks = self.get_download_tasks()
        for task in tasks:
            if task.task_id == task_id:
                return task
        return None

    def load_model(self, model_id: str) -> bool:
        """Load a downloaded model into memory.

        model_id is the folder name (e.g. "Qwen3.5-9B-MLX-8bit"),
        not the full repo_id.
        """
        encoded = urllib.parse.quote(model_id, safe="")
        result = self._request("POST", f"/admin/api/models/{encoded}/load")
        return result is not None and result.get("success", False)

    def get_model_settings(self, model_id: str) -> dict | None:
        """Get per-model settings. Returns the settings dict or None."""
        encoded = urllib.parse.quote(model_id, safe="")
        result = self._request("PUT", f"/admin/api/models/{encoded}/settings", {})
        if result is None:
            return None
        return result.get("settings", {})

    def update_model_settings(self, model_id: str, **settings) -> bool:
        """Update per-model settings (chat_template_kwargs, context size, etc.)."""
        encoded = urllib.parse.quote(model_id, safe="")
        result = self._request("PUT", f"/admin/api/models/{encoded}/settings", settings)
        return result is not None and result.get("success", False)


def is_omlx(base_url: str) -> bool:
    """Whether an oMLX answers at `base_url` (`omlx_at` is True).

        is_omlx("http://localhost:8000/v1")    # True with the oMLX app open
    """
    return omlx_at(base_url) is True


def omlx_at(base_url: str) -> bool | None:
    """Whether the server at `base_url` is an oMLX, by oMLX's own answers:
    True or False when a server answers, None when nothing does.

    oMLX's admin API answers `GET /admin/api/models` with
    `{"models": [...]}` after a login, and with 401 before. A 401 alone
    says little, because any server behind a login refuses that address
    too. So a 401 counts only when the server's API description, which
    oMLX serves without a key, names oMLX. The oMLX app 0.7.3 answers:

        GET /admin/api/models   401 {"detail": "Admin authentication required"}
        GET /openapi.json       200 {"info": {"title": "oMLX API", "version": "0.7.3", ...}}

        omlx_at("http://localhost:8000/v1")     # True: the oMLX app
        omlx_at("http://localhost:11434/v1")    # False: Ollama, 404 there
        omlx_at("http://localhost:1/v1")        # None: nothing answers
    """
    admin = base_url.rstrip("/").removesuffix("/v1")
    status, body = _get_json(admin + "/admin/api/models")
    if status is None:
        return None
    if status == 200:
        return isinstance(body, dict) and isinstance(body.get("models"), list)
    if status == 401:
        _, described = _get_json(admin + "/openapi.json")
        info = described.get("info") if isinstance(described, dict) else None
        return isinstance(info, dict) and "oMLX" in str(info.get("title", ""))
    return False


def repo_id_to_model_id(repo_id: str) -> str:
    """Convert HF repo_id to oMLX model_id (folder name).

    "mlx-community/Qwen3.5-9B-MLX-8bit" -> "Qwen3.5-9B-MLX-8bit"
    """
    return repo_id.split("/")[-1]


# ── The managed oMLX's key ───────────────────────────────────────────────

# The letters and digits of a generated key, without the ones that read
# alike (i, l, o, 0, 1), because a person may type it into an app.
KEY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
# Four groups of four: about 79 bits, and short enough to type.
KEY_GROUPS = 4
KEY_GROUP_LENGTH = 4


def settings_file() -> Path:
    """oMLX's settings file, which it reads when it starts. Read from the
    home directory on each call, so a test's home is the one used.

        settings_file()   # Path("/Users/marge/.omlx/settings.json")
    """
    return Path.home() / ".omlx" / "settings.json"


def managed_key() -> str:
    """The key oMLX takes, from its settings file; `PLACEHOLDER_KEY` when
    the file or the key is missing, as before oMLX was ever installed.

        managed_key()   # "7fk2-pq9w-m3xd-h8ra", or "local" on an older install
    """
    path = settings_file()
    if not path.exists():
        return PLACEHOLDER_KEY
    return json.loads(path.read_text()).get("auth", {}).get("api_key") or PLACEHOLDER_KEY


def new_key() -> str:
    """A key nobody can guess, from `secrets`.

        new_key()   # "7fk2-pq9w-m3xd-h8ra"
    """
    groups = ("".join(secrets.choice(KEY_ALPHABET) for _ in range(KEY_GROUP_LENGTH))
              for _ in range(KEY_GROUPS))
    return "-".join(groups)


def give_new_key(save: Callable[[str], None]) -> str:
    """Make a new key, write it into oMLX's settings file, and hand it to
    `save`, which writes it where the stack reads it (`[ai] openai_key`).
    Returns the key.

    The file keeps every other setting. oMLX takes the key only after a
    restart, which is the caller's: the install starts oMLX afterwards,
    `stack ai key new` restarts it. Raises on a settings file that is not
    JSON, rather than replace the settings it cannot read.

        give_new_key(lambda k: ctx.cfg("openai_key", k))   # "7fk2-pq9w-m3xd-h8ra"
    """
    key = new_key()
    path = settings_file()
    settings = json.loads(path.read_text()) if path.exists() else {}
    settings.setdefault("auth", {})["api_key"] = key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2))
    save(key)
    return key


# ── Asking a server whether it is oMLX ─────────────────────────────────

# A server on this Mac answers in milliseconds; one that holds the
# connection open must not hold up a page.
OMLX_PROBE_SECONDS = 3


def _get_json(url: str) -> tuple[int | None, object]:
    """`GET url`: its status and its body as JSON (None when it is not
    JSON); status None when nothing answers."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="GET"),
                                    timeout=OMLX_PROBE_SECONDS) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    except (OSError, ValueError):
        return None, None
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, None
