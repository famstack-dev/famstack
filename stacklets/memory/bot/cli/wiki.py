"""`stack memory wiki` — regenerate the family wiki's entry pages.

Walks the memory vault, pulls every document's `> [!summary]` callout,
and asks the LLM to compose the browsable pages a family lands on:

    stack memory wiki                   home + members + topics (apply)
    stack memory wiki --home            just the household home page
    stack memory wiki --member homer    just Homer's page
    stack memory wiki --topic camping   just one topic's page
    stack memory wiki --topics          every topic page, no home/members
    stack memory wiki --dry-run         preview to stdout, no writes

`--member` and `--topic` repeat and combine with `--home`: any
selection flag switches from the full sweep to "generate exactly this
set" — one invocation covers every page a filing burst touched (the
curator's incremental path). Member values take a slug or a display
name ("Homer Simpson" hits homer's bucket); unknown slugs warn and
skip, and the run fails only when nothing in the selection matched.

Pages are published to the memory repo on Forgejo, where the wiki
container picks them up within seconds. The wiki is Quartz, which
renders `index.md` as the landing page for the site (vault-root
`index.md`) and for each folder (`<member>/index.md`) -- so the
household overview becomes the wiki home and each member's overview
becomes the landing page for their folder.

Topic folders (`<bucket>/<slug>/`, bootstrapped from `Thema:` /
`Topic:` Matrix rooms by the archivist) get the same treatment:
`<bucket>/<slug>/about.md` is composed from the topic's own captures
plus cross-references that grep the rest of the vault for files whose
frontmatter `topics:` or `tags:` mention the slug. The deriver later
inherits the same cross-reference structure and refreshes it on
every capture instead of every wiki rebuild.

Updates are splice-based: the generated body lives inside a bracketed
regenerate region (`<!-- begin: generated --> ... <!-- end: generated -->`);
anything a human writes outside the brackets -- a welcome line,
frontmatter, a hand-edited note -- survives the next regen. Same
contract as the correspondent pages.

Apply by default; `--dry-run` is the opt-in preview. Mirrors
`stack memory ontology`. Lives in `memory/bot/cli/` because the LLM
client needs the bot-runner's Python environment.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import tomllib
from pathlib import Path

import yaml

# Sibling stacklets — memory.lib gives us summary callout extraction
# and frontmatter parsing without re-implementing them here.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from memory.lib import (  # noqa: E402
    _parse_frontmatter,
    extract_summary_callout,
)
from stack.vault import correspondents_dir, slug, slugify_person  # noqa: E402

from stack.ai.client import LLM, LLMUnavailableError  # noqa: E402
from stack.forgejo import ForgejoClient, ForgejoError  # noqa: E402


HELP = "Regenerate the family wiki's home and member pages"

# Repo + branch defaults match the memory stacklet's layout. Hard-coded
# rather than env-driven because the layout is stable across deploys
# (the memory stacklet owns the repo name) and we don't want a typo in
# bot config to silently retarget the write.
_REPO_NAME = "memory"
_BRANCH = "main"
_TOKEN_NAME = "stack-memory-wiki-cli"
_TOKEN_SCOPES = ["write:repository", "read:repository"]

# Subject prefix of every commit this command pushes. The curator's
# poll loop filters on it so the wiki's own publishes never trigger
# another rebuild — change it and that loop comes back.
COMMIT_PREFIX = "docs(memory): refresh"

# Decoding temperature for page generation. Sampling turns every
# rebuild into a dice roll: the same evidence produced visibly
# different pages run to run (observed live 2026-06-11 — a rich facts
# list vanished on a one-note evidence change). Default 0 (greedy) so
# a page is a near-deterministic function of its evidence; the env
# override exists for experiments and unusual models.
_TEMPERATURE = float(os.environ.get("WIKI_TEMPERATURE", "0.0"))

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

# Within-bucket reserved subdirectories. A child folder of a known
# bucket with one of these names is part of the bucket's own shape
# (capture-type folder, correspondent index, rescue folder), not a
# topic. The topic-discovery walker skips these names.
_RESERVED_BUCKET_SUBDIRS = {
    "notes", "bookmarks", "documents",
    "correspondents", "_unfiled",
}

# Subdir names a topic folder must contain at least one of to be
# discovered. The bucket-shape signal: a topic carries captures of
# at least one shape.
_CAPTURE_SUBDIRS = {"notes", "bookmarks", "documents"}


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


# YAML-safe scalar for a frontmatter value. Human strings (a display
# name, a section title like "Notes: Admin") carry colons, leading `&`,
# `#`, quotes -- all of which a bare YAML scalar mis-parses. Quartz's
# parser hard-fails the whole page on one of these (observed live: a
# `title: Notes: Admin` index page took the entire wiki build down).
# Always-quote and escape; double quotes with backslash-escaped `"` and
# `\` is the one form that round-trips every printable string.
def _yaml_str(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


# Write-boundary gate. Quartz parses frontmatter with a strict YAML
# parser and hard-fails the whole site build on one bad page; the
# failure surfaces three hops downstream, in the running wiki container,
# not here where the page is composed. So we parse the page's own
# frontmatter with the same strictness before pushing it to Forgejo. A
# page that won't load is refused at the source -- the previous good
# version stays live. `_parse_frontmatter` in memory.lib is deliberately
# lenient (skips malformed lines), so it can't stand in for this check.
def _frontmatter_error(page: str) -> str | None:
    """Return a YAML error string if `page`'s frontmatter won't parse, else None."""
    if not page.startswith("---\n"):
        return None  # no frontmatter block to validate
    end = page.find("\n---", 4)
    if end < 0:
        return "unterminated frontmatter block"
    try:
        yaml.safe_load(page[4:end])
    except yaml.YAMLError as e:
        return str(e).replace("\n", " ")
    return None


# Citation extractor — single use here, inlined to keep the command
# stacklet-local. Matches `[N]`, `[N, M]`, and back-to-back `[N][M]`
# patterns. Returns unique numbers in first-seen order so the caller
# can both filter the evidence and keep the bracket numbering aligned
# with the answer's `[N]`.
_CITATION_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


def _extract_citations(text: str) -> list[int]:
    seen: list[int] = []
    for match in _CITATION_RE.finditer(text or ""):
        for part in match.group(1).split(","):
            stripped = part.strip()
            if not stripped.isdigit():
                continue
            n = int(stripped)
            if n not in seen:
                seen.append(n)
    return seen


async def run(llm: LLM, argv: list[str]) -> int:
    dry_run = "--dry-run" in argv
    home_sel = "--home" in argv
    topics_only = "--topics" in argv
    members_sel = _arg_values(argv, "--member")
    topics_sel = _arg_values(argv, "--topic")
    correspondents_sel = _arg_values(argv, "--correspondent")

    vault_dir = os.environ.get("MEMORY_VAULT_DIR", "")
    if not vault_dir:
        _err("MEMORY_VAULT_DIR not set — is the memory stacklet installed?")
        return 1
    vault = Path(vault_dir)
    if not vault.exists():
        _err(f"vault path does not exist: {vault}")
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
        _err("no documents with summary callouts found in vault")
        return 1

    # The roster the home page links into and the default --members loop
    # walks. Computed once: bucket dirs on disk plus everyone named in
    # document frontmatter.
    roster = _member_slugs(vault, index, shared_bucket)

    # Topic locations are discovered against the roster: a topic under
    # a bucket whose owner the wiki doesn't generate a page for would
    # be a dangling page, so we skip it here too. Reads from disk
    # because topic membership isn't surfaced in the index.
    topics = _topic_locations(
        vault, shared_bucket=shared_bucket, member_slugs=roster,
    )

    # ── Selection mode ────────────────────────────────────────────────
    # Any selection flag (--home, --member, --topic, --topics,
    # --correspondent) switches from the full sweep to "generate exactly
    # this set". Flags combine and --member/--topic/--correspondent repeat,
    # so the curator's incremental rebuild covers every page a filing burst
    # touched in one invocation. Member values are normalized through
    # `slugify_person`, the same mapping the index uses, so display names
    # and slugs both land on the bucket. Unknown selections warn and skip —
    # an auto-run fed a name with no match must not sink the batch — and the
    # run fails only when NOTHING in the selection matched.
    if home_sel or topics_only or members_sel or topics_sel or correspondents_sel:
        generated = 0

        if home_sel:
            rc = await _generate_home(
                llm, index, roster=roster,
                shared_bucket=shared_bucket, lang=lang, write=not dry_run,
            )
            if rc == 0:
                generated += 1

        member_slugs: list[str] = []
        for raw in members_sel:
            slug = slugify_person(raw)
            if slug and slug not in member_slugs:
                member_slugs.append(slug)
        for slug in member_slugs:
            if slug not in roster:
                _err(f"no member bucket for '{slug}' — skipping")
                continue
            rc = await _generate_member(
                llm, slug, index, vault,
                shared_bucket=shared_bucket, lang=lang, write=not dry_run,
            )
            if rc == 0:
                generated += 1

        # Correspondents match by canonical name or slug against the roster
        # discovered from document frontmatter.
        if correspondents_sel:
            roster_c = _correspondent_roster(index)
            for raw in correspondents_sel:
                matched = [(s, c) for (s, c) in roster_c if raw in (c, s)]
                if not matched:
                    _err(f"no correspondent named '{raw}' in documents — skipping")
                    continue
                for s, c in matched:
                    rc = await _generate_correspondent(
                        s, c, index,
                        shared_bucket=shared_bucket, write=not dry_run,
                    )
                    if rc == 0:
                        generated += 1

        # `--topic camping` matches anywhere in the vault; ambiguity
        # (both `family/camping/` and `homer/camping/`) generates both
        # pages -- they're genuinely separate topics.
        if topics_only:
            topic_set = topics
        else:
            wanted = set(topics_sel)
            known = {slug for _, slug in topics}
            for t_slug in sorted(wanted - known):
                _err(f"no topic folder found with slug '{t_slug}' — skipping")
            topic_set = [t for t in topics if t[1] in wanted]
        for bucket_prefix, topic_slug in topic_set:
            rc = await _generate_topic(
                llm, bucket_prefix, topic_slug, index,
                shared_bucket=shared_bucket, lang=lang, write=not dry_run,
            )
            if rc == 0:
                generated += 1

        if generated == 0:
            _err("nothing generated — no page in the selection matched")
            return 1
        return 0

    # ── Default loop ──────────────────────────────────────────────────
    # Bare invocation: home, every member, every correspondent, every topic.
    rc = await _generate_home(
        llm, index, roster=roster,
        shared_bucket=shared_bucket, lang=lang, write=not dry_run,
    )
    if rc != 0:
        return rc

    # A member with no content is skipped (not an error) so one
    # empty bucket doesn't sink the whole run. `_generate_member`
    # returns 1 for skipped-empty, which we deliberately swallow
    # here -- the overall run still succeeds.
    for member_slug in roster:
        await _generate_member(
            llm, member_slug, index, vault,
            shared_bucket=shared_bucket, lang=lang, write=not dry_run,
        )

    # Correspondent leaf pages, one per name across all documents.
    for corr_slug, canonical in _correspondent_roster(index):
        await _generate_correspondent(
            corr_slug, canonical, index,
            shared_bucket=shared_bucket, write=not dry_run,
        )

    for bucket_prefix, topic_slug in topics:
        await _generate_topic(
            llm, bucket_prefix, topic_slug, index,
            shared_bucket=shared_bucket, lang=lang, write=not dry_run,
        )
    return 0

# ── Generation ───────────────────────────────────────────────────────────

async def _generate_home(
    llm: LLM, index: list[dict], *,
    roster: list[str], shared_bucket: str, lang: str, write: bool,
) -> int:
    """Compose the household home page from every filed summary.

    The roster (every member that has a page) is woven into the Members
    section so each name links to that person's page. Quartz turns those
    links into backlinks and graph edges for free, so the home page and
    the member pages become navigable in both directions.
    """
    prompt = _build_home_prompt(index, roster=roster, lang=lang)
    try:
        page = (await llm.complete("overview", prompt, temperature=_TEMPERATURE)).strip()
    except LLMUnavailableError as e:
        _err(f"LLM unavailable: {e}")
        return 1

    # Append References using the citations the LLM actually used. Built
    # deterministically from the index rather than asked of the model --
    # the LLM cites reliably, but the citation→document mapping is ours
    # to render so links and dates can't be fabricated. Home page lives
    # at the vault root, so links are root-relative (page_dir="").
    page = _with_references(page, index, page_dir="")

    # Index pages for the shared bucket's own captures (notes dropped in the
    # main family room, not under a topic). The `<shared>/notes/` prefix won't
    # match a topic's `<shared>/<topic>/notes/`, so the two don't overlap.
    home_display = shared_bucket.replace("-", " ").title()

    if not write:
        print(page)
        await _publish_capture_indexes(
            index, page_dir=shared_bucket, display=home_display,
            shared_bucket=shared_bucket, write=write,
        )
        return 0
    rc = await _publish(
        page, target_path="index.md", shared_bucket=shared_bucket,
        commit_msg=f"{COMMIT_PREFIX} the family wiki home page",
        # Only used if the seed's root index.md is somehow missing;
        # otherwise the seed already carries this frontmatter.
        default_preamble="---\ntitle: Family Memory\n---",
    )
    await _publish_capture_indexes(
        index, page_dir=shared_bucket, display=home_display,
        shared_bucket=shared_bucket, write=write,
    )
    return rc


async def _generate_member(
    llm: LLM, slug: str, index: list[dict], vault: Path, *,
    shared_bucket: str, lang: str, write: bool,
) -> int:
    """Compose one member's page from their slice of the vault."""
    entries = _member_entries(index, slug)
    if not entries:
        _err(f"no content involving '{slug}' — skipping")
        return 1

    display = slug.capitalize()
    facts = _load_facts(vault, slug)

    # Anchored regen: renumber the existing page's citations to the
    # current evidence order, and name what's new since that page was
    # generated. Both jobs are mechanical here so the model carries
    # neither (it fails silently at both — see _previous_generated).
    previous, prev_refs = _previous_generated(vault / slug / "about.md")
    new_evidence: list[str] = []
    if previous:
        link_to_n = {
            _relative_link(e["rel"], slug): n
            for n, e in enumerate(entries, start=1)
            if e.get("rel")
        }
        remap = {
            old: link_to_n[link]
            for link, old in prev_refs.items()
            if link in link_to_n
        }
        previous = _renumber_citations(previous, remap)
        for n, e in enumerate(entries, start=1):
            link = _relative_link(e.get("rel") or "", slug)
            if link not in prev_refs:
                meta = " · ".join(
                    x for x in (e.get("date"), e.get("title")) if x
                )
                new_evidence.append(f"[{n}] {meta}")

    prompt = _build_member_prompt(
        display, slug, entries, facts, lang=lang,
        previous=previous, new_evidence=new_evidence,
    )
    try:
        page = (await llm.complete("overview", prompt, temperature=_TEMPERATURE)).strip()
    except LLMUnavailableError as e:
        _err(f"LLM unavailable: {e}")
        return 1

    # Member page lives at `<slug>/about.md`; links climb one level to
    # reach the shared bucket and stay relative within the member's own.
    page = _with_references(page, entries, page_dir=slug)

    if not write:
        print(f"\n<!-- {slug}/about.md -->\n{page}")
        await _publish_capture_indexes(
            index, page_dir=slug, display=display,
            shared_bucket=shared_bucket, write=write,
        )
        return 0
    rc = await _publish(
        page, target_path=f"{slug}/about.md", shared_bucket=shared_bucket,
        commit_msg=f"{COMMIT_PREFIX} {slug}'s wiki page",
        # First-creation frontmatter seeds the person entity registry on
        # the page itself: `canonical` is the longest synonym (usually
        # the formal first name when the family also uses a nickname),
        # `synonyms` are the other variants seen in document
        # frontmatter. The splice keeps everything outside the markers
        # on re-runs, so a hand edit or a future deriver pass takes
        # ownership of the registry from here.
        default_preamble=_member_preamble(slug, display, _member_synonyms(index, slug)),
    )
    await _publish_capture_indexes(
        index, page_dir=slug, display=display,
        shared_bucket=shared_bucket, write=write,
    )
    return rc


