"""`stack memory wiki update` — regenerate the family wiki's entry pages.

Walks the memory vault, pulls every document's `> [!summary]` callout,
and asks the LLM to compose the browsable pages a family lands on:

    (no flags)             home + members + topics: the curator's nightly pass
    --home                 just the household home page
    --member homer         just Homer's page
    --members              every person's page
    --topic camping        just one topic's page
    --topics               every topic page, no home/members
    --correspondent duff   just one correspondent's page
    --fast                 no model: the cards as they are, the model's last wording
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

Every page is a view (ADR-016 §3, ADR-017 §7): its frontmatter says
what kind (`view`), what it is about (`about`, a card id) and which
cards it was compiled from (`derived_from`, `compiled_from`), and never
carries a `type` or a `card_id`. The curator reads `derived_from` to
compile a view again when one of its cards is pushed.

Updates are splice-based: the generated body lives inside a bracketed
regenerate region (`<!-- begin: generated --> ... <!-- end: generated -->`);
the text around the brackets (the seeded welcome on the home page)
survives the next regen. A page the
model wrote keeps the model's answer below the region, so `--fast` can
render it again with the cards as they are now.

Apply by default; `--dry-run` is the opt-in preview. Mirrors
`stack memory ontology`. Lives in `memory/bot/cli/` because the LLM
client needs the bot-runner's Python environment.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import functools
import json
import os
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, NamedTuple, TypeAlias, TypeVar


# memory.lib gives us summary callout extraction and frontmatter parsing
# without re-implementing them here.
from ...lib import (
    _parse_frontmatter,
    extract_summary_callout,
)
from stack.cards.sources import has_sources, interpretation_of, sidecar_of
from stack.cards import (
    ABOUT,
    COMPILED_FROM,
    DERIVED_FROM,
    VIEW,
    card_id_from,
    card_version,
    is_view,
)
from stack.frontmatter import FrontmatterError, dump, parse as parse_frontmatter
from stack.vault import TOPIC_CARD, correspondents_dir, slug, slugify_person
from stack.people import PEOPLE_DIR, Person, find_people, resolve

from stack import clock
from stack.ai.client import LLM, LLMError
from stack.ai.transcripts import fingerprint

# The diary compiler owns the diary's path and its reader-facing words;
# the home page only places the pointer it renders. Imported as
# `cli/diary.py` imports it, `stacklets.memory.bot.diary`, so the two
# commands share one module object and therefore one selected language.
# The home and person pages' templates and prompts live beside it.
from .. import brain_lock
from .. import call_costs
from .. import diary
from .. import home_page
from .. import open_items
from .. import person_page
from .. import topic_page

# One module owns what a todo line is; `stack memory topic` reads
# through it the same way, by bare name from this folder.
from stack.lists import LISTS_DIR, FamilyList, ListError, read_list

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

# The longest answer a page needs, in tokens. The home page is the
# longest, measured at about 1,800 for a family of five; this leaves
# room for a family several times that. A model caught in a loop
# otherwise writes until the client timeout (15 minutes) and holds the
# AI server the whole time; capped, its answer is cut off, fails to
# parse, and the page stays as it was.
_ANSWER_TOKENS = 8192

# The model role every page asks; `[ai.models]` may name its own model.
_ROLE = "overview"

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


# Write-boundary gate. Quartz parses frontmatter with a strict YAML
# parser and hard-fails the whole site build on one bad page; the
# failure surfaces three hops downstream, in the running wiki container,
# not here where the page is composed. So the page's own frontmatter
# goes through the shared parser before it is pushed to Forgejo. That
# parser refuses every unquoted value YAML would read differently, so a
# page it accepts loads in Quartz too. A page that won't load is refused
# at the source -- the previous good version stays live.
def _frontmatter_error(page: str) -> str | None:
    """Why `page`'s frontmatter won't parse, or None when it does."""
    if not page.startswith("---\n"):
        return None  # no frontmatter block to validate
    if page.find("\n---\n", 4) < 0:
        return "unterminated frontmatter block"
    try:
        parse_frontmatter(page)
    except FrontmatterError as e:
        return str(e)
    return None


# ── Views ────────────────────────────────────────────────────────────────
#
# Every page this module writes is a view (ADR-016 §3, ADR-017 §7): it
# says what kind of view it is, what it is about, and which cards it was
# compiled from, each at the version it was read at. The curator reads
# `derived_from` to know which views to compile again when a card is
# pushed; the card graph reads `compiled_from` to say when a view is
# behind one of its cards. A view never carries a `type` or a `card_id`,
# so no reader takes it for a card.

@dataclass
class ViewFields:
    """The frontmatter of one view. `sources` are its cards as (card id, version)."""

    view: str
    title: str = ""
    about: str = ""
    sources: list[tuple[str, str]] = field(default_factory=list)
    # A person's view also carries the names the person goes by, which the
    # archivist and the diary read to recognise them (`memory.lib`).
    names: dict[str, object] = field(default_factory=dict)

    def frontmatter(self) -> dict:
        sources = list(dict.fromkeys(s for s in self.sources if s[0]))
        return {
            VIEW: self.view, "title": self.title, ABOUT: self.about,
            **self.names,
            DERIVED_FROM: [card_id for card_id, _ in sources],
            COMPILED_FROM: [f"{card_id}@{version}" for card_id, version in sources],
        }


def _sources(entries: list[dict]) -> list[tuple[str, str]]:
    """The cards behind index entries, as a view names them."""
    return [(e["card_id"], e.get("version") or "") for e in entries if e.get("card_id")]


def _card_file(rel: str) -> bytes | None:
    """The file of the card at vault path `rel`, or None when there is none.

    Read from the memory repo's working copy first: a pushed card is on
    disk there before the curator has mirrored it into the brain, and the
    version a view names is the memory repo's. The brain is the fallback.
    """
    for root in (os.environ.get("MEMORY_VAULT_DIR"), os.environ.get("BRAIN_REPO_DIR")):
        if not root:
            continue
        try:
            data = (Path(root) / rel).read_bytes()
        except OSError:
            continue
        fields = _parse_frontmatter(data.decode("utf-8", errors="ignore"))
        if not is_view(fields) and card_id_from(fields):
            return data
    return None


def _card_source(rel: str) -> list[tuple[str, str]]:
    """The card at vault path `rel` as a view names it, or [] when there is none."""
    data = _card_file(rel)
    if data is None:
        return []
    fields = _parse_frontmatter(data.decode("utf-8", errors="ignore"))
    return [(card_id_from(fields), card_version(data))]


def _list_sources(lists: list[tuple[str, "FamilyList"]], keep) -> list[tuple[str, str]]:
    """The cards of the lists `keep(list)` says a view shows items of."""
    return [s for path, lst in lists if keep(lst) for s in _card_source(path)]


# The model's last answer for a view, kept in the view itself after the
# regenerated region, so a compile without the model can render the page
# again with the cards as they are now, and a compile with the model can
# skip a page whose request is the one that answer came from. Base64,
# because JSON may hold the `--` that would end an HTML comment.
_ANSWER_LINE = re.compile(r"^<!-- answer: (?P<data>[A-Za-z0-9+/=]*) -->\n?", re.M)


class _Kept(NamedTuple):
    """A model's answer as a view keeps it.

    `raw` is the answer, `sources` the sources its `[N]` numbers mean, in
    order, and `asked` the fingerprint of the request it answers
    (`_fingerprint`). `asked` is "" when no request is known to give this
    answer: a view kept before views recorded it, the empty start of a
    view, or an answer that lost a source when it was moved (`_moved`).
    Such an answer is never reused for a model compile.
    """

    raw: str
    sources: tuple[tuple[str, str], ...]
    asked: str = ""

    def encoded(self) -> str:
        """The answer line's data: base64 of the JSON."""
        data = json.dumps({"answer": self.raw, "sources": self.sources, "asked": self.asked},
                          ensure_ascii=False)
        return base64.b64encode(data.encode("utf-8")).decode("ascii")


