"""stack memory ontology — sync the live vault ontology with the shipped seed.

The classifier reads `ontology.toml` from each instance's memory vault
(in Forgejo, mirrored to the local working copy). When the famstack
seed gains new topics, keywords, or synonyms, this command pushes
those changes to the live vault without manual web-UI edits.

    stack memory ontology              Push the seed to Forgejo, pull locally.
    stack memory ontology --dry-run    Show the diff and exit without writing.

The push REPLACES the vault's `ontology.toml` wholesale — if the
household has hand-curated topics in Forgejo, those changes are
clobbered. Run `--dry-run` first when you suspect drift. The
non-destructive way to add a single topic to a curated vault is
still a Forgejo web-UI edit.
"""

from __future__ import annotations

import difflib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import (  # noqa: E402
    BOT_EMAIL,
    BOT_USERNAME,
    ONTOLOGY_PATH_IN_REPO,
    REPO_NAME,
    REPO_OWNER,
    SEED_ONTOLOGY_PATH,
    host_code_url,
    pull_vault,
    vault_path_for,
)
from stack.forgejo import ForgejoClient  # noqa: E402

HELP = "Sync the live vault ontology with the shipped seed"


def run(args, stacklet, config):
    dry_run = "--dry-run" in (args or [])

    data_dir = config.get("data_dir") if config else None
    if not data_dir:
        return {"error": "stack data_dir not configured"}

    seed_text = SEED_ONTOLOGY_PATH.read_text(encoding="utf-8")

    secrets = config.get("secrets", {}) if config else {}
    code_url = host_code_url(secrets.get("__code_url", "") or _code_url(config))
    vault = vault_path_for(Path(data_dir))
    # Two install paths leave creds in different places: the bot-token
    # install ships a `memory__MEMORY_BOT_TOKEN` secret; the admin-only
    # install (which this instance used) embeds an admin token in the
    # local clone's git remote URL. Prefer the secret when present;
    # otherwise parse the URL so the command works on both layouts.
    token = secrets.get("memory__MEMORY_BOT_TOKEN", "")
    if not token:
        token = _token_from_vault_remote(vault) or ""
    if not (token and code_url):
        return {
            "error": (
                "Forgejo credentials missing — run `stack up memory` first "
                "to seed the vault clone"
            ),
        }

    client = ForgejoClient(url=code_url, token=token)
    current = client.get_file(REPO_OWNER, REPO_NAME, ONTOLOGY_PATH_IN_REPO)
    if current is None:
        return {
            "error": (
                f"{REPO_OWNER}/{REPO_NAME}/{ONTOLOGY_PATH_IN_REPO} not found "
                "in Forgejo — run `stack up memory` first"
            ),
        }

    live_text = current.get("content", "") or ""

    if seed_text == live_text:
        print("Ontology is already in sync with the shipped seed.")
        return {"ok": True, "applied": False, "in_sync": True}

    # Always show the diff -- the user's eyes on what's about to land
    # (or what would land in a dry run). Keep it terse so the apply
    # status at the bottom stays on the same screen.
    diff = difflib.unified_diff(
        live_text.splitlines(),
        seed_text.splitlines(),
        fromfile=f"forgejo:{REPO_OWNER}/{REPO_NAME}/{ONTOLOGY_PATH_IN_REPO}",
        tofile=str(SEED_ONTOLOGY_PATH.relative_to(SEED_ONTOLOGY_PATH.parents[3])),
        lineterm="",
    )
    for line in diff:
        print(line)

    if dry_run:
        print()
        print("Dry run — nothing pushed. Drop `--dry-run` to apply.")
        return {"ok": True, "applied": False, "in_sync": False}

    client.put_file(
        REPO_OWNER, REPO_NAME, ONTOLOGY_PATH_IN_REPO,
        content=seed_text,
        message="chore(ontology): sync from shipped seed",
        sha=current["sha"],
        author_name=BOT_USERNAME, author_email=BOT_EMAIL,
    )

    pulled = pull_vault(vault) if (vault / ".git").exists() else False
    print()
    print(f"Pushed seed to {REPO_OWNER}/{REPO_NAME}/{ONTOLOGY_PATH_IN_REPO}.")
    if pulled:
        print(f"Pulled into local vault at {vault}.")
    else:
        print(
            "Local vault not refreshed automatically — run `stack memory pull` "
            "or restart the bot to pick up the change.",
        )
    return {"ok": True, "applied": True, "pulled": pulled}


_REMOTE_URL_AUTH = re.compile(r'url\s*=\s*https?://[^:/\s]+:([^@\s]+)@')


def _token_from_vault_remote(vault: Path) -> str | None:
    """Read the token embedded in `<vault>/.git/config` origin URL.

    The install hook clones the vault with an authenticated URL of
    the form `http://<user>:<token>@<host>/...`; the token is the
    only credential the API client needs. Returns None when there is
    no clone, no origin, or no embedded auth.
    """
    cfg = vault / ".git" / "config"
    if not cfg.exists():
        return None
    try:
        text = cfg.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = _REMOTE_URL_AUTH.search(text)
    return m.group(1) if m else None


def _code_url(config) -> str:
    """Best-effort code-stacklet URL when the install hook hasn't cached it."""
    stck = config.get("stack") if config else None
    if isinstance(stck, dict):
        port = stck.get("code", {}).get("port", 42040)
    else:
        port = 42040
    return f"http://localhost:{port}"
