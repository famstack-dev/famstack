"""`stack docs tags` — list, merge, prune, and delete Paperless tags/types.

Four operations sharing one concern: Paperless's tag + document_type
tables drift over time (seed-language flips, pre-taxonomy legacy labels,
LLM-invented one-offs). The housekeeping path is:

    tags            see what's there, grouped by owner and doc count
    tags prune      delete dead wrong-language entries (safe, 0 docs)
    tags merge      fold one tag/type into another, retagging live docs
    tags delete     remove a single tag (refuses if docs are attached)

Talks directly to the tag + document_type endpoints via PaperlessAPI's
aiohttp session, reusing its auth headers. PaperlessAPI itself only
knows about the bot's enrich path; admin CRUD lives here.
"""

from __future__ import annotations

from pathlib import Path

from pipeline import Classifier, PaperlessAPI

from cli._shared import _DRY_FLAGS, err, is_dry


_SUBCOMMANDS = {"merge", "prune", "delete"}


async def run(paperless: PaperlessAPI, classifier: Classifier,
              argv: list[str]) -> int:
    """Dispatch `tags [merge|prune|delete|...]`. Bare `tags` lists."""
    if argv and argv[0] in _SUBCOMMANDS:
        sub, rest = argv[0], argv[1:]
        if sub == "merge":
            return await _merge(paperless, rest)
        if sub == "prune":
            return await _prune(paperless, rest)
        if sub == "delete":
            return await _delete(paperless, rest)
    return await _list(paperless, argv)


# ── tags list ───────────────────────────────────────────────────────────

async def _list(paperless: PaperlessAPI, argv: list[str]) -> int:
    show_types = "--types" in argv
    only_used = "--used" in argv
    only_unused = "--unused" in argv
    owner_filter: int | None = None
    for a in argv:
        if a.startswith("--owner="):
            try:
                owner_filter = int(a.split("=", 1)[1])
            except ValueError:
                err(f"Invalid --owner value: {a}")
                return 2

    flag_tokens = {"--types", "--used", "--unused"}
    unknown = [a for a in argv if a.startswith("--") and a not in flag_tokens
               and not a.startswith("--owner=")]
    if unknown:
        err(f"Unknown flag(s): {' '.join(unknown)}")
        return 2
    if only_used and only_unused:
        err("--used and --unused are mutually exclusive")
        return 2

    endpoint = "document_types" if show_types else "tags"
    records = await _list_full(paperless, endpoint)
    if owner_filter is not None:
        records = [r for r in records if r.get("owner") == owner_filter]
    if only_used:
        records = [r for r in records if (r.get("document_count") or 0) > 0]
    if only_unused:
        records = [r for r in records if (r.get("document_count") or 0) == 0]

    _print_table(records, kind="type" if show_types else "tag")
    return 0


def _print_table(records: list[dict], *, kind: str) -> None:
    from stack.prompt import BOLD, DIM, RESET
    if not records:
        print(f"\n  No {kind}s match.\n")
        return
    records.sort(key=lambda r: (-(r.get("document_count") or 0),
                                r.get("name", "")))
    print()
    print(f"  {BOLD}{'ID':>4}  {'DOCS':>4}  {'OWNER':>5}  NAME{RESET}")
    print(f"  {DIM}{'─' * 60}{RESET}")
    for r in records:
        rid = r.get("id", "?")
        count = r.get("document_count") or 0
        owner = r.get("owner")
        owner_str = str(owner) if owner is not None else "-"
        name = r.get("name", "")
        dim_name = f"{DIM}{name}{RESET}" if count == 0 else name
        print(f"  {rid:>4}  {count:>4}  {owner_str:>5}  {dim_name}")
    print()
    print(f"  {DIM}{len(records)} {kind}(s){RESET}\n")


# ── tags prune ──────────────────────────────────────────────────────────

async def _prune(paperless: PaperlessAPI, argv: list[str]) -> int:
    dry_run = is_dry(argv)

    lang: str | None = None
    for a in argv:
        if a.startswith("--lang="):
            lang = a.split("=", 1)[1].strip().lower()
            break
    if lang is None and "--lang" in argv:
        idx = argv.index("--lang")
        if idx + 1 < len(argv):
            lang = argv[idx + 1].strip().lower()

    if lang not in ("de", "en"):
        err("Usage: tags prune --lang <de|en> [--dry|--dry-run]")
        return 2

    taxonomy = _read_taxonomy()
    section = taxonomy.get(lang)
    if not section:
        err(f"No [{lang}] section in taxonomy.toml")
        return 1

    target_tags = set(section.get("tags") or [])
    target_types = set(section.get("types") or [])

    tags = await _list_full(paperless, "tags")
    types_ = await _list_full(paperless, "document_types")

    prune_tags = [t for t in tags
                  if t.get("name") in target_tags
                  and (t.get("document_count") or 0) == 0]
    prune_types = [t for t in types_
                   if t.get("name") in target_types
                   and (t.get("document_count") or 0) == 0]

    _print_prune_plan(prune_tags, prune_types, lang=lang, dry_run=dry_run)

    if dry_run or not (prune_tags or prune_types):
        return 0

    failures = 0
    for t in prune_tags:
        if not await _delete_entity(paperless, "tags", t["id"]):
            failures += 1
    for t in prune_types:
        if not await _delete_entity(paperless, "document_types", t["id"]):
            failures += 1

    _print_prune_summary(len(prune_tags) + len(prune_types), failures)
    return 0 if failures == 0 else 1


