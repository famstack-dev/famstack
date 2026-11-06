"""
stack photos album — bulk operations on your Immich library

Archive, delete, or inspect assets matching filename patterns directly
from the command line. Useful for cleaning up WhatsApp forwards, messenger
downloads, and other junk that pollutes your timeline.

Usage:
  stack photos album archive --pattern whatsapp             # archive WhatsApp images
  stack photos album archive --pattern 'IMG-*-WA*'        # same thing, explicit glob
  stack photos album archive --pattern whatsapp --keep-albums
  stack photos album archive --pattern '*.gif' --dry-run  # preview first
  stack photos album archive --from "Imported"            # archive an entire album
  stack photos album unarchive --pattern whatsapp         # back on the timeline, albums are not restored

  stack photos album copy --from "Imported" --to "Family" # copy all assets between albums
  stack photos album copy --from "Imported" --to "Family" --pattern '*.jpg'
  stack photos album clear --from "Imported"              # remove all assets from an album
  stack photos album clear --from "Imported" --pattern whatsapp
  stack photos album move --from "Imported" --to "Family" # copy + clear in one pass

An album's photos are read through Immich's metadata search, because its album
detail carries `assetCount` and no `assets`. The search with `albumIds` has no
owner filter, so it also finds a partner's photos in a shared album, and it
finds archived photos, so `unarchive --from` finds what `archive --keep-albums`
left in the album. It leaves out locked photos, which an API key cannot change
anyway.

`unarchive` is not the reverse of `archive`. It puts a photo back on the timeline
and does not restore the albums the photo left, because Immich keeps no record
of them. `archive --from` therefore takes a photo out of the named album only;
`archive --pattern` names no album and takes it out of every album.

`--from` fits an album by a part of its name, because it only reads an album that
exists. `--to` means the album with exactly that name, or a new one, because
photos that land in another album cannot be told apart from that album's own.

These commands change a family's real albums, so three rules hold everywhere:
a `--dry-run` reads what a real run reads and changes nothing, a read that fails
stops the command before it changes anything, and a photo leaves an album only
once the step before has reached it (it is in the target of a move, or it is
archived).
"""

HELP = "Bulk archive/unarchive/copy/clear/move assets"

import argparse
import fnmatch
import http.client
import json
import sys
import time

from ..stack_account import key_for_imports


# ── HTTP client ──────────────────────────────────────────────────────────────

class ImmichClient:
    """Persistent HTTP connection to the Immich API."""

    def __init__(self, host, port, api_key):
        self.host = host
        self.port = port
        self.headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "Connection": "keep-alive",
        }
        self._conn = None

    def _connect(self):
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = http.client.HTTPConnection(self.host, self.port, timeout=60)

    def request(self, method, path, body=None, retries=2):
        data = json.dumps(body).encode() if body else None
        for attempt in range(1 + retries):
            try:
                if not self._conn:
                    self._connect()
                self._conn.request(method, path, body=data, headers=self.headers)
                resp = self._conn.getresponse()
                raw = resp.read().decode()
                status = resp.status
                parsed = json.loads(raw) if raw else {}
                return status, parsed
            except (http.client.RemoteDisconnected, ConnectionError, OSError):
                self._connect()
                if attempt < retries:
                    time.sleep(2)
                    continue
                return 0, {"message": "Connection failed (server not reachable)"}

    def get(self, path):
        return self.request("GET", path)

    def put(self, path, body):
        return self.request("PUT", path, body)

    def post(self, path, body):
        return self.request("POST", path, body)

    def delete(self, path, body=None):
        return self.request("DELETE", path, body)

    def close(self):
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None


# ── Immich API wrappers ──────────────────────────────────────────────────────

# The largest page Immich's metadata search accepts.
SEARCH_PAGE_SIZE = 1000


def _search_term(pattern):
    """Extract the longest fixed substring from a glob pattern for server-side filtering."""
    parts = [p for p in pattern.replace("*", "\0").split("\0") if p]
    return max(parts, key=len) if parts else None


def _read_assets(client, filters, failure):
    """Every asset a metadata search finds, read page by page to the end.

    `filters` is the search's body, such as `{"albumIds": [album_id]}`. Answers
    None and prints `failure` with the HTTP status when any page fails, never
    the pages before it: a part of an album is not the album.
    """
    assets = []
    page = 1
    while True:
        body = {**filters, "page": page, "size": SEARCH_PAGE_SIZE}
        status, resp = client.post("/api/search/metadata", body)
        if status != 200:
            print(f"  ! {failure} (HTTP {status}): {resp}", file=sys.stderr)
            return None
        found = resp.get("assets", {})
        assets.extend(found.get("items", []))
        next_page = found.get("nextPage")
        if not next_page:
            return assets
        page = int(next_page)


