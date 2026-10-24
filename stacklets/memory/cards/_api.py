"""The card store over HTTP: a request's JSON in, the store's answer out.

The curator's HTTP server (`cards_api.serve`) hands this the requests for
its routes; everything else here is translation, with no I/O of its own:

    POST /commits              `CardStore.commit`, with the write token
    POST /sync                 the curator's sync now (`sync`), with the write token;
                               answers the commit the clone stands at after it
    GET  /files/<path>         `CardStore.read`: a file and the version to write it on
    GET  /tree/<dir>           `CardStore.files`: every file under a directory, likewise
    GET  /paths[/<dir>]        `CardStore.paths`: the paths alone, of a directory or of all
    GET  /pending              `CardStore.pending`, and how the last cycle went
    GET  /cards/<id>/versions  `CardStore.history` of the card with that id

A write is a JSON object:

    {"kind": "chore(lists)", "title": "marge changed Camping", "by": "marge",
     "via": "memory-bot", "body": "...", "trailers": {"Source": "chat"},
     "changes": [{"path": "family/lists/camping.md", "text": "...", "base": "<version>"},
                 {"path": "family/lists/old.md", "remove": true, "base": "<version>"}]}

`via`, `body` and `trailers` may be left out. The answer is `201` with the
commit, or `200` with an empty one when nothing changed:

    {"commit": "<sha>", "pending": false, "mirrored": true}

`pending` is true while Forgejo does not have the commit yet; `mirrored`
says whether the trees people read (the wiki, the agent's mount) had it
when the answer was sent, as the curator's `on_write` reports it.

A refusal is a problem document (RFC 7807) whose `detail` is a sentence for
a person and whose `reason` is one word for code: `malformed` (400, the
caller's bug), `unauthorised` (401), `changed` (409, with the `paths`),
`refused` (422), `invalid` (422, with the `errors`, one `{"path",
"field", "message"}` per field, from `card_check`), `missing` (503, no
clone yet) or `failed` (500, git).
"""

from __future__ import annotations

import hmac
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from urllib.parse import unquote

from stack.cards.commits import MEMORY_BOT

from ._store import (Change, CardChanged, CardInvalid, CardStore, CloneMissing, Put, Refused, Remove,
                     StoreError, Sync)

Reply = tuple[int, dict[str, str], bytes]

JSON = "application/json"
PROBLEM = "application/problem+json"
_COMMITS, _SYNC, _PENDING, _FILES, _TREE = "/commits", "/sync", "/pending", "/files/", "/tree/"
_PATHS = "/paths"
_VERSIONS = re.compile(r"^/cards/([^/]+)/versions$")


class StoreAPI:
    """Answers the store's routes for the curator's HTTP server.

        api = StoreAPI(store, token=os.environ["CARDS_WRITE_TOKEN"], on_write=mirror_now)
        if api.serves(path):
            status, headers, body = api.answer("POST", path, headers, body)

    `on_write(commit)` is called after each write that committed something:
    the curator mirrors it at once and answers whether the trees people
    read have it, which the answer passes on as `mirrored`. `sync` is what
    `POST /sync` runs: the store's, or the curator's, which also makes the
    clone and gives a new memory its seed.
    """

    def __init__(self, store: CardStore, *, token: str,
                 on_write: Callable[[str], bool] | None = None,
                 sync: Callable[[], Sync] | None = None):
        self.store = store
        self.token = token
        self.on_write = on_write
        self.sync = sync or store.sync

    def serves(self, path: str) -> bool:
        return path in (_COMMITS, _SYNC, _PENDING, _PATHS) \
            or path.startswith((_FILES, _TREE, _PATHS + "/")) or bool(_VERSIONS.match(path))

    def answer(self, method: str, path: str, headers: Mapping[str, str], body: bytes) -> Reply:
        """(status, headers, body) for `method` on one of the routes above.
        `headers` are looked up by lower-case name."""
        if path in (_COMMITS, _SYNC):
            if method != "POST":
                return _problem(405, "method", f"{path} is a POST", {"Allow": "POST"})
            if not self._authorised(headers.get("authorization", "")):
                return _problem(401, "unauthorised", "a write to the memory needs the write token",
                                {"WWW-Authenticate": "Bearer"})
            return self._commit(body) if path == _COMMITS else self._sync()
        if method != "GET":
            return _problem(405, "method", f"{path} is read with GET", {"Allow": "GET"})
        if path == _PENDING:
            return self._pending()
        if path.startswith(_FILES):
            return self._file(unquote(path[len(_FILES):]))
        if path.startswith(_TREE):
            return self._tree(unquote(path[len(_TREE):]))
        if path == _PATHS or path.startswith(_PATHS + "/"):
            return self._paths(unquote(path[len(_PATHS) + 1:]))
        found = _VERSIONS.match(path)
        return self._versions(unquote(found.group(1))) if found else \
            _problem(404, "not-found", f"no route {path}")

    # ── The routes ──

    def _commit(self, raw: bytes) -> Reply:
        try:
            write = _write_from(raw)
        except _Malformed as e:
            return _problem(400, "malformed", str(e))
        try:
            written = self.store.commit(write.changes, by=write.by, via=write.via, kind=write.kind,
                                        title=write.title, body=write.body, trailers=write.trailers)
        except StoreError as e:
            return _refusal(e)
        mirrored = bool(written.changed and self.on_write and self.on_write(written.sha))
        return _json(201 if written.changed else 200,
                     {"commit": written.sha, "pending": written.pending, "mirrored": mirrored})

    def _sync(self) -> Reply:
        synced = self.sync()
        return _json(200, {"state": synced.state, "detail": synced.detail,
                           "commits": [asdict(p) for p in synced.pending], "head": synced.head})

    def _pending(self) -> Reply:
        if not (self.store.repo / ".git").exists():
            return _json(200, {"state": "missing", "detail": str(CloneMissing(self.store.repo)),
                               "commits": []})
        last = self.store.last_sync
        return _json(200, {"state": last.state if last else "unknown",
                           "detail": last.detail if last else "",
                           "commits": [asdict(p) for p in self.store.pending()]})

    def _file(self, path: str) -> Reply:
        try:
            found = self.store.read(path)
        except StoreError as e:
            return _refusal(e)
        if found is None:
            return _problem(404, "not-found", f"{path} is not in the memory")
        return _json(200, {"path": found.path, "text": found.text, "version": found.version})

    def _tree(self, under: str) -> Reply:
        try:
            found = self.store.files(under)
        except StoreError as e:
            return _refusal(e)
        return _json(200, {"files": [{"path": f.path, "text": f.text, "version": f.version}
                                     for f in found]})

    def _paths(self, under: str) -> Reply:
        try:
            return _json(200, {"paths": self.store.paths(under)})
        except StoreError as e:
            return _refusal(e)

    def _versions(self, card_id: str) -> Reply:
        try:
            card = self.store.card(card_id)
            if card is None:
                return _problem(404, "not-found", f"no card in the memory has the id {card_id}")
            versions = self.store.history(card.path, card.card_id)
        except StoreError as e:
            return _refusal(e)
        return _json(200, {"card_id": card.card_id, "path": card.path,
                           "versions": [v.to_json() for v in versions]})

    def _authorised(self, header: str) -> bool:
        scheme, _, given = header.partition(" ")
        return bool(self.token) and scheme.lower() == "bearer" \
            and hmac.compare_digest(given.strip().encode(), self.token.encode())