def _print_prune_plan(prune_tags: list[dict], prune_types: list[dict],
                      *, lang: str, dry_run: bool) -> None:
    from stack.prompt import BOLD, DIM, ORANGE, RESET
    marker = f"  {DIM}(DRY RUN){RESET}" if dry_run else ""
    verb = "Would delete" if dry_run else "Deleting"
    print()
    print(f"  {BOLD}tags prune --lang {lang}{RESET}{marker}")
    print(f"  {DIM}{'─' * 60}{RESET}")
    if not prune_tags and not prune_types:
        print(f"  {DIM}Nothing to prune — no dead [{lang}] entries.{RESET}\n")
        return
    if prune_tags:
        print(f"  {verb} {len(prune_tags)} tag(s):")
        for t in prune_tags:
            print(f"    {ORANGE}#{t['id']}{RESET}  {t['name']}  "
                  f"{DIM}(owner={t.get('owner')}){RESET}")
    if prune_types:
        print(f"  {verb} {len(prune_types)} type(s):")
        for t in prune_types:
            print(f"    {ORANGE}#{t['id']}{RESET}  {t['name']}  "
                  f"{DIM}(owner={t.get('owner')}){RESET}")
    print()


def _print_prune_summary(total: int, failures: int) -> None:
    from stack.prompt import GREEN, ORANGE, RESET
    ok = total - failures
    icon = f"{GREEN}✓{RESET}" if failures == 0 else f"{ORANGE}!{RESET}"
    print(f"  {icon} pruned {ok}/{total}"
          + (f" ({failures} failed)" if failures else ""))
    print()


# ── tags merge ──────────────────────────────────────────────────────────

async def _merge(paperless: PaperlessAPI, argv: list[str]) -> int:
    dry_run = is_dry(argv)
    is_type = "--type" in argv or "--types" in argv

    flag_tokens = {"--type", "--types", *_DRY_FLAGS}
    positional = [a for a in argv if a not in flag_tokens]
    unknown = [a for a in argv if a.startswith("--") and a not in flag_tokens]
    if unknown:
        err(f"Unknown flag(s): {' '.join(unknown)}")
        return 2
    if len(positional) != 2:
        err("Usage: tags merge <from> <to> [--type] [--dry|--dry-run]")
        return 2
    from_name, to_name = positional

    endpoint = "document_types" if is_type else "tags"
    kind = "type" if is_type else "tag"
    records = await _list_full(paperless, endpoint)
    by_name = {r["name"]: r for r in records}

    src = by_name.get(from_name)
    dst = by_name.get(to_name)
    if not src:
        err(f"Source {kind} not found: {from_name!r}")
        return 1
    if not dst:
        err(f"Target {kind} not found: {to_name!r}")
        return 1
    if src["id"] == dst["id"]:
        err(f"{from_name!r} and {to_name!r} resolve to the same {kind}")
        return 1

    doc_ids = await _docs_with_entity(paperless, endpoint, src["id"])
    _print_merge_plan(src, dst, doc_ids, kind=kind, dry_run=dry_run)

    if dry_run:
        return 0

    if doc_ids and not await _bulk_reassign(
        paperless, endpoint, doc_ids, src["id"], dst["id"],
    ):
        err("Bulk reassign failed; aborting before delete.")
        return 1
    if not await _delete_entity(paperless, endpoint, src["id"]):
        return 1

    _print_merge_summary(src["name"], dst["name"], len(doc_ids), kind=kind)
    return 0


async def _docs_with_entity(paperless: PaperlessAPI, endpoint: str,
                             entity_id: int) -> list[int]:
    """Return every doc id carrying a given tag or assigned a given type."""
    field = "tags__id__in" if endpoint == "tags" else "document_type__id"
    ids: list[int] = []
    page = 1
    while True:
        async with paperless.http.get(
            f"{paperless.url}/api/documents/",
            headers=paperless._headers,
            params={field: str(entity_id), "page_size": "100",
                    "page": str(page)},
        ) as resp:
            if resp.status != 200:
                body = (await resp.text())[:200]
                err(f"GET /documents/ → {resp.status}: {body}")
                return ids
            data = await resp.json()
            ids.extend(d["id"] for d in (data.get("results") or []))
            if not data.get("next"):
                return ids
            page += 1


async def _bulk_reassign(paperless: PaperlessAPI, endpoint: str,
                         doc_ids: list[int], src_id: int, dst_id: int) -> bool:
    if endpoint == "tags":
        payload = {
            "documents": doc_ids,
            "method": "modify_tags",
            "parameters": {"add_tags": [dst_id], "remove_tags": [src_id]},
        }
    else:
        payload = {
            "documents": doc_ids,
            "method": "set_document_type",
            "parameters": {"document_type": dst_id},
        }
    async with paperless.http.post(
        f"{paperless.url}/api/documents/bulk_edit/",
        headers=paperless._json_headers, json=payload,
    ) as resp:
        if resp.status == 200:
            return True
        body = (await resp.text())[:200]
        err(f"bulk_edit → {resp.status}: {body}")
        return False


