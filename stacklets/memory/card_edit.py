"""A change to one card, the one way every surface changes a card (ADR-015, ADR-017 §9).

The memory stacklet owns the memory repo, so it decides every change to a
card in it. The card browser, `stack memory card edit` and the agent
(through that command) are clients: none of them validates or commits.

    GET   /cards/<card id>    the card: {"card_id", "path", "fields", "body", "rendered"}
                              with its version as the ETag; the file itself
                              when asked for `text/markdown`
    PATCH /cards/<card id>    `application/merge-patch+json` (RFC 7396) on
                              {"fields": ...}; a list is sent whole
    PUT   /cards/<card id>    the whole card as `text/markdown`

A change names the version it was made on (`If-Match`, the ETag) and the
person it is by (`Famstack-Actor`, a declared person with an account), and
carries the write token (`Authorization: Bearer ...`). It is applied to the
file as Forgejo holds it now, its fields checked against the type's schema
(`card_schema.py`), the meta block's links derived again from the fields,
and committed as the person:

    edit: Bart Simpson

    description: Rides a skateboard.

The body names each changed field with its new value. A rebuild reads the
change back from the card's history, as it reads a hand edit (ADR-018
§7). A change that changes nothing makes no commit.

**A person changes fields; only the reprocess puts a whole card.** `PUT`
is the reprocess apply, by the memory bot (`Famstack-Actor: memory-bot`):
it keeps the card's `type` and `card_id`, derives the rest, and commits
`reprocess: <title>` without a trailer, since a regenerate replaces what
it derived. A `PUT` by anyone else is refused with `403`, so every change
a person makes is a patch that can be applied again.

**The version is the file's git blob id**, which Forgejo reports as the
file's `sha` and takes back to refuse a write over a newer file. So the
ETag a client holds is what the commit is made against, with no second
notion of a version to keep in step.

Answers: `200` the card and its new ETag; `401` no write token; `403` a
`PUT` by anyone but the reprocess; `404` no
such card; `503` no Forgejo token yet (the memory bot's is minted after the
first start), or no Matrix server name to make a person's commit address
from; `412` the card changed since (nothing written); `415` the body
is not the media type the method takes; `422` an `application/problem+json`
(RFC 9457) with one entry per field and what it may hold; `428` no
`If-Match`.

Runs in the curator's container: it imports `card_schema`, which needs
`jsonschema`. The host CLI never imports it.
"""

from __future__ import annotations

import hmac
import json
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from card_schema import CardSchemas, VaultValues  # noqa: E402

from lib import BOT_USERNAME, REPO_NAME, REPO_OWNER, NoServerName, commit_author, get_ontology  # noqa: E402
from stack.card_links import with_about_from_fields  # noqa: E402
from stack.card_lookup import find_by_card_id  # noqa: E402
from stack.card_render import render, split_body  # noqa: E402
from stack.forgejo import ForgejoClient, ForgejoError  # noqa: E402
from stack.frontmatter import FrontmatterError, parse, with_field  # noqa: E402
from stack.people import load_people  # noqa: E402

MERGE_PATCH = "application/merge-patch+json"
MARKDOWN = "text/markdown"
PROBLEM = "application/problem+json"
ACTOR = "famstack-actor"
# Who puts a whole card: the reprocess, which derives it from its source
# again as the memory bot. A person changes fields; a rebuild reads that
# change back from the card's history (ADR-018 §7).
REPROCESS_ACTOR = BOT_USERNAME
# What a reprocess keeps of the card it rewrites; it derives everything
# else, the fields read-only to a person included (`timestamp`, `model`).
_IDENTITY = {"type", "card_id"}
# Forgejo's answers to a write against a `sha` that is no longer the file's.
_STALE = ("→ 409", "→ 422")
_HEADING = "# "

Reply = tuple[int, dict[str, str], bytes]