# ── A write, read from its JSON ──────────────────────────────────────────

class _Malformed(ValueError):
    """The request is not a write as the module docstring describes one."""


@dataclass(frozen=True)
class _Write:
    changes: list[Change]
    kind: str
    title: str
    by: str
    via: str
    body: str
    trailers: dict[str, str]


def _write_from(raw: bytes) -> _Write:
    try:
        data = json.loads(raw or b"null")
    except ValueError as e:
        raise _Malformed("a write is a JSON object, and this body is not JSON") from e
    if not isinstance(data, dict):
        raise _Malformed("a write is a JSON object")
    trailers = data.get("trailers") or {}
    if not (isinstance(trailers, dict)
            and all(isinstance(k, str) and isinstance(v, str) for k, v in trailers.items())):
        raise _Malformed("`trailers` is an object whose values are text")
    changes = data.get("changes")
    if not isinstance(changes, list) or not changes:
        raise _Malformed("`changes` is a list of at least one change")
    return _Write(changes=[_change_from(c, n) for n, c in enumerate(changes)],
                  kind=_text(data, "kind"), title=_text(data, "title"), by=_text(data, "by"),
                  via=_text(data, "via", default=MEMORY_BOT), body=_text(data, "body", default=""),
                  trailers=trailers)


def _change_from(data: object, n: int) -> Change:
    where = f"changes[{n}]"
    if not isinstance(data, dict):
        raise _Malformed(f"{where} is an object")
    path, base = _text(data, "path", where=where), _text(data, "base", where=where)
    if data.get("remove") is True:
        return Remove(path, base)
    if not isinstance(data.get("text"), str):
        raise _Malformed(f"{where} needs `text`, or `remove: true`")
    return Put(path, data["text"], base)


def _text(data: dict, key: str, *, where: str = "a write", default: str | None = None) -> str:
    value = data.get(key, default)
    if not isinstance(value, str):
        raise _Malformed(f"{where} needs `{key}` as text")
    return value


# ── Answers ──────────────────────────────────────────────────────────────

def _refusal(error: StoreError) -> Reply:
    """The store's error as the answer a caller reads it from."""
    if isinstance(error, CardChanged):
        return _problem(409, "changed", str(error), paths=list(error.paths))
    if isinstance(error, CardInvalid):
        return _problem(422, "invalid", str(error), errors=list(error.errors))
    if isinstance(error, Refused):
        return _problem(422, "refused", str(error))
    if isinstance(error, CloneMissing):
        return _problem(503, "missing", str(error))
    return _problem(500, "failed", str(error))


def _json(status: int, body: dict) -> Reply:
    return status, {"Content-Type": JSON}, json.dumps(body, ensure_ascii=False).encode()


def _problem(status: int, reason: str, detail: str, headers: Mapping[str, str] | None = None,
             **extra: object) -> Reply:
    body = {"type": "about:blank", "status": status, "reason": reason, "detail": detail, **extra}
    return status, {"Content-Type": PROBLEM, **(headers or {})}, \
        json.dumps(body, ensure_ascii=False).encode()