# A view the model has not written for: an empty answer, citing nothing.
_EMPTY = _Kept("{}", ())


def _with_answer(body: str, answer: _Kept | None) -> str:
    """`body` carrying `answer` as its only answer line, or none without one."""
    body = _ANSWER_LINE.sub("", body).rstrip()
    if answer is None:
        return body + "\n"
    return f"{body}\n\n<!-- answer: {answer.encoded()} -->\n"


def _answer_on(text: str) -> _Kept | None:
    """The answer the view `text` keeps, or None."""
    found = _ANSWER_LINE.search(text)
    if not found:
        return None
    try:
        data = json.loads(base64.b64decode(found["data"]).decode("utf-8"))
        return _Kept(str(data["answer"]), tuple(tuple(ref) for ref in data["sources"]),
                     str(data.get("asked") or ""))
    except (binascii.Error, ValueError, KeyError, TypeError):
        return None


def _kept_on(target_path: str) -> _Kept | None:
    """The answer the view at `target_path` keeps.

    A view that does not exist yet keeps the empty answer, as does one
    the model never wrote for. A page compiled before views kept answers
    keeps none (None).
    """
    try:
        text = (_brain_dir() / target_path).read_text(encoding="utf-8")
    except (OSError, RuntimeError):
        return _EMPTY
    kept = _answer_on(text)
    if kept is None and _parse_frontmatter(text).get(VIEW):
        return _EMPTY
    return kept


def _fingerprint(llm: "LLM", request: tuple[str, ...], refs: list[tuple[str, str]]) -> str:
    """The fingerprint of one view's model call.

    It covers what decides the answer: the endpoint, model and request
    shape the client sends (`LLM.request_key`), the settings this module
    sends, the request (the prompt as the cards make it, and anything
    else the view's answer depends on), and the sources the answer's
    `[N]` numbers will mean. The sources' links are in no prompt, so a
    card moved in place changes the fingerprint through them. The same
    fingerprint means the same request, and a model at temperature 0
    gives the same answer again.
    """
    return fingerprint(*llm.request_key(_ROLE), "json", _TEMPERATURE, _ANSWER_TOKENS,
                       *request, json.dumps(refs, ensure_ascii=False))


class _NoAnswer(Exception):
    """A compile without the model, for a view that has no answer to reuse."""


class _MustAsk(Exception):
    """A view under `_Skip` whose request changed: its compile asks the model."""


class _Skip:
    """A model asked for nothing: a view whose request is unchanged renders
    from its kept answer, any other raises `_MustAsk`.

    `_compile` hands one to every page generator first, with the brain's
    writer lock held, so the kept answer is read, compared and written
    back with no other writer in between. No model time passes, so the
    view needs no second render.
    """

    def __init__(self, llm: "LLM"):
        self.llm = llm


class _RenderLater:
    """A model whose answer is rendered later, over the cards as they are then.

    `_compile` hands one to a page generator whose view `_Skip` could not
    render: the model's turn can take minutes, and the cards the compile
    read before it may have moved by its end.
    """

    def __init__(self, llm: "LLM"):
        self.llm = llm


