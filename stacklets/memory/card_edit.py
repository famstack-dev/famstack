"""A change to one card, the one way every surface changes a card (ADR-015, ADR-017 §9).

The memory stacklet owns the memory repo, so it decides every change to a
card in it. The card browser, `stack memory card edit` and the agent
(through that command) are clients: none of them validates or commits.

    GET   /cards/<card id>    the card: {"card_id", "path", "fields", "body", "rendered"}
                              with its version as the ETag; the file itself
                              when asked for `text/markdown`
    PATCH /cards/<card id>    `application/merge-patch+json` (RFC 7396) on
                              {"fields": ..., "text": ...}; a list is sent whole,
                              the text replaces what the card says under its title
    POST  /cards              a new card, as data (`card_create.py`): `201`
                              with {"card_id", "path"}, the card at `Location`
    POST  /cards/<id>/sources a source more for the card, appended (`card_create.py`)

A change names the version it was made on (`If-Match`, the ETag) and the
person it is by (`Famstack-Actor`, a declared person with an account), and
carries the write token (`Authorization: Bearer ...`). It is applied to the
file as the memory's clone holds it now, its fields checked against the
type's schema (`card_schema.py`), the meta block's links derived again from
the fields, and committed as the person through the card store
(`memory.cards`), which pushes it to Forgejo:

    edit: Bart Simpson

    description: Rides a skateboard.

The body names each changed field with its new value. A rebuild reads the
change back from the card's history, as it reads a hand edit (ADR-018
§7). A change that changes nothing makes no commit.

**A person changes fields and the text, nothing else.** A card's sources
are only ever added (`POST /cards/<id>/sources`). A whole card is written
only by the reprocess, through the card store (`stack memory reprocess
--apply`), never here.

**A create names no file and no commit.** It says which person or topic
the card belongs to (`in`), and the memory names the file as the
archivist does and commits it as `capture: <title>`, by the person the
call is by, through the bot it names (`via`). A create of a card that is
filed already (the same link or words, in the same place) is refused with
`409` and the card's id; nothing is written. A source added is committed
as `reply: <title>`.

**The version is the file's git blob id**, the one the card store writes
against and refuses a write over a newer file with. So the ETag a client
holds is what the commit is made against, with no second notion of a
version to keep in step.

Answers: `200` the card and its new ETag; `401` no write token; `404` no
such card; `405` a method other than GET and PATCH; `503` no clone of the
memory yet, or no Matrix server name to make a person's commit address
from; `400` a create or a source that is not as described; `409` a card
filed already, with its `card_id`; `412` the card changed since (nothing written); `415` the body
is not the media type the method takes; `422` an `application/problem+json`
(RFC 9457) with one entry per field and what it may hold; `428` no
`If-Match`; `500` git failed in the clone, with what it said.

Runs in the curator's container: it imports `card_schema`, which needs
`jsonschema`. The host CLI never imports it.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field


from .card_schema import CardSchemas, VaultValues

from .card_create import (CREATABLE, Malformed, NewCard, SourceAdded, folder_of, new_card,
                          new_source, note_fields, note_files, note_path, with_source)
from .lib import get_ontology
from .cards import CardChanged, CardStore, CloneMissing, Put, Refused, StoreError
from stack.cards import card_id_from, card_identity
from stack.cards.links import with_about_from_fields
from stack.cards.commits import MEMORY_BOT
from stack.cards.sources import interpretation_of, sidecar_path, with_text
from stack.card_render import render, split_body
from stack.frontmatter import parse, with_field
from stack.people import Person, load_people
from stack.vault import capture_folder, same_capture

MERGE_PATCH = "application/merge-patch+json"
MARKDOWN = "text/markdown"
JSON = "application/json"
_SOURCES = "/sources"
PROBLEM = "application/problem+json"
ACTOR = "famstack-actor"
_HEADING = "# "

Reply = tuple[int, dict[str, str], bytes]


@dataclass(frozen=True)
class _Checked:
    """A change that passed the schema: the card's new text and fields."""

    text: str
    fields: dict


