"""`stack docs overview` — generate the family wiki's entry pages.

Walks the memory vault, pulls every document's `> [!summary]` callout,
and asks the LLM to compose the browsable pages a family lands on:

    stack docs overview              the household home page (index.md)
    stack docs overview --members    the home page + one page per member
    stack docs overview --member X   just member X's page

Pages are published to the memory repo on Forgejo, where the wiki
container picks them up within seconds. The wiki is Quartz, which
renders `index.md` as the landing page for the site (vault-root
`index.md`) and for each folder (`<member>/index.md`) -- so the
household overview becomes the wiki home and each member's overview
becomes the landing page for their folder.

The generated body lives inside a bracketed regenerate region
(`<!-- begin: generated --> ... <!-- end: generated -->`); anything a
human writes outside the brackets -- a welcome line, frontmatter, a
hand-edited note -- survives the next regen. Same contract as the
correspondent pages.

Output goes to stdout by default so we can eyeball before committing.
`--write` splices the generated region into the page on Forgejo.

Lives in `docs/bot/cli/` because the LLM (and the bot-runner's
aiohttp / env wiring) is here, even though the artifact it writes
belongs to the memory stacklet. The natural home will move when the
memory stacklet grows its own LLM access.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tomllib
from pathlib import Path

# Sibling stacklets — memory.lib gives us summary callout extraction
# and frontmatter parsing without re-implementing them here.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from memory.lib import (  # noqa: E402
    _parse_frontmatter,
    extract_summary_callout,
)

from stack.forgejo import ForgejoClient, ForgejoError  # noqa: E402

from pipeline import Classifier, PaperlessAPI  # noqa: E402
from nl_query import extract_citations  # noqa: E402
from cli._shared import err  # noqa: E402


HELP = "Generate the family wiki entry pages (experimental)"

# Repo + branch defaults match the memory stacklet's layout. Hard-coded
# rather than env-driven because the layout is stable across deploys
# (the memory stacklet owns the repo name) and we don't want a typo in
# bot config to silently retarget the write.
_REPO_NAME = "memory"
_BRANCH = "main"
_TOKEN_NAME = "stack-docs-overview-cli"
_TOKEN_SCOPES = ["write:repository", "read:repository"]

# Bracketed regenerate markers. Match the correspondent-page convention
# verbatim so the same human contract holds across every generated page:
# edits outside the brackets are preserved, inside is ours to rewrite.
_BEGIN = "<!-- begin: generated -->"
_END = "<!-- end: generated -->"

# Top-level vault entries that are never a family member: the shared
# bucket, git/Obsidian internals, the reserved wiki dir, and the
# private/templates dirs Quartz is told to ignore. Everything else at
# the vault root is an entity (member) bucket.
_NON_MEMBER_DIRS = {".git", ".obsidian", "wiki", "private", "templates", "_shared"}


async def run(paperless: PaperlessAPI, classifier: Classifier,
              argv: list[str]) -> int:
    write = "--write" in argv
    single = _arg_value(argv, "--member")
    members_flag = "--members" in argv

    vault_dir = os.environ.get("MEMORY_VAULT_DIR", "")
    if not vault_dir:
        err("MEMORY_VAULT_DIR not set — is the memory stacklet installed?")
        return 1
    vault = Path(vault_dir)
    if not vault.exists():
        err(f"vault path does not exist: {vault}")
        return 1

    # In the default install the org name and the in-repo bucket
    # directory are the same slug (both "family"). Custom installs may
    # diverge; if they do, the right fix is to surface MIRROR_ORG in
    # the env, not to invent a second source of truth here.
    shared_bucket = os.environ.get("SHARED_BUCKET", "family")
    lang = os.environ.get("LANGUAGE", "en")

    # One walk feeds every surface. The home page reads the whole index;
    # each member page reads its slice. No re-walking per member.
    index = _index_vault(vault)
    if not index:
        err("no documents with summary callouts found in vault")
        return 1

    # The roster the home page links into and the --members loop walks.
    # Computed once: bucket dirs on disk plus everyone named in document
    # frontmatter.
    roster = _member_slugs(vault, index, shared_bucket)

    if single:
        return await _generate_member(
            classifier, single, index, vault,
            shared_bucket=shared_bucket, lang=lang, write=write,
        )

    rc = await _generate_home(
        classifier, index, roster=roster,
        shared_bucket=shared_bucket, lang=lang, write=write,
    )
    if rc != 0 or not members_flag:
        return rc

    # `--members`: regenerate every member page after the home page. A
    # member with no content is skipped (not an error) so one empty
    # bucket doesn't sink the whole run.
    for slug in roster:
        await _generate_member(
            classifier, slug, index, vault,
            shared_bucket=shared_bucket, lang=lang, write=write,
        )
    return 0


# ── Generation ───────────────────────────────────────────────────────────

async def _generate_home(
    classifier: Classifier, index: list[dict], *,
    roster: list[str], shared_bucket: str, lang: str, write: bool,
) -> int:
    """Compose the household home page from every filed summary.

    The roster (every member that has a page) is woven into the Members
    section so each name links to that person's page. Quartz turns those
    links into backlinks and graph edges for free, so the home page and
    the member pages become navigable in both directions.
    """
    prompt = _build_home_prompt(index, roster=roster, lang=lang)
    page = (await classifier._request("overview", prompt, json_mode=False)).strip()

    # Append References using the citations the LLM actually used. Built
    # deterministically from the index rather than asked of the model --
    # the LLM cites reliably, but the citation→document mapping is ours
    # to render so links and dates can't be fabricated. Home page lives
    # at the vault root, so links are root-relative (page_dir="").
    page = _with_references(page, index, page_dir="")

    if not write:
        print(page)
        return 0
    return await _publish(
        page, target_path="index.md", shared_bucket=shared_bucket,
        commit_msg="docs(memory): refresh the family wiki home page",
        # Only used if the seed's root index.md is somehow missing;
        # otherwise the seed already carries this frontmatter.
        default_preamble="---\ntitle: Family Memory\n---",
    )


async def _generate_member(
    classifier: Classifier, slug: str, index: list[dict], vault: Path, *,
    shared_bucket: str, lang: str, write: bool,
) -> int:
    """Compose one member's page from their slice of the vault."""
    entries = _member_entries(index, slug)
    if not entries:
        err(f"no content involving '{slug}' — skipping")
        return 1

    display = slug.capitalize()
    facts = _load_facts(vault, slug)
    prompt = _build_member_prompt(display, slug, entries, facts, lang=lang)
    page = (await classifier._request("overview", prompt, json_mode=False)).strip()

    # Member page lives at `<slug>/about.md`; links climb one level to
    # reach the shared bucket and stay relative within the member's own.
    page = _with_references(page, entries, page_dir=slug)

    if not write:
        print(f"\n<!-- {slug}/about.md -->\n{page}")
        return 0
    return await _publish(
        page, target_path=f"{slug}/about.md", shared_bucket=shared_bucket,
        commit_msg=f"docs(memory): refresh {slug}'s wiki page",
        # First-creation frontmatter seeds the person entity registry on
        # the page itself: `canonical` is the longest synonym (usually
        # the formal first name when the family also uses a nickname),
        # `synonyms` are the other variants seen in document
        # frontmatter. The splice keeps everything outside the markers
        # on re-runs, so a hand edit or a future deriver pass takes
        # ownership of the registry from here.
        default_preamble=_member_preamble(slug, display, _member_synonyms(index, slug)),
    )