async def _generate_topic(
    llm: LLM, bucket_prefix: str, topic_slug: str, index: list[dict], *,
    shared_bucket: str, lang: str, write: bool,
) -> int:
    """Compose one topic's `about.md` from its slice plus cross-refs.

    Mirror of `_generate_member`. The topic's own captures drive the
    typed Bookmarks / Notes / Documents sections (split by kind);
    cross-references (captures elsewhere whose `topics:` or `tags:`
    mention the slug) drive a dedicated section so the topic page
    collects the household's relevant material even when it lives in
    another bucket.

    `bucket_prefix` is the topic's owning bucket: `<shared_bucket>`
    for a shared topic, `<localpart>` for a personal one. The scope
    is derived from this (matches `<shared_bucket>` → shared; else
    personal) — the prompt branches on scope so the page reads as
    one person's interest or the household's, depending.
    """

    entries = _topic_entries(index, bucket_prefix, topic_slug)
    if not entries:
        _err(f"no content under {bucket_prefix}/{topic_slug}/ — skipping")
        return 1

    cross_refs = _topic_cross_refs(index, bucket_prefix, topic_slug)
    scope = "shared" if bucket_prefix == shared_bucket else "personal"
    display = topic_slug.replace("-", " ").title()

    prompt = _build_topic_prompt(
        display, topic_slug, scope, entries, cross_refs, lang=lang,
    )
    try:
        page = (await llm.complete("overview", prompt, temperature=_TEMPERATURE)).strip()
    except LLMUnavailableError as e:
        _err(f"LLM unavailable: {e}")
        return 1

    # Topic about.md lives at `<bucket>/<slug>/about.md`; citations climb
    # two levels (out of <slug>/, out of <bucket>/) to reach files in
    # other buckets.
    page_dir = f"{bucket_prefix}/{topic_slug}"
    page = _with_references(page, entries + cross_refs, page_dir=page_dir)

    if not write:
        print(f"\n<!-- {page_dir}/about.md -->\n{page}")
        await _publish_capture_indexes(
            index, page_dir=page_dir, display=display,
            shared_bucket=shared_bucket, write=write,
        )
        return 0
    rc = await _publish(
        page,
        target_path=f"{page_dir}/about.md",
        shared_bucket=shared_bucket,
        commit_msg=(
            f"{COMMIT_PREFIX} {bucket_prefix}/{topic_slug} topic page"
        ),
        default_preamble=_topic_preamble(topic_slug, display, scope),
    )
    await _publish_capture_indexes(
        index, page_dir=page_dir, display=display,
        shared_bucket=shared_bucket, write=write,
    )
    return rc


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
        f"title: {_yaml_str(canonical)}",
        f"slug: {slug}",
        "type: person",
        f"canonical: {_yaml_str(canonical)}",
    ]
    if others:
        lines.append("synonyms:")
        lines.extend(f"  - {_yaml_str(s)}" for s in others)
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
        slugged = [(slugify_person(p), p) for p in raw_persons]
        _corr = fm.get("correspondent")
        _fb = fm.get("filed_by")
        out.append({
            "title": fm.get("title") or md.stem,
            "date": fm.get("date") or "",
            "summary": summary,
            "rel": rel,
            "persons": [s for s, _ in slugged if s],
            "person_names": [n for s, n in slugged if s],
            # Who filed this capture (Matrix localpart), for attribution on
            # topic pages. Mirrors the git commit author set at capture time.
            "filed_by": _fb.strip() if isinstance(_fb, str) else "",
            # The document's correspondent (already canonicalised by the
            # classifier). Drives the correspondent leaf-page roster.
            "correspondent": _corr.strip() if isinstance(_corr, str) else "",
            # Both taxonomy surfaces a capture can carry. Documents
            # use `topics:` for the ontology classification; captures
            # use `tags:` (the topic-room seed merges into the
            # classifier's tag list). Cross-reference grep checks the
            # union so a single slug finds material in either shape.
            "topics": _norm_str_list(fm.get("topics")),
            "tags": _norm_str_list(fm.get("tags")),
        })
    return out


