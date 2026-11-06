"""`stack photos album`: what the commands do to a family's albums.

The commands act on real albums, so these tests pin three promises a caller
relies on. A command acts on the photos an album holds. A `--dry-run` names
the photos a real run acts on and changes nothing. A read that fails acts on
nothing.

A stand-in Immich answers over HTTP with the routes and shapes Immich 3.3.6
answers on the test rig: an album's detail carries `assetCount` and no
`assets`, an album's photos come from `POST /api/search/metadata` in pages,
and the bulk album routes answer per photo with `success` and `error`.
"""

from __future__ import annotations

import json
import re
import uuid

import pytest
from werkzeug.wrappers import Response

from stacklets.photos.cli import album


class Immich:
    """Immich 3.3.6's album and search API, as far as the commands use it."""

    def __init__(self, httpserver):
        self.photos = {}          # id -> {id, originalFileName, visibility}
        self.albums = {}          # id -> {id, albumName, photo ids}
        self.page_size = 1000     # a page, whatever `size` the client asks for
        self.broken = []          # (method, path, body) -> True: answers 500
        self.refused_to_add = set()      # photo ids the bulk add refuses
        self.refused_to_archive = set()  # photo ids the bulk update refuses
        self.refused_to_remove = set()   # photo ids the bulk album removal refuses
        self.writes = []          # every request that changes something
        httpserver.expect_request(re.compile(".*")).respond_with_handler(self._handle)
        self.port = httpserver.port

    # ── Setting the state ────────────────────────────────────────────────

    def photo(self, name):
        photo_id = str(uuid.uuid4())
        self.photos[photo_id] = {"id": photo_id, "originalFileName": name,
                                 "visibility": "timeline"}
        return photo_id

    def album(self, name, *photo_ids):
        album_id = str(uuid.uuid4())
        self.albums[album_id] = {"id": album_id, "albumName": name, "photos": list(photo_ids)}
        return album_id

    def fails_when(self, broken):
        self.broken.append(broken)

    # ── Reading the state ────────────────────────────────────────────────

    def names_in(self, album_name):
        for held in self.albums.values():
            if held["albumName"] == album_name:
                return sorted(self.photos[p]["originalFileName"] for p in held["photos"])
        raise KeyError(album_name)

    def album_names(self):
        return sorted(held["albumName"] for held in self.albums.values())

    def archived(self):
        return sorted(p["originalFileName"] for p in self.photos.values()
                      if p["visibility"] == "archive")

    # ── The API ──────────────────────────────────────────────────────────

    def _handle(self, request):
        body = json.loads(request.data) if request.data else {}
        method, path = request.method, request.path
        if any(broken(method, path, body) for broken in self.broken):
            return _answer({"message": "Internal server error"}, 500)
        if method != "GET" and path != "/api/search/metadata":
            self.writes.append((method, path))
        return self._route(method, path, body)

    def _route(self, method, path, body):
        if path == "/api/search/metadata":
            return self._search(body)
        if path == "/api/assets" and method == "PUT":
            return self._update_visibility(body)
        if path == "/api/albums" and method == "GET":
            return _answer([self._album_answer(held) for held in self.albums.values()])
        if path == "/api/albums" and method == "POST":
            album_id = self.album(body["albumName"])
            return _answer(self._album_answer(self.albums[album_id]), 201)
        matched = re.fullmatch(r"/api/albums/([^/]+)(/assets)?", path)
        if not matched or matched[1] not in self.albums:
            return _answer({"message": "Not found"}, 404)
        held = self.albums[matched[1]]
        if not matched[2]:
            return _answer(self._album_answer(held))
        if method == "PUT":
            return _answer([self._add(held, photo_id) for photo_id in body["ids"]])
        return _answer([self._remove(held, photo_id) for photo_id in body["ids"]])

    def _album_answer(self, held):
        """No `assets` field, in the list and in the detail."""
        return {"id": held["id"], "albumName": held["albumName"], "assetCount": len(held["photos"])}

    def _search(self, body):
        found = list(self.photos.values())
        if body.get("albumIds"):
            held = self.albums[body["albumIds"][0]]
            found = [self.photos[p] for p in held["photos"]]
        if body.get("originalFileName"):
            found = [p for p in found if body["originalFileName"] in p["originalFileName"]]
        page = body.get("page", 1)
        start = (page - 1) * self.page_size
        items = found[start:start + self.page_size]
        has_more = start + self.page_size < len(found)
        return _answer({"assets": {"items": items, "total": len(items),
                                   "nextPage": str(page + 1) if has_more else None}})

    def _update_visibility(self, body):
        if self.refused_to_archive.intersection(body["ids"]):
            return _answer({"message": "Not found or no asset.update access"}, 400)
        for photo_id in body["ids"]:
            self.photos[photo_id]["visibility"] = body["visibility"]
        return Response(status=204)

    def _add(self, held, photo_id):
        if photo_id in self.refused_to_add:
            return {"id": photo_id, "success": False, "error": "no_permission"}
        if photo_id in held["photos"]:
            return {"id": photo_id, "success": False, "error": "duplicate"}
        held["photos"].append(photo_id)
        return {"id": photo_id, "success": True}

    def _remove(self, held, photo_id):
        if photo_id in self.refused_to_remove:
            return {"id": photo_id, "success": False, "error": "no_permission"}
        if photo_id not in held["photos"]:
            return {"id": photo_id, "success": False, "error": "not_found"}
        held["photos"].remove(photo_id)
        return {"id": photo_id, "success": True}