def _member_preamble(slug: str, display: str, synonyms: list[str]) -> str:
    """Frontmatter for a freshly-created member page.

    Picks the longest synonym as the canonical name -- in a household
    that uses both "Margaret" and "Maggie", the longer one is reliably
    the formal first name. Empty synonyms (a member named consistently
    in every document) collapses to a single-field registry entry.
    """
    canonical = max(synonyms, key=len) if synonyms else display
    others = [s for s in synonyms if s != canonical]
    lines = [
        "---",
        f"title: {canonical}",
        f"slug: {slug}",
        f"canonical: {canonical}",
    ]
    if others:
        lines.append("synonyms:")
        lines.extend(f"  - {s}" for s in others)
    lines.append("---")
    return "\n".join(lines)


# ── Vault index (single walk) ──────────────────────────────────────────────

def _index_vault(vault: Path) -> list[dict]:
    """Walk the vault once; index every file that carries a summary callout.

    Files without a `> [!summary]` block are skipped -- they don't carry
    the structured signal the LLM needs and would only add noise. Each
    entry holds `title`, `date`, `summary`, `rel`, `persons` (frontmatter
    person list, slugified to match member buckets), and `person_names`
    (the original casing, kept aligned with `persons` so callers can
    recover synonym variants for the entity registry). The one list the
    home page and every member page draw from: one walk, many surfaces.
    """
    out: list[dict] = []
    for md in sorted(vault.rglob("*.md")):
        try:
            text = md.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        summary = extract_summary_callout(text)
        if not summary:
            continue
        fm = _parse_frontmatter(text)
        try:
            rel = str(md.relative_to(vault))
        except ValueError:
            rel = str(md)
        raw_persons = [
            p.strip()
            for p in (fm.get("persons") or [])
            if isinstance(p, str) and p.strip()
        ]
        slugged = [(_slugify_person(p), p) for p in raw_persons]
        out.append({
            "title": fm.get("title") or md.stem,
            "date": fm.get("date") or "",
            "summary": summary,
            "rel": rel,
            "persons": [s for s, _ in slugged if s],
            "person_names": [n for s, n in slugged if s],
        })
    return out