def _print_merge_plan(src: dict, dst: dict, doc_ids: list[int],
                     *, kind: str, dry_run: bool) -> None:
    from stack.prompt import BOLD, DIM, ORANGE, RESET, TEAL
    marker = f"  {DIM}(DRY RUN){RESET}" if dry_run else ""
    verb = "Would merge" if dry_run else "Merging"
    print()
    print(f"  {BOLD}{verb} {kind}: {src['name']} → {dst['name']}{RESET}{marker}")
    print(f"  {DIM}{'─' * 60}{RESET}")
    print(f"    {DIM}source:{RESET}   {ORANGE}#{src['id']}{RESET}  {src['name']}  "
          f"{DIM}(owner={src.get('owner')}, {src.get('document_count', 0)} doc(s)){RESET}")
    print(f"    {DIM}target:{RESET}   {TEAL}#{dst['id']}{RESET}  {dst['name']}  "
          f"{DIM}(owner={dst.get('owner')}, {dst.get('document_count', 0)} doc(s)){RESET}")
    if doc_ids:
        preview = ", ".join(f"#{i}" for i in doc_ids[:10])
        suffix = " …" if len(doc_ids) > 10 else ""
        print(f"    {DIM}docs:{RESET}     {len(doc_ids)} → {preview}{suffix}")
    else:
        print(f"    {DIM}docs:{RESET}     (none — source has no documents)")
    print()


def _print_merge_summary(src_name: str, dst_name: str, retagged: int,
                        *, kind: str) -> None:
    from stack.prompt import GREEN, RESET
    print(f"  {GREEN}✓{RESET} merged {kind} {src_name} → {dst_name} "
          f"({retagged} doc(s) retagged, source deleted)")
    print()


# ── tags delete ────────────────────────────────────────────────────────

async def _delete(paperless: PaperlessAPI, argv: list[str]) -> int:
    dry_run = is_dry(argv)
    is_type = "--type" in argv or "--types" in argv

    flag_tokens = {"--type", "--types", *_DRY_FLAGS}
    positional = [a for a in argv if a not in flag_tokens]
    unknown = [a for a in argv if a.startswith("--") and a not in flag_tokens]
    if unknown:
        err(f"Unknown flag(s): {' '.join(unknown)}")
        return 2
    if len(positional) != 1:
        err("Usage: tags delete <name> [--type] [--dry|--dry-run]")
        return 2
    name = positional[0]

    endpoint = "document_types" if is_type else "tags"
    kind = "type" if is_type else "tag"
    records = await _list_full(paperless, endpoint)
    by_name = {r["name"]: r for r in records}

    entity = by_name.get(name)
    if not entity:
        err(f"{kind.capitalize()} not found: {name!r}")
        return 1
    if (entity.get("document_count") or 0) > 0:
        err(f"{kind.capitalize()} {name!r} has {entity['document_count']} "
            f"document(s) — use `tags merge` instead.")
        return 1

    from stack.prompt import BOLD, DIM, GREEN, ORANGE, RESET
    marker = f"  {DIM}(DRY RUN){RESET}" if dry_run else ""
    verb = "Would delete" if dry_run else "Deleting"
    print()
    print(f"  {BOLD}{verb} {kind}: {name}{RESET}{marker}")
    print(f"    {ORANGE}#{entity['id']}{RESET}  "
          f"{DIM}(owner={entity.get('owner')}){RESET}")

    if dry_run:
        print()
        return 0

    if not await _delete_entity(paperless, endpoint, entity["id"]):
        return 1
    print(f"  {GREEN}✓{RESET} deleted {kind} {name}\n")
    return 0


# ── Paperless CRUD (tag/type endpoints) ─────────────────────────────────

async def _list_full(paperless: PaperlessAPI, endpoint: str) -> list[dict]:
    """Return full entity records (with owner + document_count)."""
    async with paperless.http.get(
        f"{paperless.url}/api/{endpoint}/?page_size=1000",
        headers=paperless._headers,
    ) as resp:
        if resp.status != 200:
            return []
        return (await resp.json()).get("results", [])


async def _delete_entity(paperless: PaperlessAPI, endpoint: str,
                        entity_id: int) -> bool:
    async with paperless.http.delete(
        f"{paperless.url}/api/{endpoint}/{entity_id}/",
        headers=paperless._headers,
    ) as resp:
        if resp.status in (200, 204):
            return True
        body = (await resp.text())[:200]
        err(f"DELETE /{endpoint}/{entity_id}/ → {resp.status}: {body}")
        return False


def _read_taxonomy() -> dict:
    try:
        import tomllib
    except ModuleNotFoundError:
        from stack._compat import tomllib
    path = Path("/stacklets/docs/taxonomy.toml")
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)