def _norm_str_list(value) -> list[str]:
    """Coerce a frontmatter list-of-strings, filtering out non-strings.

    `_parse_frontmatter` returns lists for `- foo` shaped blocks and
    strings (or absent) for scalar values. Both shapes feed the
    cross-reference grep; this normalises to a clean list[str].
    """
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if not isinstance(value, list):
        return []
    return [v.strip() for v in value if isinstance(v, str) and v.strip()]


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
    # Bots (the `-bot` convention, e.g. mail-bot) can end up with a bucket of
    # their own, but they aren't family members — keep them out of the roster
    # so they don't get a member page.
    return sorted(s for s in slugs if not s.endswith("-bot"))


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


# ── Correspondents ─────────────────────────────────────────────────────
#
# Correspondents (an insurer, a school, a tax office) are leaf entities:
# a single reference page, no folder. Documents route to the date-filed
# documents tree and merely name their correspondent in frontmatter, so a
# correspondent owns no captures -- its page is identity plus a backlink
# list of the documents that reference it. Discovered from the
# `correspondent:` field the classifier writes on each document.

def _correspondent_roster(index: list[dict]) -> list[tuple[str, str]]:
    """(slug, canonical) for every correspondent named across documents.

    The classifier canonicalises the correspondent against the ontology
    before writing it, so each distinct name is already one entity. Sorted
    by canonical name for stable, deterministic output.
    """
    canon: dict[str, str] = {}
    for entry in index:
        name = (entry.get("correspondent") or "").strip()
        if name:
            canon[slug(name)] = name
    return [(s, canon[s]) for s in sorted(canon, key=lambda s: canon[s].lower())]


