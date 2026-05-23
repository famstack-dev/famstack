"""`stack docs overview` — generate the family README from filed summaries.

Experimental, undocumented at the CLI level. Walks the memory vault,
pulls every document's `> [!summary]` callout, and asks the LLM to
compose a single README page covering: name, address, members, broader
family, home, real estate, vehicles, and insurance (names only).

Output goes to stdout by default so we can eyeball before committing.
Pass `--write` to publish it as `<shared_bucket>/README.md` in the
memory repo on Forgejo -- the file then renders inline whenever
someone navigates to the family folder in the web UI.

Lives in `docs/bot/cli/` because the LLM (and the bot-runner's
aiohttp / env wiring) is here, even though the artifact it writes
belongs to the memory stacklet. The natural home will move when the
memory stacklet grows its own LLM access; for v1, easiest path wins.
"""

from __future__ import annotations

import asyncio
import os
import sys
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


HELP = "Generate the family README from filed summaries (experimental)"

# Repo + branch defaults match the memory stacklet's layout. Hard-coded
# rather than env-driven because the layout is stable across deploys
# (the memory stacklet owns the repo name) and we don't want a typo in
# bot config to silently retarget the write.
_REPO_NAME = "memory"
_BRANCH = "main"
_TOKEN_NAME = "stack-docs-overview-cli"
_TOKEN_SCOPES = ["write:repository", "read:repository"]


async def run(paperless: PaperlessAPI, classifier: Classifier,
              argv: list[str]) -> int:
    write = "--write" in argv

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

    summaries = _collect_summaries(vault)
    if not summaries:
        err("no documents with summary callouts found in vault")
        return 1

    prompt = _build_overview_prompt(summaries, lang=lang)
    page = (await classifier._request("overview", prompt, json_mode=False)).strip()

    # Append the References section using the citations the LLM actually
    # used. Built deterministically from the summary list rather than
    # asked of the model -- the LLM is reliable about citing within
    # its prompt context, but the citation→document mapping is ours
    # to render so links and dates can't be fabricated.
    refs_section = _build_references_section(page, summaries, shared_bucket)
    if refs_section:
        page = page.rstrip() + "\n\n" + refs_section

    if not write:
        print(page)
        return 0

    return await _publish_to_forgejo(page, shared_bucket=shared_bucket)


# ── Forgejo publish ─────────────────────────────────────────────────────

async def _publish_to_forgejo(page: str, *, shared_bucket: str) -> int:
    """Commit `<bucket>/README.md` to the memory repo via Forgejo's API.

    Uses admin credentials to issue a short-lived token rather than
    reusing the archivist-bot's persisted token. The CLI is a manual
    one-shot; spinning a per-invocation token keeps it independent of
    the bot's auth lifecycle and the same admin creds the framework
    uses elsewhere are already in our env.
    """
    code_url = os.environ.get("CODE_URL", "")
    admin_user = os.environ.get("MATRIX_ADMIN_USER", "")
    admin_password = os.environ.get("MATRIX_ADMIN_PASSWORD", "")
    if not (code_url and admin_user and admin_password):
        err("CODE_URL / MATRIX_ADMIN_USER / MATRIX_ADMIN_PASSWORD not set")
        return 1

    target_path = f"{shared_bucket}/README.md"
    repo_owner = shared_bucket  # default-install convention; see run()
    commit_msg = "docs(memory): refresh family/README.md overview"

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

        await asyncio.to_thread(
            client.put_file,
            repo_owner, _REPO_NAME, target_path,
            content=page, message=commit_msg, branch=_BRANCH, sha=sha,
        )
    except ForgejoError as e:
        err(f"forgejo publish failed: {e}")
        return 1

    err(f"published {repo_owner}/{_REPO_NAME}:{target_path}")
    return 0


# ── Collection ──────────────────────────────────────────────────────────

