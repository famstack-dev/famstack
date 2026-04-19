"""Sync Forgejo REST client — the single client used by the CLI plugins,
the archivist bot, and anything else in the stack that talks to Forgejo.

stdlib-only (urllib), so it lives at framework level and has no
runtime dependencies beyond Python itself. Async callers (the bot
runner) wrap individual calls in `asyncio.to_thread(...)` — blocking
HTTP inside a thread executor is strictly simpler than maintaining a
second aiohttp client with the same method surface.

Two auth modes:
- **admin basic** — `admin_user` + `admin_password` (stackadmin +
  global admin password). Needed for user creation, org creation,
  team membership, and any `/api/v1/admin/*` endpoint.
- **token** — a personal access token for the acting user. Needed
  for per-user operations like issuing tokens and day-to-day repo
  writes under that user's identity.

`ForgejoError` carries the HTTP status and a truncated body so
failures surface a useful message without dumping a giant response.
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
    admin_user: str | None = None
    admin_password: str | None = None
    token: str | None = None
    timeout: int = 15

    # ── Auth ────────────────────────────────────────────────────────────

    def _admin_header(self) -> dict[str, str]:
        if not (self.admin_user and self.admin_password):
            raise ForgejoError("admin credentials not configured")
        creds = f"{self.admin_user}:{self.admin_password}".encode()
        return {"Authorization": "Basic " + base64.b64encode(creds).decode()}

    def _token_header(self) -> dict[str, str]:
        if not self.token:
            raise ForgejoError("token not configured")
        return {"Authorization": f"token {self.token}"}

    def _basic_for(self, username: str, password: str) -> dict[str, str]:
        creds = f"{username}:{password}".encode()
        return {"Authorization": "Basic " + base64.b64encode(creds).decode()}

    # ── Low-level request ──────────────────────────────────────────────

    def _req(self, method: str, path: str, *,
             params: dict | None = None, body: dict | None = None,
             headers: dict | None = None,
             allow_404: bool = False) -> Any:
        full = f"{self.url.rstrip('/')}{path}"
        if params:
            full += "?" + urllib.parse.urlencode(params)
        hdr = dict(headers or {})
        data: bytes | None = None
        if body is not None:
            data = json.dumps(body).encode()
            hdr["Content-Type"] = "application/json"
        req = urllib.request.Request(full, data=data, headers=hdr, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                if not raw:
                    return None
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    return raw.decode(errors="replace")
        except urllib.error.HTTPError as e:
            body_text = e.read().decode(errors="replace")
            if e.code == 404 and allow_404:
                return None
            raise ForgejoError(f"{method} {path} → {e.code}: {body_text[:300]}") from e

    # ── Reachability ────────────────────────────────────────────────────

    def ping(self) -> bool:
        """True if the Forgejo API responds. No auth required."""
        try:
            # Use a short-timeout variant so callers can gate on this.
            full = f"{self.url.rstrip('/')}/api/v1/version"
            with urllib.request.urlopen(full, timeout=3) as resp:
                return resp.status == 200
        except Exception:
            return False

    # ── Users + tokens (admin) ──────────────────────────────────────────

    def user_exists(self, username: str) -> bool:
        return self._req("GET", f"/api/v1/users/{username}",
                         headers=self._admin_header(), allow_404=True) is not None

    def create_user(self, username: str, email: str, password: str) -> None:
        """Create a regular (non-admin) user. Idempotent on 'already exists'."""
        try:
            self._req("POST", "/api/v1/admin/users",
                      headers=self._admin_header(),
                      body={
                          "username": username,
                          "email": email,
                          "password": password,
                          "must_change_password": False,
                          "source_id": 0,
                          "login_name": username,
                      })
        except ForgejoError as e:
            if "already exists" in str(e).lower():
                return
            raise

    def issue_token(self, username: str, password: str,
                    name: str, scopes: list[str]) -> str:
        """Issue a personal access token for `username`.

        Needs basic auth AS THAT USER, not admin — Forgejo restricts
        token creation to the owning account. If a token with the same
        name exists, it's deleted and reissued.
        """
        user_hdr = self._basic_for(username, password)

        existing = self._req("GET", f"/api/v1/users/{username}/tokens",
                             headers=user_hdr, allow_404=True) or []
        for tok in existing:
            if tok.get("name") == name:
                self._req("DELETE", f"/api/v1/users/{username}/tokens/{tok['id']}",
                          headers=user_hdr)

        resp = self._req("POST", f"/api/v1/users/{username}/tokens",
                         headers=user_hdr,
                         body={"name": name, "scopes": scopes})
        if not resp:
            raise ForgejoError("issue_token returned empty response")
        secret = resp.get("sha1") or resp.get("token")
        if not secret:
            raise ForgejoError(f"issue_token: no secret in response: {resp}")
        return secret

    # ── Orgs + teams (admin) ────────────────────────────────────────────

    def list_orgs(self) -> list[dict]:
        return self._req("GET", "/api/v1/admin/orgs",
                         headers=self._admin_header(),
                         params={"limit": "50"}) or []

    def get_org(self, org: str) -> dict | None:
        return self._req("GET", f"/api/v1/orgs/{org}",
                         headers=self._admin_header(), allow_404=True)

    def create_org(self, org: str, description: str = "",
                   visibility: str = "private") -> None:
        """Create an organisation. Idempotent on 'already exists'."""
        try:
            self._req("POST", "/api/v1/orgs",
                      headers=self._admin_header(),
                      body={
                          "username": org,
                          "full_name": org,
                          "description": description,
                          "visibility": visibility,
                          "repo_admin_change_team_access": True,
                      })
        except ForgejoError as e:
            if "already exists" in str(e).lower():
                return
            raise

    def list_org_members(self, org: str) -> list[str]:
        data = self._req("GET", f"/api/v1/orgs/{org}/members",
                         headers=self._admin_header(),
                         params={"limit": "50"}) or []
        return [m.get("login") for m in data if m.get("login")]

    def get_owners_team_id(self, org: str) -> int:
        teams = self._req("GET", f"/api/v1/orgs/{org}/teams",
                          headers=self._admin_header()) or []
        for t in teams:
            if t.get("name", "").lower() == "owners":
                return int(t["id"])
        raise ForgejoError(f"org {org!r} has no 'Owners' team")

    def add_team_member(self, team_id: int, username: str) -> None:
        """Idempotent — 'already-a-member' is treated as success."""
        try:
            self._req("PUT", f"/api/v1/teams/{team_id}/members/{username}",
                      headers=self._admin_header())
        except ForgejoError as e:
            if "already" in str(e).lower():
                return
            raise

    # ── Repos ───────────────────────────────────────────────────────────

    def list_repos(self, owner: str | None = None) -> list[dict]:
        if owner:
            return self._req("GET", f"/api/v1/users/{owner}/repos",
                             headers=self._admin_header(),
                             params={"limit": "50"}) or []
        return self._req("GET", "/api/v1/repos/search",
                         headers=self._admin_header(),
                         params={"limit": "50"}) or []

    def get_repo(self, owner: str, repo: str) -> dict | None:
        return self._req("GET", f"/api/v1/repos/{owner}/{repo}",
                         headers=self._admin_header(), allow_404=True)

    def update_repo(self, owner: str, repo: str, *,
                    description: str | None = None,
                    private: bool | None = None) -> None:
        """Patch repo settings. Only fields with non-None values are sent.

        Used to keep the Forgejo-side description in sync with what the
        archivist renders, so wording changes on the product side reach
        live instances on the next ensure_setup.
        """
        body: dict = {}
        if description is not None:
            body["description"] = description
        if private is not None:
            body["private"] = private
        if not body:
            return
        self._req("PATCH", f"/api/v1/repos/{owner}/{repo}",
                  headers=self._admin_header(), body=body)

    def create_repo(self, owner: str, repo: str, *,
                    description: str = "", private: bool = True,
                    owner_is_org: bool = False,
                    default_branch: str = "main") -> None:
        """Create a repo under `owner`. Idempotent on 'already exists'.

        For orgs, uses `/orgs/{owner}/repos` (admin is a member of any
        org it created). For users, uses `/admin/users/{owner}/repos`
        which creates on behalf of the target user.
        """
        path = (f"/api/v1/orgs/{owner}/repos" if owner_is_org
                else f"/api/v1/admin/users/{owner}/repos")
        try:
            self._req("POST", path,
                      headers=self._admin_header(),
                      body={
                          "name": repo,
                          "description": description,
                          "private": private,
                          "auto_init": True,
                          "default_branch": default_branch,
                      })
        except ForgejoError as e:
            msg = str(e).lower()
            if " 409" in msg or "already exists" in msg:
                return
            raise

    # ── Contents API (token) ────────────────────────────────────────────
    #
    # File ops run under the bot's own token so commit authorship is
    # correct and so ACLs match the acting identity — not the admin.

    def get_file(self, owner: str, repo: str, path: str,
                 ref: str = "main") -> dict | None:
        """Return {'content': <base64>, 'sha': ..., ...} or None if missing."""
        return self._req(
            "GET",
            f"/api/v1/repos/{owner}/{repo}/contents/{urllib.parse.quote(path)}",
            headers=self._token_header(),
            params={"ref": ref},
            allow_404=True,
        )

    def put_file(self, owner: str, repo: str, path: str, *,
                 content: str, message: str,
                 branch: str = "main", sha: str | None = None,
                 author_name: str | None = None,
                 author_email: str | None = None) -> dict:
        """Create or update a file. `sha` required for update, absent for create."""
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
        return self._req(
            method,
            f"/api/v1/repos/{owner}/{repo}/contents/{urllib.parse.quote(path)}",
            headers=self._token_header(),
            body=payload,
        )

    def delete_file(self, owner: str, repo: str, path: str, *,
                    sha: str, message: str, branch: str = "main") -> None:
        self._req(
            "DELETE",
            f"/api/v1/repos/{owner}/{repo}/contents/{urllib.parse.quote(path)}",
            headers=self._token_header(),
            body={"sha": sha, "message": message, "branch": branch},
        )

    def list_tree(self, owner: str, repo: str, ref: str = "main") -> list[dict]:
        """Full recursive tree of `owner/repo` at `ref` — used to rebuild
        the archivist's paperless_id → path cache after it's wiped."""
        data = self._req(
            "GET", f"/api/v1/repos/{owner}/{repo}/git/trees/{ref}",
            headers=self._token_header(),
            params={"recursive": "true", "per_page": "1000"},
            allow_404=True,
        )
        if not data:
            return []
        return data.get("tree", []) or []

    def list_commits(self, owner: str, repo: str, *,
                     path: str | None = None, limit: int = 20) -> list[dict]:
        params: dict[str, str] = {"limit": str(limit), "stat": "false"}
        if path:
            params["path"] = path
        return self._req(
            "GET", f"/api/v1/repos/{owner}/{repo}/commits",
            headers=self._token_header(), params=params,
        ) or []