def _correspondent_entries(index: list[dict], canonical: str) -> list[dict]:
    """Documents whose `correspondent:` matches `canonical`, newest first."""
    hits = [
        e for e in index
        if (e.get("correspondent") or "").strip() == canonical
    ]
    return sorted(hits, key=lambda e: e.get("date") or "", reverse=True)


def _correspondent_preamble(slug_: str, canonical: str) -> str:
    """First-creation frontmatter for a correspondent leaf page.

    Leaf entities are a single file, not a folder, so there is no
    `about.md`/`index.md` split -- the page IS the concept file. Carries
    `type: correspondent` (the OKF concept kind) and the canonical name
    the `memory.lib.correspondents()` reader keys on.
    """
    return "\n".join([
        "---",
        f"title: {_yaml_str(canonical)}",
        f"slug: {slug_}",
        "type: correspondent",
        f"canonical: {_yaml_str(canonical)}",
        "---",
    ])


def _correspondent_body(entries: list[dict], *, page_dir: str) -> str:
    """A deterministic `## Documents` backlink list (no LLM).

    Each document that names this correspondent becomes a relative link,
    so the page is the correspondent's index into the vault. Rebuilt on
    every run, so it auto-extends as new documents arrive -- no queue.
    """
    rows = ["## Documents", ""]
    for e in entries:
        title = (e.get("title") or "").strip() or "(untitled)"
        rel = e.get("rel") or ""
        link = _relative_link(rel, page_dir) if rel else ""
        date = (e.get("date") or "").strip()
        row = f"- [{title}]({link})" if link else f"- **{title}**"
        if date:
            row += f" - {date}"
        rows.append(row)
    return "\n".join(rows)


async def _generate_correspondent(
    slug_: str, canonical: str, index: list[dict], *,
    shared_bucket: str, write: bool,
) -> int:
    """Compose one correspondent's leaf page from its referencing documents.

    Deterministic: no LLM. The page is identity (preamble) plus the
    backlink list. Lives at `<shared_bucket>/correspondents/<slug>.md`.
    """
    entries = _correspondent_entries(index, canonical)
    if not entries:
        _err(f"no documents reference '{canonical}' — skipping")
        return 1

    page_dir = correspondents_dir(shared_bucket)
    body = _correspondent_body(entries, page_dir=page_dir)
    target_path = f"{page_dir}/{slug_}.md"

    if not write:
        print(f"\n<!-- {target_path} -->\n{body}")
        return 0
    return await _publish(
        body, target_path=target_path, shared_bucket=shared_bucket,
        commit_msg=f"docs(memory): refresh correspondent {canonical}",
        default_preamble=_correspondent_preamble(slug_, canonical),
    )


# ── Topic folders ──────────────────────────────────────────────────────
#
# Topics nest inside the bucket that owns them: shared topics under
# `<shared_bucket>/`, personal topics under `<member>/`. The wiki
# command discovers them by walking each bucket and matching the
# bucket-shape signal — a child folder that itself contains at least
# one of `notes/`, `bookmarks/`, or `documents/` is a topic. Reserved
# subdirectory names (the capture-type folders themselves, plus
# `correspondents/` and `_unfiled/`) are skipped.

