"""The card write endpoint: the one way a card changes, from the client's side.

Every surface that changes a card (the card browser, `stack memory card
edit`, the agent through that command) sends the change to the memory
stacklet, which checks it against the card type's schema and commits it
as the person it is by (ADR-015). These tests are that client: they read a
card and its version over HTTP, send changes, and look at what the memory
repo holds afterwards.

Forgejo is an external service, so it is a stand-in here
(`pytest-httpserver`) that keeps the files, answers with each file's git
blob id as its `sha`, refuses a write against a `sha` that is no longer
the file's, and records every commit. The memory stacklet's side is real:
its HTTP server, its schemas, the clone of the memory repo it finds cards
and people in. The command that is its client is described in
`tests/unit/surface/test_memory_card_edit.py`.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from werkzeug import Request, Response

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "lib"))
sys.path.insert(0, str(_REPO / "stacklets" / "memory"))
sys.path.insert(0, str(Path(__file__).parent))

from card_edit import CardEditor  # noqa: E402
from cards_api import CardsAPI, serve  # noqa: E402
from simpsons_cards import ONTOLOGY, VAULT  # noqa: E402
from test_memory_graph_cli import Memory  # noqa: E402

from stack.forgejo import ForgejoClient  # noqa: E402
from stack.frontmatter import parse  # noqa: E402

TOKEN = "write-token-for-tests"
CONTENTS = re.compile(r"^/api/v1/repos/family/memory/contents/(?P<path>.+)$")
BART = "family/people/bart.md"
NOTE = "marge/notes/2026/10/art-supplies.md"
SIDECAR = "marge/notes/2026/10/art-supplies.json"


def _blob(text: str) -> str:
    data = text.encode()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()  # noqa: S324 (git's own id)


class Forgejo:
    """The memory repo as Forgejo's contents API serves it, and its commits."""

    def __init__(self, files: dict[str, str]):
        self.files = dict(files)
        self.commits: list[dict] = []

    def __call__(self, request: Request) -> Response:
        path = CONTENTS.match(request.path)["path"]
        if request.method == "GET":
            if path not in self.files:
                return Response("{}", status=404)
            text = self.files[path]
            return Response(json.dumps({"content": base64.b64encode(text.encode()).decode(),
                                        "sha": _blob(text)}), content_type="application/json")
        payload = json.loads(request.data)
        if path not in self.files or payload.get("sha") != _blob(self.files[path]):
            return Response(json.dumps({"message": "sha does not match"}), status=422)
        self.files[path] = base64.b64decode(payload["content"]).decode()
        self.commits.append({"path": path, **payload})
        return Response(json.dumps({"content": {"sha": _blob(self.files[path])}}),
                        content_type="application/json")

    def fields(self, path: str) -> dict:
        return parse(self.files[path])


@pytest.fixture
def forgejo(httpserver):
    stand_in = Forgejo(VAULT)
    httpserver.expect_request(CONTENTS).respond_with_handler(stand_in)
    return stand_in


@pytest.fixture
def endpoint(tmp_path, httpserver, forgejo):
    memory = Memory(tmp_path)
    for path, text in {**VAULT, "ontology.toml": ONTOLOGY}.items():
        (memory.vault / path).parent.mkdir(parents=True, exist_ok=True)
        (memory.vault / path).write_text(text)
    memory._git("add", "-A")
    memory.write("ontology.toml", ONTOLOGY, by="marge", message="feat: the Simpsons")
    editor = CardEditor(vault=memory.vault, token=TOKEN, server_name="simpson",
                        forgejo=ForgejoClient(url=httpserver.url_for(""), token="memory-bot"))
    server = serve(CardsAPI(vault=memory.vault), host="127.0.0.1", port=0, editor=editor)
    yield f"http://127.0.0.1:{server.server_address[1]}"
    # `shutdown` returns at the server's next poll, half a second away, and
    # nothing after a test waits for it.
    threading.Thread(target=server.shutdown, daemon=True).start()


def _send(base: str, method: str, card_id: str, *, body: bytes | None = None,
          headers: dict | None = None) -> tuple[int, dict, dict | str]:
    request = urllib.request.Request(f"{base}/cards/{card_id}", data=body, method=method,
                                     headers=headers or {})
    try:
        with urllib.request.urlopen(request) as r:
            status, sent, raw = r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        status, sent, raw = e.code, dict(e.headers), e.read()
    kind = sent.get("Content-Type", "")
    return status, sent, json.loads(raw) if "json" in kind else raw.decode()


def _read(base: str, card_id: str) -> tuple[dict, str]:
    status, sent, card = _send(base, "GET", card_id)
    assert status == 200
    return card, sent["ETag"]


def _patch(base: str, card_id: str, fields: dict, *, etag: str, actor: str = "marge",
           token: str = TOKEN, kind: str = "application/merge-patch+json"):
    headers = {"Content-Type": kind, "Famstack-Actor": actor}
    if etag:
        headers["If-Match"] = etag
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return _send(base, "PATCH", card_id, body=json.dumps({"fields": fields}).encode(), headers=headers)


