"""`stack memory wiki update` — regenerate the family wiki's entry pages.

Walks the memory vault, pulls every document's `> [!summary]` callout,
and asks the LLM to compose the browsable pages a family lands on:

    (no flags)             home + members + topics: the curator's nightly pass
    --home                 just the household home page
    --member homer         just Homer's page
    --members              every person's page
    --topic camping        just one topic's page
    --topics               every topic page, no home/members
    --dry-run              preview to stdout, no writes
    clean                  delete every generated page (asks first)
    clean --dry-run        list the pages clean would delete
    clean --yes            skip the confirmation (scripted rebuild)

These are the arguments this module takes inside the bot-runner. Admins
reach the page flags as `stack memory wiki update --member homer` and
`clean` as `stack memory wiki clean`; a bare `stack memory wiki update`
asks the curator instead (host side, `cli/wiki.py`).

`--member` and `--topic` repeat and combine with `--home`: any
selection flag switches from the full sweep to "generate exactly this
set" — one invocation covers every page a filing burst touched (the
curator's incremental path). Member values take a slug or a display
name ("Homer Simpson" hits homer's bucket); unknown slugs warn and
skip, and the run fails only when nothing in the selection matched.

Pages are written into the brain projection working copy on disk
(`BRAIN_REPO_DIR`); the curator commits and pushes brain, where the
wiki container picks them up within seconds. Memory (the source repo)
is never written by generation. The wiki is Quartz, which renders
`index.md` as the landing page for the site (vault-root `index.md`)
and for each folder (`<member>/index.md`) -- so the household overview
becomes the wiki home and each member's overview becomes the landing
page for their folder.

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

import os
import re
import sys
import tomllib
from collections.abc import Callable
from datetime import date
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
from stack.people import Person, find_people, resolve  # noqa: E402

from stack.ai.client import LLM, LLMUnavailableError  # noqa: E402

# The diary compiler owns the diary's path and its reader-facing words;
# the home page only places the pointer it renders. Imported by bare
# name, the way `cli/diary.py` imports it, so the two commands share one
# module object and therefore one selected language. The home and
# person pages' templates and prompts live beside it.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import diary  # noqa: E402
import home_page  # noqa: E402
import open_items  # noqa: E402
import person_page  # noqa: E402
import topic_page  # noqa: E402

# One module owns what a todo line is; `stack memory topic` reads
# through it the same way, by bare name from this folder.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from stack.lists import LISTS_DIR, FamilyList, ListError, read_list  # noqa: E402

HELP = "Regenerate the family wiki's home and member pages"

# Subject prefix the curator's poll loop still filters on. Generation
# now writes the brain working copy on disk (the curator commits and
# pushes), so this is no longer the wiki publishing its own commits to
# memory — but the curator imports it and `only_own_commits` still uses
# it as a guard. Retiring the self-filter is slice 7 of the projection
# plan; kept here until then.
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
# (capture-type folder, correspondent index, person files, rescue
# folder), not a topic. The topic-discovery walker skips these names.
_RESERVED_BUCKET_SUBDIRS = {
    "notes", "bookmarks", "documents",
    "correspondents", "people", "_unfiled", "lists",
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


def _has_generated_marker(content: str) -> bool:
    value = _parse_frontmatter(content).get("generated")
    if value is True:
        return True
    return isinstance(value, str) and value.strip().lower() == "true"


def _with_generated_marker(content: str) -> str:
    """Ensure the page frontmatter declares this file as generated."""
    if not content.startswith("---\n"):
        return f"---\ngenerated: true\n---\n\n{content.lstrip()}"
    end = content.find("\n---\n", 4)
    if end < 0:
        return content

    frontmatter = content[4:end]
    lines = frontmatter.splitlines()
    for i, line in enumerate(lines):
        key, sep, _value = line.partition(":")
        if sep and key.strip() == "generated" and not line.startswith((" ", "\t")):
            lines[i] = "generated: true"
            break
    else:
        lines.append("generated: true")

    return "---\n" + "\n".join(lines) + content[end:]


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
    dry_run = "--dry-run" in argv or "--dry" in argv

    # Generation reads source and writes generated pages into ONE tree:
    # the brain projection working copy. The curator's mirror has already
    # put memory's source there, so `_index_vault` reads it and `_publish`
    # writes generated pages beside it on disk. The curator commits and
    # pushes brain — generation never touches git or Forgejo, and memory
    # (source) is never written. `BRAIN_REPO_DIR` is the required
    # write+read tree; without it, failing closed is safer than ever
    # risking a write into the memory source vault.
    brain_dir = os.environ.get("BRAIN_REPO_DIR", "")

    # `clean` is a slate-wiper, not a generator: it removes every
    # generated page from the brain working copy so a following bare
    # `wiki` rebuilds the whole site from the captures. The curator's
    # next commit carries the deletions.
    if argv and argv[0] == "clean":
        if not brain_dir:
            _err("BRAIN_REPO_DIR not set — is the memory stacklet installed?")
            return 1
        assume_yes = "--yes" in argv or "-y" in argv
        return _clean_generated(
            brain=Path(brain_dir), dry_run=dry_run, assume_yes=assume_yes,
        )

    home_sel = "--home" in argv
    topics_only = "--topics" in argv
    members_sel = _arg_values(argv, "--member")
    members_all = "--members" in argv
    topics_sel = _arg_values(argv, "--topic")
    correspondents_sel = _arg_values(argv, "--correspondent")

    if not brain_dir:
        _err("BRAIN_REPO_DIR not set — is the memory stacklet installed?")
        return 1
    vault = Path(brain_dir)
    if not vault.exists():
        _err(f"brain working copy does not exist: {vault}")
        return 1

    # In the default install the org name and the in-repo bucket
    # directory are the same slug (both "family"). Custom installs may
    # diverge; if they do, the right fix is to surface MIRROR_ORG in
    # the env, not to invent a second source of truth here.
    shared_bucket = os.environ.get("SHARED_BUCKET", "family")
    lang = os.environ.get("LANGUAGE", "en")

    # The diary renders its own pointer, so it needs the household
    # language selected here too.
    diary.configure_language(lang)

    # One walk feeds every surface. The home page reads the whole index;
    # each member page reads its slice. No re-walking per member.
    index = _index_vault(vault)
    if not index:
        _err("no documents with summary callouts found in vault")
        return 1

    # The people the wiki carries a page for, all of whom the home page
    # lists: the person files once the family has them, the
    # old guess (bucket dirs plus names in frontmatter) until
    # `stack up memory` has written them. A person file that does not
    # read is reported, and while any is, no page is retired: a typo in
    # a file must not delete a page.
    problems: list[str] = []
    people = _people(shared_bucket, problems)
    if people is not None:
        _resolve_persons(index, people)
    # Every declared person has a page and a line on the home page,
    # account holders first: the baby has no chat account and is still
    # the first person a reader looks for.
    roster = _roster(vault, index, shared_bucket, people)
    persons = {p.id: p for p in people or []}
    retire = people is not None and not problems

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
    if (home_sel or topics_only or members_sel or members_all
            or topics_sel or correspondents_sel):
        generated = 0

        if home_sel:
            rc = await _generate_home(
                llm, index, roster=roster, people=persons,
                shared_bucket=shared_bucket, lang=lang, write=not dry_run,
            )
            if rc == 0:
                generated += 1

        member_slugs: list[str] = list(roster) if members_all else []
        for raw in members_sel:
            slug = _member_id(raw, people)
            if slug and slug not in member_slugs:
                member_slugs.append(slug)
        for slug in member_slugs:
            if slug not in roster:
                _err(f"no member bucket for '{slug}' — skipping")
                continue
            rc = await _generate_member(
                llm, slug, index, vault, person=persons.get(slug), people=persons,
                shared_bucket=shared_bucket, lang=lang, write=not dry_run,
            )
            if rc == 0:
                generated += 1
        if members_all and retire:
            _retire_undeclared(roster, shared_bucket, write=not dry_run)

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
                llm, bucket_prefix, topic_slug, index, people=persons,
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
        llm, index, roster=roster, people=persons,
        shared_bucket=shared_bucket, lang=lang, write=not dry_run,
    )
    if rc != 0:
        return rc
    if retire:
        _retire_undeclared(roster, shared_bucket, write=not dry_run)

    # A member with no content is skipped (not an error) so one
    # empty bucket doesn't sink the whole run. `_generate_member`
    # returns 1 for skipped-empty, which we deliberately swallow
    # here -- the overall run still succeeds.
    for member_slug in roster:
        await _generate_member(
            llm, member_slug, index, vault, person=persons.get(member_slug), people=persons,
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
            llm, bucket_prefix, topic_slug, index, people=persons,
            shared_bucket=shared_bucket, lang=lang, write=not dry_run,
        )
    return 0

# ── Generation ───────────────────────────────────────────────────────────

async def _generate_home(
    llm: LLM, index: list[dict], *,
    roster: list[str], people: dict[str, Person],
    shared_bucket: str, lang: str, write: bool, today: date | None = None,
) -> int:
    """Compose the household home page from every filed summary.

    The roster (every member that has a page) is woven into the Members
    section so each name links to that person's page; `people` gives
    the declared full names. Quartz turns those
    links into backlinks and graph edges for free, so the home page and
    the member pages become navigable in both directions.
    """
    # The template carries the layout; the model reads the sources and
    # answers in JSON. Everyone on the roster gets a line, linked to their
    # page, whatever the model wrote about them.
    names = {slug: p.name for slug, p in people.items()}
    today = today or date.today()
    recent = home_page.recent(index, today)
    prompt = home_page.prompt(
        evidence=_format_evidence(index), ids=roster, names=names, lang=lang,
        recent=recent, today=today,
        aliases={slug: list(p.aliases) for slug, p in people.items()})
    try:
        raw = await llm.complete("overview", prompt, json_mode=True, temperature=_TEMPERATURE)
        answer = home_page.read_answer(raw, ids=roster, recent=recent, today=today)
    except LLMUnavailableError as e:
        _err(f"LLM unavailable: {e}")
        return 1
    except home_page.AnswerError as e:
        _err(f"home: the model's answer is unusable ({e}); the page stays as it was")
        return 1
    page = home_page.render(answer, ids=roster, names=names, lang=lang)

    # Append References using the citations the LLM actually used. Built
    # deterministically from the index rather than asked of the model --
    # the LLM cites reliably, but the citation→document mapping is ours
    # to render so links and dates can't be fabricated. Home page lives
    # at the vault root, so links are root-relative (page_dir="").
    # Above the sections, because it is the one link on this page that
    # leads somewhere a reader browses rather than looks something up.
    page = _with_diary_link(page, shared_bucket=shared_bucket)

    page = _with_references(page, index, page_dir="", lang=lang)

    # Index pages for the shared bucket's own captures (notes dropped in the
    # main family room, not under a topic). The `<shared>/notes/` prefix won't
    # match a topic's `<shared>/<topic>/notes/`, so the two don't overlap.
    home_display = shared_bucket.replace("-", " ").title()

    if not write:
        print(page)
        _publish_capture_indexes(
            index, page_dir=shared_bucket, display=home_display,
            shared_bucket=shared_bucket, write=write,
        )
        return 0
    rc = _publish(
        page, target_path="index.md",
        # Only used if the seed's root index.md is somehow missing;
        # otherwise the seed already carries this frontmatter.
        default_preamble="---\ntitle: Family Memory\n---",
    )
    _publish_capture_indexes(
        index, page_dir=shared_bucket, display=home_display,
        shared_bucket=shared_bucket, write=write,
    )
    return rc


# Stamped on every person page the compiler writes, with the layout it
# follows. A page without the current stamp is rebuilt from the evidence
# once instead of kept as the baseline: it has the previous layout, or
# predates the fix that renumbers diary citations by the source the
# References block shows, so its text may cite the wrong entry.
_PERSON_PAGE_STAMP = "<!-- person page: 4 -->"


async def _generate_member(
    llm: LLM, slug: str, index: list[dict], vault: Path, *,
    person: Person | None = None, people: dict[str, Person] | None = None,
    shared_bucket: str, lang: str, write: bool,
) -> int:
    """Compose one person's page from their slice of the vault.

    `person` is their declared file; without person files the name comes
    from the slug, as before. `people` is every declared person: the
    model is told who filed each source and every name each of them
    goes by, so it credits the right person and never takes an alias
    for someone new.
    """
    entries = _member_entries(index, slug)
    if not entries and person is not None:
        # A declared person always has a page: the home page and the
        # People menu link to it. Nothing filed yet means nothing for the
        # model to write, so the page says so without asking it.
        return _publish_person(
            _empty_person_body(person), slug, person, [], write=write)
    if not entries:
        _err(f"no content involving '{slug}' — skipping")
        return 1

    display = person.name if person else slug.capitalize()
    facts = _load_facts(vault, slug)

    # Anchored regen: renumber the existing page's citations to the
    # current evidence order, and name what's new since that page was
    # generated. Both jobs are mechanical here so the model carries
    # neither (it fails silently at both, see _previous_generated).
    previous, prev_refs = _previous_generated(vault / slug / "about.md")
    if _PERSON_PAGE_STAMP not in previous:
        previous, prev_refs = "", {}
    previous = previous.replace(_PERSON_PAGE_STAMP, "").strip()
    previous, new_evidence = _anchor_to_evidence(previous, prev_refs, entries)

    # The template carries the layout; the model writes only the prose,
    # as JSON. Dates, the latest entries and the documents come from the
    # index, so the model cannot misdate them or invent one.
    first_name = person.first_name if person else display.split()[0]
    lately = person_page.latest(entries)
    prompt = person_page.prompt(
        display=display, first_name=first_name,
        evidence=_format_evidence(entries, with_persons=True, people=people or {}),
        facts=facts, family=_family_names(people or {}),
        lately=lately, lang=lang, previous=previous, new_evidence=new_evidence,
    )
    try:
        raw = await llm.complete("overview", prompt, json_mode=True, temperature=_TEMPERATURE)
        parts = person_page.read_answer(raw, lately=lately)
    except LLMUnavailableError as e:
        _err(f"LLM unavailable: {e}")
        return 1
    except person_page.AnswerError as e:
        _err(f"{slug}: the model's answer is unusable ({e}); the page stays as it was")
        return 1
    docs = person_page.documents(entries)
    page = person_page.render(
        parts, name=display, first_name=first_name, entries=entries,
        lately=lately, docs=docs,
        links={n: _reference(entries[n - 1])[1] for n in docs}, lang=lang,
    )

    page = _PERSON_PAGE_STAMP + "\n\n" + _with_references(page, entries, page_dir=slug, lang=lang)

    if not write:
        print(f"\n<!-- {slug}/about.md -->\n{page}")
        _publish_capture_indexes(
            index, page_dir=slug, display=display,
            shared_bucket=shared_bucket, write=write,
        )
        return 0
    learned = _member_synonyms(index, slug)
    if person is None:
        # No person files: first-creation frontmatter seeds the names
        # on the page itself (`canonical` the longest synonym seen in
        # documents) and the splice keeps them from then on.
        rc = _publish(
            page, target_path=f"{slug}/about.md",
            default_preamble=_member_preamble(slug, display, learned),
        )
    else:
        rc = _publish_person(page, slug, person, learned, write=True)
    _publish_capture_indexes(
        index, page_dir=slug, display=display,
        shared_bucket=shared_bucket, write=write,
    )
    return rc


async def _generate_topic(
    llm: LLM, bucket_prefix: str, topic_slug: str, index: list[dict], *,
    people: dict[str, Person] | None = None,
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

    # The template carries the layout; the model reads the facts and the
    # state out of the sources, as JSON. The todo items and the lists of
    # what was filed come from disk and the index, as they are.
    page_dir = f"{bucket_prefix}/{topic_slug}"
    sources = _topic_sources(entries, cross_refs, topic_slug, people or {})
    lists = _family_lists(bucket_prefix)
    prompt = topic_page.prompt(
        display=display, scope=scope, lang=lang,
        todos=open_items.topic_texts(lists, topic_slug),
        evidence_text=topic_page.evidence(
            sources, {n: e.get("summary") or "" for n, e in enumerate(entries + cross_refs, 1)}),
    )
    try:
        raw = await llm.complete("overview", prompt, json_mode=True, temperature=_TEMPERATURE)
        answer = topic_page.read_answer(raw)
    except LLMUnavailableError as e:
        _err(f"LLM unavailable: {e}")
        return 1
    except topic_page.AnswerError as e:
        _err(f"{page_dir}: the model's answer is unusable ({e}); the page stays as it was")
        return 1
    page = topic_page.render(answer, display=display, sources=sources, lang=lang,
                             open_items=open_items.topic_items(lists, topic_slug, lang))

    page = _with_references(page, entries + cross_refs, page_dir=page_dir, lang=lang)

    if not write:
        print(f"\n<!-- {page_dir}/about.md -->\n{page}")
        _publish_capture_indexes(
            index, page_dir=page_dir, display=display,
            shared_bucket=shared_bucket, write=write,
        )
        return 0
    rc = _publish(
        page,
        target_path=f"{page_dir}/about.md",
        default_preamble=_topic_preamble(topic_slug, display, scope),
    )
    _publish_capture_indexes(
        index, page_dir=page_dir, display=display,
        shared_bucket=shared_bucket, write=write,
    )
    return rc

def _topic_sources(entries: list[dict], cross_refs: list[dict], topic_slug: str,
                   people: dict[str, Person]) -> list["topic_page.Source"]:
    """The topic's captures as the page lists them, numbered as cited.

    The topic's own captures come first, then the ones filed elsewhere,
    the same order the References block numbers them in.
    """
    sources = []
    for n, e in enumerate(entries + cross_refs, start=1):
        kind = _entry_kind(e.get("rel", ""), topic_slug) if n <= len(entries) else "elsewhere"
        title, link = _reference(e)
        sources.append(topic_page.Source(
            n, kind, title, link, e.get("date") or "", _filer(e.get("filed_by") or "", people)))
    return sources


def _filer(localpart: str, people: dict[str, Person]) -> str:
    """Who filed an entry, by the first name the family uses."""
    person = people.get(localpart)
    return person.first_name if person else localpart.capitalize()


def _family_names(people: dict[str, Person]) -> list[str]:
    """Each declared person with every other name they go by."""
    return [p.name + (f" (also called {', '.join(p.aliases)})" if p.aliases else "")
            for p in people.values()]


def _family_lists(bucket: str) -> list[tuple[str, FamilyList]]:
    """Every list in `bucket` with its vault path; a file that does not read is skipped."""
    lists = []
    for file in sorted((_brain_dir() / bucket / LISTS_DIR).glob("*.md")):
        path = f"{bucket}/{LISTS_DIR}/{file.name}"
        try:
            lists.append((path, read_list(file.read_text(encoding="utf-8"), path=path)))
        except (OSError, ListError):
            continue
    return lists


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
            "type": fm.get("type") or "",
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


def _publish_person(page: str, slug: str, person: Person, learned: list[str], *,
                    write: bool) -> int:
    """Publish a declared person's page under the names their file gives.

    The file owns the names: the full name is canonical, the family's
    aliases come first, the variants documents used follow. Refreshed on
    every rebuild, so an alias added to the file reaches the page the
    next time.
    """
    names = _distinct([*person.aliases, person.first_name, *learned], exclude=person.name)
    if not write:
        print(f"\n<!-- {slug}/about.md -->\n{page}")
        return 0
    return _publish(
        page, target_path=f"{slug}/about.md",
        transform=lambda prior: _with_member_names(
            _splice_generated(prior, page, default_preamble=_member_preamble(
                slug, person.name, [person.name, *names])),
            canonical=person.name, synonyms=names,
        ),
    )


def _empty_person_body(person: Person) -> str:
    """The page of a person nothing has been filed about yet."""
    return (f"# {person.name}\n\n"
            f"Nothing filed about {person.first_name} yet. Documents, notes and "
            f"memories that name {person.first_name} will show up here.")


def _people(shared_bucket: str, problems: list[str]) -> list[Person] | None:
    """The declared people, or None while there are no person files.

    Read from the vault working copy (`MEMORY_VAULT_DIR`) when this
    process has one: an edit to a file is on disk there before the
    curator has mirrored it into the brain. The brain is the fallback.
    Every file that does not read is logged and added to `problems`.
    """
    def report(problem: str) -> None:
        problems.append(problem)
        _err(f"person file skipped: {problem}")

    return find_people(
        (os.environ.get("MEMORY_VAULT_DIR"), os.environ.get("BRAIN_REPO_DIR")),
        shared_bucket, report=report,
    )


def _roster(vault: Path, index: list[dict], shared_bucket: str,
            people: list[Person] | None) -> list[str]:
    """The ids the wiki carries person pages for, in the order it shows them."""
    if people is not None:
        return [p.id for p in people]
    return _member_slugs(vault, index, shared_bucket)


def _member_id(raw: str, people: list[Person] | None) -> str:
    """The person a `--member` value or a `persons:` name means.

    Through every name the person files give ("Margaret" is Maggie),
    by first word otherwise, the way buckets are named.
    """
    if people is not None and (person := resolve(raw, people)):
        return person.id
    return slugify_person(raw)


def _resolve_persons(index: list[dict], people: list[Person]) -> None:
    """Point every `persons:` name in the index at its declared person.

    The index slugs names by first word, which files "Margaret" under a
    bucket of her own. With person files the names resolve through the
    family's aliases instead. Names no person answers to keep their
    first-word slug, as before.
    """
    for entry in index:
        entry["persons"] = [
            _member_id(name, people) for name in entry.get("person_names", [])
        ]


def _retire_undeclared(roster: list[str], shared_bucket: str, *,
                       write: bool) -> list[str]:
    """Delete generated person pages for anyone no person file declares.

    Before the person files, a vault folder or a surname could become a
    member page (`media/about.md`). Only pages the wiki generated as a
    person are removed; anything a person wrote is left alone, and so is
    the folder's other content.
    """
    try:
        brain = _brain_dir()
    except RuntimeError:
        return []
    skip = _NON_MEMBER_DIRS | {shared_bucket}
    retired: list[str] = []
    for about in sorted(brain.glob("*/about.md")):
        slug = about.parent.name
        if slug in roster or slug in skip or slug.startswith("."):
            continue
        try:
            text = about.read_text(encoding="utf-8")
        except OSError:
            continue
        if _parse_frontmatter(text).get("type") != "person" or not _is_generated_page(text):
            continue
        rel = f"{slug}/about.md"
        if write:
            about.unlink()
            _err(f"retired {rel}: no person file declares {slug}")
        else:
            _err(f"would retire {rel}: no person file declares {slug}")
        retired.append(rel)
    return retired


def _distinct(names: list[str], *, exclude: str = "") -> list[str]:
    """Names in order, each once (case-insensitive), without `exclude`."""
    seen = {exclude.lower()} if exclude else set()
    out: list[str] = []
    for name in names:
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out


def _with_member_names(content: str, *, canonical: str, synonyms: list[str]) -> str:
    """Set a member page's `title`, `canonical` and `synonyms` frontmatter.

    Every other frontmatter line is kept as it is. A page without
    frontmatter is returned unchanged; the splice always gives a member
    page one.
    """
    if not content.startswith("---\n"):
        return content
    end = content.find("\n---\n", 4)
    if end < 0:
        return content
    kept: list[str] = []
    skipping_list = False
    for line in content[4:end].splitlines():
        if skipping_list and line.startswith((" ", "\t")):
            continue
        skipping_list = False
        key = line.partition(":")[0].strip()
        if not line.startswith((" ", "\t")) and key in ("title", "canonical", "synonyms"):
            skipping_list = key == "synonyms"
            continue
        kept.append(line)
    names = [f"title: {_yaml_str(canonical)}", f"canonical: {_yaml_str(canonical)}"]
    if synonyms:
        names.append("synonyms:")
        names.extend(f"  - {_yaml_str(s)}" for s in synonyms)
    return "---\n" + "\n".join(names + kept) + content[end:]


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
    return _publish(
        body, target_path=target_path,
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

def _with_diary_link(page: str, *, shared_bucket: str) -> str:
    """Put a pointer to the diary above the home page's first section.

    Nothing else on the landing page leads there: the diary is two
    folders down and the sidebar files it alphabetically between
    unrelated entries, so a family that never ran the command has no
    way to find out it exists.

    Only linked once a compiled diary is on disk. The command that
    writes those pages runs on its own schedule, and a landing page
    that opens with a 404 is worse than one that says nothing.
    """
    try:
        published = _brain_dir() / shared_bucket / diary.DIARY_DIR / "about.md"
    except RuntimeError:
        return page
    if not published.exists():
        return page

    link = diary.home_link(shared_bucket)
    lines = page.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("## "):
            head = lines[:i]
            # The address under the H1 is a blockquote, and a callout is
            # one too: without the blank line between them markdown reads
            # the pair as a single quote.
            if head and head[-1].strip():
                head.append("")
            return "\n".join(head + [link, ""] + lines[i:])
    return page.rstrip() + "\n\n" + link


# The References heading in the household's language. A rebuild finds
# the block under any of them, so a language change does not leave the
# old block inside the page body.
_REFERENCES = {"en": "References", "de": "Quellen"}
_REFERENCES_HEADING = re.compile(
    r"^## (?:" + "|".join(map(re.escape, _REFERENCES.values())) + r")\s*$", re.M)


def _with_references(page: str, entries: list[dict], *, page_dir: str,
                     lang: str = "en") -> str:
    """Append a `## References` block for the citations the page used."""
    section = _build_references_section(page, entries, page_dir=page_dir, lang=lang)
    if section:
        return page.rstrip() + "\n\n" + section
    return page


def _build_references_section(
    page: str, entries: list[dict], *, page_dir: str, lang: str = "en",
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
    heading = _REFERENCES.get((lang or "").strip().lower()[:2], _REFERENCES["en"])
    rows: list[str] = [f"## {heading}", ""]
    for n in citations:
        if not (1 <= n <= len(entries)):
            continue
        s = entries[n - 1]
        title, link = _reference(s)
        date = (s.get("date") or "").strip()
        head = f"- [{n}] [{title}]({link})" if link else f"- [{n}] **{title}**"
        if date:
            head += f" - {date}"
        rows.append(head)
    return "\n".join(rows)


def _reference(entry: dict) -> tuple[str, str]:
    """The title and link a source's row in `## References` shows.

    The pair is also how a rebuilt page finds its old citations again
    (`_anchor_to_evidence`), so both read it from here. The link alone
    is not enough: every diary entry of a month links to the same month
    page.
    """
    title = (entry.get("title") or "").strip() or "(untitled)"
    rel = entry.get("rel") or ""
    return title, _relative_link(_published_path(rel), "") if rel else ""


def _anchor_to_evidence(
    previous: str, prev_refs: dict[tuple[str, str], int], entries: list[dict],
) -> tuple[str, list[str]]:
    """Renumber a page's previous body to `entries`, and list what is new.

    `prev_refs` is the previous References block, (title, link) to
    number. A citation whose source is still in `entries` takes that
    source's current number; one whose source is gone keeps its number
    and the prompt tells the model to drop it. New evidence is every
    entry the previous page did not reference, as `[N] date · title`.
    """
    if not previous:
        return previous, []
    current = {_reference(e): n for n, e in enumerate(entries, start=1)}
    remap = {old: current[ref] for ref, old in prev_refs.items() if ref in current}
    new_evidence = [
        f"[{n}] " + " · ".join(x for x in (e.get("date"), e.get("title")) if x)
        for n, e in enumerate(entries, start=1)
        if _reference(e) not in prev_refs
    ]
    return _renumber_citations(previous, remap), new_evidence


# Diary entries are the source the month pages are compiled from, and the
# wiki does not publish them (quartz.config.ts ignores `**/diary/entries/**`).
# A citation of one points at the month page that shows it.
_DIARY_ENTRY = re.compile(
    rf"^(?P<bucket>[^/]+)/{diary.DIARY_DIR}/entries/(?P<year>\d{{4}})/(?P<month>\d{{2}})/[^/]+\.md$")


def _published_path(rel: str) -> str:
    """The published page for vault file `rel`: itself, or a diary entry's month."""
    m = _DIARY_ENTRY.match(rel.lstrip("/"))
    if not m:
        return rel
    return f"{m['bucket']}/{diary.DIARY_DIR}/{m['year']}/{m['month']}.md"


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