def _matching(assets, pattern):
    """The assets whose originalFileName matches the glob pattern."""
    return [a for a in assets if fnmatch.fnmatch(a.get("originalFileName", ""), pattern)]


def _search_assets(client, pattern):
    """Search assets whose originalFileName matches the glob pattern. None if the search fails."""
    term = _search_term(pattern)
    assets = _read_assets(client, {"originalFileName": term} if term else {}, "Search failed")
    return None if assets is None else _matching(assets, pattern)


def _chunked(items, size=100):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _update_assets(client, assets, is_archived):
    """Update archive visibility in batches via the bulk endpoint.

    Returns the ids of the assets Immich updated. It refuses a chunk with one
    asset the key may not change, so a refused chunk is tried one by one.
    """
    visibility = "archive" if is_archived else "timeline"
    updated = []
    failed = []
    for chunk in _chunked(assets):
        ids = [a["id"] for a in chunk]
        status, resp = client.put("/api/assets", {
            "ids": ids,
            "visibility": visibility,
        })
        if status in (200, 204):
            updated.extend(ids)
        else:
            for a in chunk:
                status, resp = client.put("/api/assets", {
                    "ids": [a["id"]],
                    "visibility": visibility,
                })
                if status in (200, 204):
                    updated.append(a["id"])
                else:
                    failed.append(a.get("originalFileName", a["id"]))
        print(f"    {len(updated) + len(failed)}/{len(assets)}...", file=sys.stderr)
    if failed:
        print(f"  ! {len(failed)} assets owned by a different user (no permission with this key):",
              file=sys.stderr)
        for name in failed:
            print(f"      {name}", file=sys.stderr)
        print("  hint: use --api-key with that user's key or an admin key to archive these too",
              file=sys.stderr)
    return updated


def _find_album_overlaps(client, asset_ids):
    """Find which albums contain the given assets.

    Returns (album id, album name, asset ids) per album. Answers None when an
    album cannot be read, because "in no album" would then be a guess, and an
    archived photo would stay in an album that nobody named.
    """
    asset_id_set = set(asset_ids)

    albums = _list_albums(client)
    if albums is None:
        return None

    overlaps = []
    for album in albums:
        album_id = album["id"]
        album_name = album.get("albumName", "?")
        album_assets = _read_assets(client, {"albumIds": [album_id]},
                                    f"Failed to read album \"{album_name}\"")
        if album_assets is None:
            return None
        overlap = [a["id"] for a in album_assets if a["id"] in asset_id_set]
        if overlap:
            overlaps.append((album_id, album_name, overlap))

    return overlaps


def _detach_from_albums(client, overlaps, archived_ids):
    """Remove archived assets from their albums, using pre-computed overlaps.

    An asset Immich did not archive stays, because it is still on the timeline
    and its album is where the family finds it. Returns the count removed.
    """
    archived = set(archived_ids)
    total_removed = 0
    for album_id, album_name, ids in overlaps:
        leaving = [asset_id for asset_id in ids if asset_id in archived]
        if not leaving:
            continue
        removed = _remove_from_album(client, album_id, leaving)
        print(f"    removed {removed} from \"{album_name}\"", file=sys.stderr)
        total_removed += removed

    return total_removed


def _list_albums(client):
    """Every album the key can read. None, with the HTTP status printed, when the list fails."""
    status, albums = client.get("/api/albums")
    if status != 200:
        print(f"  ! Failed to list albums (HTTP {status})", file=sys.stderr)
        return None
    return albums


def _albums_named(albums, name):
    """The albums with exactly this name, ignoring case, as (id, name). Immich allows two."""
    return [(a["id"], a.get("albumName", "?")) for a in albums
            if a.get("albumName", "").lower() == name.lower()]


def _matching_albums(client, name):
    """The albums `name` fits, as (id, name): the exact matches, else every substring match.

    Case-insensitive. Answers None when the list cannot be read, which is not
    the same as an empty list.
    """
    albums = _list_albums(client)
    if albums is None:
        return None
    needle = name.lower()
    return _albums_named(albums, name) or [(a["id"], a.get("albumName", "?")) for a in albums
                                           if needle in a.get("albumName", "").lower()]


