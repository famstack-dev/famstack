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

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

# Vault-layout conventions live in the framework so both stacklets share
# one source. Re-exported here for memory's own callers (and back-compat
# with `from lib import correspondents_dir`).
from stack.vault import DEFAULT_SHARED_BUCKET, correspondents_dir  # noqa: F401

# Frontmatter parsing uses the shared stdlib-only module from stack.frontmatter
# so both host CLI and containers can parse identically.
from stack.frontmatter import parse as _parse_frontmatter_new

# `python-frontmatter` is intentionally not imported at module load.
# The CLI runs on a stdlib-only `python3` (see `./stack`), so the
# install hook would crash at import time if we pulled in a third-
# party package up here. The one function that needs it imports it
# lazily — the archivist and the `stack memory correspondents` CLI
# command both run with the bot's runtime deps available.

from stack.forgejo import ForgejoClient, ForgejoError
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

# The projection repo. `family/brain` is the derived mirror of memory's
# source plus every generated wiki page; Quartz renders it. It is fully
# disposable — the curator rebuilds it from memory at any time — so it
# carries only a minimal seed (.gitignore + a README); the rest of its
# content arrives through the mirror. Memory stays source-only and is
# never written by generation.
BRAIN_REPO_NAME = "brain"
BRAIN_REPO_DESCRIPTION = (
    "The family wiki, projected from memory. Generated and disposable — "
    "Quartz renders this; edit the source in the memory repo instead."
)

# Brain's seed. Kept inline rather than in a seeds dir because it is two
# files and the projection fills in everything else. The .gitignore
# matches memory's so an Obsidian clone of either repo behaves the same.
BRAIN_SEED_GITIGNORE = (
    "# Projection repo — rendered by Quartz, rebuilt from memory.\n"
    "# Obsidian per-user state must not hit git (see memory's .gitignore).\n"
    ".obsidian/\n"
    ".trash/\n"
    ".DS_Store\n"
)
BRAIN_SEED_README = (
    "# Family brain (projection)\n\n"
    "This repo is **generated**. It mirrors the source vault in "
    "`family/memory` and adds the wiki pages the curator composes. "
    "Quartz renders it.\n\n"
    "Do not hand-edit here — edits are overwritten on the next rebuild. "
    "Edit the source in `family/memory`; changes flow back through the "
    "mirror.\n"
)
BRAIN_SEED_COMMIT_MESSAGE = "seed: initial brain projection scaffold"
BRAIN_MIGRATION_TOKEN_NAME = "memory-brain-migration"
GENERATED_PAGE_MARKER = "<!-- begin: generated -->"
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
    """Resolve the vault (memory source) path under a stack's data dir."""
    return Path(data_dir) / "memory" / "vault"


def brain_path_for(data_dir: Path) -> Path:
    """Resolve the brain projection working-copy path under a data dir.

    The curator pushes generated pages here and Quartz renders it; it
    sits beside the memory vault clone under the same data dir.
    """
    return Path(data_dir) / "memory" / "brain"


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


def brain_remote_url(code_url: str) -> str:
    """The git remote URL for the brain projection repo."""
    return f"{code_url.rstrip('/')}/{REPO_OWNER}/{BRAIN_REPO_NAME}.git"


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


def vault_remote_head(vault_path: Path, *, timeout: int = 5) -> Optional[str]:
    """Return upstream HEAD SHA via `git ls-remote`, or None if unavailable.

    This is the cheap probe used by `refresh_vault_if_stale`: one
    network round-trip that returns a single ref, an order of
    magnitude faster than fetching packs. Auth is baked into the
    `origin` URL by `authenticated_remote` at clone time, so we don't
    pass credentials here.
    """
    vault_path = Path(vault_path)
    if not (vault_path / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(vault_path), "ls-remote", "origin", "HEAD"],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if result.returncode != 0:
        return None
    first_line = result.stdout.strip().split("\n", 1)[0]
    return first_line.split("\t", 1)[0] if first_line else None


