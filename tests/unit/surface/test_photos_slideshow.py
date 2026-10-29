"""An album as a slideshow page, as the stack asks for it and a TV opens it.

A service of the stack (the agent, later) asks core's tools server for an
album's slideshow on the internal listener and gets a link on the stack's
public address. Any browser on the home network opens that link on the
published port, without login, and the page plays the album's photos.

Driven through the real thing: the tools server's FastAPI app with its real
access rule. Immich is not the stack's code, so it is stood in at its HTTP
boundary with pytest-httpserver, answering the requests the spike verified
on Immich 3.3.5 (`POST /api/shared-links`, `GET /api/shared-links/me?key=`,
`POST /api/search/metadata`).

The page is read without running its scripts: the photos, their captions
and the title card are in its markup, and the script only plays them.

The data is the Simpsons'.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone

import pytest
from werkzeug import Request, Response

from .conftest import LIB, STACKLETS
from .dsl import Browser

TOOLS = STACKLETS / "core" / "tools-server"
for _path in (LIB, TOOLS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from access import INTERNAL_PORT, PUBLIC_PORT  # noqa: E402

API_KEY = "homers-api-key"
SHARE_KEY = "Xq3vT9cLw2pR7mK1sN8bY4hJ6dF0aZ5eU"
PHOTOS_PUBLIC = "http://192.168.1.10:42010"
LINK_BASE = "http://192.168.1.10:42000/go"

SANTORINI = {"id": "a1b2c3d4-0000-4000-8000-000000000001", "albumName": "Santorini"}
EMPTY = {"id": "a1b2c3d4-0000-4000-8000-000000000002", "albumName": "Springfield Gorge"}
ARCHIVED_ONLY = {"id": "a1b2c3d4-0000-4000-8000-000000000003", "albumName": "Moe's"}


def photo(n: int, taken: str, city: str, visibility: str = "timeline") -> dict:
    """An image asset as Immich's metadata search answers it, with EXIF."""
    return {"id": f"0000000{n}-feed-4000-8000-00000000000{n}", "type": "IMAGE",
            "fileCreatedAt": f"{taken}Z", "localDateTime": f"{taken}.000Z",
            "visibility": visibility, "exifInfo": {"city": city, "country": "Greece"}}


# Out of order on purpose: the page plays them in the order they were taken.
SUNSET = photo(3, "2026-08-12T19:40:00", "Oia")
FERRY = photo(1, "2026-08-10T09:15:00", "Athinios")
BEACH = photo(2, "2026-08-11T14:05:00", "Kamari")
ARCHIVED = photo(4, "2026-08-13T10:00:00", "Fira", visibility="archive")


class Immich:
    """Immich at its HTTP boundary: Homer's albums, the timeline photos a
    metadata search finds in each, and the share links made through it."""

    def __init__(self, server, albums: dict[str, list[dict]]):
        self.server = server
        self.albums = albums
        self.links: list[dict] = []
        server.expect_request("/api/albums", method="GET").respond_with_handler(self._albums)
        server.expect_request("/api/search/metadata", method="POST").respond_with_handler(self._search)
        server.expect_request("/api/shared-links", method="POST").respond_with_handler(self._share)
        server.expect_request("/api/shared-links/me", method="GET").respond_with_handler(self._me)

    def _albums(self, request: Request) -> Response:
        if request.headers.get("x-api-key") != API_KEY:
            return Response(status=401)
        listed = [{"id": a["id"], "albumName": a["albumName"]} for a in (SANTORINI, EMPTY, ARCHIVED_ONLY)]
        return Response(json.dumps(listed), content_type="application/json")

    def _search(self, request: Request) -> Response:
        if request.headers.get("x-api-key") != API_KEY:
            return Response(status=401)
        q = request.get_json()
        found = [a for album in q.get("albumIds", []) for a in self.albums.get(album, [])
                 if q.get("visibility") in (None, a["visibility"])]
        if not q.get("withExif"):
            found = [{k: v for k, v in a.items() if k != "exifInfo"} for a in found]
        return Response(json.dumps({"assets": {"items": found, "nextPage": None}}),
                        content_type="application/json")

    def _share(self, request: Request) -> Response:
        if request.headers.get("x-api-key") != API_KEY:
            return Response(status=401)
        link = request.get_json() | {"key": SHARE_KEY, "id": "link-1"}
        self.links.append(link)
        return Response(json.dumps(link), status=201, content_type="application/json")

    def _me(self, request: Request) -> Response:
        """Without login: the link the key names, with its assets, stripped of
        EXIF as `showMetadata: false` makes Immich do. 401 once it expired."""
        link = next((link for link in self.links if link["key"] == request.args.get("key")), None)
        if link is None or datetime.fromisoformat(link["expiresAt"]) < datetime.now(timezone.utc):
            return Response(json.dumps({"message": "Invalid share key"}), status=401,
                            content_type="application/json")
        assets = [{"id": i} for i in link["assetIds"]]
        answer = {k: v for k, v in link.items() if k != "assetIds"} | {"assets": assets}
        return Response(json.dumps(answer), content_type="application/json")

    def made_by_hand(self, key: str) -> None:
        """A share link a family member made in Immich's own share dialog."""
        self.links.append({"key": key, "id": "link-2", "type": "INDIVIDUAL", "description": "",
                           "assetIds": [BEACH["id"]], "showMetadata": True,
                           "expiresAt": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()})