def _member_slugs(vault: Path, index: list[dict], shared_bucket: str) -> list[str]:
    """Every family member the wiki should carry a page for.

    Union of two signals: entity buckets that physically exist at the
    vault root (a member who has captured notes or bookmarks), and the
    person slugs named across document frontmatter (a member who only
    appears in shared documents and has no bucket of their own yet).
    Either way they get a page.
    """
    skip = _NON_MEMBER_DIRS | {shared_bucket}
    slugs: set[str] = set()
    for child in vault.iterdir():
        if child.is_dir() and child.name not in skip and not child.name.startswith("."):
            slugs.add(child.name.lower())
    for entry in index:
        slugs.update(entry["persons"])
    return sorted(slugs)


def _member_synonyms(index: list[dict], slug: str) -> list[str]:
    """Distinct person-name variants that resolve to this member's bucket.

    The classifier writes whatever first name appears in each document
    into `persons:` -- "Maggie" on one bill, "Margaret" on the birth
    certificate. Collected and deduped across the vault, those are the
    member's known synonyms: the raw material for the entity registry
    seeded into each `about.md` page and, eventually, what the deriver
    grows over time.
    """
    seen: set[str] = set()
    for entry in index:
        for sl, raw in zip(entry.get("persons", []),
                           entry.get("person_names", [])):
            if sl == slug:
                seen.add(raw)
    return sorted(seen)


def _member_entries(index: list[dict], slug: str) -> list[dict]:
    """The index slice that belongs on `slug`'s page.

    A file counts as the member's when it lives in their bucket
    (`<slug>/...`, their own notes and bookmarks) or when their slug is
    named in a document's `persons:` frontmatter (a shared document
    that is about them). Preserves index order so citation numbers stay
    stable across a regen.
    """
    prefix = f"{slug}/"
    return [
        e for e in index
        if e["rel"].startswith(prefix) or slug in e["persons"]
    ]


def _slugify_person(name: str) -> str:
    """Map a frontmatter person name to its vault bucket slug.

    Buckets are the Matrix localpart lowercased; for the default family
    that is the first name lowercased ("Homer Simpson" -> "homer"). We
    take the first whitespace token so a full name still resolves to the
    bucket the captures landed in.
    """
    token = name.strip().split()[0] if name.strip() else ""
    return token.lower()


# ── Facts ────────────────────────────────────────────────────────────────

def _load_facts(vault: Path, slug: str) -> list[tuple[str, str]]:
    """Hand-curated facts about a member, from `facts.toml`.

    Returns (kind, text) for every `[[fact]]` whose `subject` matches the
    slug. These are ground truth the family typed in, fed to the member
    prompt alongside the document summaries. A missing or malformed
    facts.toml is non-fatal -- the page just carries no curated facts.
    """
    path = vault / "facts.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    out: list[tuple[str, str]] = []
    for fact in data.get("fact", []):
        if not isinstance(fact, dict):
            continue
        if str(fact.get("subject", "")).strip().lower() != slug:
            continue
        text = str(fact.get("text", "")).strip()
        if text:
            out.append((str(fact.get("kind", "fact")).strip() or "fact", text))
    return out