def vault_local_head(vault_path: Path) -> Optional[str]:
    """Return local HEAD SHA via `git rev-parse`, or None if not a clone."""
    vault_path = Path(vault_path)
    if not (vault_path / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(vault_path), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def refresh_vault_if_stale(
    vault_path: Path, *, timeout: int = 5,
) -> str:
    """Pull only when the remote's HEAD differs from the local HEAD.

    Reads pay one cheap `git ls-remote` round-trip (~50-150ms for the
    memory repo's size) and only the full `git pull --ff-only` when
    there's actually a new commit to merge. The common interactive
    case — searching repeatedly in the same minute — almost always
    hits the "up_to_date" fast path.

    Return values, terse so callers can log them verbatim:

        "up_to_date"  — heads match; no work done.
        "pulled"      — heads differed; pull succeeded.
        "unreachable" — couldn't read the remote (offline, wrong auth,
                        or vault is not a clone). Reads proceed
                        against the local cache.
        "pull_failed" — heads differed but the pull didn't apply
                        (non-fast-forward, local edits). Reads proceed
                        against the stale local cache.

    Best-effort: this function never raises. The caller decides
    whether to surface the status to the user — typically: warn on
    "pulled" once, silently ignore "up_to_date" and "unreachable",
    print a warning on "pull_failed".
    """
    remote = vault_remote_head(vault_path, timeout=timeout)
    if remote is None:
        return "unreachable"
    local = vault_local_head(vault_path)
    if local == remote:
        return "up_to_date"
    if pull_vault(vault_path, timeout=timeout):
        return "pulled"
    return "pull_failed"


# ─── Vault writers ───────────────────────────────────────────────────────

def _code_url_from_config(config: dict | None) -> str:
    """Host-reachable Forgejo URL, from the secret the install hook cached or
    the code stacklet's published port. Mirrors `cli/pull.py`'s resolution."""
    secrets = config.get("secrets", {}) if config else {}
    if cached := secrets.get("__code_url", ""):
        return cached
    stck = config.get("stack") if config else None
    port = stck.get("code", {}).get("port", 42040) if isinstance(stck, dict) else 42040
    return f"http://localhost:{port}"


def _actor_identity(actor: str, config: dict | None) -> tuple[str, str]:
    """Map a striker (a person slug like 'homer') to a git author name+email.

    The name carries the attribution in the commit history; the email is
    synthetic but stable, keyed to the server name so a person's commits group.
    """
    # Accept a bare handle or a full mxid (@homer:simpson) -- reduce to the
    # localpart so the author reads "homer", not "@homer:simpson".
    slug = (actor or "").strip().split(":")[0].lstrip("@") or "unknown"
    stck = config.get("stack") if config else None
    server = stck.get("server_name") if isinstance(stck, dict) else None
    return slug, f"{slug}@{server or 'famstack'}"


def update_memory(config: dict, repo_path: str,
                  transform: Callable[[str], str], *,
                  actor: str, message: str) -> dict:
    """Commit a transform of one vault file to Forgejo, attributed to `actor`.

    The single write seam for deterministic memory mutations. It runs
    host-native -- the memory-bot token already has write access, so there is
    no docker exec and no LLM in the path -- reading the canonical file from
    Forgejo (not the possibly-stale local clone), applying `transform`,
    committing with `actor` as the git author, then fast-forwarding the local
    clone so a following read reflects the change.

    Returns the framework envelope: `{"ok": True, "committed": bool}` on
    success (committed=False when the transform was a no-op, so nothing was
    written), or `{"error": ...}` when credentials are missing, the transform
    rejects the input (e.g. no matching todo), or Forgejo is unreachable.
    """
    secrets = config.get("secrets", {}) if config else {}
    token = secrets.get("memory__MEMORY_BOT_TOKEN", "")
    code_url = _code_url_from_config(config)
    if not (token and code_url):
        return {"error": "Forgejo credentials missing — run `stack up memory` first"}

    client = ForgejoClient(url=code_url, token=token)
    name, email = _actor_identity(actor, config)
    try:
        result = client.edit_file(
            REPO_OWNER, REPO_NAME, repo_path, transform,
            message=message, author_name=name, author_email=email,
        )
    except ValueError as e:            # transform rejected the input
        return {"error": str(e)}
    except ForgejoError as e:
        return {"error": f"Forgejo write failed: {e}"}

    if result is None:
        return {"ok": True, "committed": False}

    data_dir = config.get("data_dir") if config else None
    if data_dir:
        pull_vault(vault_path_for(Path(data_dir)))  # write-through so reads agree
    return {"ok": True, "committed": True, "path": repo_path}


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


# ─── Correspondents (shared-bucket entity layer) ─────────────────────────
#
# Correspondents are organizations the family corresponds with (banks,
# insurers, schools, councils, online services). They belong to the
# institutional layer — senders of mail, classifier hints, Paperless
# canonicalisers — and so live under the shared bucket alongside the
# documents they index.
#
# Layout: `<vault>/<shared_bucket>/correspondents/<name>.md`. The
# shared bucket slug is configured in `stack.toml [core] shared_bucket`
# and defaults to "family". Personal correspondents (a future feature
# for entity-specific senders, e.g. Bart's school) would live under
# the matching entity bucket — same shape, different parent.
#
# Living outside `wiki/` keeps hand-curated correspondents safe from
# the wiki-engine's regenerate pass.
#
# Example shape (family/correspondents/duff-insurance.md):
#
#     ---
#     kind: correspondent
#     canonical: Duff Insurance
#     aliases:
#       - "Duff Insurance Ortsverband Springfield"
#     topics: [insurance, vehicle]
#     address: "Hansastraße 19, 80686 München"
#     website: "https://www.duff-insurance.example"
#     ---
#
#     # Duff Insurance
#     [free-form notes]
#
# `aliases` rolls up everything the classifier has seen as the
# `correspondent_aliases` field on documents from this sender. The
# classifier prompt embeds the (canonical, aliases) pairs so the LLM
# can canonicalize new variants before they hit Paperless.

@dataclass
class Correspondent:
    """A single correspondent wiki entry."""

    canonical: str
    aliases: List[str] = field(default_factory=list)
    topics: List[str] = field(default_factory=list)
    address: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None
    # Path on disk, useful for `stack memory correspondents show`.
    source_path: Optional[Path] = None

    def all_known_names(self) -> List[str]:
        """Canonical + every alias, no duplicates, canonical first."""
        seen = {self.canonical}
        out = [self.canonical]
        for a in self.aliases:
            if a and a not in seen:
                seen.add(a)
                out.append(a)
        return out


def load_correspondents_from_vault(
    vault_path: Path,
    shared_bucket: str = DEFAULT_SHARED_BUCKET,
) -> List[Correspondent]:
    """Walk `<vault>/<shared_bucket>/correspondents/*.md` and return the parsed entries.

    `shared_bucket` defaults to "family" — the conventional name. Pass
    the configured slug when the deployment overrides it via stack.toml.

    Pages without a `kind: correspondent` frontmatter or without a
    canonical name (frontmatter `canonical:`, falling back to the file
    stem) are skipped — keeps the loader robust against partial edits.
    """
    folder = Path(vault_path) / correspondents_dir(shared_bucket)
    if not folder.exists():
        return []

    # Lazy import: keeps the CLI install path stdlib-only. Callers of
    # this function (archivist, `stack memory correspondents`) bring
    # `python-frontmatter` on their PYTHONPATH.
    import frontmatter

    result: List[Correspondent] = []
    for md_path in sorted(folder.glob("*.md")):
        try:
            with open(md_path, "r", encoding="utf-8") as f:
                post = frontmatter.load(f)
        except (OSError, ValueError):
            continue
        meta = post.metadata or {}
        if not meta:
            # No frontmatter at all — likely a README or stray note, skip.
            continue
        if meta.get("kind") and meta.get("kind") != "correspondent":
            continue
        canonical = meta.get("canonical") or md_path.stem
        if not canonical:
            continue
        result.append(Correspondent(
            canonical=str(canonical),
            aliases=[str(a) for a in (meta.get("aliases") or [])],
            topics=[str(t) for t in (meta.get("topics") or [])],
            address=meta.get("address"),
            phone=meta.get("phone"),
            email=meta.get("email"),
            website=meta.get("website"),
            source_path=md_path,
        ))
    return result


def correspondents_prompt_section(correspondents: List[Correspondent]) -> str:
    """Render the correspondents block embedded in the classifier prompt.

    Output shape:

        Existing correspondents (canonical; aliases in parens):
          - Duff Insurance (Duff Insurance Ortsverband Springfield, Duff Insurance Versicherung AG)
          - Springfield Mutual
          - Anthropic

    Returns an empty string when no correspondents are known yet —
    the prompt builder falls back to the flat Paperless list in that
    case.
    """
    if not correspondents:
        return ""
    lines = ["Existing correspondents (canonical; aliases in parens):"]
    for c in correspondents:
        if c.aliases:
            lines.append(f"  - {c.canonical} ({', '.join(c.aliases)})")
        else:
            lines.append(f"  - {c.canonical}")
    return "\n".join(lines)


# ─── Persons (entity-bucket entity layer) ────────────────────────────────
#
# Persons are the household members the wiki carries a page for. They
# parallel correspondents in shape: a canonical name plus the synonyms
# the family uses in real documents (formal, nicknames, maiden names).
# Unlike correspondents they live one-per-entity under each member's
# bucket -- the same about.md the home page links to -- so the wiki is
# the single curation surface and there is no parallel registry file
# to keep in sync.
#
# Layout: `<vault>/<slug>/about.md`. The page body is free-form prose
# the family edits; the frontmatter is the structured signal the
# classifier reads.
#
# Example shape (vault/maggie/about.md):
#
#     ---
#     title: Margaret
#     slug: maggie
#     canonical: Margaret
#     synonyms:
#       - Maggie
#       - Margaret Bouvier
#     ---
#
#     # Margaret
#     [free-form notes, links to documents]
#
# `synonyms` is the curated set the classifier needs to recognise so
# "Marge Bouvier" on a marriage certificate resolves to the same
# person as "Marge" on a utility bill.

# Skip set for `load_persons_from_vault`: top-level directories that
# carry an `about.md` but are not a household member. Kept in sync
# with the `stack memory wiki` command's own skip set; both walk the
# same vault structure.
_NON_MEMBER_DIRS = {".git", ".obsidian", "wiki", "private", "templates", "_shared"}


@dataclass
class Person:
    """A single household-member wiki entry."""

    canonical: str
    slug: str
    synonyms: List[str] = field(default_factory=list)
    # Path on disk, useful for `stack memory persons show` and tests.
    source_path: Optional[Path] = None

    def all_known_names(self) -> List[str]:
        """Canonical + every synonym, no duplicates, canonical first."""
        seen = {self.canonical}
        out = [self.canonical]
        for s in self.synonyms:
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        return out


def load_persons_from_vault(
    vault_path: Path,
    shared_bucket: str = DEFAULT_SHARED_BUCKET,
) -> List[Person]:
    """Walk `<vault>/<slug>/about.md` and return the parsed entries.

    Each entity bucket directly under the vault root is treated as a
    potential household member. Buckets that are reserved for shared
    content (the `shared_bucket`, `wiki/`, `.git/`, ...) are skipped
    by name. A bucket without an `about.md` is also skipped -- a
    member can exist on the file system (with captured notes) before
    the wiki overview pass has generated their page.

    Pages tagged with a `kind:` other than `person` are skipped --
    leaves room for non-member entries to coexist under the same
    directory shape without leaking into the classifier prompt.
    """
    vault_path = Path(vault_path)
    if not vault_path.exists():
        return []

    # Lazy import: keeps the CLI install path stdlib-only (see the
    # module-level note above `load_correspondents_from_vault`).
    import frontmatter

    skip = _NON_MEMBER_DIRS | {shared_bucket}
    result: List[Person] = []
    for about in sorted(vault_path.glob("*/about.md")):
        slug = about.parent.name
        if slug in skip or slug.startswith("."):
            continue
        try:
            with open(about, "r", encoding="utf-8") as f:
                post = frontmatter.load(f)
        except (OSError, ValueError):
            continue
        meta = post.metadata or {}
        if meta.get("kind") and meta.get("kind") != "person":
            continue
        canonical = meta.get("canonical") or meta.get("title") or slug
        if not canonical:
            continue
        result.append(Person(
            canonical=str(canonical),
            slug=str(meta.get("slug") or slug),
            synonyms=[str(s) for s in (meta.get("synonyms") or [])],
            source_path=about,
        ))
    return result


def persons_prompt_section(persons: List[Person]) -> str:
    """Render the persons block embedded in the classifier prompt.

    Output shape, mirroring `correspondents_prompt_section`:

        Family members (canonical first name; synonyms in parens):
          - Marge (Marjorie, Margaret, Marge Bouvier, Marge Simpson)
          - Homer
          - Bart (Bartholomew, Bart Simpson)

    Returns an empty string when no curated person pages exist yet --
    the prompt builder falls back to the flat list of first names
    pulled from Paperless Person tags in that case.
    """
    if not persons:
        return ""
    lines = ["Family members (canonical first name; synonyms in parens):"]
    for p in persons:
        if p.synonyms:
            lines.append(f"  - {p.canonical} ({', '.join(p.synonyms)})")
        else:
            lines.append(f"  - {p.canonical}")
    return "\n".join(lines)


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


def ensure_brain_repo(client: ForgejoClient) -> dict:
    """Create the `family/brain` projection repo if it doesn't exist.

    The `family` org is created by `ensure_memory_repo` (memory installs
    first), so this only ever has to make the repo. `create_repo` is
    idempotent on a 409/already-exists, so re-running the hook is safe.
    """
    repo_existed = client.get_repo(REPO_OWNER, BRAIN_REPO_NAME) is not None
    if not repo_existed:
        client.create_repo(
            REPO_OWNER, BRAIN_REPO_NAME,
            description=BRAIN_REPO_DESCRIPTION,
            private=True, owner_is_org=True,
        )
    return {"created_repo": not repo_existed}


def seed_brain(
    client: ForgejoClient,
    *,
    author_name: Optional[str] = None,
    author_email: Optional[str] = None,
) -> dict:
    """Push brain's minimal seed (.gitignore + README) if missing.

    Only the two scaffold files — everything else in brain arrives
    through the curator's mirror. Files already present are left alone,
    so this is idempotent and never clobbers a projection in progress.
    """
    created: list[str] = []
    skipped: list[str] = []
    seed = {
        ".gitignore": BRAIN_SEED_GITIGNORE,
        "README.md": BRAIN_SEED_README,
    }
    for repo_path, content in seed.items():
        existing = client.get_file(REPO_OWNER, BRAIN_REPO_NAME, repo_path)
        if existing is not None:
            skipped.append(repo_path)
            continue
        client.put_file(
            REPO_OWNER, BRAIN_REPO_NAME, repo_path,
            content=content,
            message=BRAIN_SEED_COMMIT_MESSAGE,
            author_name=author_name,
            author_email=author_email,
        )
        created.append(repo_path)
    return {"created": created, "skipped": skipped}


def purge_generated_memory_pages(
    client: ForgejoClient,
    *,
    message: str = "chore(memory): purge generated wiki pages from source",
) -> dict:
    """Delete legacy generated projection pages from `family/memory`.

    B1 moves generation to `family/brain`, but upgraded instances can
    already have generated wiki pages in the source repo from pre-B1
    runs. The migration is marker-based so it does not depend on stale
    path conventions: any markdown file with the generated splice marker
    is a projection artifact and is removed from memory.
    """
    deleted: list[str] = []
    tree = client.list_tree(REPO_OWNER, REPO_NAME)
    for entry in tree:
        path = entry.get("path", "")
        if entry.get("type") != "blob" or not path.endswith(".md"):
            continue
        if Path(path).name == "README.md":
            continue
        existing = client.get_file(REPO_OWNER, REPO_NAME, path)
        if not existing:
            continue
        content = existing.get("content", "")
        if GENERATED_PAGE_MARKER not in content:
            continue
        client.delete_file(
            REPO_OWNER, REPO_NAME, path,
            sha=existing["sha"], message=message,
        )
        deleted.append(path)
    return {"deleted": deleted}


def purge_local_generated_memory_pages(vault_path: Path) -> dict:
    """Remove generated page files from the local memory clone.

    This is the local companion to `purge_generated_memory_pages`. It is
    needed when a pre-fix wiki run wrote generated pages into the source
    working copy without committing them; those local modifications block
    a normal fast-forward after the remote purge.
    """
    deleted: list[str] = []
    vault_path = Path(vault_path)
    if not vault_path.exists():
        return {"deleted": deleted}
    for md in sorted(vault_path.rglob("*.md")):
        if ".git" in md.parts or md.name == "README.md":
            continue
        try:
            content = md.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if GENERATED_PAGE_MARKER not in content:
            continue
        try:
            rel = str(md.relative_to(vault_path))
            md.unlink()
        except OSError:
            continue
        deleted.append(rel)
    return {"deleted": deleted}


def ensure_brain_projection_admin(
    *,
    code_url: str,
    admin_user: str,
    admin_password: str,
    brain_path: Optional[Path] = None,
) -> dict:
    """Ensure upgraded instances have the brain repo and local checkout.

    `on_install_success` only runs once. Instances installed before the
    brain projection existed already have `memory.setup-done`, so normal
    `stack up memory` skips the install hook forever. This helper is the
    idempotent migration path used from `on_start_ready`: create
    `family/brain` if missing, seed its scaffold if missing, and clone it
    locally when the working copy is absent.
    """
    admin = ForgejoClient(
        url=code_url,
        admin_user=admin_user, admin_password=admin_password,
    )
    if not admin.ping():
        return {"skipped_reason": "forgejo unreachable"}

    brain_state = ensure_brain_repo(admin)
    admin_token = admin.issue_token(
        admin_user, admin_password, BRAIN_MIGRATION_TOKEN_NAME, TOKEN_SCOPES,
    )
    admin_token_client = ForgejoClient(url=code_url, token=admin_token)
    brain_seeds = seed_brain(admin_token_client)
    memory_purge = purge_generated_memory_pages(admin_token_client)

    cloned_brain = False
    if brain_path is not None:
        had_brain = (brain_path / ".git").exists()
        brain_remote = authenticated_remote(
            brain_remote_url(code_url),
            admin_user, admin_token,
        )
        cloned_brain = ensure_vault_cloned(brain_path, brain_remote) and not had_brain

    return {
        "created_brain_repo": brain_state["created_repo"],
        "brain_seeds": brain_seeds,
        "memory_purge": memory_purge,
        "cloned_brain": cloned_brain,
    }


def install_seeds(
    client: ForgejoClient,
    seed_dir: Optional[Path] = None,
    *,
    commit_message: str = "seed: initial memory",
    author_name: Optional[str] = None,
    author_email: Optional[str] = None,
    shared_bucket: str = DEFAULT_SHARED_BUCKET,
) -> dict:
    """Push every file under `seed_dir` to the memory repo if missing.

    Walks the seed directory recursively. For each file, the relative
    path inside `seed_dir` becomes the path inside the repo. A leading
    `_shared/` segment is rewritten to the configured shared bucket
    slug — so `seeds/_shared/correspondents/README.md` lands at
    `family/correspondents/README.md` (or whatever the bucket is named).
    Files already present in the repo are left alone — the instance
    owns the live copy.
    """
    src = (seed_dir or SEEDS_DIR).resolve()
    created: list[str] = []
    skipped: list[str] = []

    for file_path in sorted(p for p in src.rglob("*") if p.is_file()):
        rel = file_path.relative_to(src).as_posix()
        repo_path = _resolve_seed_repo_path(rel, shared_bucket)
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


# Seed paths beginning with `_shared/` get retargeted to the
# configured bucket slug. Everything else lands at the vault root
# verbatim — keeps `ontology.toml`, `facts.toml`, `README.md` at the
# top regardless of bucket configuration.
_SHARED_SEED_PREFIX = "_shared/"


def _resolve_seed_repo_path(rel_path: str, shared_bucket: str) -> str:
    if rel_path.startswith(_SHARED_SEED_PREFIX):
        return f"{shared_bucket}/{rel_path[len(_SHARED_SEED_PREFIX):]}"
    return rel_path


def install_memory_to_forgejo(
    *,
    code_url: str,
    admin_user: str,
    admin_password: str,
    bot_password: str,
    bot_token: Optional[str] = None,
    seed_dir: Optional[Path] = None,
    vault_path: Optional[Path] = None,
    shared_bucket: str = DEFAULT_SHARED_BUCKET,
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
        shared_bucket=shared_bucket,
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


# ─── Memory vault search ────────────────────────────────────────────────
#
# Full-text query over the curated vault. Pure-Python regex walk over
# `*.md` files, post-filtered by YAML frontmatter (`persons`, `tags`).
# Two callers today: `stack memory search` (CLI argparse + formatter
# wrapper) and the archivist bot (formats results into Matrix). The
# CLI is the agent-facing surface; the lib function is the in-process
# call for code that already imports `memory.lib`.
#
# `refresh_vault_if_stale` is the caller's concern -- `search_memory`
# is the search itself, not the sync policy. The CLI calls refresh
# before dispatch; the archivist does the same before each query.


def _parse_frontmatter(text: str) -> dict:
    """Parse vault entry frontmatter using the shared stdlib-only module.

    Delegates to stack.frontmatter.parse, which handles the strict
    §2 subset per the vault format spec. On parse errors, this still
    silently returns the dict-so-far (graceful degradation for older
    files or edge cases).
    """
    try:
        return _parse_frontmatter_new(text)
    except Exception:
        # Graceful degradation: if parse fails, return empty dict
        # so the file still surfaces in search, just without metadata
        return {}


def _fm_list(fm: dict, key: str) -> List[str]:
    """Read a frontmatter field as a list of strings, regardless of shape."""
    v = fm.get(key)
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str):
        return [v]
    return []


def _norm_tag(value: str) -> str:
    """Collapse whitespace and lower-case for tag comparison.

    Writers and queriers disagree on spacing: `'Person: Homer'`,
    `Person:Homer`, and `PERSON :HOMER` all normalize to the same
    token so the filter doesn't care which spelling was used.
    """
    return re.sub(r"\s+", "", value).lower()


def body_only(text: str) -> str:
    """Return the text after the closing `---` of the frontmatter block.

    Public because the natural-language query layer also strips
    frontmatter when feeding full file contents into the synthesis
    prompt -- the YAML header is metadata the model already sees as
    structured `kind`/`date`/`persons` fields, so duplicating it in
    the body context only crowds the context window.

    Why this exists: the search regex must not match against YAML
    field names. A query for `date` would otherwise hit every memory
    file via its `date:` frontmatter line; same for `tags`,
    `correspondent`, `persons`, etc. Structural-vs-content matters
    here -- the family asks "when did Bart…", not "tell me every
    file with a `date` field". Returns the original text unchanged
    when no frontmatter is present, so files that pre-date the
    classifier still surface.
    """
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    if end < 0:
        return text
    return text[end + len("\n---\n"):]


def extract_summary_callout(text: str) -> str:
    """Pull the `> [!summary]` callout out of a memory-vault file.

    The archivist writes every classified document with an Obsidian-style
    callout that contains the LLM's prose summary, the bulleted facts,
    and any action items. That block is the right size for feeding
    into a downstream LLM when answering a question -- big enough to
    carry the actual content, small enough that fitting N hits into
    one context is cheap.

    Returns the callout body with the leading `> ` blockquote prefix
    stripped from each line, or "" when no callout is present (files
    older than the classifier, or non-archivist-written notes).
    """
    body = body_only(text)
    lines = body.splitlines()
    captured: List[str] = []
    in_callout = False
    for line in lines:
        if not in_callout:
            if line.strip().startswith("> [!summary]"):
                in_callout = True
            continue
        # Inside the callout: every line starts with `>`. A line that
        # doesn't ends the block. Blank `>` lines stay as paragraph
        # breaks inside the summary -- they're meaningful (separate
        # the prose from the Facts heading from the Action items).
        if not line.startswith(">"):
            break
        captured.append(line[1:].lstrip(" "))
    return "\n".join(captured).strip()


def _excerpt(text: str, query: str, max_len: int = 200) -> str:
    """First non-empty body line that mentions `query` (case-insensitive).

    Body starts after the *closing* `---` of the frontmatter block --
    otherwise hits on `title:` or `persons:` lines would surface as
    excerpts, which is noisy and misleading.
    """
    needle = query.lower()
    body = body_only(text)
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if needle in stripped.lower():
            if len(stripped) > max_len:
                stripped = stripped[:max_len] + "…"
            return stripped
    return ""


def search_memory(
    query: str,
    vault: Path,
    persons: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    scopes: Optional[List[str]] = None,
    limit: int = 20,
) -> List[dict]:
    """Walk the vault, return result dicts sorted newest-first.

    `query` is a Python regex matched case-insensitively against the
    *body* of each file -- frontmatter is stripped before matching so
    generic field names (`date:`, `tags:`, `persons:`, ...) don't
    pull in every file in the vault. `persons` and `tags` still
    narrow against the structured frontmatter: a doc passes when,
    for every supplied axis, at least one of its frontmatter values
    matches at least one requested value. Persons compare
    case-insensitively; tags through `_norm_tag` (whitespace-and-case
    normalized).

    `scopes` is a list of allowed path prefixes (entity-rooted, e.g.
    `["family/", "marge/"]`). A doc passes when its vault-relative
    path starts with any prefix. `None` means no scope filter (all
    docs allowed); an empty list means no docs allowed -- the
    archivist passes a closed scope set for unknown senders so they
    can't read personal notes by accident.

    Returns dicts with keys `path`, `rel`, `title`, `date`,
    `persons`, `tags`, `excerpt`. Sorted by frontmatter `date`
    descending; files without a date sort to the end.

    On a missing vault directory or invalid regex, returns `[]`
    rather than raising -- callers decide how to surface the
    failure.
    """
    if not vault.exists():
        return []

    try:
        pattern = re.compile(query, re.IGNORECASE)
    except re.error:
        return []

    persons = persons or []
    tags = tags or []
    want_persons = {p.lower() for p in persons}
    want_tags = {_norm_tag(t) for t in tags}

    # Normalize scopes: ensure each prefix ends with "/" so "marge"
    # doesn't accidentally match "margery/...". `None` keeps the
    # historic open behavior; `[]` denies everything.
    if scopes is None:
        scope_prefixes: Optional[List[str]] = None
    else:
        scope_prefixes = [s if s.endswith("/") else f"{s}/" for s in scopes]

    results: List[dict] = []
    for md_path in vault.rglob("*.md"):
        if not md_path.is_file():
            continue
        try:
            rel = str(md_path.relative_to(vault))
        except ValueError:
            rel = str(md_path)
        if scope_prefixes is not None and not any(
            rel.startswith(p) for p in scope_prefixes
        ):
            continue
        try:
            text = md_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        # Match against body only -- frontmatter field names (`date:`,
        # `tags:`, `persons:`, ...) would otherwise trivially match
        # generic keywords and drown real hits.
        if not pattern.search(body_only(text)):
            continue

        fm = _parse_frontmatter(text)
        doc_persons = _fm_list(fm, "persons")
        if persons and not any(p.lower() in want_persons for p in doc_persons):
            continue
        doc_tags = _fm_list(fm, "tags")
        if tags:
            doc_norm = {_norm_tag(t) for t in doc_tags}
            if not (doc_norm & want_tags):
                continue

        results.append({
            "path": md_path,
            "rel": rel,
            "title": fm.get("title") or md_path.stem,
            "date": fm.get("date") or "",
            "persons": doc_persons,
            "tags": doc_tags,
            "excerpt": _excerpt(text, query),
            # The `> [!summary]` callout, stripped of blockquote
            # prefixes. Drives the synthesis step: feeding summaries
            # to the LLM is cheaper than feeding bodies and usually
            # enough to answer the question.
            "summary": extract_summary_callout(text),
            # Paperless source id for the document this memory file
            # mirrors. Lets a downstream deduper recognise that a
            # `Memory` hit and a `Paperless` hit are the same doc and
            # collapse them in the synthesis evidence list. Empty
            # when the file isn't a Paperless mirror (e.g. capture
            # notes, hand-written wiki entries).
            "paperless_id": fm.get("paperless_id") or "",
        })

    results.sort(
        key=lambda r: (str(r.get("date") or ""), r["rel"]),
        reverse=True,
    )
    return results[:limit]


def install_memory_to_forgejo_admin(
    *,
    code_url: str,
    admin_user: str,
    admin_password: str,
    seed_dir: Optional[Path] = None,
    vault_path: Optional[Path] = None,
    brain_path: Optional[Path] = None,
    shared_bucket: str = DEFAULT_SHARED_BUCKET,
) -> dict:
    """Run the install pipeline using admin credentials directly.

    No bot user is created. All repo operations (org/repo creation,
    seed push, vault clone) go through the admin account. The
    archivist-bot (created by the docs stacklet) is the sole bot
    account for day-to-day writes.

    Two repos are stood up: `family/memory` (the source vault) and
    `family/brain` (the projection Quartz renders). Memory carries the
    full seed; brain carries only a .gitignore + README scaffold and
    fills in from the mirror.

    Idempotent across every step. When `vault_path` / `brain_path` are
    supplied, the pipeline also clones the freshly-seeded repos locally
    so the curator and readers can use the working copies.

    Returns a dict describing what changed. On `forgejo unreachable`,
    returns `{\"skipped_reason\": \"...\"}` and makes no further calls.
    """
    admin = ForgejoClient(
        url=code_url,
        admin_user=admin_user, admin_password=admin_password,
    )
    if not admin.ping():
        return {"skipped_reason": "forgejo unreachable"}

    repo_state = ensure_memory_repo(admin)
    brain_state = ensure_brain_repo(admin)

    # Use admin token for file writes (admin has write:repository scope).
    admin_token = admin.issue_token(
        admin_user, admin_password, "memory-install", TOKEN_SCOPES,
    )
    admin_token_client = ForgejoClient(url=code_url, token=admin_token)
    seeds = install_seeds(
        admin_token_client, seed_dir,
        commit_message=SEED_COMMIT_MESSAGE,
        shared_bucket=shared_bucket,
    )
    brain_seeds = seed_brain(admin_token_client)

    cloned_vault = False
    if vault_path is not None:
        remote = authenticated_remote(
            vault_remote_url(code_url),
            admin_user, admin_token,
        )
        cloned_vault = ensure_vault_cloned(vault_path, remote)

    cloned_brain = False
    if brain_path is not None:
        brain_remote = authenticated_remote(
            brain_remote_url(code_url),
            admin_user, admin_token,
        )
        cloned_brain = ensure_vault_cloned(brain_path, brain_remote)

    return {
        "created_org": repo_state["created_org"],
        "created_repo": repo_state["created_repo"],
        "created_brain_repo": brain_state["created_repo"],
        "seeds": seeds,
        "brain_seeds": brain_seeds,
        "cloned_vault": cloned_vault,
        # The write-scoped token host-side writers need (`update_memory`,
        # and the clone-recovery path in on_start_ready). The caller persists
        # it as a secret; nothing else here keeps it. Author attribution is set
        # per-commit, so a shared write token is fine -- it is transport auth,
        # not identity.
        "write_token": admin_token,
        "cloned_brain": cloned_brain,
    }
