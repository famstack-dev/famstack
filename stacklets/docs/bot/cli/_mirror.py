"""Mirror bootstrap + publish — shared by `reprocess` and `mirror` commands.

Reads the archivist's bot.toml to honour `mirror_to_git` / `mirror_org`,
builds a GitMirror using the same env the bot reads from, and publishes
a doc to Forgejo. Separated from the commands so the two callers share
one authoritative mirror-init path.
"""

from __future__ import annotations

import os
from pathlib import Path

from pipeline import EnrichResult

from cli._shared import err


_BOT_TOML_PATH = Path("/stacklets/docs/bot/bot.toml")
_DATA_DIR = Path("/data/docs/bot")


def read_bot_toml_settings() -> dict:
    """Read [settings] from the archivist's bot.toml (same file the bot reads)."""
    try:
        import tomllib
    except ModuleNotFoundError:
        from stack._compat import tomllib
    if not _BOT_TOML_PATH.exists():
        return {}
    with open(_BOT_TOML_PATH, "rb") as f:
        data = tomllib.load(f)
    return data.get("settings", {})


def mirror_enabled_in_bot_toml() -> bool:
    return bool(read_bot_toml_settings().get("mirror_to_git", False))


def build_mirror_like_bot():
    """Build a GitMirror exactly like the archivist bot's `_init_mirror`.

    Reuses the bot's creds file at /data/docs/bot/forgejo-creds.json so
    the CLI and the bot authenticate as the same Forgejo user. Returns
    None when required env is missing (typically code stacklet down).
    """
    code_url = os.environ.get("CODE_URL", "")
    admin_user = os.environ.get("MATRIX_ADMIN_USER", "")
    admin_password = os.environ.get("MATRIX_ADMIN_PASSWORD", "")
    admin_ids = os.environ.get("STACK_ADMIN_USER_IDS", "")
    if not (code_url and admin_user and admin_password):
        return None

    admin_usernames: list[str] = []
    for raw in admin_ids.split(","):
        raw = raw.strip()
        if not raw:
            continue
        name = raw.lstrip("@").split(":", 1)[0]
        if name and name != admin_user:
            admin_usernames.append(name)

    from git_mirror import GitMirror
    settings = read_bot_toml_settings()
    return GitMirror(
        code_url=code_url,
        admin_user=admin_user,
        admin_password=admin_password,
        admin_usernames=admin_usernames,
        data_dir=_DATA_DIR,
        org_name=settings.get("mirror_org", "family"),
    )


async def publish_enriched(
    mirror, doc: dict, result: EnrichResult,
    *, formatted: str | None,
) -> str | None:
    """Publish a mirror entry for an enriched doc. Used by `reprocess`."""
    from stack import resolve_model

    ocr_text = doc.get("content") or ""
    if formatted:
        body_text = formatted
        processing = "ai_formatted"
        try:
            model = resolve_model("archivist-bot/reformat")
        except ValueError:
            model = None
    else:
        body_text = ocr_text
        processing = "ocr"
        model = None

    enriched = dict(result.classification) if result.classification else {}
    enriched["topics"] = result.resolved_topics
    enriched["persons"] = result.resolved_persons
    enriched["correspondent"] = result.resolved_correspondent
    enriched["document_type"] = result.resolved_type
    paperless_tags = [
        *result.resolved_topics,
        *(f"Person: {p}" for p in result.resolved_persons),
    ]

    paperless_url = (os.environ.get("PAPERLESS_PUBLIC_URL")
                     or os.environ.get("PAPERLESS_URL", ""))
    try:
        ok = await mirror.publish(
            paperless_id=doc["id"],
            classification=enriched,
            body_text=body_text,
            processing=processing,
            model=model,
            paperless_url=paperless_url,
            tags=paperless_tags,
            fallback_title=doc.get("title"),
        )
    except Exception as e:
        err(f"#{doc['id']}: mirror publish failed — {e}")
        return None
    return mirror._cache.get(doc["id"]) if ok else None


async def publish_current_state(
    mirror, doc: dict, tags: dict, doc_types: dict, correspondents: dict,
) -> str | None:
    """Publish a mirror entry from the doc's current Paperless state.

    No LLM call, no classification run — used by the standalone `mirror`
    backfill command. `processing` is pinned to "ocr" because we're
    shipping whatever Paperless's content field currently holds without
    tracking earlier provenance.
    """
    classification = _classification_from_doc(doc, tags, doc_types, correspondents)
    paperless_tag_names = [
        *classification.get("topics", []),
        *(f"Person: {p}" for p in classification.get("persons", [])),
    ]
    body_text = doc.get("content") or ""
    paperless_url = (os.environ.get("PAPERLESS_PUBLIC_URL")
                     or os.environ.get("PAPERLESS_URL", ""))

    try:
        ok = await mirror.publish(
            paperless_id=doc["id"],
            classification=classification,
            body_text=body_text,
            processing="ocr",
            model=None,
            paperless_url=paperless_url,
            tags=paperless_tag_names,
            fallback_title=doc.get("title"),
        )
    except Exception as e:
        err(f"#{doc['id']}: mirror publish failed — {e}")
        return None
    return mirror._cache.get(doc["id"]) if ok else None


def _classification_from_doc(doc: dict, tags: dict, doc_types: dict,
                              correspondents: dict) -> dict:
    """Reshape a Paperless doc's current fields into classification form.

    Mirror.publish expects topics / persons / correspondent / document_type
    as human-readable strings (not ids) — same shape the LLM produces.
    """
    tag_name = {tid: name for name, tid in tags.items()}
    type_name = {tid: name for name, tid in doc_types.items()}
    corr_name = {tid: name for name, tid in correspondents.items()}

    doc_tag_names = [tag_name[t] for t in (doc.get("tags") or []) if t in tag_name]
    topics = [t for t in doc_tag_names if not t.startswith("Person: ")]
    persons = [t.replace("Person: ", "") for t in doc_tag_names
               if t.startswith("Person: ")]
    date = (doc.get("created") or "")[:10] or None

    return {
        "title": doc.get("title") or "",
        "date": date,
        "topics": topics,
        "persons": persons,
        "correspondent": corr_name.get(doc.get("correspondent")),
        "document_type": type_name.get(doc.get("document_type")),
    }
