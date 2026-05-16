"""In-process API for the memory stacklet.

Memory is a host stacklet — no container, no port. It keeps the
family's curated knowledge in two places:

  - **Forgejo** (`family/memory`) — the canonical source of truth and
    the commit log. Web edits via Forgejo and Obsidian clones land
    here.
  - **A local working copy** under `<data_dir>/memory/vault/` —
    a `git clone` of the Forgejo repo. Every reader (the archivist
    classifier, `stack memory ...` CLI, future deriver bot, Phase 5
    wiki-rebuild) walks this directory. obsidiantools and Dataview
    expect a filesystem vault, so this is what gets us into the
    Obsidian ecosystem.

Sync model — pull-heavy, write-deliberate:

  - The install hook clones the vault after pushing seeds.
  - `on_start_ready` pulls on every `stack up memory`.
  - `stack memory pull` is the explicit refresh.
  - Writes (Phase 2 facts CLI, Phase 5 wiki-rebuild) edit the working
    copy then `git add && commit && push`.

The seed under `stacklets/memory/seeds/` stays in the repo as the
canonical starting state. It is read directly only when no vault
exists yet (a fresh checkout before the install hook runs, or when
Forgejo was unreachable on install).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from stack.forgejo import ForgejoClient
from stack.ontology import Ontology


STACKLET_DIR = Path(__file__).resolve().parent
SEEDS_DIR = STACKLET_DIR / "seeds"
SEED_ONTOLOGY_PATH = SEEDS_DIR / "ontology.toml"

# The org and repo names the `memory` repo lives under in Forgejo. The
# `family` org is shared with the documents repo (created by archivist-
# bot's GitMirror), so memory just slots in alongside.
REPO_OWNER = "family"
REPO_NAME = "memory"
REPO_DESCRIPTION = (
    "The family's curated knowledge — ontology, facts, and wiki. "
    "Hand-edit any file here; the commit log is the learning history."
)
ORG_DESCRIPTION = (
    "Your family's Forgejo — documents, knowledge, and shared repos."
)
ONTOLOGY_PATH_IN_REPO = "ontology.toml"
INSTALL_COMMIT_MESSAGE = "seed: initial memory from famstack {version}"

BOT_USERNAME = "memory-bot"
BOT_EMAIL = "memory-bot@local"
TOKEN_NAME = "memory-bot"
TOKEN_SCOPES = [
    "write:repository", "read:repository",
    "read:user", "write:organization",
]
SEED_COMMIT_MESSAGE = "seed: initial memory from famstack"


# ─── Seed loader ─────────────────────────────────────────────────────────
#
# The seed is the canonical *starting* state of an instance's memory.
# It mirrors `stacklets/docs/taxonomy.toml` with ids + synonyms +
# keywords + type cross-refs. Read directly only when no vault is
# available yet.

def load_seed_ontology() -> Ontology:
    """Read the shipped seed ontology and return a parsed `Ontology`."""
    return Ontology.load(SEED_ONTOLOGY_PATH)


# ─── Vault path + auth ───────────────────────────────────────────────────

def vault_path_for(data_dir: Path) -> Path:
    """Resolve the vault path under a stack's data dir."""
    return Path(data_dir) / "memory" / "vault"


def authenticated_remote(remote_url: str, username: str, token: str) -> str:
    """Inject HTTPS basic auth credentials into a Forgejo remote URL.

    `http://stack-code:3000/family/memory.git` becomes
    `http://memory-bot:<token>@stack-code:3000/family/memory.git`.
    Tokens are URL-safe characters by Forgejo's spec; no escaping needed.
    """
    if "://" not in remote_url:
        return remote_url
    scheme, rest = remote_url.split("://", 1)
    return f"{scheme}://{username}:{token}@{rest}"


def vault_remote_url(code_url: str) -> str:
    """The git remote URL for the memory repo on a given code stacklet."""
    return f"{code_url.rstrip('/')}/{REPO_OWNER}/{REPO_NAME}.git"


# ─── Vault sync ──────────────────────────────────────────────────────────
#
# Clone-if-missing and best-effort pulls. Both shell out to `git` —
# every Mac and Linux has it, and the framework already uses subprocess
# liberally for Docker. No GitPython dep, no libgit2.

def ensure_vault_cloned(
    vault_path: Path,
    remote_url: str,
    *,
    timeout: int = 60,
) -> bool:
    """Clone the memory repo into `vault_path` if not already present.

    `remote_url` is expected to carry credentials in the URL when the
    repo is private — see `authenticated_remote`. Returns True when a
    clone happened or a working copy was already there.
    """
    vault_path = Path(vault_path)
    if (vault_path / ".git").exists():
        return True

    vault_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "clone", remote_url, str(vault_path)],
        capture_output=True, text=True, timeout=timeout,
    )
    return result.returncode == 0


def pull_vault(vault_path: Path, *, timeout: int = 30) -> bool:
    """Fast-forward the vault from its remote. Best-effort.

    Returns False when the vault is missing, the remote is unreachable,
    or the local copy has diverged. Callers fall back to reading
    whatever is already on disk.
    """
    vault_path = Path(vault_path)
    if not (vault_path / ".git").exists():
        return False
    result = subprocess.run(
        ["git", "-C", str(vault_path), "pull", "--ff-only"],
        capture_output=True, text=True, timeout=timeout,
    )
    return result.returncode == 0