@dataclass
class CardEditor:
    """Answers the three routes above for the memory repo.

        editor = CardEditor(vault=Path("/data/memory/vault"), forgejo=client, token=token)
        status, headers, body = editor.answer("PATCH", "person-bart", headers, body)

    `vault` is the clone the curator keeps: where a card's file is, who the
    people are, which topics there are. The file itself is read from and
    written to Forgejo, which holds the version a change is made against.
    """

    vault: Path
    forgejo: ForgejoClient | None
    token: str
    shared_bucket: str = "family"
    # The Matrix server's name, a person's commit address (`commit_author`).
    server_name: str = ""
    language: str = "en"
    on_write: Callable[[], None] | None = None
    schemas: CardSchemas = field(default_factory=CardSchemas.load)

    def answer(self, method: str, card_id: str, headers: Mapping[str, str], body: bytes) -> Reply:
        """(status, headers, body) for `method` on the card `card_id`.

        `headers` are looked up by lower-case name."""
        if self.forgejo is None:
            return _error(503, "the card endpoint has no Forgejo token yet; run `stack up memory`")
        if method == "GET":
            return self._get(card_id, markdown=MARKDOWN in headers.get("accept", ""))
        if method not in ("PATCH", "PUT"):
            return _error(405, "a card is read with GET and changed with PATCH or PUT")
        if not self._authorised(headers.get("authorization", "")):
            return _error(401, "a change to a card needs the write token", {"WWW-Authenticate": "Bearer"})
        wanted = MERGE_PATCH if method == "PATCH" else MARKDOWN
        if _media_type(headers.get("content-type", "")) != wanted:
            return _error(415, f"{method} takes {wanted}", {"Accept-Patch": MERGE_PATCH})
        if_match = _etag_value(headers.get("if-match", ""))
        if not if_match:
            return _error(428, "a change names the version it was made on: send If-Match with the ETag")
        return self._change(method, card_id, headers.get(ACTOR, "").strip(), if_match, body)

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
        """(path, text, sha) of the card's file as Forgejo holds it, or None."""
        found = find_by_card_id(card_id, root=self.vault)
        if found is None or self.forgejo is None:
            return None
        current = self.forgejo.get_file(REPO_OWNER, REPO_NAME, found.path)
        if not current:
            return None
        return found.path, current.get("content", ""), current.get("sha", "")

    def _card_reply(self, path: str, text: str, sha: str) -> Reply:
        fields = parse(text)
        people = load_people(self.vault, self.shared_bucket) or []
        rendered = render(fields, split_body(text), names={p.id: p.first_name for p in people},
                          language=self.language).markdown
        card = {"card_id": str(fields.get("card_id") or ""), "path": path, "fields": fields,
                "body": split_body(text), "rendered": rendered}
        return 200, {"Content-Type": "application/json; charset=utf-8", "ETag": _etag(sha)}, \
            json.dumps(card, ensure_ascii=False).encode()

    # ── Changing ──────────────────────────────────────────────────────────

    def _change(self, method: str, card_id: str, actor: str, if_match: str, body: bytes) -> Reply:
        reprocess = method == "PUT"
        if reprocess and actor != REPROCESS_ACTOR:
            return _error(403, "a whole card is put only by the reprocess; change its fields with PATCH")
        found = self._file(card_id)
        if found is None:
            return _error(404, f"no card {card_id!r}")
        path, text, sha = found
        if if_match != sha:
            return _error(412, "the card changed since that version; read it again", {"ETag": _etag(sha)})
        people = load_people(self.vault, self.shared_bucket) or []
        members = sorted(p.id for p in people if p.member)
        if not reprocess and actor not in members:
            return _problem([{"field": "Famstack-Actor", "allowed": members,
                              "message": "a change is by a declared person with an account"}])
        before = parse(text)
        proposed = _put(body) if reprocess else _patched(text, before, body)
        if isinstance(proposed, list):
            return _problem(proposed)
        after_text, after = proposed
        values = VaultValues([p.id for p in people], get_ontology(self.vault))
        kept = _IDENTITY if reprocess else self.schemas.read_only(str(before.get("type")))
        problems = _read_only_problems(before, after, kept)
        problems += [p for p in self.schemas.problems(after, values)
                     if p["field"] not in {q["field"] for q in problems}]
        if problems:
            return _problem(problems)
        after_text = _with_heading(with_about_from_fields(after_text, path, bucket=self.shared_bucket),
                                   before, after)
        if after_text == text:
            return self._card_reply(path, text, sha)
        message = reprocess_message(before, after, text, after_text) if reprocess \
            else edit_message(before, after)
        return self._commit(path, after_text, sha, actor, message)

    def _commit(self, path: str, text: str, sha: str, actor: str, message: str) -> Reply:
        assert self.forgejo is not None  # `_file` found the card through it
        try:
            name, email = commit_author(actor, self.server_name)
        except NoServerName as e:
            return _error(503, str(e))
        try:
            written = self.forgejo.put_file(REPO_OWNER, REPO_NAME, path, content=text, message=message,
                                            sha=sha, author_name=name, author_email=email)
        except ForgejoError as e:
            if any(code in str(e) for code in _STALE):
                return _error(412, "the card changed while this change was made; read it again")
            return _error(502, f"the memory repo did not take the change: {e}")
        if self.on_write is not None:
            self.on_write()
        new_sha = ((written or {}).get("content") or {}).get("sha", "")
        return self._card_reply(path, text, new_sha)

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


