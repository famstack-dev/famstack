"""Tests for the oMLX admin API client."""

import json
import sys
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pytest

# Add stacklets/ai to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "stacklets" / "ai"))

from omlx import OMLXClient, ModelInfo, DownloadTask, is_omlx, repo_id_to_model_id


class TestRepoIdToModelId:
    """repo_id_to_model_id extracts the folder name from HF repo IDs."""

    def test_extracts_model_name(self):
        assert repo_id_to_model_id("mlx-community/Qwen3.5-9B-MLX-8bit") == "Qwen3.5-9B-MLX-8bit"

    def test_handles_bare_name(self):
        assert repo_id_to_model_id("Qwen3.5-9B-MLX-8bit") == "Qwen3.5-9B-MLX-8bit"

    def test_handles_deep_path(self):
        assert repo_id_to_model_id("org/user/model") == "model"


class TestOMLXClientLogin:
    """Login authenticates to the admin API."""

    def test_login_success(self):
        client = OMLXClient("http://localhost:8000", api_key="local")

        with patch.object(client, '_request') as mock:
            mock.return_value = {"success": True}
            assert client.login() is True
            mock.assert_called_once_with("POST", "/admin/api/login", {"api_key": "local"})

    def test_login_failure(self):
        client = OMLXClient("http://localhost:8000", api_key="wrong")

        with patch.object(client, '_request') as mock:
            mock.return_value = None
            assert client.login() is False


class TestOMLXClientModels:
    """Model listing and info."""

    def test_list_downloaded(self):
        client = OMLXClient("http://localhost:8000")

        with patch.object(client, '_request') as mock:
            mock.return_value = {
                "models": [
                    {"id": "Qwen2.5-14B-Instruct-4bit", "loaded": False},
                    {"id": "Qwen3.5-9B-MLX-8bit", "loaded": True},
                ]
            }
            models = client.list_downloaded()
            assert len(models) == 2
            assert models[0]["id"] == "Qwen2.5-14B-Instruct-4bit"

    def test_list_downloaded_empty(self):
        client = OMLXClient("http://localhost:8000")

        with patch.object(client, '_request') as mock:
            mock.return_value = {"models": []}
            assert client.list_downloaded() == []

    def test_list_downloaded_error(self):
        client = OMLXClient("http://localhost:8000")

        with patch.object(client, '_request') as mock:
            mock.return_value = None
            assert client.list_downloaded() == []

    def test_get_model_info(self):
        client = OMLXClient("http://localhost:8000")

        with patch.object(client, '_request') as mock:
            mock.return_value = {
                "repo_id": "mlx-community/Qwen3.5-9B-MLX-8bit",
                "size": 10426433504,
                "size_formatted": "9.7 GB",
                "downloads": 6771,
                "likes": 4,
            }
            info = client.get_model_info("mlx-community/Qwen3.5-9B-MLX-8bit")
            assert info is not None
            assert info.repo_id == "mlx-community/Qwen3.5-9B-MLX-8bit"
            assert info.size_formatted == "9.7 GB"

    def test_get_model_info_not_found(self):
        client = OMLXClient("http://localhost:8000")

        with patch.object(client, '_request') as mock:
            mock.return_value = {"error": "Model not found"}
            assert client.get_model_info("nonexistent/model") is None


class TestOMLXClientDownload:
    """Download task management."""

    def test_start_download(self):
        client = OMLXClient("http://localhost:8000")

        with patch.object(client, '_request') as mock:
            mock.return_value = {
                "success": True,
                "task": {
                    "task_id": "abc-123",
                    "status": "pending",
                    "progress": 0.0,
                }
            }
            task = client.start_download("mlx-community/Qwen3.5-9B-MLX-8bit")
            assert task is not None
            assert task.task_id == "abc-123"
            assert task.status == "pending"

    def test_start_download_failure(self):
        client = OMLXClient("http://localhost:8000")

        with patch.object(client, '_request') as mock:
            mock.return_value = {"success": False, "error": "Disk full"}
            assert client.start_download("mlx-community/model") is None

    def test_get_download_tasks(self):
        client = OMLXClient("http://localhost:8000")

        with patch.object(client, '_request') as mock:
            mock.return_value = {
                "tasks": [
                    {
                        "task_id": "abc-123",
                        "repo_id": "mlx-community/Qwen3.5-9B-MLX-8bit",
                        "status": "downloading",
                        "progress": 45.2,
                        "total_size": 10426433504,
                        "downloaded_size": 4712345678,
                    }
                ]
            }
            tasks = client.get_download_tasks()
            assert len(tasks) == 1
            assert tasks[0].progress == 45.2
            assert tasks[0].status == "downloading"

    def test_get_task_by_id(self):
        client = OMLXClient("http://localhost:8000")

        with patch.object(client, 'get_download_tasks') as mock:
            mock.return_value = [
                DownloadTask("abc-123", "model1", "downloading", 50.0),
                DownloadTask("def-456", "model2", "pending", 0.0),
            ]
            task = client.get_task("abc-123")
            assert task is not None
            assert task.repo_id == "model1"

            assert client.get_task("nonexistent") is None


class TestOMLXClientLoad:
    """Model loading."""

    def test_load_model_success(self):
        client = OMLXClient("http://localhost:8000")

        with patch.object(client, '_request') as mock:
            mock.return_value = {"success": True}
            assert client.load_model("Qwen3.5-9B-MLX-8bit") is True

    def test_load_model_failure(self):
        client = OMLXClient("http://localhost:8000")

        with patch.object(client, '_request') as mock:
            mock.return_value = {"success": False, "error": "Out of memory"}
            assert client.load_model("HugeModel") is False