class _Answered(Exception):
    """The model answered for a view: render it now over the cards as they are.

    Raised by `_ask` for a `_RenderLater` model once it has answered, so
    nothing the compile read before the model's turn is written.
    `compiled` in `_compile` catches it, reads the cards again under the
    brain's writer lock and compiles the view once more with this as its
    model (`_ask` gives back its answer, renumbered from the sources it
    cited to the cards as they are then).
    """

    def __init__(self, answer: _Kept):
        super().__init__("the model answered")
        self.answer = answer


# What a page generator takes as its model.
Model: TypeAlias = "LLM | _Skip | _RenderLater | _Answered | None"

_Read = TypeVar("_Read")


async def _ask(llm: Model, target_path: str, prompt: "str | Callable[[], str]",
               refs: list[tuple[str, str]], read: "Callable[[str], _Read]", *,
               request: tuple[str, ...] | None = None) -> tuple[_Read, _Kept]:
    """The model's answer for one view, as `read` reads it, and what the view keeps.

    `refs` are the view's sources in the order `prompt` numbers them.
    `request` is what decides the answer, the prompt by default; a
    generator whose prompt carries more than the cards (a person page's
    previous text) names the part the cards make, and passes `prompt` as
    a function so it is built only when the model is asked.

    A model (`LLM`, `_Skip`) answers from the view's kept answer when
    its request is the one that answer came from (`_unchanged`). Else
    `_Skip` raises `_MustAsk`, and an `LLM` is asked, the call's cost
    recorded for `target_path` (`call_costs`). A `_RenderLater` one is
    asked and its answer raised as `_Answered`; given that back, the
    answer is returned moved to the sources as they are now. With no
    model (`--fast`), the answer is the one the view keeps, moved the
    same way (`_moved`), so what the cards say is current at once and
    the model's wording follows when it is next asked. A page compiled
    before views kept answers raises `_NoAnswer` and stays as it is
    until the model's turn.
    """
    if request is None:
        request = (prompt if isinstance(prompt, str) else prompt(),)
    if isinstance(llm, _Answered):
        # Read as the page reads it before it is moved: an answer that is
        # no JSON fails with the page's own error, not as "no answer yet".
        read(llm.answer.raw)
        kept = _moved(llm.answer, refs, target_path)
        return read(kept.raw), kept
    if isinstance(llm, _RenderLater):
        raw = await _call(llm.llm, target_path, prompt)
        raise _Answered(_Kept(raw, tuple(refs), _fingerprint(llm.llm, request, refs)))
    if llm is None:
        kept = _kept_on(target_path)
        if kept is None:
            raise _NoAnswer(target_path)
        kept = _moved(kept, refs, target_path)
        return read(kept.raw), kept
    model = llm.llm if isinstance(llm, _Skip) else llm
    asked = _fingerprint(model, request, refs)
    unchanged = _unchanged(target_path, asked, refs, read)
    if unchanged is not None:
        return unchanged
    if isinstance(llm, _Skip):
        raise _MustAsk(target_path)
    raw = await _call(model, target_path, prompt)
    return read(raw), _Kept(raw, tuple(refs), asked)


def _unchanged(target_path: str, asked: str, refs: list[tuple[str, str]],
               read: "Callable[[str], _Read]") -> tuple[_Read, _Kept] | None:
    """The view's kept answer, read, when it answers request `asked`; else None.

    A kept answer that no longer reads (a release made `read` stricter)
    is no answer: the model is asked again rather than the view kept
    stale every night. Nor is one that cited a card that is gone (`--fast`
    moved it to a card filed since, and that card was removed again).
    """
    kept = _kept_on(target_path)
    if kept is None or not kept.asked or kept.asked != asked:
        return None
    try:
        kept = _moved(kept, refs, target_path)
        answer = read(kept.raw)
    except (_NoAnswer, ValueError) as e:
        _err(f"{target_path}: its kept answer does not read ({e}), the model is asked")
        return None
    if not kept.asked:
        # The request is the same, but a card the answer cited is gone:
        # rendered from it, the page would lose that citation.
        _err(f"{target_path}: a source of its kept answer is gone, the model is asked")
        return None
    _err(f"{target_path}: unchanged since the model wrote it, not asked again")
    return answer, kept


async def _call(llm: "LLM", target_path: str, prompt: "str | Callable[[], str]") -> str:
    """One model call for the view at `target_path`, its cost recorded."""
    text = prompt if isinstance(prompt, str) else prompt()
    return await llm.complete(_ROLE, text, json_mode=True,
                              temperature=_TEMPERATURE, max_tokens=_ANSWER_TOKENS,
                              on_cost=call_costs.recorder("view", target_path))


def _moved(answer: _Kept, refs: list[tuple[str, str]], target_path: str) -> _Kept:
    """`answer` renumbered to sources `refs`.

    `[N]` citations follow their sources and are dropped where a source
    is gone. An answer that lost a source keeps no request: the model's
    next turn writes it again, so a source that comes back is cited again.
    """
    now = {ref: n for n, ref in enumerate(refs, start=1)}
    remap = {old: now[ref] for old, ref in enumerate(answer.sources, start=1) if ref in now}
    try:
        raw = json.dumps(_renumbered(json.loads(answer.raw), remap), ensure_ascii=False)
    except json.JSONDecodeError:
        raise _NoAnswer(target_path) from None
    lost = any(ref not in now for ref in answer.sources)
    return _Kept(raw, tuple(refs), "" if lost else answer.asked)