def _topic_locations(
    vault: Path, *, shared_bucket: str, member_slugs: list[str],
) -> list[tuple[str, str]]:
    """Every topic folder in the vault, as `(bucket_prefix, topic_slug)`.

    Sorted output keeps render order stable across runs and makes
    test assertions and human review easier.
    """

    out: list[tuple[str, str]] = []
    candidate_buckets = [shared_bucket, *member_slugs]
    for bucket in candidate_buckets:
        bucket_path = vault / bucket
        if not bucket_path.is_dir():
            continue
        for child in bucket_path.iterdir():
            if not child.is_dir():
                continue
            if child.name in _RESERVED_BUCKET_SUBDIRS:
                continue
            if child.name.startswith("."):
                continue
            if not any((child / sub).is_dir() for sub in _CAPTURE_SUBDIRS):
                # The folder lives inside a bucket but doesn't carry
                # the capture-type shape -- treat it as user-shaped
                # content, not a topic. Avoids false positives like
                # `family/2023/` or hand-made staging folders.
                continue
            out.append((bucket, child.name))
    return sorted(out)


def _topic_entries(
    index: list[dict], bucket_prefix: str, topic_slug: str,
) -> list[dict]:
    """The index slice that lives under this topic's folder.

    The slash on the prefix (`<bucket>/<slug>/`) prevents an
    accidental match between a short topic (`camp`) and a longer
    sibling (`camping`)."""

    prefix = f"{bucket_prefix}/{topic_slug}/"
    return [e for e in index if e["rel"].startswith(prefix)]


def _topic_cross_refs(
    index: list[dict], bucket_prefix: str, topic_slug: str,
) -> list[dict]:
    """Captures elsewhere in the vault whose frontmatter mentions this
    topic's slug.

    The deriver-style grep, run cheaply over the in-memory index:
    files OUTSIDE the topic's own folder whose `topics:` (the document
    ontology) or `tags:` (the capture tag list, which carries the
    topic-room seed) includes the slug. The union catches both shapes
    so the same call finds material on either surface.

    Skipping the topic's own folder avoids `about.md` citing every
    file in the folder it already represents; the page's `Recent
    Activity` section is the right place for that, not
    `Cross-references`.
    """

    own_prefix = f"{bucket_prefix}/{topic_slug}/"
    out: list[dict] = []
    for entry in index:
        rel = entry.get("rel") or ""
        if rel.startswith(own_prefix):
            continue
        topics = entry.get("topics") or []
        tags = entry.get("tags") or []
        if topic_slug in topics or topic_slug in tags:
            out.append(entry)
    return out


def _topic_preamble(slug: str, display: str, scope: str) -> str:
    """First-creation frontmatter for a topic `about.md`.

    Mirrors `_member_preamble`'s contract: laid down above the
    bracketed regenerate region the first time the page is written.
    Carries the topic's identity (type, slug, display name, scope)
    so future deriver passes and hand-inspection know what they are
    looking at without re-parsing the bucket path.
    """

    return "\n".join([
        "---",
        f"title: {_yaml_str(display)}",
        f"slug: {slug}",
        "type: topic",
        f"scope: {_yaml_str(scope)}",
        "---",
    ])


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
    citations = _extract_citations(page)
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
    """Link to vault file `rel`, as a full path from the vault root.

    Every internal link is rendered absolute-from-root rather than relative to
    the page. Quartz resolves links absolute-from-root (markdownLinkResolution
    "absolute"), and Obsidian resolves a vault-root path identically, so one
    form works at every page depth. Page-relative paths broke on nested topic
    pages: Quartz's "shortest" mode shortened a `notes/...` link to a root slug
    that 404s. `page_dir` is unused now but kept so callers don't change.
    """
    return "/" + rel.lstrip("/")


# ── Folder index pages ──────────────────────────────────────────────────────
#
# Each capture folder (`<bucket-or-topic>/notes/`, `.../bookmarks/`) gets an
# `index.md`, so clicking the folder lands on a real, newest-first list instead
# of Quartz's bare auto-listing or a drill through YYYY/MM. Built from the vault
# index — no LLM — with who filed each item and its tags, links absolute so they
# resolve at any depth.

# Capture kinds: one canonical source for the display label, the order
# sections appear on a page, and the subset of folders that get an index page.
_KIND_LABEL = {"bookmark": "Bookmarks", "note": "Notes", "document": "Documents"}
_TOPIC_KIND_ORDER = ("bookmark", "note", "document")  # section order on a page
_CAPTURE_INDEX_KINDS = ("bookmark", "note")           # folders that get index.md
_MONTHS = ("", "January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")


def _month_label(date_str: str) -> str:
    """`2026-06-25` -> `June 2026`; anything unparseable -> `Undated`."""
    m = re.match(r"^(\d{4})-(\d{2})", date_str or "")
    return f"{_MONTHS[int(m.group(2))]} {m.group(1)}" if m else "Undated"


def _render_capture_index(folder_entries: list[dict], kind: str, display: str) -> str:
    """A folder landing page: every capture newest-first, grouped by month,
    each line carrying who filed it and its tags. Deterministic, no LLM."""
    label = _KIND_LABEL.get(kind, f"{kind.title()}s")
    items = sorted(
        folder_entries,
        key=lambda e: (e.get("date") or "", e.get("title") or ""),
        reverse=True,
    )
    count = len(items)
    noun = label.lower() if count != 1 else label.lower().rstrip("s")
    lines = [f"# {label}: {display}", "", f"*{count} {noun}, newest first*", ""]
    month = None
    for e in items:
        date = (e.get("date") or "").strip()
        label_month = _month_label(date)
        if label_month != month:
            lines += [f"## {label_month}", ""]
            month = label_month
        title = (e.get("title") or "(untitled)").strip()
        link = _relative_link(e.get("rel") or "", "")
        line = f"- **{date or 'undated'}** · [{title}]({link})"
        who = (e.get("filed_by") or "").strip()
        if who:
            line += f" · _{who}_"
        lines.append(line)
        # Tags are deliberately omitted here: a chip row per item buried the
        # title and added noise. They live (clickable) on each capture page.
    return "\n".join(lines).rstrip() + "\n"


