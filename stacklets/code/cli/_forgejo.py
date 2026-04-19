"""
Sync Forgejo REST client for the code stacklet's CLI plugins.

Uses urllib (stdlib) to stay dependency-free at the CLI layer —
framework and plugins both run on the host's plain Python without
the bot-runner's aiohttp stack.

Auth is always Forgejo site-admin basic auth (stackadmin + global
admin password). Plugins resolve those from the stacklet's `config`
dict; this module just accepts the creds and makes calls.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


class ForgejoError(RuntimeError):
    """HTTP error from Forgejo. Message carries status + response body."""


@dataclass
class ForgejoClient:
    url: str
    admin_user: str
    admin_password: str

    def _auth(self) -> str:
        creds = f"{self.admin_user}:{self.admin_password}".encode()
        return "Basic " + base64.b64encode(creds).decode()

    def _req(self, method: str, path: str, *, params: dict | None = None,
             body: dict | None = None) -> Any:
        full = f"{self.url.rstrip('/')}{path}"
        if params:
            full += "?" + urllib.parse.urlencode(params)
        headers = {"Authorization": self._auth()}
        data: bytes | None = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(full, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            body_text = e.read().decode(errors="replace")
            raise ForgejoError(f"{method} {path} → {e.code}: {body_text[:300]}") from e

    # ── Orgs ────────────────────────────────────────────────────────────

    def list_orgs(self) -> list[dict]:
        """Every organisation the admin can see."""
        return self._req("GET", "/api/v1/admin/orgs", params={"limit": "50"}) or []

    def get_org(self, org: str) -> dict | None:
        try:
            return self._req("GET", f"/api/v1/orgs/{org}")
        except ForgejoError as e:
            if "404" in str(e):
                return None
            raise

    def create_org(self, org: str, description: str = "",
                   visibility: str = "private") -> dict:
        return self._req("POST", "/api/v1/orgs", body={
            "username": org,
            "full_name": org,
            "description": description,
            "visibility": visibility,
            "repo_admin_change_team_access": True,
        })

    def list_org_members(self, org: str) -> list[str]:
        data = self._req("GET", f"/api/v1/orgs/{org}/members",
                         params={"limit": "50"}) or []
        return [m.get("login") for m in data if m.get("login")]

    def get_owners_team_id(self, org: str) -> int:
        teams = self._req("GET", f"/api/v1/orgs/{org}/teams") or []
        for t in teams:
            if t.get("name", "").lower() == "owners":
                return int(t["id"])
        raise ForgejoError(f"org {org!r} has no 'Owners' team")

    def add_team_member(self, team_id: int, username: str) -> None:
        try:
            self._req("PUT", f"/api/v1/teams/{team_id}/members/{username}")
        except ForgejoError as e:
            if "already" in str(e).lower():
                return
            raise

    # ── Repos ───────────────────────────────────────────────────────────

    def list_repos(self, owner: str | None = None) -> list[dict]:
        if owner:
            return self._req("GET", f"/api/v1/users/{owner}/repos",
                             params={"limit": "50"}) or []
        return self._req("GET", "/api/v1/repos/search",
                         params={"limit": "50"}) or []

    def get_repo(self, owner: str, repo: str) -> dict | None:
        try:
            return self._req("GET", f"/api/v1/repos/{owner}/{repo}")
        except ForgejoError as e:
            if "404" in str(e):
                return None
            raise

    def create_repo(self, owner: str, repo: str, *,
                    description: str = "", private: bool = True,
                    owner_is_org: bool = False) -> dict:
        if owner_is_org:
            path = f"/api/v1/orgs/{owner}/repos"
        else:
            path = f"/api/v1/admin/users/{owner}/repos"
        return self._req("POST", path, body={
            "name": repo,
            "description": description,
            "private": private,
            "auto_init": True,
            "default_branch": "main",
        })
