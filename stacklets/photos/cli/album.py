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
  stack photos album unarchive --pattern whatsapp         # undo — restore to timeline

  stack photos album copy --from "Imported" --to "Family" # copy all assets between albums
  stack photos album copy --from "Imported" --to "Family" --pattern '*.jpg'
  stack photos album clear --from "Imported"              # remove all assets from an album
  stack photos album clear --from "Imported" --pattern whatsapp
  stack photos album move --from "Imported" --to "Family" # copy + clear in one pass
"""

HELP = "Bulk archive/unarchive/copy/clear/move assets"

import argparse
import fnmatch
import http.client
import json
import sys
import time


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

def _get_api_key(config):
    secrets = config.get("secrets", {})
    return secrets.get("photos__IMPORT_API_KEY") or secrets.get("photos__API_KEY")


def _search_term(pattern):
    """Extract the longest fixed substring from a glob pattern for server-side filtering."""
    parts = [p for p in pattern.replace("*", "\0").split("\0") if p]
    return max(parts, key=len) if parts else None


def _search_assets(client, pattern):
    """Search assets whose originalFileName matches the glob pattern."""
    term = _search_term(pattern)
    assets = []
    page = 1
    while True:
        body = {"page": page, "size": 1000}
        if term:
            body["originalFileName"] = term
        status, resp = client.post("/api/search/metadata", body)
        if status != 200:
            print(f"  ! Search failed (HTTP {status}): {resp}", file=sys.stderr)
            return None
        items = resp.get("assets", {}).get("items", [])
        if not items:
            break
        for a in items:
            if fnmatch.fnmatch(a.get("originalFileName", ""), pattern):
                assets.append(a)
        next_page = resp.get("assets", {}).get("nextPage")
        if not next_page:
            break
        page = int(next_page)
    return assets


def _chunked(items, size=100):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _update_assets(client, assets, is_archived):
    """Update archive visibility in batches via the bulk endpoint."""
    visibility = "archive" if is_archived else "timeline"
    updated = 0
    failed = []
    for chunk in _chunked(assets):
        ids = [a["id"] for a in chunk]
        status, resp = client.put("/api/assets", {
            "ids": ids,
            "visibility": visibility,
        })
        if status in (200, 204):
            updated += len(chunk)
        else:
            for a in chunk:
                status, resp = client.put("/api/assets", {
                    "ids": [a["id"]],
                    "visibility": visibility,
                })
                if status in (200, 204):
                    updated += 1
                else:
                    failed.append(a.get("originalFileName", a["id"]))
        print(f"    {updated + len(failed)}/{len(assets)}...", file=sys.stderr)
    if failed:
        print(f"  ! {len(failed)} assets owned by a different user (no permission with this key):",
              file=sys.stderr)
        for name in failed:
            print(f"      {name}", file=sys.stderr)
        print(f"  hint: use --api-key with that user's key or an admin key to archive these too",
              file=sys.stderr)
    return updated


def _find_album_overlaps(client, asset_ids):
    """Find which albums contain the given assets."""
    asset_id_set = set(asset_ids)

    status, albums = client.get("/api/albums")
    if status != 200:
        print(f"  ! Failed to list albums (HTTP {status})", file=sys.stderr)
        return []

    overlaps = []
    for album in albums:
        album_id = album["id"]
        status, detail = client.get(f"/api/albums/{album_id}")
        if status != 200:
            continue
        album_assets = detail.get("assets", [])
        overlap = [a["id"] for a in album_assets if a["id"] in asset_id_set]
        if overlap:
            overlaps.append((album_id, album.get("albumName", "?"), overlap))

    return overlaps


def _detach_from_albums(client, overlaps):
    """Remove assets from albums using pre-computed overlaps."""
    total_removed = 0
    for album_id, album_name, ids in overlaps:
        for chunk in _chunked(ids):
            client.delete(f"/api/albums/{album_id}/assets", {"ids": chunk})
        print(f"    removed {len(ids)} from \"{album_name}\"", file=sys.stderr)
        total_removed += len(ids)

    return total_removed


def _resolve_album(client, name):
    """Find an album by case-insensitive substring match. Returns (id, name) or None."""
    status, albums = client.get("/api/albums")
    if status != 200:
        print(f"  ! Failed to list albums (HTTP {status})", file=sys.stderr)
        return None
    needle = name.lower()
    matches = [(a["id"], a.get("albumName", "?")) for a in albums
               if needle in a.get("albumName", "").lower()]
    if not matches:
        print(f"  ! No album matching '{name}'", file=sys.stderr)
        return None
    exact = [(aid, aname) for aid, aname in matches if aname.lower() == needle]
    if exact:
        return exact[0]
    if len(matches) > 1:
        print(f"  ! Ambiguous album name '{name}', matches:", file=sys.stderr)
        for _, aname in matches:
            print(f"      {aname}", file=sys.stderr)
        return None
    return matches[0]


def _get_album_assets(client, album_id, pattern=None):
    """Get all assets in an album, optionally filtered by glob pattern."""
    status, detail = client.get(f"/api/albums/{album_id}")
    if status != 200:
        print(f"  ! Failed to read album (HTTP {status})", file=sys.stderr)
        return None
    assets = detail.get("assets", [])
    if pattern:
        assets = [a for a in assets if fnmatch.fnmatch(a.get("originalFileName", ""), pattern)]
    return assets


def _add_to_album(client, album_id, asset_ids):
    """Add assets to an album in chunks. Returns count added."""
    added = 0
    for chunk in _chunked(asset_ids):
        status, resp = client.put(f"/api/albums/{album_id}/assets", {"ids": chunk})
        if status == 200:
            success = [r for r in resp if r.get("success")]
            added += len(success)
        elif status == 204:
            added += len(chunk)
        else:
            print(f"  ! Failed to add {len(chunk)} assets (HTTP {status}): {resp}", file=sys.stderr)
        print(f"    {added}/{len(asset_ids)}...", file=sys.stderr)
    return added


def _remove_from_album(client, album_id, asset_ids):
    """Remove assets from an album in chunks. Returns count removed."""
    removed = 0
    for chunk in _chunked(asset_ids):
        status, _ = client.delete(f"/api/albums/{album_id}/assets", {"ids": chunk})
        if status in (200, 204):
            removed += len(chunk)
        else:
            print(f"  ! Failed to remove {len(chunk)} assets (HTTP {status})", file=sys.stderr)
        print(f"    {removed}/{len(asset_ids)}...", file=sys.stderr)
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
                        help="Target album name (created if it doesn't exist)")
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
    print(f"\n  Sample:", file=sys.stderr)
    for a in sample:
        print(f"    {a.get('originalFileName', '?')}", file=sys.stderr)
    if len(assets) > 10:
        print(f"    ... and {len(assets) - 10} more", file=sys.stderr)


def run(args, stacklet, config):
    opts = _parse_args(args)

    api_key = opts.api_key or _get_api_key(config)
    if not api_key:
        return {"error": (
            "No Immich API key found. Create one in the Immich admin UI "
            "(Administration > API Keys) and add it to .stack/secrets.toml "
            "as photos__IMPORT_API_KEY = \"your-key\""
        )}

    port = stacklet.get("port", 42010)
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

    if from_album:
        src = _resolve_album(client, from_album)
        if not src:
            return {"error": f"Album '{from_album}' not found"}
        src_id, src_name = src
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
        print(f"\n  Scanning albums...", file=sys.stderr)
        overlaps = _find_album_overlaps(client, ids)
        if overlaps:
            print(f"  Will detach from {len(overlaps)} album(s):", file=sys.stderr)
            for _, name, ids_in_album in overlaps:
                print(f"    {name} ({len(ids_in_album)} assets)", file=sys.stderr)
        else:
            print(f"  No album memberships found", file=sys.stderr)

    if opts.dry_run:
        print(f"\n  Dry run — no changes made.\n", file=sys.stderr)
        return {"ok": True, "matched": len(matched), "would_update": len(matched)}

    print(f"\n  Updating...", file=sys.stderr)
    updated = _update_assets(client, matched, is_archived)
    print(f"  {action.title()}d {updated} assets.", file=sys.stderr)

    detached = 0
    if overlaps and updated:
        print(f"  Detaching from albums...", file=sys.stderr)
        detached = _detach_from_albums(client, overlaps)

    print(file=sys.stderr)
    return {"ok": True, "matched": len(matched), "updated": updated, "detached": detached}


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
        dst = _resolve_album(client, opts.to_album)
        if not dst:
            print(f"  Creating album \"{opts.to_album}\"...", file=sys.stderr)
            dst = _create_album(client, opts.to_album)
            if not dst:
                return {"error": f"Failed to create album '{opts.to_album}'"}
        dst_id, dst_name = dst
        print(f"  Target album: {dst_name}", file=sys.stderr)

    if opts.dry_run:
        print(f"\n  Dry run — no changes made.\n", file=sys.stderr)
        result = {"ok": True, "matched": len(assets)}
        if action in ("copy", "move"):
            result["would_copy"] = len(assets)
        if action in ("clear", "move"):
            result["would_clear"] = len(assets)
        return result

    result = {"ok": True, "matched": len(assets)}

    if action in ("copy", "move"):
        print(f"\n  Copying to \"{dst_name}\"...", file=sys.stderr)
        added = _add_to_album(client, dst_id, ids)
        print(f"  Copied {added} assets.", file=sys.stderr)
        result["copied"] = added

    if action in ("clear", "move"):
        print(f"\n  Removing from \"{src_name}\"...", file=sys.stderr)
        removed = _remove_from_album(client, src_id, ids)
        print(f"  Removed {removed} assets.", file=sys.stderr)
        result["removed"] = removed

    print(file=sys.stderr)
    return result