def _capture_index_pages(
    index: list[dict], page_dir: str, display: str,
) -> list[tuple[str, str, str]]:
    """`(kind, target_path, content)` for each capture folder under `page_dir`
    that holds entries — the notes/ and bookmarks/ index pages."""
    pages: list[tuple[str, str, str]] = []
    for kind in _CAPTURE_INDEX_KINDS:
        prefix = f"{page_dir}/{kind}s/"
        folder_entries = [
            e for e in index if (e.get("rel") or "").startswith(prefix)
        ]
        if folder_entries:
            content = _render_capture_index(folder_entries, kind, display)
            pages.append((kind, f"{prefix}index.md", content))
    return pages


async def _publish_capture_indexes(
    index: list[dict], *, page_dir: str, display: str,
    shared_bucket: str, write: bool,
) -> None:
    """Generate, then publish (or print under --dry-run), the folder index
    pages for `page_dir`'s notes/ and bookmarks/."""
    for kind, target_path, content in _capture_index_pages(index, page_dir, display):
        if not write:
            print(f"\n<!-- {target_path} -->\n{content}")
            continue
        await _publish(
            content, target_path=target_path, shared_bucket=shared_bucket,
            commit_msg=f"{COMMIT_PREFIX} {page_dir} {kind} index",
            default_preamble=f"---\ntitle: {_yaml_str(f'{_KIND_LABEL[kind]}: {display}')}\n---",
        )


# ── Bracketed-region splice ────────────────────────────────────────────────

def _previous_generated(page_path: Path) -> tuple[str, dict[str, int]]:
    """The current generated body of a page, for prompt anchoring.

    Returns ``(body, refs)``: the content between the regenerate
    markers with the rendered References section stripped, and the
    reference map that section carried (link path → citation number).
    The map lets the caller renumber the baseline's citations to the
    CURRENT evidence order before prompting — new evidence shifts the
    numbering, and a model asked to renumber silently doesn't
    (measured 2026-06-12: byte-identical body over a shifted
    references block — every citation pointing at the wrong source).
    ``("", {})`` when the page doesn't exist yet.
    """
    try:
        text = page_path.read_text(encoding="utf-8")
    except OSError:
        return "", {}
    start = text.find(_BEGIN)
    end = text.find(_END)
    if start == -1 or end == -1 or end <= start:
        return "", {}
    body = text[start + len(_BEGIN):end].strip()
    refs_map = {
        m.group(2): int(m.group(1))
        for m in re.finditer(
            r"^- \[(\d+)\] \[[^\]]*\]\(([^)]+)\)", body, re.M,
        )
    }
    refs = body.find("## References")
    if refs != -1:
        body = body[:refs].rstrip()
    return body, refs_map


def _renumber_citations(text: str, remap: dict[int, int]) -> str:
    """Rewrite every ``[N]``/``[N, M]`` citation through ``remap``.

    Numbers without a mapping pass through unchanged (evidence that
    vanished from the index; the prompt tells the model to drop those
    lines). Single-pass over citation groups, so chained renumbering
    (1→2 while 2→3) cannot double-apply.
    """
    def _sub(m: re.Match) -> str:
        nums = [int(x) for x in re.split(r",\s*", m.group(1))]
        return "[" + ", ".join(str(remap.get(n, n)) for n in nums) + "]"

    return re.sub(r"\[(\d+(?:,\s*\d+)*)\]", _sub, text)


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
        _err("CODE_URL / MATRIX_ADMIN_USER / MATRIX_ADMIN_PASSWORD not set")
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

        # Refuse to publish a page whose frontmatter won't parse -- one
        # bad page takes the entire Quartz build down, so it never leaves
        # this process. The previously published version stays live.
        fm_error = _frontmatter_error(merged)
        if fm_error:
            _err(f"refusing to publish {target_path}: invalid frontmatter ({fm_error})")
            return 1

        await asyncio.to_thread(
            client.put_file,
            repo_owner, _REPO_NAME, target_path,
            content=merged, message=commit_msg, branch=_BRANCH, sha=sha,
        )
    except ForgejoError as e:
        _err(f"forgejo publish failed: {e}")
        return 1

    _err(f"published {repo_owner}/{_REPO_NAME}:{target_path}")
    return 0


# ── Arg helpers ──────────────────────────────────────────────────────────