class TestReadingACard:

    def test_a_card_comes_with_its_fields_its_text_and_its_version(self, endpoint, forgejo):
        card, etag = _read(endpoint, "person-bart")

        assert card["fields"]["title"] == "Bart Simpson"
        assert card["rendered"].startswith("# Bart Simpson")
        assert etag == f'"{_blob(forgejo.files[BART])}"'

    def test_asked_for_markdown_it_is_the_file(self, endpoint, forgejo):
        status, sent, text = _send(endpoint, "GET", "person-bart", headers={"Accept": "text/markdown"})

        assert (status, text) == (200, forgejo.files[BART])
        assert sent["Content-Type"].startswith("text/markdown")

    def test_a_card_no_file_carries_is_not_found(self, endpoint):
        assert _send(endpoint, "GET", "person-grampa")[0] == 404


class TestChangingACard:

    def test_a_field_of_a_person_is_committed_once_as_the_person(self, endpoint, forgejo):
        _, etag = _read(endpoint, "person-bart")

        status, sent, card = _patch(endpoint, "person-bart", {"description": "Rides a skateboard."}, etag=etag)

        assert status == 200
        assert card["fields"]["description"] == "Rides a skateboard."
        assert forgejo.fields(BART)["description"] == "Rides a skateboard."
        [commit] = forgejo.commits
        assert commit["author"] == {"name": "marge", "email": "marge@simpson"}
        assert commit["message"] == ('edit: Bart Simpson\n\ndescription: Rides a skateboard.\n\n'
                                     'Edit: {"fields": {"description": "Rides a skateboard."}}\n')
        assert sent["ETag"] == f'"{_blob(forgejo.files[BART])}"'

    def test_only_the_field_changes_every_other_byte_stays(self, endpoint, forgejo):
        before = forgejo.files[BART]
        _, etag = _read(endpoint, "person-bart")

        _patch(endpoint, "person-bart", {"description": "Rides a skateboard."}, etag=etag)

        assert forgejo.files[BART] == before.replace("account: bart\n", "account: bart\ndescription: Rides a skateboard.\n")

    def test_a_change_that_changes_nothing_makes_no_commit(self, endpoint, forgejo):
        _, etag = _read(endpoint, "person-bart")

        status, sent, _ = _patch(endpoint, "person-bart", {"title": "Bart Simpson"}, etag=etag)

        assert (status, forgejo.commits) == (200, [])
        assert sent["ETag"] == etag

    def test_new_people_are_linked_in_the_meta_block(self, endpoint, forgejo):
        """The frontmatter holds the ids, the body the links (ADR-017 §3)."""
        _, etag = _read(endpoint, "note-7f3a9c21e4")

        _patch(endpoint, "note-7f3a9c21e4", {"persons": ["marge", "bart"]}, etag=etag)

        assert ("> **About** [Marge](../../../../family/people/marge.md), "
                "[Bart](../../../../family/people/bart.md)\n") in forgejo.files[NOTE]

    def test_a_change_to_a_note_leaves_its_sidecar_as_it_is(self, endpoint, forgejo):
        """A note's sidecar keeps where it came from and how it was made
        (ADR-018): a person changes what the card says, so the commit holds
        the card alone, and its sources are as they were."""
        sidecar = forgejo.files[SIDECAR]
        _, etag = _read(endpoint, "note-7f3a9c21e4")

        status, _, _ = _patch(endpoint, "note-7f3a9c21e4", {"description": "For Marge's class."}, etag=etag)

        assert status == 200
        assert [c["path"] for c in forgejo.commits] == [NOTE]
        assert forgejo.files[SIDECAR] == sidecar
        assert forgejo.files[NOTE].endswith("## Sources\n\n> brushes + paper for next time\n")

    def test_a_removed_field_is_gone_from_the_file(self, endpoint, forgejo):
        _, etag = _read(endpoint, "note-7f3a9c21e4")

        _patch(endpoint, "note-7f3a9c21e4", {"tags": None}, etag=etag)

        assert "tags" not in forgejo.fields(NOTE)
        assert "tags: (removed)" in forgejo.commits[0]["message"]

    def test_a_whole_card_can_be_put(self, endpoint, forgejo):
        _, etag = _read(endpoint, "person-bart")
        text = forgejo.files[BART].replace("# Bart Simpson\n", "# Bart Simpson\n\nSkateboards.\n")

        status, _, card = _send(endpoint, "PUT", "person-bart", body=text.encode(), headers={
            "Content-Type": "text/markdown", "If-Match": etag, "Famstack-Actor": "homer",
            "Authorization": f"Bearer {TOKEN}"})

        assert status == 200
        assert "Skateboards." in card["body"]
        assert forgejo.commits[0]["message"].startswith("edit: Bart Simpson\n\nThe text changed.")


