"""The family memory as the archivist reads and writes it: through its curator.

The memory stacklet's curator holds the memory's clone and is the memory's
only writer (ADR-019). The archivist files what people send it into the
memory with this client of the curator's HTTP API, which it reaches on the
stack network:

    memory = MemoryClient.from_environ()            # in the bot runner
    note = memory.file("marge/notes/2026/10/tent-3f2a1b.md")   # File, or None
    memory.paths("marge/notes")                     # the paths under a directory
    memory.versions("note-3f2a1b9c0d")              # the card's history, or None
    written = memory.commit([put(note.path, new_text, base=note.version)],
                            by="marge", kind="capture", title="Tent for the trip",
                            trailers={"Source": "(paste)"})

Every commit is the archivist's (`archivist-bot` is its committer) and by
the person a change is for (`by`, its author, ADR-015). A change names the
version of each file it was made from ("" for a file that must not exist
yet); the curator refuses it when a file moved meanwhile (`Refused` with
`reason` "changed" and the `paths`) and writes nothing. No answer at all
raises `CuratorDown`.

The docs stacklet does not import the memory's code (ADR-019 §2): this is
its own client of the curator's contract, on the framework's JSON client.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import quote

from stack.http_json import Answer, JsonClient, Unreachable

CURATOR_URL = "http://stack-memory-curator:8090"
# The archivist's account, the committer of everything it writes.
ARCHIVIST = "archivist-bot"


@dataclass(frozen=True)
class File:
    """A file of the memory as the curator's clone has it, and its version."""

    path: str
    text: str
    version: str


@dataclass(frozen=True)
class Written:
    """What a write did: its commit ("" when nothing changed), and whether
    it still waits for Forgejo, which the curator pushes it to later."""

    commit: str
    pending: bool = False

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


class Refused(Exception):
    """The curator answered, and did not do what was asked. Its text is
    the curator's sentence for a person; `reason` is one word for code."""

    def __init__(self, status: int, reason: str, detail: str, *, paths: tuple[str, ...] = ()):
        self.status, self.reason, self.paths = status, reason, paths
        super().__init__(detail)


class CuratorDown(Exception):
    """No answer from the memory's curator."""


def put(path: str, text: str, *, base: str) -> dict:
    """A change that writes `text` at `path`, made from the file at `base`
    ("" for a file that must not exist yet)."""
    return {"path": path, "text": text, "base": base}


def remove(path: str, *, base: str) -> dict:
    """A change that removes the file at `path`, read at `base`."""
    return {"path": path, "remove": True, "base": base}


class MemoryClient:
    """The curator's API at `base_url`, with the memory's write `token`."""

    def __init__(self, base_url: str, *, token: str):
        self._http = JsonClient(base_url, token=token)
        self._token = token

    @classmethod
    def from_environ(cls, environ: Mapping[str, str] = os.environ) -> MemoryClient:
        """The curator on the stack network, with the token core's env carries."""
        return cls(environ.get("MEMORY_API_URL") or CURATOR_URL,
                   token=environ.get("CARDS_WRITE_TOKEN", ""))

    def file(self, path: str) -> File | None:
        """`path` as the curator's clone has it, None when it is not there."""
        answer = self._ask("GET", f"/files/{quote(path)}")
        if answer.status == 404:
            return None
        body = self._ok(answer)
        return File(body["path"], body["text"], body["version"])

    def paths(self, under: str = "") -> list[str]:
        """The path of every file under the directory `under`, at any depth,
        or of every file of the memory with no `under`, sorted."""
        return list(self._ok(self._ask("GET", f"/paths/{quote(under)}" if under else "/paths"))["paths"])

    def versions(self, card_id: str) -> CardVersions | None:
        """Every version of the card that carries `card_id`, across its
        moves, oldest first; None when no card carries it."""
        answer = self._ask("GET", f"/cards/{quote(card_id, safe='')}/versions")
        if answer.status == 404:
            return None
        body = self._ok(answer)
        return CardVersions(body["card_id"], body["path"], tuple(body["versions"]))

    def commit(self, changes: list[dict], *, by: str, kind: str, title: str, body: str = "",
               trailers: Mapping[str, str] | None = None) -> Written:
        """Write `changes` (`put`, `remove`) as one commit by `by`, through the
        archivist's account; pushed to Forgejo before it returns, or pending."""
        sent = self._ok(self._ask("POST", "/commits", {
            "kind": kind, "title": title, "body": body, "trailers": dict(trailers or {}),
            "by": by, "via": ARCHIVIST, "changes": changes}))
        return Written(sent["commit"], bool(sent["pending"]))

    def _ask(self, method: str, path: str, body: object = None) -> Answer:
        try:
            return self._http.request(method, path, body)
        except Unreachable as e:
            raise CuratorDown(f"the memory's curator does not answer ({e.reason}); "
                              "`stack up memory` starts it") from e

    def _ok(self, answer: Answer) -> dict:
        if answer.ok:
            return answer.body
        if answer.status == 401 and not self._token:
            raise Refused(401, "unauthorised", "there is no memory write token here yet: "
                                               "`stack up memory` makes it")
        raise Refused(answer.status, str(answer.body.get("reason") or ""), answer.detail,
                      paths=tuple(answer.body.get("paths") or ()))
