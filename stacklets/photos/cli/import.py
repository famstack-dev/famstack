"""
stack photos import — import media from any mounted source into Immich

Scans a source path (NAS, external drive, local folder), builds a manifest
of all media files, and uploads them to Immich in controllable batches.
A cursor tracks progress per source path, so imports can be paused and
resumed across sessions — and you can switch between sources freely.

Two-layer deduplication:
  1. Hash ledger: SHA-256 set from all previous imports (fast local skip)
  2. Immich server-side: catches files already uploaded via phone/web

State layout:
  {data_dir}/photos/import/
    hashes.txt                         # global — shared across all sources
    sources/
      volumes-files-bilder-2020/       # per-source manifest + cursor
        manifest.txt
        state.json
      volumes-files-bilder/
        manifest.txt
        state.json

Usage:
  stack photos import --source /Volumes/files/Bilder/2020   # scan subfolder first
  stack photos import --status                               # show all sources
  stack photos import --proceed 100                          # upload next 100 files
  stack photos import --proceed 10 --dry-run                 # preview, no upload
  stack photos import --proceed 10 --verify                  # spot-check EXIF first
  stack photos import --proceed 500 --album-per-year         # auto-bucket by year

  # later — switch to the full directory
  stack photos import --source /Volumes/files/Bilder         # new source, own cursor
  stack photos import --proceed 1000                         # continues from this source

  # go back to the subfolder
  stack photos import --source /Volumes/files/Bilder/2020 --status
"""

HELP = "Import media from a mounted source into Immich"

import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

MEDIA_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".gif", ".bmp",
    ".tiff", ".tif", ".avif", ".jxl",
    ".mp4", ".mov", ".avi", ".mkv", ".wmv", ".m4v", ".3gp", ".mts",
    ".dng", ".cr2", ".cr3", ".nef", ".arw", ".orf", ".rw2", ".raf",
}

IMPORT_DIR = "photos/import"


# ── Path helpers ────────────────────────────────────────────────────────────

def _import_root(config):
    data_dir = Path(config.get("data_dir", config.get("repo_root", ".")))
    return data_dir / IMPORT_DIR


def _source_slug(source_path):
    """Turn a source path into a filesystem-safe directory name."""
    resolved = str(Path(source_path).resolve())
    return re.sub(r"[^a-zA-Z0-9]+", "-", resolved).strip("-").lower()


def _source_dir(import_root, source_path):
    return import_root / "sources" / _source_slug(source_path)


def _ledger_path(import_root):
    return import_root / "hashes.txt"


# ── State management ────────────────────────────────────────────────────────

def _load_state(state_path):
    if state_path.exists():
        return json.loads(state_path.read_text())
    return None


def _save_state(state_path, state):
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2) + "\n")


def _find_active_source(import_root):
    """Find the most recently used source directory."""
    sources_dir = import_root / "sources"
    if not sources_dir.exists():
        return None
    candidates = []
    for d in sources_dir.iterdir():
        if not d.is_dir():
            continue
        state_path = d / "state.json"
        state = _load_state(state_path)
        if state:
            manifest = _load_manifest(d / "manifest.txt")
            remaining = len(manifest) - state.get("cursor", 0)
            if remaining > 0:
                ts = state.get("last_used", state.get("created", ""))
                candidates.append((ts, d, state))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1], candidates[0][2]


def _list_sources(import_root):
    """List all source directories with their state."""
    sources_dir = import_root / "sources"
    if not sources_dir.exists():
        return []
    result = []
    for d in sorted(sources_dir.iterdir()):
        if not d.is_dir():
            continue
        state = _load_state(d / "state.json")
        if state:
            manifest = _load_manifest(d / "manifest.txt")
            remaining = len(manifest) - state.get("cursor", 0)
            result.append((d, state, remaining))
    return result


# ── Manifest ────────────────────────────────────────────────────────────────

def _scan_media(root):
    """Walk source tree, return sorted list of media file paths (relative to root)."""
    files = []
    root = Path(root)
    for dirpath, _, filenames in os.walk(root):
        for f in filenames:
            if Path(f).suffix.lower() in MEDIA_EXTENSIONS:
                rel = (Path(dirpath) / f).relative_to(root)
                files.append(str(rel))
    return sorted(files)