def _arg_values(argv: list[str], flag: str) -> list[str]:
    """Every value following an occurrence of `flag`, in argv order."""
    out: list[str] = []
    for i, arg in enumerate(argv):
        if arg == flag and i + 1 < len(argv):
            out.append(argv[i + 1])
    return out


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
        # Closed enumeration: the model copies this list instead of
        # deriving the household from the evidence. Deriving is where
        # small models split one person into two ("Maggie" and
        # "Margaret") or drop the page links.
        skeleton = "\n".join(
            f"- **[<full name of {slug.capitalize()}>]({slug}/about)** — <detail>. [N]"
            for slug in roster
        )
        member_links = (
            f"\nThe household has EXACTLY these {len(roster)} members, one bullet "
            "each, in this order — fill in the full name and detail, keep the "
            "link target as given:\n"
            f"{skeleton}\n"
            "A person can appear under different name variants in the sources "
            "(the baby Margaret is the same person as Maggie) — never list "
            "anyone twice, never add a person that is not in the list above.\n"
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
One bullet per person living in the household, oldest first. Format each as exactly:
- **[<Full Name>](<member page>)** — <their role or occupation, stated about them in a source>. [N]
{member_links}Nothing else in the bullet: no birthdates, no nicknames, no placeholders. The member pages carry those details.

## Broader Family
Relatives outside the household who appear in the documents: parents, in-laws, grandparents, siblings. One bullet per person:
- <Full Name> — <relationship to a household member> [N]

## Home
Two or three lines about the dwelling itself: ownership/rental, year acquired or moved in, anything structurally noted (mortgage, deed). Skip if nothing on record.

## Real Estate
Other properties owned (rentals, second homes, plots). One bullet per property.

## Vehicles
One bullet per vehicle: year, make/model, owner.

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
- Use ONLY what the source summaries support. A section with no source material is omitted entirely — never invent, never write placeholder lines.
- Cite the document that established each fact as [N] inline. Multiple sources: [1, 3].
- Keep entries dense and skimmable. One bullet per member; one bullet per item elsewhere.
- Do NOT add a "References" section — it is appended programmatically after your response. Don't list source paths or URLs in your output.
"""


def _build_member_prompt(
    display: str, slug: str, entries: list[dict],
    facts: list[tuple[str, str]], *, lang: str,
    previous: str = "", new_evidence: list[str] | None = None,
) -> str:
    """Prompt for one member's profile card: summaries plus facts.

    The page is a curated overview for assistants and browsing family,
    not an archive (docs/design/brain/wiki-page-anatomy.md): recency-
    weighted About, themed interests, capped activity, no ledger
    content. Curated facts ride in as ground truth (the family typed
    them); the document summaries are the cited evidence. Section
    layout is fixed for the same reason the home page's is.

    ``previous`` is the page's current generated body (sans rendered
    references): with it the prompt anchors the regen to the existing
    wording so a new document produces a reviewable diff instead of a
    full resample — temp 0 alone does NOT give that (evidence+1 changes
    the input, and everything redraws; measured 2026-06-12).

    The evidence slice is shared by design: Lisa's birth certificate is
    on Lisa's page AND describes her parents. Small models conflate
    those roles onto the page's subject ("Lisa ... her husband Homer"),
    so the prompt names who each document involves and carries explicit
    attribution rules. Weakest-model-first: every rule here earned its
    place by a 9B failure.
    """
    evidence = _format_evidence(entries, with_persons=True)
    if facts:
        fact_lines = "\n".join(f"- ({kind}) {text}" for kind, text in facts)
        facts_block = (
            f"Hand-curated facts about {display} "
            f"(ground truth the family typed in):\n{fact_lines}"
        )
    else:
        facts_block = f"No hand-curated facts on file for {display}."

    if previous:
        if new_evidence:
            fresh = "\n".join(new_evidence)
            fresh_block = (
                "NEW since the baseline (not yet on the page) — work it "
                "into the right sections, citing its [N]:\n" + fresh
            )
        else:
            fresh_block = (
                "Nothing is new since the baseline — reproduce it, "
                "applying corrections only where it contradicts the "
                "evidence above."
            )
        anchor_block = f"""
The page already exists. Its current version is below — treat it as the baseline, not as something to rewrite:
- Keep its wording, section names, themes, and bullet order EXACTLY wherever the evidence still supports them.
- The baseline's [N] citations already match the evidence numbering above. Do not change them.
- Touch existing lines only where new evidence forces it (e.g. the Recent Activity cap pushing out the oldest entry).
- Drop a line only when its evidence no longer appears above.
- Never rephrase, restyle, or reorganize anything else.

{fresh_block}

CURRENT PAGE (baseline):
{previous}
"""
    else:
        anchor_block = ""

    return f"""You are composing the profile page for one member of a family's private, self-hosted memory wiki. It is an OVERVIEW, not an archive: the card a family member (or the family's assistant) reads to get a feel for who this person is right now — their role, their current life, their interests and quirks. Detail lives in the cited documents, one click away.

This page is about: {display} (vault slug: {slug}).

{facts_block}

Source summaries involving {display} (cite as [N] inline where the fact came from a specific document):

{evidence}
{anchor_block}
Produce a markdown page with this EXACT structure and section order (omit a section entirely when nothing supports it):

# {display}
> <one short line: who they are in the family, only if a source states it explicitly — omit the blockquote otherwise>

## About
One short paragraph: who {display} is TODAY — role in the family, occupation or school, current life context. When sources span years, prefer the most recent picture; old contracts and certificates are background, not news. [N]

## Interests & Preferences
Themed bullets that give a feel for the person: hobbies, habits, favorite places, pets, preferences, quirks. Merge the hand-curated facts above with what the documents reveal (a league membership, a regular haunt). Format: **<Theme>:** <detail>. [N]

## Recent Activity
The most recent events involving them, newest first, AT MOST 8 bullets: <date> — <what happened>. [N]

## Key Documents
The load-bearing documents naming them (identity, contracts, insurance, medical, school, financial). One bullet each. [N] Receipts and everyday ephemera stay out — they remain reachable through Recent Activity citations.