def _answer(body, status=200):
    return Response(json.dumps(body), status=status, content_type="application/json")


@pytest.fixture
def immich(httpserver):
    """An album "Imported" with two photos, as an import leaves it, and
    the family's album "Family"."""
    server = Immich(httpserver)
    server.album("Imported", server.photo("beach.jpg"), server.photo("IMG-20150704-WA0001.jpg"))
    server.album("Family", server.photo("birthday.jpg"))
    return server


def album_command(immich, *arguments):
    """Run the command as the framework runs it, with a key of its own."""
    config = {"is_healthy": lambda: True, "stack": {}, "secrets": {}}
    return album.run([*arguments, "--api-key", "homers-key"], {"port": immich.port}, config)


# ── A command acts on the album's photos ─────────────────────────────────

def test_move_moves_the_albums_photos(immich):
    """Given an album with photos, when `album move` runs, then the target
    holds them and the source is empty. Moving is what an import's album
    is sorted with."""
    result = album_command(immich, "move", "--from", "Imported", "--to", "Family")

    assert result["copied"] == 2 and result["removed"] == 2
    assert immich.names_in("Imported") == []
    assert immich.names_in("Family") == ["IMG-20150704-WA0001.jpg", "beach.jpg", "birthday.jpg"]


def test_copy_leaves_the_photos_in_the_source(immich):
    """Given an album with photos, when `album copy` runs, then both albums
    hold them."""
    result = album_command(immich, "copy", "--from", "Imported", "--to", "Family")

    assert result["copied"] == 2
    assert len(immich.names_in("Imported")) == 2
    assert len(immich.names_in("Family")) == 3


def test_clear_empties_the_album_and_keeps_the_photos(immich):
    """Given an album with photos, when `album clear` runs, then the album
    is empty and the photos stay in the library."""
    result = album_command(immich, "clear", "--from", "Imported")

    assert result["removed"] == 2
    assert immich.names_in("Imported") == []
    assert len(immich.photos) == 3


def test_a_pattern_narrows_the_album_to_the_photos_it_matches(immich):
    """Given an album with two photos and a pattern that matches one, when
    `album clear` runs, then only that photo leaves."""
    album_command(immich, "clear", "--from", "Imported", "--pattern", "whatsapp")

    assert immich.names_in("Imported") == ["beach.jpg"]


def test_archive_from_an_album_archives_its_photos(immich):
    """Given an album with photos, when `album archive --from` runs, then
    they are archived and leave the album."""
    result = album_command(immich, "archive", "--from", "Imported")

    assert result["updated"] == 2 and result["detached"] == 2
    assert immich.archived() == ["IMG-20150704-WA0001.jpg", "beach.jpg"]
    assert immich.names_in("Imported") == []


