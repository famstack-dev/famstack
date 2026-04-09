"""oMLX admin API client for model management.

oMLX is the primary local LLM backend. Its admin API supports:
- Listing downloaded models
- Downloading models from HuggingFace
- Loading/unloading models

This module handles the oMLX-specific protocol. The generic interface
is in backend.py which calls this for oMLX backends.
"""

import http.cookiejar
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


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
        client = OMLXClient("http://localhost:8000", api_key="local")
        if client.login():
            models = client.list_downloaded()
            info = client.get_model_info("mlx-community/Qwen3.5-9B-MLX-8bit")
    """

    def __init__(self, base_url: str, api_key: str = "local"):
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

    def list_downloaded(self) -> list[dict]:
        """List models downloaded to the oMLX cache."""
        result = self._request("GET", "/admin/api/models")
        if result is None:
            return []
        return result.get("models", [])

    def get_model_info(self, repo_id: str) -> ModelInfo | None:
        """Get model info from HuggingFace. Returns None if not found."""
        encoded = urllib.request.quote(repo_id, safe="")
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
        encoded = urllib.request.quote(model_id, safe="")
        result = self._request("POST", f"/admin/api/models/{encoded}/load")
        return result is not None and result.get("success", False)

    def get_model_settings(self, model_id: str) -> dict | None:
        """Get per-model settings. Returns the settings dict or None."""
        encoded = urllib.request.quote(model_id, safe="")
        result = self._request("PUT", f"/admin/api/models/{encoded}/settings", {})
        if result is None:
            return None
        return result.get("settings", {})

    def update_model_settings(self, model_id: str, **settings) -> bool:
        """Update per-model settings (chat_template_kwargs, context size, etc.)."""
        encoded = urllib.request.quote(model_id, safe="")
        result = self._request("PUT", f"/admin/api/models/{encoded}/settings", settings)
        return result is not None and result.get("success", False)


def is_omlx(base_url: str) -> bool:
    """Check if a URL points to an oMLX server (has admin API)."""
    url = base_url.rstrip("/").replace("/v1", "") + "/admin/api/models"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status in (200, 401)  # 401 = auth required = oMLX
    except urllib.error.HTTPError as e:
        return e.code == 401
    except Exception:
        return False


def repo_id_to_model_id(repo_id: str) -> str:
    """Convert HF repo_id to oMLX model_id (folder name).

    "mlx-community/Qwen3.5-9B-MLX-8bit" -> "Qwen3.5-9B-MLX-8bit"
    """
    return repo_id.split("/")[-1]