# ── References ─────────────────────────────────────────────────────────────

def _with_references(page: str, entries: list[dict], *, page_dir: str) -> str:
    """Append a `## References` block for the citations the page used."""
    section = _build_references_section(page, entries, page_dir=page_dir)
    if section:
        return page.rstrip() + "\n\n" + section
    return page


def _build_references_section(
    page: str, entries: list[dict], *, page_dir: str,
) -> str:
    """Render a `## References` block listing the cited source documents.

    Reads `[N]` citations out of the LLM's output and maps each to the
    corresponding entry. Link paths are computed relative to where the
    page lives (`page_dir`), so they resolve in both Forgejo and Quartz.
    Returns "" when the LLM cited nothing -- an empty heading helps no
    one.
    """
    citations = extract_citations(page)
    if not citations:
        return ""
    rows: list[str] = ["## References", ""]
    for n in citations:
        if not (1 <= n <= len(entries)):
            continue
        s = entries[n - 1]
        title = (s.get("title") or "").strip() or "(untitled)"
        date = (s.get("date") or "").strip()
        rel = s.get("rel") or ""
        link = _relative_link(rel, page_dir) if rel else ""
        head = f"- [{n}] [{title}]({link})" if link else f"- [{n}] **{title}**"
        if date:
            head += f" - {date}"
        rows.append(head)
    return "\n".join(rows)


def _relative_link(rel: str, page_dir: str) -> str:
    """Path to a vault file `rel` as seen from a page living in `page_dir`.

    `page_dir` is the vault-relative directory of the page: "" for the
    root home page, "homer" for a member page. A target inside the same
    directory drops the shared prefix; anything else climbs out with one
    `../` per path segment, then descends. Pure string work -- the bucket
    boundary is structural in the layout, not a runtime fact, so no
    `os.path.relpath` rerouting through the local FS.
    """
    if not page_dir:
        return rel
    prefix = page_dir.strip("/") + "/"
    if rel.startswith(prefix):
        return rel[len(prefix):]
    depth = len([p for p in page_dir.strip("/").split("/") if p])
    return "../" * depth + rel


# ── Bracketed-region splice ────────────────────────────────────────────────

def _splice_generated(existing: str, generated: str, *,
                      default_preamble: str = "") -> str:
    """Place `generated` inside the regenerate region of `existing`.

    If the page already has the markers, only the content between them is
    replaced -- everything a human wrote outside survives. If it has no
    markers yet but has other content (a freshly seeded `index.md`), the
    region is appended below whatever is already there, so a seeded
    welcome stays on top of the generated overview. If the file is
    entirely empty (a member folder that never had a page), the caller's
    `default_preamble` is laid down above the region -- Quartz only
    treats a folder index as the folder's landing page when it carries a
    `title` frontmatter field, so freshly-created pages need at least
    that much up top.
    """
    # Blank lines around the markers so any markdown parser treats them
    # as discrete block elements rather than glueing the next heading
    # onto the comment block.
    block = f"{_BEGIN}\n\n{generated.strip()}\n\n{_END}"
    start = existing.find(_BEGIN)
    end = existing.find(_END)
    if start != -1 and end != -1 and end > start:
        before = existing[:start].rstrip()
        after = existing[end + len(_END):].lstrip()
        parts = [p for p in (before, block, after) if p]
        return "\n\n".join(parts) + "\n"
    base = existing.rstrip()
    if base:
        return f"{base}\n\n{block}\n"
    if default_preamble:
        return f"{default_preamble.rstrip()}\n\n{block}\n"
    return f"{block}\n"


# ── Forgejo publish ─────────────────────────────────────────────────────────