def _print_ambiguous(name, matches):
    print(f"  ! Ambiguous album name '{name}', matches:", file=sys.stderr)
    for _, aname in matches:
        print(f"      {aname}", file=sys.stderr)


def _resolve_album(client, name):
    """Find an album that exists by its name. Returns (id, name) or None, and says why."""
    matches = _matching_albums(client, name)
    if matches is None:
        return None
    if not matches:
        print(f"  ! No album matching '{name}'", file=sys.stderr)
        return None
    if len(matches) > 1:
        _print_ambiguous(name, matches)
        return None
    return matches[0]


def _target_album(client, name, dry_run):
    """The album with exactly this name to copy into, as (id, name), created when there is none.

    A dry run creates nothing and answers (None, name). Answers None, and says
    why, when the list cannot be read or two albums have the name. A name that
    is a part of another album's name means a new album.
    """
    albums = _list_albums(client)
    if albums is None:
        return None
    matches = _albums_named(albums, name)
    if len(matches) > 1:
        _print_ambiguous(name, matches)
        return None
    if matches:
        return matches[0]
    if dry_run:
        print(f"  Would create album \"{name}\"", file=sys.stderr)
        return None, name
    print(f"  Creating album \"{name}\"...", file=sys.stderr)
    return _create_album(client, name)


def _get_album_assets(client, album_id, pattern=None):
    """Get all assets in an album, optionally filtered by glob pattern. None if the read fails."""
    assets = _read_assets(client, {"albumIds": [album_id]}, "Failed to read album")
    if assets is None or not pattern:
        return assets
    return _matching(assets, pattern)


def _add_to_album(client, album_id, asset_ids):
    """Add assets to an album in chunks.

    Returns the count added and the ids the album holds now, which also counts
    an asset it already held, so that a move removes from the source only what
    reached the target.
    """
    added = 0
    held = []
    for chunk in _chunked(asset_ids):
        status, resp = client.put(f"/api/albums/{album_id}/assets", {"ids": chunk})
        if status == 200:
            success = [r["id"] for r in resp if r.get("success")]
            added += len(success)
            held += success + [r["id"] for r in resp if r.get("error") == "duplicate"]
        elif status == 204:
            added += len(chunk)
            held += chunk
        else:
            print(f"  ! Failed to add {len(chunk)} assets (HTTP {status}): {resp}", file=sys.stderr)
        print(f"    {added}/{len(asset_ids)}...", file=sys.stderr)
    return added, held


def _remove_from_album(client, album_id, asset_ids):
    """Remove assets from an album in chunks. Returns the count removed, and prints how many stayed.

    Immich answers 200 with `success` per asset, also for an asset it refuses
    to remove, so the status alone does not say that an asset left.
    """
    removed = 0
    for chunk in _chunked(asset_ids):
        status, resp = client.delete(f"/api/albums/{album_id}/assets", {"ids": chunk})
        if status == 200:
            removed += sum(1 for r in resp if r.get("success"))
        elif status == 204:
            removed += len(chunk)
        else:
            print(f"  ! Failed to remove {len(chunk)} assets (HTTP {status})", file=sys.stderr)
        print(f"    {removed}/{len(asset_ids)}...", file=sys.stderr)
    stayed = len(asset_ids) - removed
    if stayed:
        print(f"  ! {stayed} assets stayed in the album: Immich did not remove them", file=sys.stderr)
    return removed


def _create_album(client, name):
    """Create a new album. Returns (id, name) or None."""
    status, resp = client.post("/api/albums", {"albumName": name})
    if status in (200, 201):
        return resp["id"], resp.get("albumName", name)
    print(f"  ! Failed to create album '{name}' (HTTP {status}): {resp}", file=sys.stderr)
    return None


# ── CLI ──────────────────────────────────────────────────────────────────────

PATTERN_ALIASES = {
    "whatsapp": "IMG-*-WA*",
}


def _resolve_pattern(pattern):
    """Expand known aliases like 'whatsapp' to their glob pattern."""
    if pattern:
        return PATTERN_ALIASES.get(pattern.lower(), pattern)
    return pattern


def _add_pattern_arg(sp):
    sp.add_argument("--pattern",
                    help="Glob pattern or alias (e.g. 'IMG-*-WA*', 'whatsapp')")