def test_an_album_longer_than_one_page_is_read_to_its_end(immich):
    """Given an album that spans several pages of search, when `album
    clear` runs, then every photo leaves. A photo past the first page
    would stay behind without a word."""
    immich.page_size = 1

    result = album_command(immich, "clear", "--from", "Imported")

    assert result["matched"] == 2
    assert immich.names_in("Imported") == []


# ── Archiving by pattern takes photos out of their albums ────────────────

def test_archive_by_pattern_takes_the_photos_out_of_their_albums(immich):
    """Given a matching photo in an album, when `album archive --pattern`
    runs, then it is archived and leaves the album. The album's other
    photos stay."""
    result = album_command(immich, "archive", "--pattern", "whatsapp")

    assert result["updated"] == 1 and result["detached"] == 1
    assert immich.archived() == ["IMG-20150704-WA0001.jpg"]
    assert immich.names_in("Imported") == ["beach.jpg"]


def test_keep_albums_leaves_the_photos_in_their_albums(immich):
    """Given a matching photo in an album, when `album archive --pattern
    --keep-albums` runs, then it is archived and stays in the album."""
    result = album_command(immich, "archive", "--pattern", "whatsapp", "--keep-albums")

    assert result["updated"] == 1 and result["detached"] == 0
    assert immich.archived() == ["IMG-20150704-WA0001.jpg"]
    assert len(immich.names_in("Imported")) == 2


def test_a_photo_that_was_not_archived_stays_in_its_album(immich):
    """Given a photo Immich refuses to archive (a partner's), when `album
    archive --pattern` runs, then it stays in its album. Only an archived
    photo is still in the library's reach once it leaves."""
    refused = immich.photo("IMG-20160101-WA0002.jpg")
    immich.albums[next(iter(immich.albums))]["photos"].append(refused)
    immich.refused_to_archive.add(refused)

    result = album_command(immich, "archive", "--pattern", "whatsapp")

    assert result["updated"] == 1 and result["detached"] == 1
    assert immich.names_in("Imported") == ["IMG-20160101-WA0002.jpg", "beach.jpg"]


# ── A dry run names what a real run acts on ──────────────────────────────

@pytest.mark.parametrize("arguments, facts", [
    (["move", "--from", "Imported", "--to", "Family"], {"would_copy": 2, "would_clear": 2}),
    (["copy", "--from", "Imported", "--to", "Family"], {"would_copy": 2}),
    (["clear", "--from", "Imported"], {"would_clear": 2}),
    (["archive", "--from", "Imported"], {"would_update": 2}),
])
def test_a_dry_run_names_the_photos_and_changes_nothing(immich, capsys, arguments, facts):
    """Given an album with photos, when a command runs with `--dry-run`,
    then it names the photos a real run acts on and sends no change."""
    result = album_command(immich, *arguments, "--dry-run")

    assert result["matched"] == 2
    assert facts.items() <= result.items()
    printed = capsys.readouterr().err
    assert "beach.jpg" in printed and "IMG-20150704-WA0001.jpg" in printed
    assert immich.writes == []


def test_a_dry_run_of_archive_by_pattern_names_the_albums_it_would_leave(immich, capsys):
    """Given a matching photo in an album, when `album archive --pattern
    --dry-run` runs, then it names the photo and the album and sends no
    change."""
    result = album_command(immich, "archive", "--pattern", "whatsapp", "--dry-run")

    assert result["would_update"] == 1
    printed = capsys.readouterr().err
    assert "IMG-20150704-WA0001.jpg" in printed and "Imported" in printed
    assert immich.writes == []


def test_a_dry_run_does_not_create_the_target_album(immich, capsys):
    """Given a target album that does not exist, when `album move --dry-run`
    runs, then no album is created and the answer names the one a real run
    would create."""
    result = album_command(immich, "move", "--from", "Imported", "--to", "Holiday", "--dry-run")

    assert result["would_create"] == "Holiday"
    assert immich.album_names() == ["Family", "Imported"]
    assert immich.writes == []