async def _publish(page: str, *, target_path: str, shared_bucket: str,
                   commit_msg: str, default_preamble: str = "") -> int:
    """Splice the generated page into `target_path` on the memory repo.

    Uses admin credentials to issue a short-lived token rather than
    reusing the archivist-bot's persisted token. The CLI is a manual
    one-shot; spinning a per-invocation token keeps it independent of the
    bot's auth lifecycle, and the same admin creds the framework uses
    elsewhere are already in our env.

    The existing page is read from Forgejo (the canonical source, not the
    wiki's working copy on disk) so the splice preserves whatever the
    family last committed outside the brackets.
    """
    code_url = os.environ.get("CODE_URL", "")
    admin_user = os.environ.get("MATRIX_ADMIN_USER", "")
    admin_password = os.environ.get("MATRIX_ADMIN_PASSWORD", "")
    if not (code_url and admin_user and admin_password):
        err("CODE_URL / MATRIX_ADMIN_USER / MATRIX_ADMIN_PASSWORD not set")
        return 1

    repo_owner = shared_bucket  # default-install convention; see run()

    try:
        # Issue a token for the admin user. issue_token deletes and
        # reissues on name collision, so repeated CLI runs are safe.
        admin_client = await asyncio.to_thread(
            ForgejoClient,
            url=code_url, admin_user=admin_user, admin_password=admin_password,
        )
        token = await asyncio.to_thread(
            admin_client.issue_token,
            admin_user, admin_password, _TOKEN_NAME, _TOKEN_SCOPES,
        )
        client = await asyncio.to_thread(ForgejoClient, url=code_url, token=token)

        existing = await asyncio.to_thread(
            client.get_file, repo_owner, _REPO_NAME, target_path, _BRANCH,
        )
        sha = existing.get("sha") if existing else None
        prior = existing.get("content", "") if existing else ""
        merged = _splice_generated(prior, page, default_preamble=default_preamble)

        await asyncio.to_thread(
            client.put_file,
            repo_owner, _REPO_NAME, target_path,
            content=merged, message=commit_msg, branch=_BRANCH, sha=sha,
        )
    except ForgejoError as e:
        err(f"forgejo publish failed: {e}")
        return 1

    err(f"published {repo_owner}/{_REPO_NAME}:{target_path}")
    return 0


# ── Arg helpers ──────────────────────────────────────────────────────────

def _arg_value(argv: list[str], flag: str) -> str | None:
    """Value following `flag` in argv, or None if the flag is absent."""
    if flag in argv:
        i = argv.index(flag)
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


# ── Prompts ──────────────────────────────────────────────────────────────

def _build_home_prompt(entries: list[dict], *, roster: list[str], lang: str) -> str:
    """Single-shot prompt: feed all summaries, ask for one home page.

    The section layout is fixed -- we want the same headings every time
    so re-runs produce comparable output and a future deriver can read
    the page back into structured form. The model still decides what
    facts land in each section. The roster is handed in as an explicit,
    closed set of page paths so the Members section can link each name
    to a member page that actually exists -- the model resolves identity
    (the baby "Margaret" maps to the `maggie/` page), but it can only
    pick from the paths we give it.
    """
    evidence = _format_evidence(entries)
    if roster:
        targets = "\n".join(
            f"- `{slug}/about` — the page for {slug.capitalize()}" for slug in roster
        )
        member_links = (
            "\nEach family member has their own page in the wiki:\n"
            f"{targets}\n"
            "Make each person's bold name in the Members section a markdown link "
            "to their page, matching by who the person is, not just spelling (a "
            "baby named Margaret may have the page `maggie/about`). Link only to "
            "a page listed above; if a person has no page, leave their name bold "
            "but unlinked.\n"
        )
    else:
        member_links = ""
    return f"""You are composing the home page for a family's private, self-hosted memory wiki. It is the first page someone sees when they open the wiki, so this page is the family's "about" surface. The vault is private — never shared publicly — so identifying details (full names, addresses, vehicle plates, account numbers) are fine to include when documents reveal them.

Source summaries (each is one document the archivist filed; cite as [N] inline where the fact came from a specific document):

{evidence}

Produce a markdown page with this EXACT structure and section order:

# The <Family Surname>
> <Primary Address>

## Members
One short paragraph per person living in the household. Format each as:
**[<Full Name>](<member page>)** *(<synonym 1>, <synonym 2>)* — born <YYYY-MM-DD>, <one or two defining details (profession, role)>. [N]
{member_links}Omit the italic synonyms parens if the documents don't show any. Omit the birthdate clause if unknown.

## Broader Family
Relatives outside the household who appear in the documents: parents, in-laws, grandparents, siblings. One bullet per person:
- <Full Name> — <relationship to a household member> [N]

## Home
Two or three lines about the dwelling itself: ownership/rental, year acquired or moved in, anything structurally noted (mortgage, deed). Skip if nothing on record.

## Real Estate
Other properties owned (rentals, second homes, plots). One bullet per property. Write "(no information on file)" if nothing on record.

## Vehicles
One bullet per vehicle: year, make/model, owner. Write "(no information on file)" if nothing.

## Insurance
One bullet per policy: name / provider — what it covers — owner.

## Subscriptions
Recurring paid services (streaming, software, subscriptions to physical goods). One bullet each:
- <Service name> — <cadence and amount if known> — <owner>

## Memberships
Clubs, teams, associations, professional bodies. One bullet each:
- <Org name> — <role / sport / purpose> — <member>

Rules:
- The H1 is the family surname prefixed with "The" (e.g. "# The Simpsons"). Pick the surname most frequently associated with household members; if multiple appear (blended family), use the dominant one.
- The blockquote line below the H1 is the current primary address. Omit the blockquote entirely if no address is on file.
- Respond in: {lang}.
- Use ONLY what the source summaries support. If a section has no source material, write "(no information on file)" — never invent.
- Cite the document that established each fact as [N] inline. Multiple sources: [1, 3].
- Keep entries dense and skimmable. Single paragraph per member; one bullet per item elsewhere.
- Do NOT add a "References" section — it is appended programmatically after your response. Don't list source paths or URLs in your output.
"""