def _renumbered(value, remap: dict[int, int], key: str = ""):
    """A model's answer with every citation number moved through `remap`.

    Citations sit in three places: `[N]` inside text, a `cite` list, and
    the keys of a person's `lately` lines. A number `remap` lacks is dropped.
    """
    if isinstance(value, str):
        return _renumber_citations(value, remap, drop_unknown=True)
    if isinstance(value, list):
        if key == "cite":
            return [remap[n] for n in value if isinstance(n, int) and n in remap]
        return [_renumbered(v, remap, key) for v in value]
    if isinstance(value, dict):
        if key == "lately":
            return {str(remap[int(k)]): _renumbered(v, remap)
                    for k, v in value.items() if k.isdigit() and int(k) in remap}
        return {k: _renumbered(v, remap, k) for k, v in value.items()}
    return value


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
    # `--fast` compiles without the model: the cards as they are now, the
    # model's wording kept from its last answer (`_ask`). The curator runs
    # it at once after a push, and the full compile after the quiet window.
    model: LLM | None = None if "--fast" in argv else llm

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

    if not brain_dir:
        _err("BRAIN_REPO_DIR not set — is the memory stacklet installed?")
        return 1
    vault = Path(brain_dir)
    if not vault.exists():
        _err(f"brain working copy does not exist: {vault}")
        return 1

    # A compile without the model holds the brain's writer lock from
    # reading the cards to writing the last view: it takes seconds, and a
    # compile with the model in another process must not write between.
    # One with the model holds it while views need no model, and lets go
    # for the model's turn (`compiled` below).
    if model is None:
        with brain_lock.held(vault):
            return await _compile(None, vault, argv, dry_run=dry_run,
                                  held=contextlib.ExitStack())
    with contextlib.ExitStack() as held:
        return await _compile(model, vault, argv, dry_run=dry_run, held=held)


async def _compile(model: "LLM | None", vault: Path, argv: list[str], *, dry_run: bool,
                   held: contextlib.ExitStack) -> int:
    """Compile the views `argv` selects, every view without a selection.

    With the model, each view is first compiled with the brain's writer
    lock held (`held`), the cards read again under it: a view whose
    request is unchanged renders from its kept answer there (`_Skip`).
    The lock stays held across such views, so a quiet night reads the
    cards once. Any other view is compiled in two steps: the lock is let
    go and the model asked from the cards as read, and its answer is
    rendered over the cards as they are when the view is written, read
    again under the lock. A compile without the model that ran meanwhile
    is never written over with what the cards said minutes before.
    """
    home_sel = "--home" in argv
    topics_only = "--topics" in argv
    members_sel = _arg_values(argv, "--member")
    members_all = "--members" in argv
    topics_sel = _arg_values(argv, "--topic")
    correspondents_sel = _arg_values(argv, "--correspondent")

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

    def locked() -> list[dict]:
        """Take the brain's writer lock into `held` and read the cards under it."""
        held.enter_context(brain_lock.held(vault))
        now = _index_vault(vault)
        if people is not None:
            _resolve_persons(now, people)
        return now

    # The cards as read under the lock `held` holds now, None while it is free.
    fresh: list[dict] | None = None

    async def compiled(generate) -> int:
        """`generate(model, index)` for one view, its model text over the cards as they are."""
        nonlocal fresh
        if model is None:
            return await generate(None, index)
        if fresh is None:
            fresh = locked()
        try:
            return await generate(_Skip(model), fresh)
        except _MustAsk:
            pass
        read, fresh = fresh, None
        held.close()
        try:
            return await generate(_RenderLater(model), read)
        except _Answered as answered:
            fresh = locked()
            return await generate(answered, fresh)

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
            rc = await compiled(lambda m, ix: _generate_home(
                m, ix, roster=roster, people=persons,
                shared_bucket=shared_bucket, lang=lang, write=not dry_run,
            ))
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
            rc = await compiled(lambda m, ix, slug=slug: _generate_member(
                m, slug, ix, vault, person=persons.get(slug), people=persons,
                shared_bucket=shared_bucket, lang=lang, write=not dry_run,
            ))
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
            rc = await compiled(lambda m, ix, b=bucket_prefix, t=topic_slug: _generate_topic(
                m, b, t, ix, people=persons,
                shared_bucket=shared_bucket, lang=lang, write=not dry_run,
            ))
            if rc == 0:
                generated += 1

        if generated == 0:
            _err("nothing generated — no page in the selection matched")
            return 1
        return 0

    # ── Default loop ──────────────────────────────────────────────────
    # Bare invocation: home, every member, every correspondent, every topic.
    rc = await compiled(lambda m, ix: _generate_home(
        m, ix, roster=roster, people=persons,
        shared_bucket=shared_bucket, lang=lang, write=not dry_run,
    ))
    if rc != 0:
        return rc
    if retire:
        _retire_undeclared(roster, shared_bucket, write=not dry_run)

    # A member with no content is skipped (not an error) so one
    # empty bucket doesn't sink the whole run. `_generate_member`
    # returns 1 for skipped-empty, which we deliberately swallow
    # here -- the overall run still succeeds.
    for member_slug in roster:
        await compiled(lambda m, ix, slug=member_slug: _generate_member(
            m, slug, ix, vault, person=persons.get(slug), people=persons,
            shared_bucket=shared_bucket, lang=lang, write=not dry_run,
        ))

    # Correspondent leaf pages, one per name across all documents.
    for corr_slug, canonical in _correspondent_roster(index):
        await _generate_correspondent(
            corr_slug, canonical, index,
            shared_bucket=shared_bucket, write=not dry_run,
        )

    for bucket_prefix, topic_slug in topics:
        await compiled(lambda m, ix, b=bucket_prefix, t=topic_slug: _generate_topic(
            m, b, t, ix, people=persons,
            shared_bucket=shared_bucket, lang=lang, write=not dry_run,
        ))
    return 0