def _parse_args(argv):
    p = argparse.ArgumentParser(prog="stack photos album", description=HELP)
    sub = p.add_subparsers(dest="action", required=True)

    for name in ("archive", "unarchive"):
        sp = sub.add_parser(name, help=f"{name.title()} matching assets")
        sp.add_argument("--from", dest="from_album",
                        help="Scope to assets in this album")
        _add_pattern_arg(sp)
        sp.add_argument("--api-key",
                        help="Immich API key (default: from secrets.toml)")
        sp.add_argument("--dry-run", action="store_true",
                        help="Show what would be affected, don't change anything")
        if name == "archive":
            sp.add_argument("--keep-albums", action="store_true",
                            help="Don't remove archived assets from their albums (default: detach)")

    for name in ("copy", "move"):
        sp = sub.add_parser(name, help=f"{name.title()} assets between albums")
        sp.add_argument("--from", dest="from_album", required=True,
                        help="Source album name")
        sp.add_argument("--to", dest="to_album", required=True,
                        help="Exact name of the target album (created if it doesn't exist)")
        _add_pattern_arg(sp)
        sp.add_argument("--api-key",
                        help="Immich API key (default: from secrets.toml)")
        sp.add_argument("--dry-run", action="store_true",
                        help="Show what would be affected, don't change anything")

    sp = sub.add_parser("clear", help="Remove assets from an album")
    sp.add_argument("--from", dest="from_album", required=True,
                    help="Album name to clear")
    _add_pattern_arg(sp)
    sp.add_argument("--api-key",
                    help="Immich API key (default: from secrets.toml)")
    sp.add_argument("--dry-run", action="store_true",
                    help="Show what would be affected, don't change anything")

    opts = p.parse_args(argv)
    opts.pattern = _resolve_pattern(getattr(opts, "pattern", None))
    if opts.action in ("archive", "unarchive"):
        if not opts.pattern and not getattr(opts, "from_album", None):
            p.error(f"{opts.action} requires --pattern or --from")
    return opts


def _print_sample(assets):
    sample = assets[:10]
    print("\n  Sample:", file=sys.stderr)
    for a in sample:
        print(f"    {a.get('originalFileName', '?')}", file=sys.stderr)
    if len(assets) > 10:
        print(f"    ... and {len(assets) - 10} more", file=sys.stderr)


def run(args, stacklet, config):
    opts = _parse_args(args)

    port = stacklet.get("port", 42010)
    api_key = opts.api_key or key_for_imports(config, f"http://localhost:{port}")
    if not api_key:
        return {"error": (
            "No Immich API key found. Create one in the Immich admin UI "
            "(Administration > API Keys) and add it to .stack/secrets.toml "
            "as photos__IMPORT_API_KEY = \"your-key\""
        )}

    client = ImmichClient("localhost", port, api_key)

    try:
        if opts.action in ("archive", "unarchive"):
            return _run_archive(client, opts)
        return _run_album(client, opts)
    finally:
        client.close()


def _run_archive(client, opts):
    is_archived = opts.action == "archive"
    pattern = getattr(opts, "pattern", None)
    from_album = getattr(opts, "from_album", None)
    named_album = None

    if from_album:
        src = _resolve_album(client, from_album)
        if not src:
            return {"error": f"Album '{from_album}' not found"}
        src_id, src_name = src
        named_album = src
        print(f"\n  Album: {src_name}", file=sys.stderr)
        matched = _get_album_assets(client, src_id, pattern)
        if matched is None:
            return {"error": "Failed to read album"}
        label = f" matching '{pattern}'" if pattern else ""
        if not matched:
            print(f"  No assets{label} in \"{src_name}\"\n", file=sys.stderr)
            return {"ok": True, "matched": 0}
        print(f"  {len(matched)} assets{label}", file=sys.stderr)
    else:
        print(f"\n  Searching assets matching '{pattern}'...", file=sys.stderr)
        matched = _search_assets(client, pattern)
        if matched is None:
            return {"error": "Failed to search assets"}
        if not matched:
            print(f"  No assets match pattern '{pattern}'\n", file=sys.stderr)
            return {"ok": True, "matched": 0}
        print(f"  {len(matched)} assets match '{pattern}'", file=sys.stderr)

    action = "archive" if is_archived else "unarchive"
    _print_sample(matched)

    ids = [a["id"] for a in matched]
    detach = is_archived and not getattr(opts, "keep_albums", False)
    overlaps = []
    if detach:
        overlaps = _albums_to_leave(client, named_album, ids)
        if overlaps is None:
            return {"error": "Failed to read the albums, nothing was changed"}
        if overlaps:
            print(f"  Will detach from {len(overlaps)} album(s):", file=sys.stderr)
            for _, name, ids_in_album in overlaps:
                print(f"    {name} ({len(ids_in_album)} assets)", file=sys.stderr)
        else:
            print("  No album memberships found", file=sys.stderr)

    if opts.dry_run:
        print("\n  Dry run — no changes made.\n", file=sys.stderr)
        return {"ok": True, "matched": len(matched), "would_update": len(matched)}

    print("\n  Updating...", file=sys.stderr)
    updated = _update_assets(client, matched, is_archived)
    print(f"  {action.title()}d {len(updated)} assets.", file=sys.stderr)

    detached = 0
    if overlaps and updated:
        print("  Detaching from albums...", file=sys.stderr)
        detached = _detach_from_albums(client, overlaps, updated)

    print(file=sys.stderr)
    return {"ok": True, "matched": len(matched), "updated": len(updated), "detached": detached}


