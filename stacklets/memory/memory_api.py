"""The memory's API, as the memory stacklet's own code outside the curator calls it.

The curator holds the memory's clone and is the memory's only writer
(ADR-019). The host commands, the hooks and the migrations, and the memory's
code in the bot runner, read and write through the curator's HTTP API with
this client:

    memory = MemoryAPI.from_host(config)              # a host command's config
    memory = MemoryAPI.from_hook(ctx)                 # a hook's or a migration's context
    camping = memory.file("family/lists/camping.md")  # File, or None
    memory.files("family/lists")        # every File under the directory, by path
    written = memory.commit([put(camping.path, new_text, base=camping.version)],
                            by="marge", kind="chore(lists)", title="marge changed Camping")
    written.commit, written.pending, written.mirrored

    memory.sync()                       # the curator pulls Forgejo now: an edit made there
    memory.card("person-bart")          # (the card as the editor shows it, its version)
    memory.edit("person-bart", {"description": "Rides a skateboard."}, version=v, by="marge")
    memory.versions("note-3f2a1b9c0d")  # its id now, its path, every version, oldest first

From the host it reaches the curator through core's card door on this Mac
(`[core] cards_port`, 42002 unless stack.toml names another), with the card
write token from the instance's secrets. In a container it reaches the
curator on the stack network, `stack-memory-curator:8090`, with the token
from the environment.

What the curator refuses raises `Refused`, whose text is the curator's own
sentence for a person: `reason` is one word for code (`changed`,
`refused`, `missing`, `unauthorised`, ...), `paths` the files that moved,
`errors` the fields a card change got wrong. No answer at all raises
`CuratorDown`, which says what to start. Standard library and the
framework's JSON client only, because the host CLI imports it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import quote

from stack.http_json import Answer, JsonClient, Unreachable

# Core's door to the card browser and the memory's API: published on this
# Mac only (stacklets/core/docker-compose.yml, `127.0.0.1:42002`).
CARDS_PORT = 42002
CURATOR_URL = "http://stack-memory-curator:8090"
TOKEN_SECRET = "memory__CARDS_WRITE_TOKEN"
MEMORY_BOT = "memory-bot"


@dataclass(frozen=True)
class File:
    """A file of the memory as the curator's clone has it, and its version."""

    path: str
    text: str
    version: str


@dataclass(frozen=True)
class Written:
    """What a write did: its commit ("" when nothing changed), whether it
    still waits for Forgejo, and whether the trees people read (the wiki,
    the agent's mount) had it when the curator answered. A write not yet
    mirrored is made all the same; the readers catch up."""

    commit: str
    pending: bool = False
    mirrored: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.commit)


@dataclass(frozen=True)
class CardVersions:
    """A card's history: the id it carries now (a former id finds it too),
    where it is, and every version of it as JSON, oldest first
    (`stack.cards.history.Version.from_json` reads one)."""

    card_id: str
    path: str
    versions: tuple[dict, ...]


@dataclass(frozen=True)
class Synced:
    """How the curator's clone stands against Forgejo: `state` ("ok",
    "unreachable", "refused", "diverged", "missing", or "unknown" before the
    curator's first cycle), what git said, the commits that wait, and, after
    a sync, the commit the clone stands at."""

    state: str
    detail: str = ""
    waiting: tuple[dict, ...] = ()
    head: str = ""


class Refused(Exception):
    """The curator answered, and did not do what was asked."""

    def __init__(self, status: int, reason: str, detail: str, *,
                 paths: tuple[str, ...] = (), errors: tuple[dict, ...] = ()):
        self.status, self.reason, self.paths, self.errors = status, reason, paths, errors
        super().__init__(detail)


class CuratorDown(Exception):
    """No answer from the curator, or from core's door to it."""


def put(path: str, text: str, *, base: str) -> dict:
    """A change that writes `text` at `path`, made from the file at `base`
    ("" for a file that must not exist yet)."""
    return {"path": path, "text": text, "base": base}


def remove(path: str, *, base: str) -> dict:
    """A change that removes the file at `path`, read at `base`."""
    return {"path": path, "remove": True, "base": base}