# ─── Vault readers ───────────────────────────────────────────────────────

def load_ontology_from_vault(vault_path: Path) -> Optional[Ontology]:
    """Read `ontology.toml` from the vault working copy.

    Returns None when the vault has no ontology file yet (a freshly-
    cloned repo where the seed push hasn't landed, or a hand-curated
    instance that deleted the file).
    """
    ontology_file = Path(vault_path) / ONTOLOGY_PATH_IN_REPO
    if not ontology_file.exists():
        return None
    return Ontology.load(ontology_file)


def get_ontology(vault_path: Optional[Path] = None) -> Ontology:
    """Return the live ontology from the vault, else the shipped seed.

    Callers pass an explicit vault path so this stays a pure function
    with no env or framework reach. The CLI commands read the path
    from `config["data_dir"]`, the install hook from `ctx.stack.data`,
    and the archivist from `MEMORY_VAULT_DIR` in its container.

    Calling with `None` is the pre-install / test-friendly path:
    seed-only, no filesystem touch beyond the shipped file.
    """
    if vault_path is not None:
        live = load_ontology_from_vault(vault_path)
        if live is not None:
            return live
    return load_seed_ontology()


# ─── Install (HTTP API) ──────────────────────────────────────────────────
#
# Day-zero seeding still goes through Forgejo's HTTP contents API: the
# vault doesn't exist yet, so we have nowhere local to commit against.
# Subsequent writes (facts CLI, wiki-rebuild) work through the vault
# and `git push`.

def ensure_memory_repo(client: ForgejoClient) -> dict:
    """Create the `family` org and `memory` repo if they don't exist."""
    org_existed = client.get_org(REPO_OWNER) is not None
    if not org_existed:
        client.create_org(REPO_OWNER, description=ORG_DESCRIPTION)

    repo_existed = client.get_repo(REPO_OWNER, REPO_NAME) is not None
    if not repo_existed:
        client.create_repo(
            REPO_OWNER, REPO_NAME,
            description=REPO_DESCRIPTION,
            private=True, owner_is_org=True,
        )

    return {
        "created_org": not org_existed,
        "created_repo": not repo_existed,
    }


def install_seeds(
    client: ForgejoClient,
    seed_dir: Optional[Path] = None,
    *,
    commit_message: str = "seed: initial memory",
    author_name: Optional[str] = None,
    author_email: Optional[str] = None,
) -> dict:
    """Push every file under `seed_dir` to the memory repo if missing.

    Walks the seed directory recursively. For each file, the relative
    path inside `seed_dir` becomes the path inside the repo. Files
    already present in the repo are left alone (no overwrite — the
    instance owns the live copy).
    """
    src = (seed_dir or SEEDS_DIR).resolve()
    created: list[str] = []
    skipped: list[str] = []

    for file_path in sorted(p for p in src.rglob("*") if p.is_file()):
        repo_path = file_path.relative_to(src).as_posix()
        existing = client.get_file(REPO_OWNER, REPO_NAME, repo_path)
        if existing is not None:
            skipped.append(repo_path)
            continue
        client.put_file(
            REPO_OWNER, REPO_NAME, repo_path,
            content=file_path.read_text(encoding="utf-8"),
            message=commit_message,
            author_name=author_name,
            author_email=author_email,
        )
        created.append(repo_path)

    return {"created": created, "skipped": skipped}


def install_memory_to_forgejo(
    *,
    code_url: str,
    admin_user: str,
    admin_password: str,
    bot_password: str,
    bot_token: Optional[str] = None,
    seed_dir: Optional[Path] = None,
    vault_path: Optional[Path] = None,
) -> dict:
    """Run the full install pipeline against Forgejo.

    Idempotent across every step. When `vault_path` is supplied, the
    pipeline also clones the freshly-seeded repo locally so readers
    can use the working copy.

    Returns a dict describing what changed. On `forgejo unreachable`,
    returns `{"skipped_reason": "..."}` and makes no further calls.
    """
    admin = ForgejoClient(
        url=code_url,
        admin_user=admin_user, admin_password=admin_password,
    )
    if not admin.ping():
        return {"skipped_reason": "forgejo unreachable"}

    admin.create_user(BOT_USERNAME, BOT_EMAIL, bot_password)

    if not bot_token:
        bot_token = admin.issue_token(
            BOT_USERNAME, bot_password, TOKEN_NAME, TOKEN_SCOPES,
        )

    repo_state = ensure_memory_repo(admin)

    # Bot joins the org's Owners team so it can write to any repo
    # under `family/` (memory today, more later). Idempotent.
    owners_team_id = admin.get_owners_team_id(REPO_OWNER)
    admin.add_team_member(owners_team_id, BOT_USERNAME)

    token_client = ForgejoClient(url=code_url, token=bot_token)
    seeds = install_seeds(
        token_client, seed_dir,
        commit_message=SEED_COMMIT_MESSAGE,
        author_name=BOT_USERNAME, author_email=BOT_EMAIL,
    )

    cloned_vault = False
    if vault_path is not None:
        remote = authenticated_remote(
            vault_remote_url(code_url),
            BOT_USERNAME, bot_token,
        )
        cloned_vault = ensure_vault_cloned(vault_path, remote)

    return {
        "bot_token": bot_token,
        "created_org": repo_state["created_org"],
        "created_repo": repo_state["created_repo"],
        "seeds": seeds,
        "cloned_vault": cloned_vault,
    }