def _collect_summaries(vault: Path) -> list[dict]:
    """Pull every memory file's summary callout + frontmatter title/date.

    Files without a `> [!summary]` block are skipped -- they don't
    carry the structured signal the LLM needs and would just add noise
    to the context. Returns dicts with `title`, `date`, `summary`,
    `rel`, in the vault's natural walk order (deterministic per file
    system, good enough for a manual-run CLI).
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
        out.append({
            "title": fm.get("title") or md.stem,
            "date": fm.get("date") or "",
            "summary": summary,
            "rel": rel,
        })
    return out


# ── References ──────────────────────────────────────────────────────────

def _build_references_section(
    page: str, summaries: list[dict], shared_bucket: str,
) -> str:
    """Render a `## References` block listing the cited source documents.

    Reads `[N]` citations out of the LLM's output and maps each to the
    corresponding entry in `summaries`. The link path is computed
    relative to the README's location -- the README lives at
    `<shared_bucket>/README.md`, so a doc at `<shared_bucket>/documents/
    x.md` is `documents/x.md`, and a doc at `homer/notes/y.md` becomes
    `../homer/notes/y.md`. Forgejo renders these as clickable links
    when the file is viewed in the web UI.

    Returns "" when the LLM cited nothing (the section would just be
    an empty heading otherwise).
    """
    citations = extract_citations(page)
    if not citations:
        return ""
    rows: list[str] = ["## References", ""]
    for n in citations:
        if not (1 <= n <= len(summaries)):
            continue
        s = summaries[n - 1]
        title = (s.get("title") or "").strip() or "(untitled)"
        date = (s.get("date") or "").strip()
        rel = s.get("rel") or ""
        link_target = _relative_to_readme(rel, shared_bucket) if rel else ""
        if link_target:
            head = f"- [{n}] [{title}]({link_target})"
        else:
            head = f"- [{n}] **{title}**"
        if date:
            head += f" — {date}"
        rows.append(head)
    return "\n".join(rows)


def _relative_to_readme(rel: str, shared_bucket: str) -> str:
    """Path of a vault file as seen from `<shared_bucket>/README.md`.

    The README sits inside the shared bucket; a sibling under the
    same bucket reaches its target without a parent hop, an entity
    bucket needs one `../` to climb out. Pure string work -- the
    bucket boundary is structural in the vault layout, not a runtime
    fact, so no `os.path.relpath` rerouting through the local FS.
    """
    bucket_prefix = f"{shared_bucket}/"
    if rel.startswith(bucket_prefix):
        return rel[len(bucket_prefix):]
    return f"../{rel}"


# ── Prompt ──────────────────────────────────────────────────────────────

def _build_overview_prompt(summaries: list[dict], *, lang: str) -> str:
    """Single-shot prompt: feed all summaries, ask for one README page.

    The section layout is fixed -- we want the same headings every
    time so re-runs produce comparable output and a future deriver
    can read the page back into structured form. The model still
    decides what facts land in each section.
    """
    lines: list[str] = []
    for n, s in enumerate(summaries, start=1):
        meta_bits = [s["date"]] if s["date"] else []
        meta = " · ".join(meta_bits + [s["title"]])
        lines.append(f"[{n}] {meta}")
        lines.append("    " + s["summary"].replace("\n", "\n    "))
        lines.append("")
    evidence = "\n".join(lines).rstrip()

    return f"""You are composing the README page for a family's private, self-hosted document vault. It is the index page of the family's memory bucket; Forgejo renders README.md inline when someone opens the folder, so this page is the family's "about" surface. The vault is private — never shared publicly — so identifying details (full names, addresses, vehicle plates, account numbers) are fine to include when documents reveal them.

Source summaries (each is one document the archivist filed; cite as [N] inline where the fact came from a specific document):

{evidence}

Produce a markdown page with this EXACT structure and section order:

# The <Family Surname>
> <Primary Address>

## Members
One short paragraph per person living in the household. Format each as:
**<Full Name>** *(<synonym 1>, <synonym 2>)* — born <YYYY-MM-DD>, <one or two defining details (profession, role)>. [N]
Omit the italic synonyms parens if the documents don't show any. Omit the birthdate clause if unknown.

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
