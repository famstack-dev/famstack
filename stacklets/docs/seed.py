"""Seed Paperless-ngx with person tags from users.toml.

Idempotent -- safe to run on every `stack up docs`. Skips tags that
already exist. Creates "Person: X" tags for each family member so
the Archivist can associate documents with people.

    users.toml              Paperless tags
    ──────────              ──────────────
    name = "Homer"    ->    "Person: Homer"  (blue)
    name = "Marge"    ->    "Person: Marge"  (blue)
    name = "Bart"     ->    "Person: Bart"   (blue)

Topic tags and correspondents are NOT seeded. They grow organically
from the Archivist's LLM classifications in the document's language.
"""

import json

PERSON_TAG_COLOR = "#2196f3"


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
    import urllib.request

    if not users or not token:
        return []

    headers = {"Authorization": f"Token {token}"}

    # Fetch existing tags once
    existing_tags = set()
    try:
        req = urllib.request.Request(
            f"{paperless_url}/api/tags/?page_size=1000",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            existing_tags = {t["name"] for t in data.get("results", [])}
    except Exception:
        pass

    created = []
    for user in users:
        # First name only: "Homer J. Simpson" -> "Homer"
        name = user.get("name", "").split()[0]
        if not name:
            continue

        tag_name = f"Person: {name}"
        if tag_name in existing_tags:
            continue

        try:
            body = json.dumps({
                "name": tag_name,
                "color": PERSON_TAG_COLOR,
                "matching_algorithm": 0,
            }).encode()
            req = urllib.request.Request(
                f"{paperless_url}/api/tags/",
                data=body,
                headers={**headers, "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 201:
                    created.append(tag_name)
        except Exception as e:
            if step:
                step(f"Could not create tag {tag_name}: {e}")

    if created and step:
        step(f"Seeded {len(created)} person tag(s): {', '.join(created)}")

    return created