@pytest.fixture
def immich(httpserver, monkeypatch) -> Immich:
    """Immich with three of Homer's albums, and core pointed at it as its env does."""
    stand_in = Immich(httpserver, {SANTORINI["id"]: [SUNSET, FERRY, BEACH, ARCHIVED],
                                   EMPTY["id"]: [], ARCHIVED_ONLY["id"]: [ARCHIVED]})
    monkeypatch.setenv("IMMICH_URL", httpserver.url_for("").rstrip("/"))
    monkeypatch.setenv("IMMICH_API_KEY", API_KEY)
    monkeypatch.setenv("IMMICH_PUBLIC_URL", PHOTOS_PUBLIC)
    monkeypatch.setenv("LINK_BASE_URL", LINK_BASE)
    return stand_in


@pytest.fixture(scope="module")
def tools_server():
    import server

    return server.app


@pytest.fixture
def the_agent(tools_server) -> Browser:
    """A service of the stack, on the Docker network: the internal listener."""
    return Browser(tools_server, listener=INTERNAL_PORT, host="stack-core-tools:8000")


@pytest.fixture
def tv(tools_server) -> Browser:
    """The TV's browser on the home network, opening the Mac's published port."""
    return Browser(tools_server, listener=PUBLIC_PORT, host="192.168.1.10:42000")


def path_of(link: str) -> str:
    assert link.startswith(LINK_BASE + "/"), link
    return link.removeprefix(LINK_BASE.removesuffix("/go"))


# ── Asking for a slideshow ───────────────────────────────────────────────

def test_an_album_opens_as_a_slideshow_of_its_timeline_photos_in_the_order_taken(immich, the_agent, tv):
    """Given Homer's Santorini album with three timeline photos and an archived
    one, when the agent asks for its slideshow, then the link it gets opens on
    the published port without login and the page holds exactly the three, in
    the order they were taken, each loaded from Immich's public address with
    the share key, each captioned with its day and place.

    The TV reaches Immich at the LAN address, never at the containers' one,
    and the share key is its only way in.
    """
    status, answer = the_agent.posts("/tools/photos/slideshow", {"album": "Santorini"})

    assert status == 200, answer
    page = tv.opens(path_of(answer["link"]))
    assert page.status == 200 and "html" in page.kind
    shots = page.links("shot")
    assert [src for _, src in shots] == [
        f"{PHOTOS_PUBLIC}/api/assets/{a['id']}/thumbnail?size=preview&key={SHARE_KEY}"
        for a in (FERRY, BEACH, SUNSET)]
    captions = [caption for caption, _ in shots]
    for caption, day, place in zip(captions, ("10", "11", "12"), ("Athinios", "Kamari", "Oia")):
        assert day in caption and "Aug" in caption and place in caption, caption
    assert page.texts("show-title") == ["Santorini"]
    assert all(d in page.texts("show-dates")[0] for d in ("10", "12", "Aug"))