class MemoryAPI:
    """The curator's API at `base_url`, with the card write `token`."""

    def __init__(self, base_url: str, *, token: str, where: str):
        self._http = JsonClient(base_url, token=token)
        self.token = token
        self._where = where

    @classmethod
    def from_host(cls, config: Mapping) -> MemoryAPI:
        """The API through core's card door on this Mac, as a host command,
        hook or migration reaches it."""
        core = ((config or {}).get("stack") or {}).get("core") or {}
        token = ((config or {}).get("secrets") or {}).get(TOKEN_SECRET, "")
        return cls(f"http://127.0.0.1:{core.get('cards_port') or CARDS_PORT}/cards/api",
                   token=token, where="core's card door on this Mac (`stack up core`)")

    @classmethod
    def from_hook(cls, ctx) -> MemoryAPI:
        """The API through core's card door, as one of memory's hooks or
        migrations on this Mac reaches it: stack.toml and the token from
        the hook context (`ctx.stack`, `ctx.secret`)."""
        return cls.from_host({"stack": ctx.stack.config,
                              "secrets": {TOKEN_SECRET: ctx.secret("CARDS_WRITE_TOKEN") or ""}})

    @classmethod
    def from_container(cls, environ: Mapping[str, str] = os.environ) -> MemoryAPI:
        """The API on the stack network, as code in the bot runner reaches it."""
        return cls(environ.get("MEMORY_API_URL") or CURATOR_URL,
                   token=environ.get("CARDS_WRITE_TOKEN", ""),
                   where="the memory's curator (`stack up memory`)")

    # ── Files and commits ──

    def file(self, path: str) -> File | None:
        """`path` as the curator's clone has it, None when it is not there."""
        answer = self._ask("GET", f"/files/{quote(path)}")
        if answer.status == 404:
            return None
        body = self._ok(answer)
        return File(body["path"], body["text"], body["version"])

    def files(self, under: str) -> list[File]:
        """Every file under the directory `under`, at any depth, by path, as
        the curator's clone has it; none when there is no such directory."""
        body = self._ok(self._ask("GET", f"/tree/{quote(under)}"))
        return [File(f["path"], f["text"], f["version"]) for f in body["files"]]

    def commit(self, changes: list[dict], *, by: str, kind: str, title: str, body: str = "",
               trailers: Mapping[str, str] | None = None, via: str = MEMORY_BOT) -> Written:
        """Write `changes` (`put`, `remove`) as one commit by `by`, through the
        bot `via`; pushed to Forgejo before it returns, or pending."""
        answer = self._ask("POST", "/commits", {
            "kind": kind, "title": title, "body": body, "trailers": dict(trailers or {}),
            "by": by, "via": via, "changes": changes})
        sent = self._ok(answer)
        return Written(sent["commit"], bool(sent["pending"]), bool(sent.get("mirrored")))

    def sync(self) -> Synced:
        """Ask the curator to pull Forgejo and push what waits, now."""
        return _synced(self._ok(self._ask("POST", "/sync", {})))

    def pending(self) -> Synced:
        """What waits for Forgejo, and how the curator's last cycle went."""
        return _synced(self._ok(self._ask("GET", "/pending")))

    def versions(self, card_id: str) -> CardVersions:
        """The card's history; `Refused` with status 404 when no card has the id."""
        body = self._ok(self._ask("GET", f"/cards/{quote(card_id, safe='')}/versions"))
        return CardVersions(body["card_id"], body["path"], tuple(body["versions"]))

    # ── One card's fields, through the card editor ──

    def card(self, card_id: str) -> tuple[dict, str]:
        """The card as the editor shows it (`card_id`, `path`, `fields`,
        `body`, `rendered`) and its version, to change it on."""
        answer = self._ask("GET", f"/cards/{quote(card_id, safe='')}")
        return self._ok(answer), answer.headers.get("etag", "")

    def edit(self, card_id: str, fields: Mapping[str, object], *, version: str, by: str) -> dict:
        """Change the card's `fields` (a merge patch, RFC 7396) as `by`, on
        the `version` it was read at; the card as it is now."""
        answer = self._ask("PATCH", f"/cards/{quote(card_id, safe='')}", {"fields": dict(fields)},
                           headers={"Content-Type": "application/merge-patch+json",
                                    "If-Match": version, "Famstack-Actor": by})
        return self._ok(answer)

    # ── Asking ──

    def _ask(self, method: str, path: str, body: object = None, *,
             headers: dict[str, str] | None = None) -> Answer:
        try:
            return self._http.request(method, path, body, headers=headers)
        except Unreachable as e:
            raise CuratorDown(f"{self._where} does not answer ({e.reason})") from e

    def _ok(self, answer: Answer) -> dict:
        if answer.ok:
            return answer.body
        if answer.status == 401 and not self.token:
            raise Refused(401, "unauthorised", "there is no card write token here yet: "
                                               "`stack up memory` makes it")
        raise Refused(answer.status, str(answer.body.get("reason") or ""), answer.detail,
                      paths=tuple(answer.body.get("paths") or ()),
                      errors=tuple(answer.body.get("errors") or ()))


def _synced(body: dict) -> Synced:
    return Synced(str(body.get("state") or "unknown"), str(body.get("detail") or ""),
                  tuple(body.get("commits") or ()), str(body.get("head") or ""))