def test_move_creates_a_target_album_that_does_not_exist(immich):
    """Given a target album that does not exist, when `album move` runs,
    then the album is created and holds the photos."""
    album_command(immich, "move", "--from", "Imported", "--to", "Holiday")

    assert immich.names_in("Holiday") == ["IMG-20150704-WA0001.jpg", "beach.jpg"]
    assert immich.names_in("Imported") == []


# ── The resting state: an empty album ────────────────────────────────────

@pytest.mark.parametrize("arguments", [
    ["move", "--from", "Empty", "--to", "Family"],
    ["copy", "--from", "Empty", "--to", "Family"],
    ["clear", "--from", "Empty"],
    ["archive", "--from", "Empty"],
])
def test_an_empty_album_answers_that_it_has_no_photos(immich, capsys, arguments):
    """Given an empty album, when a command runs, then it matches nothing,
    says which album, and sends no change."""
    immich.album("Empty")

    result = album_command(immich, *arguments)

    assert result == {"ok": True, "matched": 0}
    assert "Empty" in capsys.readouterr().err
    assert immich.writes == []


# ── A failed read acts on nothing ────────────────────────────────────────

def _is_album_search(path, body):
    return path == "/api/search/metadata" and bool(body.get("albumIds"))


def _is_album_list(method, path):
    return method == "GET" and path == "/api/albums"


@pytest.mark.parametrize("page", [1, 2])
@pytest.mark.parametrize("arguments", [
    ["move", "--from", "Imported", "--to", "Family"],
    ["clear", "--from", "Imported"],
    ["archive", "--from", "Imported"],
])
def test_a_failed_read_of_the_album_acts_on_nothing(immich, capsys, arguments, page):
    """Given a search that fails on any page, when a command reads the album,
    then it answers an error, prints the HTTP status and sends no change. A
    part of an album is not the album."""
    immich.page_size = 1
    immich.fails_when(lambda _, path, body: _is_album_search(path, body) and body["page"] == page)

    result = album_command(immich, *arguments)

    assert "error" in result
    assert "500" in capsys.readouterr().err
    assert immich.writes == []


@pytest.mark.parametrize("dry_run", [[], ["--dry-run"]])
def test_a_failed_scan_of_the_albums_archives_nothing(immich, capsys, dry_run):
    """Given a list of albums that cannot be read, when `album archive
    --pattern` runs, then it answers an error, prints the HTTP status and
    archives nothing. Otherwise a photo is archived and stays in its album,
    and the answer says it was in none."""
    immich.fails_when(lambda method, path, _: _is_album_list(method, path))

    result = album_command(immich, "archive", "--pattern", "whatsapp", *dry_run)

    assert "error" in result
    assert "500" in capsys.readouterr().err
    assert immich.archived() == [] and immich.writes == []


def test_a_failed_read_of_one_albums_photos_archives_nothing(immich, capsys):
    """Given a search that finds the matching photos and then fails for the
    albums, when `album archive --pattern` runs, then it archives nothing."""
    immich.fails_when(lambda _, path, body: _is_album_search(path, body))

    result = album_command(immich, "archive", "--pattern", "whatsapp")

    assert "error" in result
    assert "500" in capsys.readouterr().err
    assert immich.archived() == [] and immich.writes == []


def test_a_failed_read_of_the_target_creates_no_album(immich, capsys):
    """Given a list of albums that fails after the source was found, when
    `album move` looks for the target, then it answers an error and creates
    no album. A target that could not be read is not a target that is
    missing."""
    lists = []

    def on_the_second_list(method, path, _):
        if _is_album_list(method, path):
            lists.append(path)
        return len(lists) > 1

    immich.fails_when(on_the_second_list)

    result = album_command(immich, "move", "--from", "Imported", "--to", "Holiday")

    assert "error" in result
    assert "500" in capsys.readouterr().err
    assert immich.album_names() == ["Family", "Imported"]
    assert immich.writes == []


# ── A move keeps a photo that did not reach the target ───────────────────

