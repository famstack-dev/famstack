"""Minimal async Forgejo REST client for the archivist bot.

Covers only what the git mirror needs: ensuring a bot user exists,
ensuring a repo exists, adding collaborators, and committing files via
the contents API (no working tree, no clone).

Two auth modes:
- admin_basic: username + password for admin bootstrap (create users,
  issue tokens). Used during ensure_setup only.
- token: archivist-bot's personal access token for day-to-day writes.

All methods are idempotent — "already exists" is treated as success.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

import aiohttp
from loguru import logger


class ForgejoError(Exception):
    """Forgejo API returned an unexpected response."""


@dataclass
class ForgejoClient:
    base_url: str
    http: aiohttp.ClientSession
    admin_user: str | None = None
    admin_password: str | None = None
    token: str | None = None

    def _auth_headers(self, admin: bool = False) -> dict[str, str]:
        if admin:
            if not (self.admin_user and self.admin_password):
                raise ForgejoError("admin credentials not set")
            creds = f"{self.admin_user}:{self.admin_password}".encode()
            return {"Authorization": "Basic " + base64.b64encode(creds).decode()}
        if not self.token:
            raise ForgejoError("token not set")
        return {"Authorization": f"token {self.token}"}

    async def ping(self) -> bool:
        """Return True if the Forgejo API is reachable."""
        try:
            async with self.http.get(f"{self.base_url}/api/v1/version", timeout=aiohttp.ClientTimeout(total=3)) as r:
                return r.status == 200
        except Exception:
            return False

    # ── Admin: users + tokens ────────────────────────────────────────────

    async def user_exists(self, username: str) -> bool:
        async with self.http.get(
            f"{self.base_url}/api/v1/users/{username}",
            headers=self._auth_headers(admin=True),
        ) as r:
            return r.status == 200

    async def create_user(self, username: str, email: str, password: str) -> None:
        """Create a regular (non-admin) user. Idempotent."""
        async with self.http.post(
            f"{self.base_url}/api/v1/admin/users",
            headers={**self._auth_headers(admin=True), "Content-Type": "application/json"},
            json={
                "username": username,
                "email": email,
                "password": password,
                "must_change_password": False,
                "source_id": 0,
                "login_name": username,
            },
        ) as r:
            if r.status in (200, 201):
                logger.info("[forgejo] Created user: {}", username)
                return
            body = await r.text()
            if r.status == 422 and "already exists" in body.lower():
                logger.debug("[forgejo] User exists: {}", username)
                return
            raise ForgejoError(f"create_user({username}) failed: HTTP {r.status} {body[:200]}")

    async def issue_token(self, username: str, password: str, name: str, scopes: list[str]) -> str:
        """Issue a personal access token for `username` using their password.

        Requires basic-auth as that user (not admin). If a token with the
        same name already exists, deletes it and reissues.
        """
        user_auth = base64.b64encode(f"{username}:{password}".encode()).decode()
        auth_headers = {"Authorization": f"Basic {user_auth}"}

        async with self.http.get(
            f"{self.base_url}/api/v1/users/{username}/tokens",
            headers=auth_headers,
        ) as r:
            if r.status == 200:
                for tok in await r.json():
                    if tok.get("name") == name:
                        await self.http.delete(
                            f"{self.base_url}/api/v1/users/{username}/tokens/{tok['id']}",
                            headers=auth_headers,
                        )
                        logger.info("[forgejo] Deleted existing token: {}", name)

        async with self.http.post(
            f"{self.base_url}/api/v1/users/{username}/tokens",
            headers={**auth_headers, "Content-Type": "application/json"},
            json={"name": name, "scopes": scopes},
        ) as r:
            if r.status not in (200, 201):
                body = await r.text()
                raise ForgejoError(f"issue_token({username}/{name}) failed: HTTP {r.status} {body[:200]}")
            data = await r.json()
            secret = data.get("sha1") or data.get("token")
            if not secret:
                raise ForgejoError(f"issue_token returned no secret: {data}")
            logger.info("[forgejo] Issued token for {}: {}", username, name)
            return secret

    # ── Repos ────────────────────────────────────────────────────────────

    async def repo_exists(self, owner: str, repo: str) -> bool:
        async with self.http.get(
            f"{self.base_url}/api/v1/repos/{owner}/{repo}",
            headers=self._auth_headers(),
        ) as r:
            return r.status == 200

    async def create_repo(
        self, owner: str, repo: str,
        description: str = "", private: bool = True,
        default_branch: str = "main",
    ) -> None:
        """Create a repo under `owner`. Uses admin creds, auto-inits with README."""
        async with self.http.post(
            f"{self.base_url}/api/v1/admin/users/{owner}/repos",
            headers={**self._auth_headers(admin=True), "Content-Type": "application/json"},
            json={
                "name": repo,
                "description": description,
                "private": private,
                "auto_init": True,
                "default_branch": default_branch,
            },
        ) as r:
            if r.status in (200, 201):
                logger.info("[forgejo] Created repo: {}/{}", owner, repo)
                return
            body = await r.text()
            if r.status == 409 or "already exists" in body.lower():
                logger.debug("[forgejo] Repo exists: {}/{}", owner, repo)
                return
            raise ForgejoError(f"create_repo({owner}/{repo}) failed: HTTP {r.status} {body[:200]}")

    async def add_collaborator(self, owner: str, repo: str, username: str, permission: str = "write") -> None:
        """Grant `username` access to `owner/repo`. permission ∈ read|write|admin."""
        async with self.http.put(
            f"{self.base_url}/api/v1/repos/{owner}/{repo}/collaborators/{username}",
            headers={**self._auth_headers(admin=True), "Content-Type": "application/json"},
            json={"permission": permission},
        ) as r:
            if r.status in (200, 201, 204):
                logger.info("[forgejo] Collaborator {}/{} = {} ({})", owner, repo, username, permission)
                return
            body = await r.text()
            raise ForgejoError(f"add_collaborator({owner}/{repo}, {username}) failed: HTTP {r.status} {body[:200]}")

    # ── Contents API ─────────────────────────────────────────────────────

    async def get_file(self, owner: str, repo: str, path: str, ref: str = "main") -> dict | None:
        """Return file metadata + content, or None if not found."""
        async with self.http.get(
            f"{self.base_url}/api/v1/repos/{owner}/{repo}/contents/{path}",
            headers=self._auth_headers(),
            params={"ref": ref},
        ) as r:
            if r.status == 404:
                return None
            if r.status != 200:
                body = await r.text()
                raise ForgejoError(f"get_file({path}) failed: HTTP {r.status} {body[:200]}")
            return await r.json()

    async def put_file(
        self, owner: str, repo: str, path: str,
        content: str, message: str,
        branch: str = "main", sha: str | None = None,
        author_name: str | None = None, author_email: str | None = None,
    ) -> dict:
        """Create or update a file. If `sha` is given, it's an update."""
        payload: dict[str, Any] = {
            "content": base64.b64encode(content.encode("utf-8")).decode(),
            "message": message,
            "branch": branch,
        }
        if sha:
            payload["sha"] = sha
        if author_name and author_email:
            payload["author"] = {"name": author_name, "email": author_email}
            payload["committer"] = {"name": author_name, "email": author_email}

        method = "PUT" if sha else "POST"
        async with self.http.request(
            method,
            f"{self.base_url}/api/v1/repos/{owner}/{repo}/contents/{path}",
            headers={**self._auth_headers(), "Content-Type": "application/json"},
            json=payload,
        ) as r:
            if r.status in (200, 201):
                return await r.json()
            body = await r.text()
            raise ForgejoError(f"put_file({path}) failed: HTTP {r.status} {body[:200]}")

    async def delete_file(
        self, owner: str, repo: str, path: str,
        sha: str, message: str, branch: str = "main",
    ) -> None:
        """Delete a file at `path` (used when renaming via delete+create)."""
        async with self.http.request(
            "DELETE",
            f"{self.base_url}/api/v1/repos/{owner}/{repo}/contents/{path}",
            headers={**self._auth_headers(), "Content-Type": "application/json"},
            json={"sha": sha, "message": message, "branch": branch},
        ) as r:
            if r.status in (200, 204):
                return
            body = await r.text()
            raise ForgejoError(f"delete_file({path}) failed: HTTP {r.status} {body[:200]}")

    async def list_tree(self, owner: str, repo: str, ref: str = "main") -> list[dict]:
        """Return the full recursive tree of `owner/repo` at `ref`.

        Used to rebuild the paperless_id → filepath cache when it's lost.
        Forgejo's commits API doesn't support message-trailer search, so
        we scan the tree and let the caller match filename patterns.
        """
        async with self.http.get(
            f"{self.base_url}/api/v1/repos/{owner}/{repo}/git/trees/{ref}",
            headers=self._auth_headers(),
            params={"recursive": "true"},
        ) as r:
            if r.status != 200:
                return []
            data = await r.json()
            return data.get("tree", []) or []