# ── Generation ───────────────────────────────────────────────────────────

async def _generate_home(
    llm: Model, index: list[dict], *,
    roster: list[str], people: dict[str, Person],
    shared_bucket: str, lang: str, write: bool, today: date | None = None,
) -> int:
    """Compose the household home page from every filed summary.

    The roster (every member that has a page) is woven into the Members
    section so each name links to that person's page; `people` gives
    the declared full names. Quartz turns those
    links into backlinks and graph edges for free, so the home page and
    the member pages become navigable in both directions.

    The view is derived from every card it read: the person cards of the
    roster and every filed summary.
    """
    # The template carries the layout; the model reads the sources and
    # answers in JSON. Everyone on the roster gets a line, linked to their
    # page, whatever the model wrote about them.
    names = {slug: p.name for slug, p in people.items()}
    today = today or clock.today()
    recent = home_page.recent(index, today)
    refs = [_reference(e) for e in index]
    prompt = home_page.prompt(
        evidence=_format_evidence(index), ids=roster, names=names, lang=lang,
        recent=recent, today=today,
        aliases={slug: list(p.aliases) for slug, p in people.items()})
    try:
        answer, kept = await _ask(llm, _HOME_PAGE, prompt, refs, lambda raw: home_page.read_answer(
            raw, ids=roster, recent=recent, today=today))
    except _NoAnswer:
        _err(f"kept {_HOME_PAGE} as it is until the model writes it")
        return 0
    except LLMError as e:
        _err(f"home: no answer from the model ({e}); the page stays as it was")
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
    people_cards = [s for pid in roster
                    for s in _card_source(f"{shared_bucket}/{PEOPLE_DIR}/{pid}.md")]
    rc = _publish(
        page, target_path=_HOME_PAGE,
        # The seeded `index.md` names the site; a brain without one gets this.
        view=ViewFields("home", title=_prior_title(_HOME_PAGE) or "Family Memory",
                        sources=people_cards + _sources(index)),
        answer=kept,
    )
    _publish_capture_indexes(
        index, page_dir=shared_bucket, display=home_display,
        shared_bucket=shared_bucket, write=write,
    )
    return rc


# The home page is the root `index.md`, the page the wiki opens on.
_HOME_PAGE = "index.md"


def _prior_title(target_path: str) -> str:
    """The title the page at `target_path` has now, "" without one."""
    try:
        text = (_brain_dir() / target_path).read_text(encoding="utf-8")
    except (OSError, RuntimeError):
        return ""
    return str(_parse_frontmatter(text).get("title") or "")


# Stamped on every person page the compiler writes, with the layout it
# follows. A page without the current stamp is rebuilt from the evidence
# once instead of kept as the baseline: it has the previous layout, or
# predates the fix that renumbers diary citations by the source the
# References block shows, so its text may cite the wrong entry.
_PERSON_PAGE_STAMP = "<!-- person page: 4 -->"


async def _generate_member(
    llm: Model, slug: str, index: list[dict], vault: Path, *,
    person: Person | None = None, people: dict[str, Person] | None = None,
    shared_bucket: str, lang: str, write: bool,
) -> int:
    """Compose one person's page from their slice of the vault.

    `person` is their declared file; without person files the name comes
    from the slug, as before. `people` is every declared person: the
    model is told who filed each source and every name each of them
    goes by, so it credits the right person and never takes an alias
    for someone new.

    The view is about the person's card and derived from it, every record
    about them and every list with an open item linked to them.
    """
    entries = _member_entries(index, slug)
    lists = _family_lists(shared_bucket)
    card = _card_source(f"{shared_bucket}/{PEOPLE_DIR}/{slug}.md")
    view = ViewFields("person", about=card[0][0] if card else "", sources=(
        card + _sources(entries)
        + _list_sources(lists, lambda lst: any(slug in i.people for i in lst.open_items))))
    if not entries and person is not None:
        # A declared person always has a page: the home page and the
        # People menu link to it. Nothing filed yet means nothing for the
        # model to write, so the page says so without asking it.
        return _publish_person(
            _empty_person_body(person), view, slug, person.name, person, [], write=write)
    if not entries:
        _err(f"no content involving '{slug}' — skipping")
        return 1

    display = person.name if person else slug.capitalize()
    facts = _load_facts(vault, slug)
    target_path = f"{slug}/about.md"

    # The template carries the layout; the model writes only the prose,
    # as JSON. Dates, the latest entries and the documents come from the
    # index, so the model cannot misdate them or invent one.
    first_name = person.first_name if person else display.split()[0]
    lately = person_page.latest(entries)
    refs = [_reference(e) for e in entries]
    from_cards = functools.partial(
        person_page.prompt, display=display, first_name=first_name,
        evidence=_format_evidence(entries, with_persons=True, people=people or {}),
        facts=facts, family=_family_names(people or {}), lately=lately, lang=lang,
    )

    def prompt() -> str:
        """The prompt with the previous page, built only when the model is asked.

        Anchored regen: renumber the existing page's citations to the
        current evidence order, and name what's new since that page was
        generated. Both jobs are mechanical here so the model carries
        neither (it fails silently at both, see _previous_generated).
        """
        previous, prev_refs = _previous_generated(vault / target_path)
        if _PERSON_PAGE_STAMP not in previous:
            previous, prev_refs = "", {}
        previous = previous.replace(_PERSON_PAGE_STAMP, "").strip()
        previous, new_evidence = _anchor_to_evidence(previous, prev_refs, entries)
        return from_cards(previous=previous, new_evidence=new_evidence)

    try:
        # The previous page is the model's own last answer, so the request
        # is the prompt without it: with it, no two nights would ask the
        # same. The stamp is in it, so a new layout asks once per page.
        parts, kept = await _ask(llm, target_path, prompt, refs,
                                 lambda raw: person_page.read_answer(raw, lately=lately),
                                 request=(_PERSON_PAGE_STAMP, from_cards()))
    except _NoAnswer:
        _err(f"kept {target_path} as it is until the model writes it")
        return 0
    except LLMError as e:
        _err(f"{slug}: no answer from the model ({e}); the page stays as it was")
        return 1
    except person_page.AnswerError as e:
        _err(f"{slug}: the model's answer is unusable ({e}); the page stays as it was")
        return 1
    docs = person_page.documents(entries)
    page = person_page.render(
        parts, name=display, first_name=first_name, entries=entries,
        lately=lately, docs=docs,
        links={n: _reference(entries[n - 1])[1] for n in docs}, lang=lang,
        open_items=open_items.person_items(lists, slug, lang),
    )

    page = _PERSON_PAGE_STAMP + "\n\n" + _with_references(page, entries, page_dir=slug, lang=lang)

    if not write:
        print(f"\n<!-- {target_path} -->\n{page}")
        _publish_capture_indexes(
            index, page_dir=slug, display=display,
            shared_bucket=shared_bucket, write=write,
        )
        return 0
    rc = _publish_person(page, view, slug, display, person, _member_synonyms(index, slug),
                         answer=kept, write=True)
    _publish_capture_indexes(
        index, page_dir=slug, display=display,
        shared_bucket=shared_bucket, write=write,
    )
    return rc