def test_move_keeps_a_photo_the_target_refused_in_the_source(immich):
    """Given a photo the target album refuses, when `album move` runs, then
    it stays in the source. It would otherwise be in neither album."""
    source = next(a for a in immich.albums.values() if a["albumName"] == "Imported")
    refused = next(p for p in source["photos"]
                   if immich.photos[p]["originalFileName"] == "beach.jpg")
    immich.refused_to_add.add(refused)

    result = album_command(immich, "move", "--from", "Imported", "--to", "Family")

    assert result["copied"] == 1 and result["removed"] == 1
    assert immich.names_in("Imported") == ["beach.jpg"]
    assert "IMG-20150704-WA0001.jpg" in immich.names_in("Family")


def test_a_copy_the_target_refused_says_so(immich, capsys):
    """Given a shared album whose photos the target refuses, when `album copy`
    runs, then the answer counts none copied and the command names the target
    and how many stayed behind. Immich answers 200 for a refused photo, so a
    family sees nothing happen unless the command says it."""
    source = next(a for a in immich.albums.values() if a["albumName"] == "Imported")
    immich.refused_to_add.update(source["photos"])

    result = album_command(immich, "copy", "--from", "Imported", "--to", "Family")

    assert result["copied"] == 0
    printed = capsys.readouterr().err
    assert "2 assets" in printed and "Family" in printed


def test_move_of_a_photo_already_in_the_target_empties_the_source(immich):
    """Given a photo that both albums hold, when `album move` runs, then it
    leaves the source, because the target holds it."""
    source = next(a for a in immich.albums.values() if a["albumName"] == "Imported")
    target = next(a for a in immich.albums.values() if a["albumName"] == "Family")
    target["photos"].append(source["photos"][0])

    result = album_command(immich, "move", "--from", "Imported", "--to", "Family")

    assert result["removed"] == 2
    assert immich.names_in("Imported") == []


# ── A move never empties its own source ──────────────────────────────────

@pytest.mark.parametrize("dry_run", [[], ["--dry-run"]])
@pytest.mark.parametrize("source, target, album_name", [
    ("Family", "family", "Family"),        # the same name
    ("Family", "Family", "Family"),
    ("Import", "Imported", "Imported"),    # --from fits by a part of the name
])
def test_a_move_into_its_own_source_is_refused(immich, source, target, album_name, dry_run):
    """Given a target that resolves to the source, when `album move` runs,
    then it answers an error, names the album and sends no change. Every photo
    would answer `duplicate`, count as in the target and leave the source,
    which empties the album."""
    before = {a["albumName"]: list(a["photos"]) for a in immich.albums.values()}

    result = album_command(immich, "move", "--from", source, "--to", target, *dry_run)

    assert album_name in result["error"]
    assert {a["albumName"]: a["photos"] for a in immich.albums.values()} == before
    assert immich.writes == []


def test_a_target_name_inside_the_sources_name_is_a_new_album(immich):
    """Given a source "2015 Import" and no album "2015", when `album move
    --to 2015` runs, then a new album "2015" holds the photos."""
    immich.album("2015 Import", immich.photo("harbour.jpg"), immich.photo("lighthouse.jpg"))

    result = album_command(immich, "move", "--from", "2015 Import", "--to", "2015")

    assert result["copied"] == 2 and result["removed"] == 2
    assert immich.names_in("2015") == ["harbour.jpg", "lighthouse.jpg"]
    assert immich.names_in("2015 Import") == []


# ── A name means one album ───────────────────────────────────────────────

def test_a_target_means_the_album_with_exactly_that_name(immich):
    """Given "Holiday 2015" and no album "Holiday", when `album move --to
    Holiday` runs, then a new album "Holiday" holds the photos and "Holiday
    2015" is untouched. Moved photos that mix with an album's own cannot be
    told apart afterwards."""
    immich.album("Holiday 2015", immich.photo("tent.jpg"))

    album_command(immich, "move", "--from", "Imported", "--to", "Holiday")

    assert immich.names_in("Holiday") == ["IMG-20150704-WA0001.jpg", "beach.jpg"]
    assert immich.names_in("Holiday 2015") == ["tent.jpg"]


def test_a_dry_run_says_a_part_of_a_name_is_a_new_album(immich):
    """Given "Holiday 2015" and no album "Holiday", when `album copy --to
    Holiday --dry-run` runs, then the answer names the album a real run would
    create."""
    immich.album("Holiday 2015", immich.photo("tent.jpg"))

    result = album_command(immich, "copy", "--from", "Imported", "--to", "Holiday", "--dry-run")

    assert result["would_create"] == "Holiday"
    assert immich.writes == []