Rules:
- The whole page stays under roughly 300 words. It is a profile card, not a ledger: never copy line items, account numbers, or ID numbers onto the page — the citations carry that detail.
- The H1 is the person's full name as it appears in the documents; fall back to "{display}".
- The documents above also involve OTHER family members (see each entry's "involves:" list). A birth certificate is mostly about the parents; school papers name a parent. State a fact about {display} ONLY when the source ties it to {display} by name. Never give {display} another person's role, profession, or relationship (mother/father, spouse, caretaker, employer).
- When a summary says "the mother", "her husband", or similar without naming {display}, that fact belongs to someone else — leave it off this page.
- Unsure who a fact is about? Leave it out. A short page is correct; a wrong page is not.
- Respond in: {lang}.
- Use ONLY what the sources and facts above support. Never invent. Omit empty sections — no placeholder lines.
- Cite the source document as [N] inline. Multiple sources: [1, 3].
- Do NOT add a "References" section — it is appended programmatically after your response. Don't list source paths or URLs in your output.
"""


def _entry_kind(rel: str, slug: str) -> str:
    """The capture kind of an entry, read from its vault path.

    A topic's captures live at `<bucket>/<slug>/<folder>/...`; the folder
    right after the topic slug carries the kind (`bookmarks`, `notes`,
    `documents`). Anything unrecognised collapses to "note" — the
    catch-all the capture pipeline itself defaults to.
    """
    parts = rel.split("/")
    try:
        i = parts.index(slug)
    except ValueError:
        return "note"
    folder = parts[i + 1] if i + 1 < len(parts) else ""
    return {
        "bookmarks": "bookmark", "notes": "note", "documents": "document",
    }.get(folder, "note")


def _format_topic_evidence(entries: list[dict], slug: str) -> str:
    """Number entries `[N]` in list order, grouped under their kind.

    The `[N]` index matches each entry's position in `entries`, so the
    deterministic References section (which maps `[N]` back to
    `entries[N-1]`) stays aligned no matter how the LLM orders the page.
    Grouping by kind tells the model which captures are saved links vs.
    the family's own notes, so it can split them into the right sections.
    """
    groups: dict[str, list[str]] = {}
    for n, s in enumerate(entries, start=1):
        kind = _entry_kind(s.get("rel", ""), slug)
        parts = [s["date"]] if s.get("date") else []
        parts.append(s.get("title") or "(untitled)")
        who = (s.get("filed_by") or "").strip()
        if who:
            parts.append(f"filed by {who}")
        meta = " · ".join(parts)
        block = f"[{n}] {meta}\n    " + (s.get("summary") or "").replace("\n", "\n    ")
        groups.setdefault(kind, []).append(block)
    out: list[str] = []
    for kind in _TOPIC_KIND_ORDER:
        if groups.get(kind):
            out.append(f"{_KIND_LABEL[kind]}:")
            out.append("\n\n".join(groups[kind]))
            out.append("")
    return "\n".join(out).rstrip()


def _build_topic_prompt(
    display: str, slug: str, scope: str,
    entries: list[dict], cross_refs: list[dict], *, lang: str,
) -> str:
    """Prompt for one topic's `about.md` page.

    Two surfaces feed the LLM: `entries` are captures filed under
    the topic's own folder (the household's direct material on this
    subject); `cross_refs` are captures elsewhere whose taxonomy
    fields cited the slug (insurance docs that mention camping,
    Marge's gear list that tagged camping, etc.). Both are numbered
    `[N]` evidence; the LLM cites by number.

    Scope branches the tone: a shared topic reads as the family's
    common interest; a personal topic frames it as one person's.

    Captures are grouped by kind so the page separates saved links
    (Bookmarks) from the household's own notes (Notes) and filed
    documents (Documents) instead of flattening everything into one
    feed. About is a recency-weighted overview, not a changelog — the
    latest developments are folded into the prose.
    """

    main_evidence = _format_topic_evidence(entries, slug)
    if cross_refs:
        # Numbering continues from main_evidence so a single citation
        # space spans the whole prompt -- the LLM and the rendered
        # references section stay aligned.
        offset = len(entries)
        cross_block_lines: list[str] = []
        for n, s in enumerate(cross_refs, start=offset + 1):
            meta_bits = [s["date"]] if s.get("date") else []
            meta = " · ".join(meta_bits + [s.get("title") or "(untitled)"])
            cross_block_lines.append(f"[{n}] {meta}")
            cross_block_lines.append(
                "    " + (s.get("summary") or "").replace("\n", "\n    "),
            )
            cross_block_lines.append("")
        cross_block = "\n".join(cross_block_lines).rstrip()
        cross_section = (
            "\nThe household also has material on this topic filed elsewhere "
            "(insurance docs, personal notes, other members' captures). "
            "Treat these as cross-references — surface them in a "
            "`## Cross-references` section, one bullet each, citing as "
            f"`[N]`:\n\n{cross_block}\n"
        )
    else:
        cross_section = (
            "\n(No cross-references on file yet — when other captures "
            "tag this topic, they will appear in a `## Cross-references` "
            "section. Omit the section entirely for now.)\n"
        )

    if scope == "shared":
        framing = (
            f"This topic is a shared household interest. Frame the "
            f"`## About` section as the family's collective material on "
            f"{display.lower()} — what the household uses this topic for, "
            f"what shows up in the captures."
        )
    else:
        framing = (
            f"This topic is a personal interest. Frame the `## About` "
            f"section from the perspective of the one household member who "
            f"keeps these notes — what they care about, what they're "
            f"tracking on {display.lower()}."
        )

    # Only emit a typed section for a kind that actually has captures, so a
    # topic with no bookmarks doesn't carry an empty Bookmarks heading.
    present = [
        _KIND_LABEL[kind]
        for kind in _TOPIC_KIND_ORDER
        if any(_entry_kind(e.get("rel", ""), slug) == kind for e in entries)
    ]
    section_specs = "\n\n".join(
        f"## {label}\nEvery {label[:-1].lower()} from above, newest first, one "
        f"bullet each: `<what it is> — <who filed it, if known> [N]`."
        for label in present
    )

    return f"""You are composing the landing page for a topic folder in a family's private, self-hosted memory wiki. It is an OVERVIEW someone (or the family's assistant) reads to understand what this topic is about and what has happened in it lately — not an archive. Detail lives in the cited captures, one click away. The vault is private — identifying details (full names, places, prices) are fine to include when documents reveal them.

This page is about the topic: {display} (vault slug: {slug}, scope: {scope}).

{framing}

Captures filed directly under this topic (cite as [N] inline where a fact came from a specific capture):

{main_evidence}
{cross_section}
Produce a markdown page with this EXACT structure and section order:

# {display}
> <one short line: what this topic is, in the household's voice — omit the blockquote if not derivable>

## About
One paragraph that reads as a CURRENT overview of the topic — what it is and where it stands right now — weighting recent captures more heavily than older ones. Fold the latest developments into the prose; do not list them as a separate changelog. [N]

{section_specs}

## Cross-references
{"Captures filed in other buckets that mention this topic. One bullet each: `<date> — <what it was> [from <bucket>]`. [N]" if cross_refs else "(omit this section)"}

Rules:
- Respond in: {lang}.
- Use ONLY what the sources above support. Never invent. Omit empty sections — no placeholder lines.
- Cite the source capture as [N] inline. Multiple sources: [1, 3].
- Keep entries dense and skimmable.
- Do NOT add a "References" section — it is appended programmatically after your response. Don't list source paths or URLs in your output.
"""


def _format_evidence(entries: list[dict], *, with_persons: bool = False) -> str:
    """Number the summaries as `[N]` evidence blocks for a prompt.

    ``with_persons`` appends each entry's `persons:` list to its header
    line. Member pages need this: a summary's "the mother" or "her
    husband" has no referent on its own, and a small model will happily
    pin those roles on whoever the page is about. Naming who a document
    involves gives the model an anchor to attribute against.
    """
    lines: list[str] = []
    for n, s in enumerate(entries, start=1):
        meta_bits = [s["date"]] if s["date"] else []
        meta = " · ".join(meta_bits + [s["title"]])
        if with_persons and s.get("persons"):
            meta += "  (involves: " + ", ".join(s["persons"]) + ")"
        lines.append(f"[{n}] {meta}")
        lines.append("    " + s["summary"].replace("\n", "\n    "))
        lines.append("")
    return "\n".join(lines).rstrip()