def _publish_capture_indexes(
    index: list[dict], *, page_dir: str, display: str,
    shared_bucket: str, write: bool,
) -> None:
    """Generate, then publish (or print under --dry-run), the folder index
    pages for `page_dir`'s notes/ and bookmarks/."""
    for kind, target_path, content in _capture_index_pages(index, page_dir, display):
        if not write:
            print(f"\n<!-- {target_path} -->\n{content}")
            continue
        _publish(
            content, target_path=target_path,
            default_preamble=f"---\ntitle: {_yaml_str(f'{_KIND_LABEL[kind]}: {display}')}\n---",
        )


# ── Bracketed-region splice ────────────────────────────────────────────────

def _previous_generated(page_path: Path) -> tuple[str, dict[tuple[str, str], int]]:
    """The current generated body of a page, for prompt anchoring.

    Returns ``(body, refs)``: the content between the regenerate
    markers with the rendered References section stripped, and the
    reference map that section carried ((title, link) → citation number).
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
        (m.group(2), m.group(3)): int(m.group(1))
        for m in re.finditer(
            r"^- \[(\d+)\] \[([^\]]*)\]\(([^)]+)\)", body, re.M,
        )
    }
    refs = _REFERENCES_HEADING.search(body)
    if refs:
        body = body[:refs.start()].rstrip()
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


# ── Projection write (brain working copy on disk) ───────────────────────────
#
# Generation writes generated pages straight into the brain working copy
# on disk. The curator commits and pushes brain (one commit per cycle),
# so generation never touches git or Forgejo and memory stays source-
# only. `_publish` splices against the file already on disk (the mirror
# put memory's source there; a previous regen put any prior generated
# body there), and `_clean_generated` is a local `rm`.


def _is_generated_page(content: str) -> bool:
    """True if `content` is a page the wiki produced.

    The durable signal is `generated: true` frontmatter. The splice
    marker remains a legacy backstop so `clean` can remove pages written
    before the marker existed. Source captures (notes, bookmarks,
    documents, email threads) carry only their own `<!-- mid:... -->` /
    section markers, never this one, so the predicate is the safe delete
    filter for `clean`: it cannot match a capture.
    """
    return _has_generated_marker(content) or _BEGIN in content


def _is_affirmative(response: str) -> bool:
    """A yes to a `[y/N]` prompt -- anything else (including empty) is no."""
    return response.strip().lower() in ("y", "yes")


def _generated_pages_on_disk(brain: Path) -> list[Path]:
    """Every generated `.md` file in the brain working copy.

    Walks the tree, reads each markdown file, and keeps the ones bearing
    the generated splice marker. `.git/` and `README.md` are skipped:
    README pairs hand-written guidance with a generated region, so it is
    not a pure projection to throw away. Pure helper — no deletes — so
    `clean` and its tests share one definition of "what is generated".
    """
    out: list[Path] = []
    for md in sorted(brain.rglob("*.md")):
        if ".git" in md.parts or md.name == "README.md":
            continue
        try:
            content = md.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if _is_generated_page(content):
            out.append(md)
    return out


def _clean_generated(*, brain: Path, dry_run: bool,
                     assume_yes: bool = False) -> int:
    """Delete every generated page from the brain working copy.

    "Generated" means the page carries the splice marker (see
    `_is_generated_page`); source captures never do, so this only ever
    removes derived pages — and the curator's next commit records the
    deletions, so git history keeps the copy. `--dry-run` lists what
    would go, one path per line, without touching disk.
    """
    targets = _generated_pages_on_disk(brain)
    if not targets:
        _err("no generated wiki pages found -- nothing to clean")
        return 0

    rels = [str(p.relative_to(brain)) for p in targets]

    # Destructive: confirm before deleting (skipped under --dry-run,
    # which deletes nothing, and --yes, for scripted rebuilds). A
    # non-interactive stdin reads as "no" so an automated caller without
    # --yes aborts safely rather than wiping the wiki.
    if not dry_run and not assume_yes:
        for rel in rels:
            _err(f"  {rel}")
        prompt = f"Delete {len(targets)} generated wiki page(s)? [y/N] "
        try:
            answer = input(prompt)
        except EOFError:
            answer = ""
        if not _is_affirmative(answer):
            _err("aborted -- nothing deleted")
            return 0

    for page, rel in zip(targets, rels):
        if dry_run:
            print(rel)
            continue
        try:
            page.unlink()
        except OSError as e:
            _err(f"could not delete {rel}: {e}")
            continue
        _err(f"deleted {rel}")

    _err(f"{'would delete' if dry_run else 'deleted'} {len(targets)} generated page(s)")
    return 0


def _brain_dir() -> Path:
    """The brain working copy generation writes into.

    `BRAIN_REPO_DIR` is the projection tree. Missing env is a hard
    configuration error: generation must never fall back to the memory
    source vault.
    """
    value = os.environ.get("BRAIN_REPO_DIR", "")
    if not value:
        raise RuntimeError("BRAIN_REPO_DIR not set")
    return Path(value)


def _publish(page: str, *, target_path: str,
             default_preamble: str = "",
             transform: "Callable[[str], str] | None" = None) -> int:
    """Splice the generated page into `target_path` in the brain working copy.

    The previous file is read from disk (the mirror put memory's source
    there; a previous regen put any prior generated body there) so the
    merge preserves whatever lives outside the brackets. By default that
    merge is the marker splice; a caller can pass `transform` to fold
    prior to new some other way. The write is to disk only -- the
    curator's commit+push carries it to Forgejo and on to Quartz, so
    memory (source) is never touched.
    """
    try:
        page_path = _brain_dir() / target_path
    except RuntimeError as e:
        _err(f"{e} — refusing to write generated wiki pages")
        return 1
    try:
        prior = page_path.read_text(encoding="utf-8")
    except OSError:
        prior = ""
    if prior and not _is_generated_page(prior):
        _err(f"source page {target_path} has no generated marker, skipped")
        return 0
    if transform is not None:
        merged = transform(prior)
    else:
        merged = _splice_generated(prior, page, default_preamble=default_preamble)
    merged = _with_generated_marker(merged)

    if merged == prior:
        _err(f"unchanged {target_path}, skipped")
        return 0

    # Refuse to write a page whose frontmatter won't parse -- one bad
    # page takes the entire Quartz build down, so it never reaches disk.
    # The previously written version stays live.
    fm_error = _frontmatter_error(merged)
    if fm_error:
        _err(f"refusing to publish {target_path}: invalid frontmatter ({fm_error})")
        return 1

    try:
        page_path.parent.mkdir(parents=True, exist_ok=True)
        page_path.write_text(merged, encoding="utf-8")
    except OSError as e:
        _err(f"failed to write {target_path}: {e}")
        return 1

    _err(f"published {target_path}")
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


def _format_evidence(entries: list[dict], *, with_persons: bool = False,
                     people: dict[str, Person] | None = None) -> str:
    """Number the summaries as `[N]` evidence blocks for a prompt.

    ``with_persons`` appends each entry's `persons:` list to its header
    line. Member pages need this: a summary's "the mother" or "her
    husband" has no referent on its own, and a small model will happily
    pin those roles on whoever the page is about. Naming who a document
    involves gives the model an anchor to attribute against.

    ``people`` names who filed each entry, so a booking Marge filed is
    not credited to whoever else it mentions.
    """
    lines: list[str] = []
    for n, s in enumerate(entries, start=1):
        meta_bits = [s["date"]] if s["date"] else []
        meta = " · ".join(meta_bits + [s["title"]])
        if people is not None and s.get("filed_by"):
            meta += f", filed by {_filer(s['filed_by'], people)}"
        if with_persons and s.get("persons"):
            meta += "  (involves: " + ", ".join(s["persons"]) + ")"
        lines.append(f"[{n}] {meta}")
        lines.append("    " + s["summary"].replace("\n", "\n    "))
        lines.append("")
    return "\n".join(lines).rstrip()