def test_a_source_still_fits_by_a_part_of_its_name(immich):
    """Given the album "Imported", when `album clear --from import` runs, then
    it acts on that album."""
    result = album_command(immich, "clear", "--from", "import")

    assert result["removed"] == 2


@pytest.mark.parametrize("arguments", [
    ["clear", "--from", "Twice"],
    ["move", "--from", "Twice", "--to", "Family"],
    ["copy", "--from", "Twice", "--to", "Family"],
    ["archive", "--from", "Twice"],
    ["move", "--from", "Imported", "--to", "Twice"],
    ["copy", "--from", "Imported", "--to", "Twice"],
])
def test_two_albums_with_one_exact_name_are_refused(immich, arguments):
    """Given two albums with one name, when a command names them as source or
    target, then it answers an error and sends no change. Immich allows the
    same name twice, and no output could show which of the two was changed."""
    immich.album("Twice", immich.photo("one.jpg"))
    immich.album("Twice", immich.photo("two.jpg"))

    result = album_command(immich, *arguments)

    assert "error" in result
    assert immich.writes == []


# ── A removal Immich refuses is reported as refused ──────────────────────

def test_a_refused_removal_says_how_many_stayed(immich, capsys):
    """Given an album whose removal Immich refuses for one photo, when `album
    clear` runs, then the answer counts only the other, and the command prints
    how many stayed. Immich answers 200 for a refused photo."""
    source = next(a for a in immich.albums.values() if a["albumName"] == "Imported")
    immich.refused_to_remove.add(source["photos"][0])

    result = album_command(immich, "clear", "--from", "Imported")

    assert result["removed"] == 1
    assert len(immich.names_in("Imported")) == 1
    assert "1 assets" in capsys.readouterr().err


def test_a_refused_detach_is_not_counted(immich):
    """Given an archived photo Immich refuses to take out of its album, when
    `album archive --from` runs, then the answer does not count it as detached."""
    source = next(a for a in immich.albums.values() if a["albumName"] == "Imported")
    immich.refused_to_remove.update(source["photos"])

    result = album_command(immich, "archive", "--from", "Imported")

    assert result["updated"] == 2 and result["detached"] == 0
    assert len(immich.names_in("Imported")) == 2


# ── Archiving from an album leaves its other albums alone ────────────────

@pytest.fixture
def in_two_albums(immich):
    """A photo in "Imported" and "Family"."""
    source = next(a for a in immich.albums.values() if a["albumName"] == "Imported")
    family = next(a for a in immich.albums.values() if a["albumName"] == "Family")
    family["photos"].append(source["photos"][0])
    return immich.photos[source["photos"][0]]["originalFileName"]


def test_archive_from_takes_a_photo_out_of_the_named_album_only(immich, in_two_albums):
    """Given a photo in two albums, when `album archive --from` names one, then
    it is archived, leaves that album and stays in the other, which nobody
    named."""
    result = album_command(immich, "archive", "--from", "Imported")

    assert result["detached"] == 2
    assert immich.names_in("Imported") == []
    assert in_two_albums in immich.names_in("Family")


def test_a_dry_run_of_archive_from_names_only_the_named_album(immich, in_two_albums, capsys):
    """Given a photo in two albums, when `album archive --from --dry-run` names
    one, then it names only that album as one the photo leaves."""
    album_command(immich, "archive", "--from", "Imported", "--dry-run")

    printed = capsys.readouterr().err
    assert "Imported" in printed and "Family" not in printed
    assert immich.writes == []


def test_archive_by_pattern_takes_a_photo_out_of_every_album(immich, in_two_albums):
    """Given a photo in two albums, when `album archive --pattern` matches it,
    then it leaves both."""
    pattern = in_two_albums

    result = album_command(immich, "archive", "--pattern", pattern)

    assert result["detached"] == 2
    assert in_two_albums not in immich.names_in("Imported")
    assert in_two_albums not in immich.names_in("Family")