async def _generate_topic(
    llm: Model, bucket_prefix: str, topic_slug: str, index: list[dict], *,
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

    The view is about the topic's card and derived from it, its captures,
    the captures elsewhere that name it, and the lists that name it.
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
    target_path = f"{page_dir}/about.md"
    sources = _topic_sources(entries, cross_refs, topic_slug, people or {})
    lists = _family_lists(bucket_prefix)
    refs = [_reference(e) for e in entries + cross_refs]
    prompt = topic_page.prompt(
        display=display, scope=scope, lang=lang,
        todos=open_items.topic_texts(lists, topic_slug),
        evidence_text=topic_page.evidence(
            sources, {n: e.get("summary") or "" for n, e in enumerate(entries + cross_refs, 1)}),
    )
    try:
        answer, kept = await _ask(llm, target_path, prompt, refs, topic_page.read_answer)
    except _NoAnswer:
        _err(f"kept {target_path} as it is until the model writes it")
        return 0
    except LLMError as e:
        _err(f"{page_dir}: no answer from the model ({e}); the page stays as it was")
        return 1
    except topic_page.AnswerError as e:
        _err(f"{page_dir}: the model's answer is unusable ({e}); the page stays as it was")
        return 1
    page = topic_page.render(answer, display=display, sources=sources, lang=lang,
                             open_items=open_items.topic_items(lists, topic_slug, lang))

    page = _with_references(page, entries + cross_refs, page_dir=page_dir, lang=lang)

    if not write:
        print(f"\n<!-- {target_path} -->\n{page}")
        _publish_capture_indexes(
            index, page_dir=page_dir, display=display,
            shared_bucket=shared_bucket, write=write,
        )
        return 0
    card = _card_source(f"{page_dir}/{TOPIC_CARD}")
    rc = _publish(
        page, target_path=target_path, answer=kept,
        view=ViewFields("topic", title=display, about=card[0][0] if card else "", sources=(
            card + _sources(entries + cross_refs)
            + _list_sources(lists, lambda lst: topic_slug in lst.topics))),
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


# ── Vault index (single walk) ──────────────────────────────────────────────

def _index_vault(vault: Path) -> list[dict]:
    """Walk the vault once; index every file that carries a summary callout,
    or a note's clean version in its place.

    Files without either are skipped -- they don't carry
    the structured signal the LLM needs and would only add noise. Each
    entry holds `title`, `date`, `summary`, `rel`, `persons` (frontmatter
    person list, slugified to match member buckets), and `person_names`
    (the original casing, kept aligned with `persons` so callers can
    recover synonym variants for the entity registry), and `card_id` and
    `version`, which the views compiled from it name. The one list the
    home page and every member page draw from: one walk, many surfaces.
    Views are never evidence, so the walk skips them.

    The entries are in the order the cards were filed (`_filed_order`),
    which is the order every prompt numbers its sources in.
    """
    added = _first_commits(vault)
    out: list[tuple[tuple, dict]] = []
    for md in sorted(vault.rglob("*.md")):
        try:
            data = md.read_bytes()
        except OSError:
            continue
        text = data.decode("utf-8", errors="ignore")
        # A note filed as its own words has no summary: its clean version
        # says what it is about (ADR-018 §2).
        summary = extract_summary_callout(text) or (
            interpretation_of(text) if has_sources(text) else "")
        if not summary:
            continue
        fm = _parse_frontmatter(text, md)
        if is_view(fm):
            continue
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
        # A note keeps who filed it, and when, in its sidecar (ADR-018).
        sidecar = sidecar_of(md)
        _fb = fm.get("filed_by") or sidecar.get("filed_by")
        entry = {
            # The card, as the views compiled from it name it (`ViewFields`).
            "card_id": card_id_from(fm),
            "version": card_version(data),
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
        }
        filed = _filed_order(added.get(rel), fm.get("timestamp") or sidecar.get("timestamp"),
                             entry["card_id"], rel)
        out.append((filed, entry))
    return [entry for _, entry in sorted(out, key=lambda pair: pair[0])]


# ── The order cards were filed in ───────────────────────────────────────
#
# A prompt numbers its sources in the order the cards were filed, so a
# new filing is always the last source. The model server (oMLX) reuses
# what it read of a prompt up to the first token that differs from one
# it read before; in path or date order a new filing lands in the middle,
# and everything after it is read again (a home page rebuild over about
# 95,000 tokens took over six minutes, 2026-10).
#
# The brain's git history says when a card was filed: the curator
# mirrors a new card into the brain, compiles, and then commits. So at
# the compile right after a filing the card has no commit yet, and it is
# the newest card there is.

def _first_commits(repo: Path) -> dict[str, int]:
    """Path -> when (Unix seconds) the commit that added it was made; {} without git.

    Paths unquoted (`core.quotepath=off`) and NUL-separated, so a path
    with a space or an umlaut matches the index's `rel`.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "-c", "core.quotepath=off", "log",
             "--diff-filter=A", "--name-only", "-z", "--format=%x1e%at"],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    added: dict[str, int] = {}
    for block in out.split("\x1e")[1:]:
        at, _, names = block.partition("\0")
        for name in names.lstrip("\n").split("\0"):
            if name and at.strip().isdigit():
                # The log is newest first: a path added twice keeps its first add.
                added[name] = int(at)
    return added


def _filed_order(committed_at: int | None, timestamp, card_id: str, rel: str) -> tuple:
    """The sort key that puts a card where it was filed, never by its `date`.

    Committed cards first, by the commit that added them, then by their
    `timestamp` within a commit. Then the cards with no commit yet, the
    newest, by their `timestamp`: every card, where there is no git. Ties
    are broken by `card_id` and path. Within a commit the `timestamp`
    keeps a burst's order: the curator commits a burst in one commit,
    and record ids are random, so without it the order the quiet-window
    compile numbered would change at the next one.
    """
    if committed_at is not None:
        return (0, committed_at, _seconds(timestamp), card_id, rel)
    return (1, _seconds(timestamp), card_id, rel)


def _seconds(timestamp) -> float:
    """An ISO timestamp as Unix seconds, read as UTC without a zone; 0 when there is none."""
    try:
        at = datetime.fromisoformat(str(timestamp).strip())
    except ValueError:
        return 0.0
    return (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).timestamp()


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


def _publish_person(page: str, view: ViewFields, slug: str, display: str,
                    person: Person | None, learned: list[str], *,
                    answer: _Kept | None = None, write: bool) -> int:
    """Publish a person's view under the names they go by.

    With a person file, the file owns the names: the full name is
    canonical, the family's aliases come first, the variants documents
    used follow. Without one, the longest name documents used is
    canonical: in a household that uses both "Margaret" and "Maggie",
    the longer one is reliably the formal first name. Refreshed on every
    compile, so an alias added to the file reaches the page the next time.
    """
    if person is not None:
        canonical = person.name
        names = _distinct([*person.aliases, person.first_name, *learned], exclude=canonical)
    else:
        canonical = max(learned, key=len) if learned else display
        names = _distinct(learned, exclude=canonical)
    if not write:
        print(f"\n<!-- {slug}/about.md -->\n{page}")
        return 0
    view.title, view.names = canonical, {"canonical": canonical, "synonyms": names}
    return _publish(page, target_path=f"{slug}/about.md", view=view, answer=answer)


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
        fields = _parse_frontmatter(text, about)
        if "person" not in (fields.get(VIEW), fields.get("type")) or not _is_generated_page(text):
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
# one card each, `<shared>/correspondents/<slug>.md`, and no captures of
# their own. Documents route to the date-filed documents tree and merely
# name their correspondent in frontmatter, so a correspondent's view is a
# backlink list of the documents that reference it. Discovered from the
# `correspondent:` field the classifier writes on each document.
#
# The view is `about.md` in a folder named after the card, beside it, the
# way a person's and a topic's views have paths of their own: a view
# written over the card's path takes the card's `card_id` out of the
# brain, and every `/go/` link to the correspondent with it.

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


# A relative link on a card (`[Homer](../people/homer.md)`) is relative to
# the card's folder. The view is `about.md` one folder further down, so
# each such link gets one more `../` to reach the same file.
_CARD_RELATIVE_LINK = re.compile(r"\]\((?=\.\.?/)")


def _written_on(card_path: str) -> str:
    """What the card at `card_path` says below its title, as its view shows it.

    The household's own words about the correspondent, read from the card
    as the memory repo holds it, with its relative links pointed from the
    view's folder. "" for a card that is only its title, as the archivist
    declares one.
    """
    data = _card_file(card_path)
    if data is None:
        return ""
    body = _body_of(data.decode("utf-8", errors="ignore")).strip()
    title, _, rest = body.partition("\n")
    if title.startswith("# "):
        body = rest.strip()
    return _CARD_RELATIVE_LINK.sub("](../", body)


async def _generate_correspondent(
    slug_: str, canonical: str, index: list[dict], *,
    shared_bucket: str, write: bool,
) -> int:
    """Compose one correspondent's leaf page from its card and referencing documents.

    Deterministic: no LLM. Lives at
    `<shared_bucket>/correspondents/<slug>/about.md`, beside the card at
    `<shared_bucket>/correspondents/<slug>.md`, which stays as the memory
    repo holds it. The page shows what the card says below its title, then
    the documents. It is about that card and derived from it and the
    documents that name it.
    """
    entries = _correspondent_entries(index, canonical)
    if not entries:
        _err(f"no documents reference '{canonical}' — skipping")
        return 1

    card_path = f"{correspondents_dir(shared_bucket)}/{slug_}.md"
    page_dir = card_path.removesuffix(".md")
    body = "\n\n".join(part for part in (
        _written_on(card_path), _correspondent_body(entries, page_dir=page_dir)) if part)
    target_path = f"{page_dir}/about.md"

    if not write:
        print(f"\n<!-- {target_path} -->\n{body}")
        return 0
    _give_back_card(card_path)
    card = _card_source(card_path)
    return _publish(
        body, target_path=target_path,
        view=ViewFields("correspondent", title=canonical, about=card[0][0] if card else "",
                        sources=card + _sources(entries)),
    )


def _give_back_card(card_path: str) -> None:
    """Make the brain's file at `card_path` the card again where a view is there.

    A compile before correspondents' views had a path of their own wrote
    the view over the card's. The card is copied back from the memory
    repo's working copy, as the curator's mirror copies it. Without a card
    there the view goes all the same, and the mirror brings the card on
    its next pass. A card, or no file at all, is left as it is.
    """
    try:
        brain = _brain_dir()
    except RuntimeError:
        return
    with brain_lock.held(brain):
        stale = brain / card_path
        try:
            text = stale.read_text(encoding="utf-8")
        except OSError:
            return
        if not is_view(_parse_frontmatter(text)):
            return
        source = os.environ.get("MEMORY_VAULT_DIR")
        try:
            card = (Path(source) / card_path).read_bytes() if source else None
        except OSError:
            card = None
        try:
            if card is None:
                stale.unlink()
            else:
                stale.write_bytes(card)
        except OSError as e:
            _err(f"could not take the view off {card_path}: {e}")
            return
        _err(f"took the view off {card_path}, the card's path")


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
        folder = target_path.rsplit("/", 1)[0] + "/"
        _publish(content, target_path=target_path, view=ViewFields(
            "folder", title=f"{_KIND_LABEL[kind]}: {display}",
            sources=_sources([e for e in index if (e.get("rel") or "").startswith(folder)])))


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


def _renumber_citations(text: str, remap: dict[int, int], *,
                        drop_unknown: bool = False) -> str:
    """Rewrite every ``[N]``/``[N, M]`` citation through ``remap``.

    Numbers without a mapping pass through unchanged (evidence that
    vanished from the index; the prompt tells the model to drop those
    lines), or with ``drop_unknown`` are removed, a citation left empty
    with them. Single-pass over citation groups, so chained renumbering
    (1→2 while 2→3) cannot double-apply.
    """
    def _sub(m: re.Match) -> str:
        nums = [int(x) for x in re.split(r",\s*", m.group(2))]
        kept = [remap[n] for n in nums if n in remap] if drop_unknown \
            else [remap.get(n, n) for n in nums]
        return m.group(1) + "[" + ", ".join(map(str, kept)) + "]" if kept else ""

    return re.sub(r"(\s?)\[(\d+(?:,\s*\d+)*)\]", _sub, text)


def _splice_generated(existing: str, generated: str) -> str:
    """Place `generated` inside the regenerate region of `existing`.

    If the page already has the markers, only the content between them is
    replaced -- everything a human wrote outside survives. If it has no
    markers yet but has other content (a freshly seeded `index.md`), the
    region is appended below whatever is already there, so a seeded
    welcome stays on top of the generated overview. An empty file
    becomes the region alone. The frontmatter is not this function's:
    `_publish` writes the view's own above whatever this returns.
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

    The durable signal is the view's frontmatter (`stack.cards.is_view`).
    The splice marker remains a backstop for pages written before there
    was one. Source captures (notes, bookmarks, documents, email threads)
    carry only their own `<!-- mid:... -->` / section markers, never this
    one, so the predicate is the safe delete filter for `clean`: it
    cannot match a capture.
    """
    return is_view(_parse_frontmatter(content)) or _BEGIN in content


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


def _publish(page: str, *, target_path: str, view: ViewFields, answer: _Kept | None = None) -> int:
    """Write view `page` to `target_path` in the brain working copy.

    The previous file is read from disk and its body kept around the
    regenerated region: the seeded welcome above the home page. The
    frontmatter is the view's own (`view`), whatever was there before.
    `answer` is the model's answer the view keeps (`_Kept`); without
    one, the answer the page keeps goes too. The write is to disk only:
    the curator's commit and push carry it to Forgejo and on to Quartz,
    so memory (source) is never touched.

    A file there that is no view is left alone: a person's own page
    written by hand into the memory repo stays theirs, and a card is
    never written over.
    """
    try:
        page_path = _brain_dir() / target_path
    except RuntimeError as e:
        _err(f"{e} — refusing to write generated wiki pages")
        return 1
    with brain_lock.held(_brain_dir()):
        return _write_view(page, page_path, target_path, view, answer)


def _write_view(page: str, page_path: Path, target_path: str, view: ViewFields,
                answer: _Kept | None) -> int:
    """`_publish`'s read, merge and write, with the brain's writer lock held."""
    try:
        prior = page_path.read_text(encoding="utf-8")
    except OSError:
        prior = ""
    if prior and not _is_generated_page(prior):
        _err(f"source page {target_path} is no view, skipped")
        return 0
    body = _with_answer(_splice_generated(_body_of(prior), page), answer)
    merged = f"---\n{dump(view.frontmatter())}\n---\n\n{body.lstrip()}"

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


def _body_of(text: str) -> str:
    """`text` without its frontmatter block."""
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    return text if end < 0 else text[end + len("\n---\n"):]


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