def _load_manifest(manifest_path):
    if not manifest_path.exists():
        return []
    return [line.strip() for line in manifest_path.read_text().splitlines() if line.strip()]


def _save_manifest(manifest_path, files):
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text("\n".join(files) + "\n")


# ── Hash ledger ─────────────────────────────────────────────────────────────

def _load_ledger(path):
    if not path.exists():
        return set()
    with open(path) as f:
        return {line.strip() for line in f if line.strip()}


def _append_ledger(path, new_hashes):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        for h in new_hashes:
            f.write(h + "\n")


def _hash_file(path, buf_size=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(buf_size):
            h.update(chunk)
    return h.hexdigest()


# ── Metadata verification ──────────────────────────────────────────────────

def _verify_metadata(source_root, rel_paths, count=5):
    if not shutil.which("exiftool"):
        print("  ! exiftool not installed — install with: brew install exiftool",
              file=sys.stderr)
        return True

    sample = random.sample(rel_paths, min(count, len(rel_paths)))
    print(f"\n  Metadata spot-check ({len(sample)} files):\n", file=sys.stderr)
    ok = True
    for rel in sample:
        full = Path(source_root) / rel
        result = subprocess.run(
            ["exiftool", "-DateTimeOriginal", "-FileType", "-ImageSize", "-s3", str(full)],
            capture_output=True, text=True,
        )
        fields = result.stdout.strip().splitlines()
        has_date = len(fields) >= 1 and fields[0].strip()
        icon = "+" if has_date else "!"
        date_str = fields[0].strip() if has_date else "no DateTimeOriginal"
        name = Path(rel).name
        print(f"    {icon} {name:40s} {date_str}", file=sys.stderr)
        if not has_date:
            ok = False
    if not ok:
        print("\n  ! Some files lack DateTimeOriginal — Immich will fall back to file date",
              file=sys.stderr)
    return ok


# ── Year extraction ─────────────────────────────────────────────────────────

_YEAR_FROM_NAME = re.compile(r'(?:^|[_\-])(\d{4})(?:\d{4}|[_\-])')


def _year_from_filename(name):
    """Try to extract a four-digit year (1990–2039) from a filename."""
    m = _YEAR_FROM_NAME.search(name)
    if m:
        y = int(m.group(1))
        if 1990 <= y <= 2039:
            return str(y)
    return None


def _parse_exiftool_output(stdout):
    """Parse exiftool batch output into a list of values, one per file.

    Exiftool outputs per file:
        ======== /path/to/file.jpg
        2018:12:31 20:59:06
    With -f, missing tags show as '-'. A summary line at the end is ignored.
    """
    values = []
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("========"):
            continue
        if line and line[0].isdigit() and "image file" in line:
            continue
        values.append(line)
    return values


_EXIFTOOL_CHUNK = 200


def _extract_years(source_root, rel_paths):
    """Return a dict mapping rel_path -> year string.

    Uses exiftool in batch mode for speed, processing files in chunks to
    avoid timeouts on large batches. File paths are passed via stdin (-@ -)
    to avoid OS argument length limits. Falls back to filename patterns for
    files without EXIF dates.
    """
    years = {}

    if shutil.which("exiftool"):
        for i in range(0, len(rel_paths), _EXIFTOOL_CHUNK):
            chunk_rels = rel_paths[i:i + _EXIFTOOL_CHUNK]
            full_paths = [str(Path(source_root) / rel) for rel in chunk_rels]
            argfile = "\n".join(full_paths) + "\n"
            result = subprocess.run(
                ["exiftool", "-DateTimeOriginal", "-s3", "-f", "-@", "-"],
                input=argfile, capture_output=True, text=True, timeout=120,
            )
            values = _parse_exiftool_output(result.stdout)
            for rel, val in zip(chunk_rels, values):
                if val and val != "-" and len(val) >= 4:
                    y = val[:4]
                    if y.isdigit() and 1990 <= int(y) <= 2039:
                        years[rel] = y
                        continue
                years[rel] = _year_from_filename(Path(rel).name)
    else:
        for rel in rel_paths:
            years[rel] = _year_from_filename(Path(rel).name)

    return years


# ── Upload ──────────────────────────────────────────────────────────────────

def _get_api_key(config):
    secrets = config.get("secrets", {})
    return secrets.get("photos__IMPORT_API_KEY") or secrets.get("photos__API_KEY")


def _get_immich_version(config):
    env_file = Path(config.get("repo_root", "")) / "stacklets" / "photos" / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("IMMICH_VERSION="):
                return line.split("=", 1)[1].strip().strip('"')
    return "release"


def _upload_batch(source_root, rel_paths, immich_version, api_key, album_name):
    """Copy files to a local temp dir, upload via sidecar container, clean up.

    Network/SMB mounts can't be bind-mounted into Docker containers on macOS,
    so we stage files locally first.
    """
    import shutil as _shutil
    import tempfile
    from shlex import quote

    staging = Path(tempfile.mkdtemp(prefix="immich-import-"))
    try:
        for rel in rel_paths:
            src = Path(source_root) / rel
            dst = staging / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            _shutil.copy2(str(src), str(dst))

        staged_count = sum(1 for _ in staging.rglob("*") if _.is_file())
        print(f"    staged {staged_count} files to {staging}", file=sys.stderr)

        staging_abs = str(staging)
        album_flag = f" --album-name {quote(album_name)}" if album_name else ""
        shell_cmd = (
            f"immich login-key http://stack-photos-server:2283 {quote(api_key)}"
            f" && immich upload{album_flag} --recursive /import"
        )
        cmd = [
            "docker", "run", "--rm",
            "--entrypoint", "",
            "--network", "stack",
            "-v", f"{staging_abs}:/import:ro",
            f"ghcr.io/immich-app/immich-server:{immich_version}",
            "sh", "-c", shell_cmd,
        ]
        print(f"    docker: {' '.join(cmd[:8])} ...", file=sys.stderr)
        result = subprocess.run(cmd, timeout=7200, capture_output=True, text=True)
        for line in (result.stdout + result.stderr).splitlines():
            if line.strip():
                print(f"    immich: {line.strip()}", file=sys.stderr)
        if result.returncode != 0:
            print(f"    exit code: {result.returncode}", file=sys.stderr)
        return result.returncode == 0
    finally:
        _shutil.rmtree(staging, ignore_errors=True)


def _upload_files(source_root, rel_paths, immich_version, api_key, album_name):
    """Upload files in chunks, staging each batch to a temp dir."""
    chunk_size = 200
    print(f"\n  Uploading {len(rel_paths)} files to album \"{album_name}\"...\n",
          file=sys.stderr)
    for i in range(0, len(rel_paths), chunk_size):
        chunk = rel_paths[i:i + chunk_size]
        if len(rel_paths) > chunk_size:
            print(f"    chunk {i // chunk_size + 1}: {len(chunk)} files", file=sys.stderr)
        if not _upload_batch(source_root, chunk, immich_version, api_key, album_name):
            return False
    return True


# ── CLI argument parsing ────────────────────────────────────────────────────

def _parse_args(argv):
    p = argparse.ArgumentParser(prog="stack photos import", description=HELP)
    p.add_argument("--source", type=Path,
                   help="Path to mounted source (NAS, external drive). Scans and builds manifest.")
    p.add_argument("--proceed", type=int, metavar="N",
                   help="Process next N files from the active source")
    p.add_argument("--status", action="store_true",
                   help="Show import progress (all sources, or specific with --source)")
    p.add_argument("--dry-run", action="store_true",
                   help="Show what would be uploaded, don't actually upload")
    p.add_argument("--verify", action="store_true",
                   help="Spot-check EXIF metadata before uploading")
    album_group = p.add_mutually_exclusive_group()
    album_group.add_argument("--album", default=None,
                             help="Immich album name for uploaded files (default: Imported)")
    album_group.add_argument("--album-per-year", action="store_true",
                             help="Auto-create albums by year from EXIF date (falls back to filename)")
    p.add_argument("--force", action="store_true",
                   help="Skip hash ledger check — upload even if previously imported")
    p.add_argument("--reset-cursor", action="store_true",
                   help="Reset cursor to 0 for the active source")
    return p.parse_args(argv)


# ── Commands ────────────────────────────────────────────────────────────────

def _cmd_source(source_path, iroot):
    """Scan source, build manifest, initialize or resume state."""
    if not source_path.is_dir():
        return {"error": f"Source path does not exist: {source_path}"}

    sdir = _source_dir(iroot, source_path)

    # Check for existing state — resume or rescan
    existing_state = _load_state(sdir / "state.json")
    if existing_state:
        manifest = _load_manifest(sdir / "manifest.txt")
        cursor = existing_state.get("cursor", 0)
        remaining = len(manifest) - cursor
        if remaining > 0:
            print(f"  Source already scanned — {remaining} files remaining at cursor {cursor}.",
                  file=sys.stderr)
            print("  Use --proceed N to continue uploading.\n", file=sys.stderr)
            existing_state["last_used"] = datetime.now(timezone.utc).isoformat()
            _save_state(sdir / "state.json", existing_state)
            return {"ok": True, "resumed": True, "remaining": remaining}

    print(f"  Scanning {source_path}...", file=sys.stderr)
    files = _scan_media(source_path)

    if not files:
        return {"error": f"No media files found in {source_path}"}

    _save_manifest(sdir / "manifest.txt", files)
    state = {
        "source": str(source_path.resolve()),
        "total_scanned": len(files),
        "total_queued": len(files),
        "cursor": 0,
        "uploaded": 0,
        "dupes_skipped": 0,
        "created": datetime.now(timezone.utc).isoformat(),
        "last_used": datetime.now(timezone.utc).isoformat(),
    }
    _save_state(sdir / "state.json", state)

    print("\n  Manifest built:", file=sys.stderr)
    print(f"    Source:            {source_path}", file=sys.stderr)
    print(f"    Media files found: {len(files)}", file=sys.stderr)
    print("\n  Run 'stack photos import --proceed N' to start uploading.\n",
          file=sys.stderr)

    return {"ok": True, "queued": len(files)}


def _cmd_status(iroot, specific_source=None):
    """Show import progress for all sources or a specific one."""
    if specific_source:
        sdir = _source_dir(iroot, specific_source)
        state = _load_state(sdir / "state.json")
        if not state:
            print(f"  No import found for {specific_source}", file=sys.stderr)
            print(f"  Run: stack photos import --source {specific_source}\n",
                  file=sys.stderr)
            return {"ok": True, "active": False}
        manifest = _load_manifest(sdir / "manifest.txt")
        _print_source_status(state, manifest)
        return {"ok": True}

    sources = _list_sources(iroot)
    if not sources:
        print("  No imports in progress.", file=sys.stderr)
        print("  Start with: stack photos import --source /path/to/media\n",
              file=sys.stderr)
        return {"ok": True, "active": False}

    ledger = _load_ledger(_ledger_path(iroot))
    print(f"\n  Import overview ({len(ledger)} total hashes in ledger):\n",
          file=sys.stderr)

    for sdir, state, remaining in sources:
        source = state.get("source", "?")
        uploaded = state.get("uploaded", 0)
        queued = state.get("total_queued", 0)
        status = "done" if remaining == 0 else f"{remaining} remaining"
        marker = " *" if remaining > 0 else ""
        print(f"    {source}", file=sys.stderr)
        print(f"      {uploaded}/{queued} uploaded, {status}{marker}", file=sys.stderr)

    print(file=sys.stderr)
    return {"ok": True, "sources": len(sources)}


def _print_source_status(state, manifest):
    cursor = state.get("cursor", 0)
    remaining = len(manifest) - cursor

    print("\n  Import status:", file=sys.stderr)
    print(f"    Source:            {state.get('source', '?')}", file=sys.stderr)
    print(f"    Total scanned:     {state.get('total_scanned', '?')}", file=sys.stderr)
    print(f"    Already imported:  {state.get('already_imported', 0)}", file=sys.stderr)
    print(f"    Queued:            {state.get('total_queued', len(manifest))}", file=sys.stderr)
    print(f"    Uploaded:          {state.get('uploaded', 0)}", file=sys.stderr)
    print(f"    Dupes skipped:     {state.get('dupes_skipped', 0)}", file=sys.stderr)
    print(f"    Cursor:            {cursor}/{len(manifest)}", file=sys.stderr)
    print(f"    Remaining:         {remaining}", file=sys.stderr)

    if remaining > 0 and manifest:
        next_files = manifest[cursor:cursor + 3]
        print("\n  Next up:", file=sys.stderr)
        for f in next_files:
            print(f"    {f}", file=sys.stderr)
        if remaining > 3:
            print(f"    ... and {remaining - 3} more", file=sys.stderr)
    elif remaining == 0:
        print("\n  Import complete!", file=sys.stderr)

    print(file=sys.stderr)


def _cmd_proceed(n, iroot, config, source_path=None, dry_run=False, verify=False,
                 album="Imported", force=False, album_per_year=False):
    """Process next N files from a source."""
    # Resolve which source to use
    if source_path:
        sdir = _source_dir(iroot, source_path)
    else:
        found = _find_active_source(iroot)
        if not found:
            return {"error": "No active import. Run --source first."}
        sdir, _ = found

    state_path = sdir / "state.json"
    manifest_path = sdir / "manifest.txt"
    ledger_p = _ledger_path(iroot)

    state = _load_state(state_path)
    if not state:
        return {"error": "No import in progress for this source. Run --source first."}

    manifest = _load_manifest(manifest_path)
    source = state["source"]
    cursor = state.get("cursor", 0)

    if cursor >= len(manifest):
        print("  All files have been processed.\n", file=sys.stderr)
        return {"ok": True, "message": "complete"}

    if not Path(source).is_dir():
        return {"error": f"Source not accessible: {source}\nIs the drive mounted?"}

    batch = manifest[cursor:cursor + n]
    print(f"\n  Processing {len(batch)} files (#{cursor + 1} – #{cursor + len(batch)}"
          f" of {len(manifest)}):\n", file=sys.stderr)

    # Hash and dedup against ledger
    ledger = set() if force else _load_ledger(ledger_p)
    to_upload = []
    to_upload_hashes = []
    dupes = 0

    for i, rel in enumerate(batch):
        full = Path(source) / rel
        if not full.exists():
            print(f"    ! missing: {rel}", file=sys.stderr)
            continue

        h = _hash_file(full)
        if h in ledger:
            dupes += 1
        else:
            to_upload.append(rel)
            to_upload_hashes.append(h)

        if (i + 1) % 100 == 0:
            print(f"    hashed {i + 1}/{len(batch)}...", file=sys.stderr)

    skip_msg = " (--force, hash check skipped)" if force else ""
    print(f"    {len(to_upload)} new, {dupes} duplicates skipped{skip_msg}", file=sys.stderr)

    if verify and to_upload:
        _verify_metadata(source, to_upload)

    # Group by year if requested
    year_groups = None
    if album_per_year and to_upload:
        print("\n  Extracting years...", file=sys.stderr)
        years = _extract_years(source, to_upload)
        year_groups = {}
        unknown = []
        for rel in to_upload:
            y = years.get(rel)
            if y:
                year_groups.setdefault(y, []).append(rel)
            else:
                unknown.append(rel)
        if unknown:
            year_groups["Unknown Year"] = unknown
        for y in sorted(year_groups):
            label = y if y != "Unknown Year" else "Unknown Year (no EXIF date or year in filename)"
            print(f"    {label}: {len(year_groups[y])} files", file=sys.stderr)

    if dry_run:
        if year_groups:
            print(f"\n  Would upload {len(to_upload)} files into {len(year_groups)} album(s). "
                  "No changes made.\n", file=sys.stderr)
        else:
            print(f"\n  Dry run — next {min(10, len(to_upload))} files:\n", file=sys.stderr)
            for rel in to_upload[:10]:
                print(f"    {rel}", file=sys.stderr)
            if len(to_upload) > 10:
                print(f"    ... and {len(to_upload) - 10} more", file=sys.stderr)
            print(f"\n  Would upload {len(to_upload)} files. No changes made.\n",
                  file=sys.stderr)
        return {"ok": True, "would_upload": len(to_upload), "dupes": dupes}

    if not config["is_healthy"]():
        return {"error": "Photos is not running — start it with 'stack up photos'"}

    api_key = _get_api_key(config)
    if not api_key:
        return {"error": (
            "No Immich API key found. Create one in the Immich admin UI "
            "(Administration > API Keys) and add it to .stack/secrets.toml "
            "as photos__IMPORT_API_KEY = \"your-key\""
        )}

    if not to_upload:
        state["cursor"] = cursor + len(batch)
        state["dupes_skipped"] = state.get("dupes_skipped", 0) + dupes
        state["last_used"] = datetime.now(timezone.utc).isoformat()
        _save_state(state_path, state)
        print(f"  All {dupes} files already imported. Cursor advanced.\n",
              file=sys.stderr)
        return {"ok": True, "uploaded": 0, "dupes": dupes}

    immich_version = _get_immich_version(config)

    if year_groups:
        success = True
        for y in sorted(year_groups):
            album_name = y
            files = year_groups[y]
            print(f"\n  Album \"{album_name}\" ({len(files)} files):", file=sys.stderr)
            if not _upload_files(source, files, immich_version, api_key, album_name):
                success = False
                break
    else:
        success = _upload_files(source, to_upload, immich_version, api_key, album)

    if success:
        _append_ledger(ledger_p, to_upload_hashes)
        state["cursor"] = cursor + len(batch)
        state["uploaded"] = state.get("uploaded", 0) + len(to_upload)
        state["dupes_skipped"] = state.get("dupes_skipped", 0) + dupes
        state["last_used"] = datetime.now(timezone.utc).isoformat()
        _save_state(state_path, state)

        remaining = len(manifest) - state["cursor"]
        print(f"  Uploaded {len(to_upload)} files. {remaining} remaining.\n",
              file=sys.stderr)
        return {"ok": True, "uploaded": len(to_upload), "dupes": dupes,
                "remaining": remaining}
    else:
        return {"error": "Upload failed. Cursor not advanced — safe to retry."}


def _cmd_reset_cursor(iroot, source_path=None):
    """Reset cursor to 0 for a source."""
    if source_path:
        sdir = _source_dir(iroot, source_path)
    else:
        found = _find_active_source(iroot)
        if not found:
            return {"error": "No active import. Use --source to specify which one."}
        sdir, _ = found

    state_path = sdir / "state.json"
    state = _load_state(state_path)
    if not state:
        return {"error": "No import state found for this source."}

    old_cursor = state.get("cursor", 0)
    state["cursor"] = 0
    state["uploaded"] = 0
    state["dupes_skipped"] = 0
    state["last_used"] = datetime.now(timezone.utc).isoformat()
    _save_state(state_path, state)

    manifest = _load_manifest(sdir / "manifest.txt")
    print(f"  Cursor reset: {old_cursor} → 0 ({len(manifest)} files queued)", file=sys.stderr)
    print(f"  Source: {state.get('source', '?')}\n", file=sys.stderr)
    return {"ok": True}


# ── Entry point ─────────────────────────────────────────────────────────────

def run(args, stacklet, config):
    opts = _parse_args(args)
    iroot = _import_root(config)

    if opts.reset_cursor:
        return _cmd_reset_cursor(iroot, source_path=opts.source)

    if opts.source and opts.status:
        return _cmd_status(iroot, specific_source=opts.source)

    album = opts.album or ("Imported" if not opts.album_per_year else None)

    if opts.source and opts.proceed:
        _cmd_source(opts.source, iroot)
        return _cmd_proceed(opts.proceed, iroot, config, source_path=opts.source,
                            dry_run=opts.dry_run, verify=opts.verify,
                            album=album, force=opts.force,
                            album_per_year=opts.album_per_year)

    if opts.source:
        return _cmd_source(opts.source, iroot)

    if opts.status:
        return _cmd_status(iroot)

    if opts.proceed:
        return _cmd_proceed(opts.proceed, iroot, config,
                            dry_run=opts.dry_run, verify=opts.verify,
                            album=album, force=opts.force,
                            album_per_year=opts.album_per_year)

    print("  Usage:", file=sys.stderr)
    print("    stack photos import --source /Volumes/NAS/photos   # scan source", file=sys.stderr)
    print("    stack photos import --status                        # show progress", file=sys.stderr)
    print("    stack photos import --proceed 100                   # upload next 100", file=sys.stderr)
    print("    stack photos import --proceed 10 --force            # skip hash check", file=sys.stderr)
    print("    stack photos import --reset-cursor                  # reset to start", file=sys.stderr)
    print("    stack photos import --source /path --status         # status for one source\n",
          file=sys.stderr)
    return {"error": "No action specified. Use --source, --status, --proceed, --reset-cursor."}