def edit_message(before: Mapping[str, object], after: Mapping[str, object]) -> str:
    """A person's change: `edit: <title>` and each changed field with its
    new value. No trailer: a rebuild reads the change from the diff
    (ADR-018 §7)."""
    patch = fields_patch(before, after)
    return f"edit: {after.get('title') or before.get('title')}\n\n" + _changed(patch) + "\n"


def reprocess_message(before: Mapping[str, object], after: Mapping[str, object],
                      text_before: str, text_after: str) -> str:
    """The reprocess apply: `reprocess: <title>` and what changed. No trailer:
    the card is derived again, which a regenerate replaces (ADR-015 §4)."""
    lines = [_changed(fields_patch(before, after))]
    if split_body(text_before) != split_body(text_after):
        lines.append("The text changed.")
    return f"reprocess: {after.get('title') or before.get('title')}\n\n" \
        + "\n".join(line for line in lines if line) + "\n"


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
    if not isinstance(patch, dict) or set(patch) - {"fields"} or not isinstance(patch.get("fields", {}), dict):
        return [{"field": "body", "allowed": ['{"fields": {...}}'],
                 "message": "a patch changes `fields` only; PUT the card to change its text"}]
    after = merge_patch(before, patch.get("fields", {}))
    assert isinstance(after, dict)
    for key in [k for k in before if k not in after]:
        text = with_field(text, key, "")
    for key, value in after.items():
        if before.get(key) != value:
            text = with_field(text, key, value)
    return text, after


def _put(body: bytes) -> tuple[str, dict] | list[dict]:
    """The card a PUT sends, or the problem with its frontmatter."""
    text = body.decode("utf-8", errors="replace")
    try:
        fields = parse(text)
    except FrontmatterError as e:
        return [{"field": "frontmatter", "message": f"does not parse: {e}", "allowed": []}]
    if not fields:
        return [{"field": "frontmatter", "message": "a card starts with its frontmatter", "allowed": []}]
    return text, fields


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


def _error(status: int, detail: str, headers: Mapping[str, str] | None = None) -> Reply:
    body = {"type": "about:blank", "status": status, "detail": detail}
    return status, {"Content-Type": PROBLEM, **(headers or {})}, json.dumps(body).encode()


def _problem(errors: list[dict]) -> Reply:
    body = {"type": "about:blank", "title": "The card does not meet its schema", "status": 422,
            "errors": [{"pointer": f"/fields/{e['field']}", **e} for e in errors]}
    return 422, {"Content-Type": PROBLEM}, json.dumps(body, ensure_ascii=False).encode()