def test_the_share_link_hides_metadata_and_expires(immich, the_agent):
    """Given a slideshow asked for, then the share link Immich makes holds only
    the album's timeline photos, hides EXIF and downloads, and expires in
    twelve hours, so the link cannot outlive the evening."""
    before = datetime.now(timezone.utc)
    the_agent.posts("/tools/photos/slideshow", {"album": "Santorini"})

    [link] = immich.links
    assert link["type"] == "INDIVIDUAL"
    assert sorted(link["assetIds"]) == sorted(a["id"] for a in (FERRY, BEACH, SUNSET))
    assert (link["showMetadata"], link["allowDownload"]) == (False, False)
    lives = datetime.fromisoformat(link["expiresAt"]) - before
    assert timedelta(hours=11, minutes=59) < lives <= timedelta(hours=12, minutes=1)


def test_an_album_is_found_by_its_id_as_well(immich, the_agent):
    status, answer = the_agent.posts("/tools/photos/slideshow", {"album": SANTORINI["id"]})

    assert status == 200 and answer["photos"] == 3


@pytest.mark.parametrize("album", [EMPTY, ARCHIVED_ONLY], ids=["empty", "archived only"])
def test_an_album_with_no_timeline_photos_gives_no_link(immich, the_agent, album):
    """Given an empty album, or one with only archived photos, then the answer
    says why and Immich holds no new share link."""
    status, answer = the_agent.posts("/tools/photos/slideshow", {"album": album["albumName"]})

    assert status == 422 and "error" in answer and "link" not in answer
    assert immich.links == []


def test_an_unknown_album_gives_no_link(immich, the_agent):
    status, answer = the_agent.posts("/tools/photos/slideshow", {"album": "Shelbyville"})

    assert status == 404 and "error" in answer
    assert immich.links == []


def test_without_immich_access_the_answer_says_so(immich, the_agent, monkeypatch):
    """Given the stack has no Immich API key, then nothing is asked of Immich."""
    monkeypatch.setenv("IMMICH_API_KEY", "")

    status, answer = the_agent.posts("/tools/photos/slideshow", {"album": "Santorini"})

    assert status == 503 and "error" in answer
    assert immich.server.log == []


def test_with_photos_down_the_answer_says_so(the_agent, monkeypatch):
    monkeypatch.setenv("IMMICH_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("IMMICH_API_KEY", API_KEY)

    status, answer = the_agent.posts("/tools/photos/slideshow", {"album": "Santorini"})

    assert status == 503 and "error" in answer


# ── What the network reaches ─────────────────────────────────────────────

def test_a_device_on_the_network_cannot_ask_for_a_slideshow(immich, tv):
    """The request is for the stack's own services; the network only opens a
    link it was given. Same 404 as a path that does not exist."""
    status, _ = tv.posts("/tools/photos/slideshow", {"album": "Santorini"})

    assert status == 404
    assert immich.links == []


def test_a_share_link_made_by_hand_does_not_open_as_a_slideshow(immich, tv):
    """Given a link Homer made in Immich's share dialog, then its key does not
    open a slideshow page: the page would show whatever that link shares."""
    immich.made_by_hand("Hk8sQ2mW5nB7vC1xZ4lP9rT3yU6oE0iA")

    page = tv.opens("/go/slideshow/Hk8sQ2mW5nB7vC1xZ4lP9rT3yU6oE0iA")

    assert page.status == 404 and page.links("shot") == []


def test_an_expired_slideshow_says_it_has_ended(immich, the_agent, tv):
    """Given a slideshow past its expiry, then its link answers a page that
    says so, with no photos on it."""
    _, answer = the_agent.posts("/tools/photos/slideshow", {"album": "Santorini", "hours": 12})
    immich.links[0]["expiresAt"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()

    page = tv.opens(path_of(answer["link"]))

    assert page.status == 410 and "html" in page.kind
    assert page.texts("ended") and page.links("shot") == []


def test_nobody_asking_creates_nothing(immich, tv):
    """Resting state: a page opened with a key no slideshow has asks Immich
    nothing that makes a share link."""
    page = tv.opens("/go/slideshow/nosuchkey")

    assert page.status == 410
    assert immich.links == []
    assert all(r.method == "GET" for r, _ in immich.server.log)