class TestWhatIsRefused:

    def test_a_tag_that_is_no_topic_id_and_no_keyword(self, endpoint, forgejo):
        _, etag = _read(endpoint, "note-7f3a9c21e4")

        status, sent, problem = _patch(endpoint, "note-7f3a9c21e4",
                                       {"tags": ["watercolour", "Gesundheit"]}, etag=etag)

        assert status == 422
        assert sent["Content-Type"] == "application/problem+json"
        [entry] = problem["errors"]
        assert entry["field"] == "tags" and "medical" in entry["allowed"]
        assert forgejo.commits == []

    def test_a_field_no_schema_names(self, endpoint, forgejo):
        _, etag = _read(endpoint, "person-bart")

        status, _, problem = _patch(endpoint, "person-bart", {"nickname": "Bartman"}, etag=etag)

        assert status == 422
        [entry] = problem["errors"]
        assert entry["field"] == "nickname" and "aliases" in entry["allowed"]
        assert forgejo.commits == []

    def test_where_a_note_came_from_which_its_sidecar_keeps(self, endpoint, forgejo):
        """Who filed a note, and from which message, is in its sidecar
        (ADR-018), not among the card's fields, so a change cannot put it
        back on the card."""
        _, etag = _read(endpoint, "note-7f3a9c21e4")

        status, _, problem = _patch(endpoint, "note-7f3a9c21e4", {"filed_by": "bart"}, etag=etag)

        assert status == 422
        [entry] = problem["errors"]
        assert entry["field"] == "filed_by" and "persons" in entry["allowed"]
        assert forgejo.commits == []

    def test_a_read_only_field_with_what_it_holds(self, endpoint, forgejo):
        _, etag = _read(endpoint, "note-7f3a9c21e4")

        status, _, problem = _patch(endpoint, "note-7f3a9c21e4", {"card_id": "note-mine"}, etag=etag)

        assert status == 422
        assert problem["errors"] == [{"pointer": "/fields/card_id", "field": "card_id",
                                      "message": "`card_id` is read-only", "allowed": ["note-7f3a9c21e4"]}]

    def test_a_stale_version_and_nothing_is_written(self, endpoint, forgejo):
        _, etag = _read(endpoint, "person-bart")
        forgejo.files[BART] += "Marge changed this meanwhile.\n"

        status, sent, _ = _patch(endpoint, "person-bart", {"description": "Rides a skateboard."}, etag=etag)

        assert status == 412
        assert sent["ETag"] == f'"{_blob(forgejo.files[BART])}"'
        assert forgejo.commits == [] and "description" not in forgejo.fields(BART)

    def test_a_change_without_a_version(self, endpoint, forgejo):
        assert _patch(endpoint, "person-bart", {"description": "Rides a skateboard."}, etag="")[0] == 428

    def test_a_change_in_another_media_type(self, endpoint):
        _, etag = _read(endpoint, "person-bart")

        assert _patch(endpoint, "person-bart", {"description": "Rides a skateboard."}, etag=etag,
                      kind="application/json")[0] == 415

    def test_a_change_to_a_card_that_is_not_there(self, endpoint):
        assert _patch(endpoint, "person-grampa", {"description": "Rides a skateboard."}, etag='"abc"')[0] == 404

    def test_a_change_without_the_write_token(self, endpoint, forgejo):
        """The token is generated for the memory stacklet and read by the
        host CLI; no other container carries it. A container that reaches the
        endpoint on the stack network, the agent's among them, cannot write."""
        _, etag = _read(endpoint, "person-bart")

        assert _patch(endpoint, "person-bart", {"description": "Rides a skateboard."}, etag=etag, token="")[0] == 401
        assert _patch(endpoint, "person-bart", {"description": "Rides a skateboard."}, etag=etag, token="guess")[0] == 401
        assert forgejo.commits == []

    def test_a_change_by_someone_who_is_no_member(self, endpoint):
        _, etag = _read(endpoint, "person-bart")

        status, _, problem = _patch(endpoint, "person-bart", {"description": "Rides a skateboard."}, etag=etag,
                                    actor="maggie")

        assert status == 422
        assert problem["errors"][0]["allowed"] == ["bart", "homer", "marge"]


def test_before_the_memory_bot_has_a_token_the_endpoint_says_so(tmp_path):
    """The memory bot's Forgejo token is minted after the first start, so for a
    while the curator has none: that is no missing card."""
    editor = CardEditor(vault=tmp_path, forgejo=None, token=TOKEN)

    status, _, body = editor.answer("GET", "person-bart", {}, b"")

    assert status == 503 and b"stack up memory" in body


def test_no_container_but_the_curator_is_given_the_write_token():
    """The write token is in memory's `.env`, which only the curator's
    environment reads from; core's `.env`, read by the bot runner, and the
    agent's do not carry it."""
    holders = [p.relative_to(_REPO).as_posix() for p in (_REPO / "stacklets").rglob("*")
               if p.suffix in (".yml", ".toml") and "CARDS_WRITE_TOKEN" in p.read_text()]

    assert sorted(holders) == ["stacklets/memory/docker-compose.yml", "stacklets/memory/stacklet.toml"]
