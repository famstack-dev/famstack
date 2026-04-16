"""Seed Paperless-ngx with person tags and document taxonomy.

Idempotent -- safe to run on every `stack up docs`. Skips entries that
already exist. Seeds two things:

1. Person tags from users.toml:

    users.toml              Paperless tags
    ──────────              ──────────────
    name = "Homer"    ->    "Person: Homer"  (blue)
    name = "Marge"    ->    "Person: Marge"  (blue)

2. Category tags and document types from taxonomy.yaml:

    taxonomy.yaml           Paperless tags / types
    ─────────────           ──────────────────────
    de.tags.Versicherung -> tag "Versicherung"    (green)
    de.types.Rechnung    -> doc type "Rechnung"

stdlib only -- no framework dependencies so hooks can import this directly.
"""

import json
from pathlib import Path

PERSON_TAG_COLOR = "#2196f3"
CATEGORY_TAG_COLOR = "#4caf50"
TAXONOMY_PATH = Path(__file__).parent / "taxonomy.toml"


def _load_taxonomy(language: str) -> dict:
    """Load taxonomy for the given language from taxonomy.toml.

    Falls back to English if the requested language isn't defined.
    Returns {"tags": [...], "types": [...]}.
    """
    try:
        import tomllib
    except ModuleNotFoundError:
        from stack._compat import tomllib

    if not TAXONOMY_PATH.exists():
        return {"tags": [], "types": []}

    with open(TAXONOMY_PATH, "rb") as f:
        data = tomllib.load(f)

    lang_key = language[:2].lower()
    section = data.get(lang_key, data.get("en", {}))

    return {
        "tags": section.get("tags", []),
        "types": section.get("types", []),
    }


def _fetch_existing(paperless_url: str, headers: dict, endpoint: str) -> set:
    """Fetch existing entity names from a Paperless API endpoint."""
    import urllib.request

    try:
        req = urllib.request.Request(
            f"{paperless_url}/api/{endpoint}/?page_size=1000",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return {t["name"] for t in data.get("results", [])}
    except Exception:
        return set()


def _create_entity(paperless_url: str, headers: dict, endpoint: str, body: dict) -> bool:
    """Create a single entity via Paperless API. Returns True on success."""
    import urllib.request

    try:
        req = urllib.request.Request(
            f"{paperless_url}/api/{endpoint}/",
            data=json.dumps(body).encode(),
            headers={**headers, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 201
    except Exception:
        return False


def seed_person_tags(paperless_url: str, token: str, users: list[dict], step=None):
    """Create person tags in Paperless for each user.

    Args:
        paperless_url: Paperless base URL (e.g. "http://localhost:42020")
        token: Paperless API token
        users: list of user dicts from users.toml
        step: optional callback for progress messages

    Returns:
        list of tag names that were created (empty if all existed)
    """
    if not users or not token:
        return []

    headers = {"Authorization": f"Token {token}"}
    existing_tags = _fetch_existing(paperless_url, headers, "tags")

    created = []
    for user in users:
        name = user.get("name", "").split()[0]
        if not name:
            continue

        tag_name = f"Person: {name}"
        if tag_name in existing_tags:
            continue

        if _create_entity(paperless_url, headers, "tags", {
            "name": tag_name,
            "color": PERSON_TAG_COLOR,
            "matching_algorithm": 0,
        }):
            created.append(tag_name)
        elif step:
            step(f"Could not create tag {tag_name}")

    if created and step:
        step(f"Seeded {len(created)} person tag(s): {', '.join(created)}")

    return created


def seed_taxonomy(paperless_url: str, token: str, language: str = "en", step=None):
    """Seed category tags and document types from taxonomy.yaml.

    Args:
        paperless_url: Paperless base URL
        token: Paperless API token
        language: language code (e.g. "de", "en")
        step: optional callback for progress messages

    Returns:
        dict with "tags" and "types" lists of created names
    """
    if not token:
        return {"tags": [], "types": []}

    taxonomy = _load_taxonomy(language)
    headers = {"Authorization": f"Token {token}"}

    existing_tags = _fetch_existing(paperless_url, headers, "tags")
    existing_types = _fetch_existing(paperless_url, headers, "document_types")

    created_tags = []
    for tag_name in taxonomy["tags"]:
        if tag_name in existing_tags:
            continue
        if _create_entity(paperless_url, headers, "tags", {
            "name": tag_name,
            "color": CATEGORY_TAG_COLOR,
            "matching_algorithm": 0,
        }):
            created_tags.append(tag_name)

    created_types = []
    for type_name in taxonomy["types"]:
        if type_name in existing_types:
            continue
        if _create_entity(paperless_url, headers, "document_types", {
            "name": type_name,
            "matching_algorithm": 0,
        }):
            created_types.append(type_name)

    if created_tags and step:
        step(f"Seeded {len(created_tags)} category tag(s)")
    if created_types and step:
        step(f"Seeded {len(created_types)} document type(s)")

    return {"tags": created_tags, "types": created_types}