def _build_member_prompt(
    display: str, slug: str, entries: list[dict],
    facts: list[tuple[str, str]], *, lang: str,
) -> str:
    """Prompt for one member's page: their slice of summaries plus facts.

    Curated facts ride in as ground truth (the family typed them); the
    document summaries are the cited evidence. Section layout is fixed
    for the same reason the home page's is.
    """
    evidence = _format_evidence(entries)
    if facts:
        fact_lines = "\n".join(f"- ({kind}) {text}" for kind, text in facts)
        facts_block = (
            f"Hand-curated facts about {display} "
            f"(ground truth the family typed in):\n{fact_lines}"
        )
    else:
        facts_block = f"No hand-curated facts on file for {display}."

    return f"""You are composing the personal page for one member of a family's private, self-hosted memory wiki. The page is the landing page when someone opens this person's folder in the wiki. The vault is private — identifying details are fine to include.

This page is about: {display} (vault slug: {slug}).

{facts_block}

Source summaries involving {display} (cite as [N] inline where the fact came from a specific document):

{evidence}

Produce a markdown page with this EXACT structure and section order:

# {display}
> <one short line: who they are in the family — omit the blockquote if not derivable>

## About
One short paragraph: their role in the household, profession, and defining details drawn from the documents and facts above. [N]

## Facts & Preferences
Bullet the hand-curated facts above (rules, habits, preferences, goals), one per line. Write "(none on file)" if there are none.

## Recent Activity
The most recent notes, bookmarks, and documents involving them, newest first. One bullet each: <date> — <what it was>. [N]

## Documents
Key filed documents that name them (insurance, medical, school, financial). One bullet each. [N]

## People & Organizations
Organizations and correspondents associated with their documents. One bullet each. [N]

Rules:
- The H1 is the person's full name as it appears in the documents; fall back to "{display}".
- Respond in: {lang}.
- Use ONLY what the sources and facts above support. Empty section → "(none on file)". Never invent.
- Cite the source document as [N] inline. Multiple sources: [1, 3].
- Keep entries dense and skimmable.
- Do NOT add a "References" section — it is appended programmatically after your response. Don't list source paths or URLs in your output.
"""


def _format_evidence(entries: list[dict]) -> str:
    """Number the summaries as `[N]` evidence blocks for a prompt."""
    lines: list[str] = []
    for n, s in enumerate(entries, start=1):
        meta_bits = [s["date"]] if s["date"] else []
        meta = " · ".join(meta_bits + [s["title"]])
        lines.append(f"[{n}] {meta}")
        lines.append("    " + s["summary"].replace("\n", "\n    "))
        lines.append("")
    return "\n".join(lines).rstrip()
