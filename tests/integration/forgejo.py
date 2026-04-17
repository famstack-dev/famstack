"""Minimal Forgejo API client for integration tests.

Uses urllib to stay dependency-free at this layer. Only covers the
endpoints the git mirror e2e test needs: auth ping, tree walk, file
fetch + frontmatter parse, and commit history. Mirrors the shape of
`paperless.py`.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

import yaml


@dataclass
class ForgejoAPI:
    url: str
    admin_user: str
    admin_password: str

    def _auth_header(self) -> str:
        creds = f"{self.admin_user}:{self.admin_password}".encode()
        return "Basic " + base64.b64encode(creds).decode()

    def _req(self, method: str, path: str, params: dict | None = None) -> Any:
        full = f"{self.url.rstrip('/')}{path}"
        if params:
            full += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            full, headers={"Authorization": self._auth_header()}, method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raise ForgejoError(
                f"{method} {path} → {e.code}: {e.read().decode(errors='replace')[:200]}"
            ) from e

    # ── Reachability ─────────────────────────────────────────────────────

    def ping(self) -> bool:
        try:
            self._req("GET", "/api/v1/version")
            return True
        except Exception:
            return False

    # ── Repo inspection ──────────────────────────────────────────────────

    def list_tree(self, owner: str, repo: str, ref: str = "main") -> list[dict]:
        data = self._req(
            "GET",
            f"/api/v1/repos/{owner}/{repo}/git/trees/{ref}",
            {"recursive": "true", "per_page": "1000"},
        )
        return (data or {}).get("tree", []) or []

    def get_file(self, owner: str, repo: str, path: str, ref: str = "main") -> dict | None:
        """Return {'content': decoded_str, 'sha': ..., 'raw_url': ...}. None if 404."""
        try:
            data = self._req(
                "GET",
                f"/api/v1/repos/{owner}/{repo}/contents/{urllib.parse.quote(path)}",
                {"ref": ref},
            )
        except ForgejoError as e:
            if "404" in str(e):
                return None
            raise
        if not data:
            return None
        raw = data.get("content")
        if raw:
            data["content"] = base64.b64decode(raw).decode("utf-8", errors="replace")
        return data

    def list_commits(self, owner: str, repo: str, path: str | None = None,
                     limit: int = 20) -> list[dict]:
        params: dict[str, str] = {"limit": str(limit), "stat": "false"}
        if path:
            params["path"] = path
        return self._req("GET", f"/api/v1/repos/{owner}/{repo}/commits", params) or []

    def repo_exists(self, owner: str, repo: str) -> bool:
        try:
            self._req("GET", f"/api/v1/repos/{owner}/{repo}")
            return True
        except ForgejoError:
            return False

    def list_collaborators(self, owner: str, repo: str) -> list[str]:
        data = self._req(
            "GET", f"/api/v1/repos/{owner}/{repo}/collaborators",
            {"per_page": "100"},
        ) or []
        return [c.get("login") for c in data if c.get("login")]

    def list_org_members(self, org: str) -> list[str]:
        """Everyone in `org`, regardless of team. Requires admin or membership."""
        data = self._req(
            "GET", f"/api/v1/orgs/{org}/members",
            {"per_page": "100"},
        ) or []
        return [m.get("login") for m in data if m.get("login")]

    # ── Convenience ──────────────────────────────────────────────────────

    def find_by_paperless_id(self, owner: str, repo: str, paperless_id: int) -> str | None:
        """Return the filepath of the mirror file for a Paperless doc."""
        suffix = f"-p{paperless_id}.md"
        flat = f"/p{paperless_id}.md"
        for entry in self.list_tree(owner, repo):
            path = entry.get("path", "")
            if entry.get("type") != "blob":
                continue
            if path.endswith(suffix) or path.endswith(flat):
                return path
        return None

    def load_frontmatter(self, owner: str, repo: str, path: str) -> tuple[dict, str]:
        """Fetch a .md file and split it into (frontmatter_dict, body_str)."""
        f = self.get_file(owner, repo, path)
        if not f:
            raise ForgejoError(f"file not found: {path}")
        text = f["content"]
        if not text.startswith("---\n"):
            return {}, text
        parts = text.split("---\n", 2)
        if len(parts) < 3:
            return {}, text
        _, fm_yaml, body = parts
        fm = yaml.safe_load(fm_yaml) or {}
        return fm, body


class ForgejoError(RuntimeError):
    """HTTP error from Forgejo. Message contains status + body snippet."""


def cleanup_mirror_files(api: ForgejoAPI, owner: str, repo: str,
                         title_prefix: str) -> None:
    """Delete every mirror file whose frontmatter title starts with `prefix`.

    Best-effort cleanup for tests. Uses the admin creds to delete via the
    contents API. Errors are swallowed — a failed cleanup shouldn't fail
    the test it's trying to clean up for. If the repo or bot user doesn't
    exist yet (test aborted before first publish), return quietly.
    """
    try:
        tree = api.list_tree(owner, repo)
    except ForgejoError:
        return
    for entry in tree:
        path = entry.get("path", "")
        if entry.get("type") != "blob" or not path.endswith(".md") or path == "README.md":
            continue
        try:
            fm, _ = api.load_frontmatter(owner, repo, path)
        except Exception:
            continue
        title = (fm.get("title") or "") if isinstance(fm, dict) else ""
        if not isinstance(title, str) or not title.startswith(title_prefix):
            continue
        try:
            f = api.get_file(owner, repo, path)
            if not f:
                continue
            body = {
                "sha": f["sha"],
                "message": f"chore: test cleanup {title_prefix}",
                "branch": "main",
            }
            req = urllib.request.Request(
                f"{api.url.rstrip('/')}/api/v1/repos/{owner}/{repo}/contents/{urllib.parse.quote(path)}",
                data=json.dumps(body).encode(),
                headers={
                    "Authorization": api._auth_header(),
                    "Content-Type": "application/json",
                },
                method="DELETE",
            )
            with urllib.request.urlopen(req, timeout=15):
                pass
        except Exception:
            continue
