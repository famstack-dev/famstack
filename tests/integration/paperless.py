"""Minimal Paperless-ngx API client for integration tests.

Uses urllib.request to avoid adding a requests dependency. Mirrors the
endpoints the archivist bot actually uses, so tests exercise the real
Paperless API contract — same JSON shapes, same auth, same error codes.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass
class PaperlessAPI:
    url: str
    token: str

    def _req(self, method: str, path: str, body: dict | None = None,
             content_type: str = "application/json") -> Any:
        headers = {"Authorization": f"Token {self.token}"}
        data: bytes | None = None
        if body is not None:
            if content_type == "application/json":
                data = json.dumps(body).encode()
                headers["Content-Type"] = "application/json"
            else:
                data = urllib.parse.urlencode(body).encode()
                headers["Content-Type"] = content_type

        req = urllib.request.Request(
            f"{self.url.rstrip('/')}{path}",
            data=data, headers=headers, method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raise PaperlessError(
                f"{method} {path} → {e.code}: {e.read().decode(errors='replace')[:200]}"
            ) from e

    # ── Reads ────────────────────────────────────────────────────────────

    def list_tags(self) -> list[dict]:
        return self._req("GET", "/api/tags/?page_size=1000")["results"]

    def list_document_types(self) -> list[dict]:
        return self._req("GET", "/api/document_types/?page_size=1000")["results"]

    def list_correspondents(self) -> list[dict]:
        return self._req("GET", "/api/correspondents/?page_size=1000")["results"]

    def list_documents(self, query: str | None = None) -> list[dict]:
        path = "/api/documents/?page_size=1000"
        if query:
            path += f"&query={urllib.parse.quote(query)}"
        return self._req("GET", path)["results"]

    def list_notes(self, doc_id: int) -> list[dict]:
        """Notes Paperless has for a document. The archivist writes a
        structured Markdown summary here after classification."""
        body = self._req("GET", f"/api/documents/{doc_id}/notes/")
        if isinstance(body, list):
            return body
        if isinstance(body, dict):
            return body.get("results", []) or []
        return []

    # ── Writes ───────────────────────────────────────────────────────────

    def create_tag(self, name: str, color: str = "#9e9e9e") -> dict:
        return self._req("POST", "/api/tags/", {
            "name": name, "color": color, "matching_algorithm": 0,
        })

    def delete_tag(self, tag_id: int) -> None:
        self._req("DELETE", f"/api/tags/{tag_id}/")

    def delete_document_type(self, type_id: int) -> None:
        self._req("DELETE", f"/api/document_types/{type_id}/")

    def delete_correspondent(self, cid: int) -> None:
        self._req("DELETE", f"/api/correspondents/{cid}/")

    def delete_document(self, doc_id: int) -> None:
        self._req("DELETE", f"/api/documents/{doc_id}/")


class PaperlessError(RuntimeError):
    """HTTP error from Paperless. Message contains status + body snippet."""


def cleanup_prefix(api: PaperlessAPI, prefix: str) -> None:
    """Delete every Paperless entity whose name starts with `prefix`.

    Best-effort: errors deleting one entity don't stop the sweep. Called
    per-test in teardown to keep the shared instance clean.
    """
    def _purge(items, fetch_name, delete):
        for item in items:
            if fetch_name(item).startswith(prefix):
                try:
                    delete(item["id"])
                except PaperlessError:
                    pass

    _purge(api.list_documents(), lambda d: d.get("title", ""), api.delete_document)
    _purge(api.list_tags(), lambda t: t["name"], api.delete_tag)
    _purge(api.list_document_types(), lambda t: t["name"], api.delete_document_type)
    _purge(api.list_correspondents(), lambda c: c["name"], api.delete_correspondent)