@dataclass
class CardEditor:
    """Answers the two routes above for the memory repo.

        editor = CardEditor(store=vault.store, token=token)
        status, headers, body = editor.answer("PATCH", "person-bart", headers, body)

    `store` is the curator's card store: its clone says where a card's
    file is, who the people are and which topics there are, and a change
    is committed through it.
    """

    store: CardStore
    token: str
    shared_bucket: str = "family"
    language: str = "en"
    on_write: Callable[[], None] | None = None
    schemas: CardSchemas = field(default_factory=CardSchemas.load)

    def answer(self, method: str, card_id: str, headers: Mapping[str, str], body: bytes) -> Reply:
        """(status, headers, body) for `method` on the card `card_id`.

        `headers` are looked up by lower-case name."""
        try:
            return self._answer(method, card_id, headers, body)
        except CloneMissing as e:
            return _error(503, str(e))

    def _answer(self, method: str, card_id: str, headers: Mapping[str, str], body: bytes) -> Reply:
        if method == "GET":
            return self._get(card_id, markdown=MARKDOWN in headers.get("accept", ""))
        if method not in ("PATCH", "POST"):
            return _error(405, "a card is read with GET, changed with PATCH and created or "
                          "given a source with POST", {"Allow": "GET, PATCH, POST"})
        if not self._authorised(headers.get("authorization", "")):
            return _error(401, "a change to a card needs the write token", {"WWW-Authenticate": "Bearer"})
        if method == "POST":
            return self._post(card_id, headers, body)
        if _media_type(headers.get("content-type", "")) != MERGE_PATCH:
            return _error(415, f"PATCH takes {MERGE_PATCH}", {"Accept-Patch": MERGE_PATCH})
        if_match = _etag_value(headers.get("if-match", ""))
        if not if_match:
            return _error(428, "a change names the version it was made on: send If-Match with the ETag")
        return self._change(card_id, headers.get(ACTOR, "").strip(), if_match, body)

    # ── Reading ───────────────────────────────────────────────────────────

    def _get(self, card_id: str, *, markdown: bool) -> Reply:
        found = self._file(card_id)
        if found is None:
            return _error(404, f"no card {card_id!r}")
        path, text, sha = found
        if markdown:
            return 200, {"Content-Type": f"{MARKDOWN}; charset=utf-8", "ETag": _etag(sha)}, text.encode()
        return self._card_reply(path, text, sha)

    def _file(self, card_id: str) -> tuple[str, str, str] | None:
        """(path, text, version) of the card's file as the clone's last
        commit has it, or None."""
        card = self.store.card(card_id)
        return (card.path, card.text, card.version) if card else None

    def _card_reply(self, path: str, text: str, sha: str) -> Reply:
        fields = parse(text)
        people = load_people(self.store.repo, self.shared_bucket) or []
        rendered = render(fields, split_body(text), names={p.id: p.first_name for p in people},
                          language=self.language).markdown
        card = {"card_id": str(fields.get("card_id") or ""), "path": path, "fields": fields,
                "body": split_body(text), "rendered": rendered}
        return 200, {"Content-Type": "application/json; charset=utf-8", "ETag": _etag(sha)}, \
            json.dumps(card, ensure_ascii=False).encode()

    # ── Changing ──────────────────────────────────────────────────────────

    def _change(self, card_id: str, actor: str, if_match: str, body: bytes) -> Reply:
        found = self._file(card_id)
        if found is None:
            return _error(404, f"no card {card_id!r}")
        path, text, sha = found
        if if_match != sha:
            return _error(412, "the card changed since that version; read it again", {"ETag": _etag(sha)})
        people = load_people(self.store.repo, self.shared_bucket) or []
        if problem := _not_a_member(actor, people):
            return problem
        before = parse(text)
        proposed = _patched(text, before, body)
        changed = self._checked(path, text, before, proposed, people)
        if not isinstance(changed, _Checked):
            return changed
        after_text, after = changed.text, changed.fields
        if after_text == text:
            return self._card_reply(path, text, sha)
        message = "\n".join(filter(None, [edit_body(before, after),
                                           _text_changed(text, after_text)]))
        return self._commit([Put(path, after_text, base=sha)], by=actor, kind="edit",
                            title=edit_title(before, after), body=message)

    def _checked(self, path: str, text: str, before: dict,
                 proposed: tuple[str, dict] | list[dict], people: list[Person]) -> _Checked | Reply:
        """The card a change makes, checked against its type's schema and with
        its meta block and heading following its fields; or the refusal."""
        if isinstance(proposed, list):
            return _problem(proposed)
        after_text, after = proposed
        values = VaultValues([p.id for p in people], get_ontology(self.store.repo))
        problems = _read_only_problems(before, after, self.schemas.read_only(str(before.get("type"))))
        problems += [p for p in self.schemas.problems(after, values)
                     if p["field"] not in {q["field"] for q in problems}]
        if problems:
            return _problem(problems)
        return _Checked(_with_heading(with_about_from_fields(after_text, path, bucket=self.shared_bucket),
                                      before, after), after)

    # ── Creating a card, adding a source ─────────────────────────────────

    def _post(self, card_id: str, headers: Mapping[str, str], body: bytes) -> Reply:
        """`POST /cards` creates a card, `POST /cards/<id>/sources` adds a source."""
        if _media_type(headers.get("content-type", "")) != JSON:
            return _error(415, f"POST takes {JSON}")
        people = load_people(self.store.repo, self.shared_bucket) or []
        actor = headers.get(ACTOR, "").strip()
        if problem := _not_a_member(actor, people):
            return problem
        try:
            if not card_id:
                return self._create(new_card(body), actor, people)
            if card_id.endswith(_SOURCES):
                return self._add_source(card_id.removesuffix(_SOURCES), new_source(body), actor, people)
        except Malformed as e:
            return _error(400, str(e))
        return _error(405, "a card is given a source at /cards/<id>/sources", {"Allow": "GET, PATCH"})

    def _create(self, card: NewCard, actor: str, people: list[Person]) -> Reply:
        """Write a new note and its sidecar in one commit by `actor`, unless it is filed already."""
        if card.type not in CREATABLE:
            return _problem([{"field": "type", "allowed": list(CREATABLE),
                              "message": f"creating a {card.type or 'card'} is not supported yet"}])
        topic = self.store.card(card.belongs_to) if card.belongs_to.startswith("topic-") else None
        folder = folder_of(card.belongs_to, people=people, topic_card=topic.path if topic else None,
                           shared_bucket=self.shared_bucket)
        if folder is None:
            return _problem([{"field": "in", "allowed": [],
                              "message": "a card belongs to a declared person or a topic, by its card id"}])
        path = note_path(card, folder)
        if filed := self._filed(path):
            return _error(409, f"this note is filed already, as {filed}", extra={"card_id": filed})
        fields = note_fields(card, card_identity({}, card.type, path))
        values = VaultValues([p.id for p in people], get_ontology(self.store.repo))
        if problems := self.schemas.problems(fields, values):
            return _problem(problems)
        text, sidecar = note_files(card, fields, path, by=actor, people=people,
                                   shared_bucket=self.shared_bucket)
        return self._commit([Put(path, text, base=""), Put(sidecar_path(path), sidecar, base="")],
                            by=actor, via=card.via, kind="capture", title=card.title,
                            trailers=card.trailers(), status=201)

    def _filed(self, path: str) -> str:
        """The id of the card filed already with `path`'s identity, "" for none."""
        folder = capture_folder(path)
        found = same_capture(path, self.store.paths(folder)) if folder else None
        file = self.store.read(found) if found else None
        return card_id_from(parse(file.text)) if file else ""

    def _add_source(self, card_id: str, added: SourceAdded, actor: str,
                    people: list[Person]) -> Reply:
        """Append the source to the card and its sidecar, with the text and fields
        it changed, in one commit by `actor`, as the archivist's reply is one."""
        card = self.store.card(card_id)
        if card is None:
            return _error(404, f"no card {card_id!r}")
        sidecar = self.store.read(sidecar_path(card.path))
        if sidecar is None:
            return _problem([{"field": "type", "allowed": list(CREATABLE),
                              "message": "only a card with sources is given a source"}])
        text, fields = card.text, parse(card.text)
        if added.patch:
            changed = self._checked(card.path, text, fields, _applied(text, fields, added.patch), people)
            if not isinstance(changed, _Checked):
                return changed
            text, fields = changed.text, changed.fields
        text, side = with_source(text, sidecar.text, added.source, made=added.made)
        return self._commit([Put(card.path, text, base=card.version),
                             Put(sidecar.path, side, base=sidecar.version)],
                            by=actor, via=added.via, kind="reply", title=str(fields.get("title") or ""),
                            trailers=added.trailers())

    # ── Committing ────────────────────────────────────────────────────────

    def _commit(self, changes: list[Put], *, by: str, kind: str, title: str, body: str = "",
                trailers: dict[str, str] | None = None, via: str = MEMORY_BOT,
                status: int = 200) -> Reply:
        """Commit `changes` through the card store; the card (the first change) as
        it is then, with `status`. A card changed meanwhile is `412`, nothing written."""
        path, text = changes[0].path, changes[0].text
        try:
            self.store.commit(changes, by=by, via=via, kind=kind, title=title, body=body,
                              trailers=trailers)
        except CardChanged:
            current = self.store.read(path)
            return _error(412, "the card changed while this change was made; read it again",
                          {"ETag": _etag(current.version)} if current else None)
        except (Refused, CloneMissing) as e:
            return _error(503, str(e))
        except StoreError as e:
            return _error(500, str(e))
        if self.on_write is not None:
            self.on_write()
        written = self.store.read(path)
        reply = self._card_reply(path, text, written.version if written else "")
        if status == 201:
            card_id = json.loads(reply[2])["card_id"]
            return 201, {**reply[1], "Location": f"/cards/{card_id}"}, reply[2]
        return reply

    def _authorised(self, header: str) -> bool:
        scheme, _, given = header.partition(" ")
        return bool(self.token) and scheme.lower() == "bearer" \
            and hmac.compare_digest(given.strip().encode(), self.token.encode())