def _albums_to_leave(client, named_album, ids):
    """The albums an archive takes the photos out of, as `_find_album_overlaps` answers them.

    With `--from` that is the named album only, which holds all of them. A
    photo's other albums were not named, and `unarchive` cannot bring it back
    to them. Without `--from` every album is scanned. None if the scan fails.
    """
    if named_album:
        return [(*named_album, ids)]
    print("\n  Scanning albums...", file=sys.stderr)
    return _find_album_overlaps(client, ids)


def _run_album(client, opts):
    action = opts.action
    pattern = getattr(opts, "pattern", None)

    src = _resolve_album(client, opts.from_album)
    if not src:
        return {"error": f"Source album '{opts.from_album}' not found"}
    src_id, src_name = src

    print(f"\n  Source album: {src_name}", file=sys.stderr)
    assets = _get_album_assets(client, src_id, pattern)
    if assets is None:
        return {"error": "Failed to read source album"}

    label = f" matching '{pattern}'" if pattern else ""
    if not assets:
        print(f"  No assets{label} in \"{src_name}\"\n", file=sys.stderr)
        return {"ok": True, "matched": 0}

    print(f"  {len(assets)} assets{label}", file=sys.stderr)
    _print_sample(assets)

    ids = [a["id"] for a in assets]
    dst_id, dst_name = None, None

    if action in ("copy", "move"):
        dst = _target_album(client, opts.to_album, opts.dry_run)
        if not dst:
            return {"error": f"Cannot use '{opts.to_album}' as the target album"}
        dst_id, dst_name = dst
        print(f"  Target album: {dst_name}", file=sys.stderr)

    # Every photo would answer `duplicate`, count as in the target and then
    # leave the source, which empties the album. Nothing printed could restore it.
    if action == "move" and dst_id == src_id:
        return {"error": f"Cannot move album '{src_name}' into itself, nothing was changed"}

    if opts.dry_run:
        print("\n  Dry run — no changes made.\n", file=sys.stderr)
        result = {"ok": True, "matched": len(assets)}
        if action in ("copy", "move"):
            result["would_copy"] = len(assets)
        if action in ("clear", "move"):
            result["would_clear"] = len(assets)
        if action in ("copy", "move") and dst_id is None:
            result["would_create"] = dst_name
        return result

    result = {"ok": True, "matched": len(assets)}

    if action in ("copy", "move"):
        print(f"\n  Copying to \"{dst_name}\"...", file=sys.stderr)
        added, held = _add_to_album(client, dst_id, ids)
        print(f"  Copied {added} assets.", file=sys.stderr)
        result["copied"] = added
        if len(held) < len(ids):
            print(f"  ! {len(ids) - len(held)} assets stay only in \"{src_name}\": "
                  f"\"{dst_name}\" did not take them (no permission with this key)",
                  file=sys.stderr)
        if action == "move":
            ids = held

    if action in ("clear", "move"):
        print(f"\n  Removing from \"{src_name}\"...", file=sys.stderr)
        removed = _remove_from_album(client, src_id, ids)
        print(f"  Removed {removed} assets.", file=sys.stderr)
        result["removed"] = removed

    print(file=sys.stderr)
    return result