# ── What a change makes of the card ───────────────────────────────────────

def merge_patch(target: object, patch: object) -> object:
    """`patch` applied to `target` as RFC 7396 says: a null removes, an object
    merges key by key, anything else (a list included) replaces.

    >>> merge_patch({"tags": ["a"], "title": "x"}, {"tags": ["a", "b"], "title": None})
    {'tags': ['a', 'b']}
    """
    if not isinstance(patch, dict):
        return patch
    result = dict(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = merge_patch(result.get(key), value)
    return result


def fields_patch(before: Mapping[str, object], after: Mapping[str, object]) -> dict:
    """The merge patch that takes the fields `before` to `after`.

    >>> fields_patch({"title": "x", "kind": "todo"}, {"title": "y"})
    {'title': 'y', 'kind': None}
    """
    changed = {k: v for k, v in after.items() if before.get(k) != v}
    removed = {k: None for k in before if k not in after}
    return {**changed, **removed}


def edit_title(before: Mapping[str, object], after: Mapping[str, object]) -> str:
    """What a person's change is about: the card's title, `edit: <title>`."""
    return str(after.get("title") or before.get("title") or "")


def edit_body(before: Mapping[str, object], after: Mapping[str, object]) -> str:
    """Each changed field with its new value, one per line. No trailer: a
    rebuild reads the change from the diff (ADR-018 §7)."""
    return _changed(fields_patch(before, after))


def _text_changed(before: str, after: str) -> str:
    """The line an edit's commit body gives a changed text."""
    return "text: (changed)" if interpretation_of(before) != interpretation_of(after) else ""


def _not_a_member(actor: str, people: list[Person]) -> Reply | None:
    """The refusal for a call by anyone but a declared person with an account."""
    members = sorted(p.id for p in people if p.member)
    if actor in members:
        return None
    return _problem([{"field": "Famstack-Actor", "allowed": members,
                      "message": "a change is by a declared person with an account"}])


def _changed(patch: Mapping[str, object]) -> str:
    return "\n".join(f"{k}: {_shown(v)}" for k, v in patch.items())


def _shown(value: object) -> str:
    if value is None:
        return "(removed)"
    if isinstance(value, list):
        return ", ".join(map(str, value)) or "(none)"
    return str(value)


def _patched(text: str, before: dict, body: bytes) -> tuple[str, dict] | list[dict]:
    """The card text and fields a merge patch makes, or the problems with it."""
    try:
        patch = json.loads(body or b"null")
    except ValueError as e:
        return [{"field": "body", "message": f"the patch is no JSON: {e}", "allowed": []}]
    return _applied(text, before, patch)


def _applied(text: str, before: dict, patch: object) -> tuple[str, dict] | list[dict]:
    """The card text and fields `patch` ({"fields", "text"}) makes, or the problems with it."""
    if not isinstance(patch, dict) or set(patch) - {"fields", "text"} \
            or not isinstance(patch.get("fields", {}), dict) \
            or not isinstance(patch.get("text", ""), str):
        return [{"field": "body", "allowed": ['{"fields": {...}, "text": "..."}'],
                 "message": "a patch changes `fields` and `text` only"}]
    if "text" in patch:
        text = with_text(text, patch["text"])
    after = merge_patch(before, patch.get("fields", {}))
    assert isinstance(after, dict)
    for key in [k for k in before if k not in after]:
        text = with_field(text, key, "")
    for key, value in after.items():
        if before.get(key) != value:
            text = with_field(text, key, value)
    return text, after


def _read_only_problems(before: Mapping[str, object], after: Mapping[str, object],
                        read_only: set[str]) -> list[dict]:
    return [{"field": name, "message": f"`{name}` is read-only",
             "allowed": [before[name]] if name in before else []}
            for name in sorted(read_only) if before.get(name) != after.get(name)]


def _with_heading(text: str, before: Mapping[str, object], after: Mapping[str, object]) -> str:
    """`text` with its title heading following a changed `title`; a heading
    that said something else is the person's and stays."""
    old, new = str(before.get("title") or ""), str(after.get("title") or "")
    if old == new or not new:
        return text
    return text.replace(f"\n{_HEADING}{old}\n", f"\n{_HEADING}{new}\n", 1)


# ── Answers ───────────────────────────────────────────────────────────────

def _media_type(header: str) -> str:
    return header.split(";", 1)[0].strip().lower()


def _etag(sha: str) -> str:
    return f'"{sha}"'


def _etag_value(header: str) -> str:
    return header.strip().removeprefix("W/").strip('"')


def _error(status: int, detail: str, headers: Mapping[str, str] | None = None, *,
           extra: Mapping[str, object] | None = None) -> Reply:
    body = {"type": "about:blank", "status": status, "detail": detail, **(extra or {})}
    return status, {"Content-Type": PROBLEM, **(headers or {})}, json.dumps(body).encode()


def _problem(errors: list[dict]) -> Reply:
    body = {"type": "about:blank", "title": "The card does not meet its schema", "status": 422,
            "errors": [{"pointer": f"/fields/{e['field']}", **e} for e in errors]}
    return 422, {"Content-Type": PROBLEM}, json.dumps(body, ensure_ascii=False).encode()
