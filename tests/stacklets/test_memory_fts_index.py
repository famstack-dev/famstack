"""The ranked search index behind `stack memory search`.

Today's engine is a regex walk: it matches lines and sorts what it finds
by date, so "who was worried about bugs on the trip" reaches the right
page only when the family happened to write those words. This index is
the ranked alternative -- one SQLite FTS5 table over the vault, BM25 for
the order, the frontmatter promoted to columns that can be weighted and
filtered instead of stripped as noise.

These tests drive it the way the CLI will: build an index over a vault
directory, hand it the 2-4 keywords the model produces, read the hits
back. They pin the promises the regex engine already makes (frontmatter
field names are not content, persons/tags/scopes narrow the same way)
plus the three this index adds: diacritics fold for a German-speaking
family, better matches come first, and every hit says which of the
query's keywords it actually contains -- the raw material for deciding
whether an answer is in the vault at all.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory"))

import fts_index  # noqa: E402


# ── fixtures ────────────────────────────────────────────────────────────

def page(vault: Path, rel: str, *, title: str, body: str,
         persons: list[str] | None = None,
         tags: list[str] | None = None,
         date: str = "2026-09-01") -> Path:
    """Write one vault page, frontmatter and all, the way the archivist does."""
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = [f"title: {title}", f"date: {date}", "type: note"]
    if persons:
        fm.append("persons:")
        fm += [f"  - {p}" for p in persons]
    if tags:
        fm.append("tags:")
        fm += [f"  - {t}" for t in tags]
    path.write_text("---\n" + "\n".join(fm) + "\n---\n\n" + body + "\n",
                    encoding="utf-8")
    return path


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """A small vault with the shapes the engine has to tell apart.

    Two pages mention camping; only one is about it. One page is
    German, one sits in a personal bucket, one belongs to nobody in
    particular. That is enough to ask ranking, folding and scope
    questions of.
    """
    v = tmp_path / "vault"
    page(v, "family/camping/about.md",
         title="Camping trip",
         body="The tent leaks. We bought a new one before the trip.",
         persons=["homer", "bart"], tags=["camping"])
    page(v, "family/groceries/todos.md",
         title="Shopping list",
         body="- [ ] milk\n- [ ] batteries for the camping lamp",
         persons=["marge"], tags=["groceries"])
    page(v, "family/birthday/about.md",
         title="Omas Geburtstagsfeier",
         body="Wir backen einen Kuchen mit Käse und grünem Zuckerguss.",
         persons=["marge", "lisa"], tags=["birthday"])
    page(v, "marge/notes/gift-ideas.md",
         title="Gift ideas",
         body="A telescope for Lisa. Do not tell her.",
         persons=["marge"], tags=["private"])
    return v


@pytest.fixture
def index(vault: Path, tmp_path: Path) -> Path:
    db = tmp_path / "vault-index.sqlite3"
    fts_index.build_index(vault, db)
    return db


def rels(hits) -> list[str]:
    return [h.rel for h in hits]


# ── what the index promises ─────────────────────────────────────────────

def test_a_word_from_a_page_body_finds_that_page(index):
    """The floor: anything written in the vault can be searched for."""
    assert rels(fts_index.search(index, ["telescope"])) == [
        "marge/notes/gift-ideas.md"
    ]


def test_frontmatter_field_names_are_not_searchable_content(index):
    """`date` is structure, not something the family wrote.

    The regex engine strips frontmatter for exactly this reason: a
    query for "date" or "tags" would otherwise match every page in the
    vault. Promoting the *values* to columns must not promote the
    *keys* with them.
    """
    assert fts_index.search(index, ["date"]) == []
    assert fts_index.search(index, ["type"]) == []


def test_frontmatter_values_are_searchable(index):
    """Who and what a page is about is part of the page.

    The regex engine throws this away, so "groceries" only finds pages
    that happen to say the word in prose. Here the persons and tags
    columns are indexed, which is the upgrade.
    """
    hits = fts_index.search(index, ["groceries"])
    assert "family/groceries/todos.md" in rels(hits)


def test_german_diacritics_fold_to_their_base_letter(index):
    """A family that types "Kase" on a phone keyboard still finds "Käse".

    `unicode61 remove_diacritics 2` is the tokenizer setting that does
    this, and it has to work in both directions -- the query may carry
    the umlaut and the page may not, or the reverse.
    """
    assert rels(fts_index.search(index, ["Kase"])) == [
        "family/birthday/about.md"
    ]
    assert rels(fts_index.search(index, ["grunem"])) == [
        "family/birthday/about.md"
    ]
    assert rels(fts_index.search(index, ["Käse"])) == [
        "family/birthday/about.md"
    ]


def test_a_compound_reaches_its_base_word(index):
    """"Geburtstag" has to reach a page that only says "Geburtstagsfeier".

    German compounds are the reason a plain token match is not enough
    for this family. Prefix matching covers the direction that matters
    most -- the short word the family asks with, against the long word
    the page happens to use.
    """
    assert rels(fts_index.search(index, ["Geburtstag"])) == [
        "family/birthday/about.md"
    ]


def test_the_back_half_of_a_compound_needs_the_substring_index(index):
    """The half prefix matching cannot reach.

    German puts the word the family asks with at either end of a
    compound. "Geburtstag" finds "Geburtstagsfeier" because the page
    word starts with the query word; "Feier" does not, because nothing
    in a word index starts there. The regex engine gets this case right
    by accident -- it matches substrings -- so shipping the word index
    alone would be a regression for a German-speaking family.
    """
    assert fts_index.search(index, ["Feier"]) == []
    assert rels(fts_index.search(index, ["Feier"], substrings=True)) == [
        "family/birthday/about.md"
    ]


def test_the_substring_index_still_ranks_the_right_page_first(index):
    """Fusing a second ranking must not scramble the first one.

    A substring index matches inside words, so it finds more and means
    less. If switching it on cost the ordering the word index gets
    right, it would be trading one failure for another.
    """
    hits = fts_index.search(index, ["camping"], substrings=True)
    assert rels(hits)[0] == "family/camping/about.md"


def test_the_page_a_query_is_about_outranks_a_passing_mention(index):
    """This is the whole point of the change.

    Two pages contain "camping": the trip page and a shopping list that
    needs batteries for a camping lamp. The regex engine returns both
    in date order and leaves the choosing to the model. BM25 puts the
    trip first, because the word carries the page's title and tag
    rather than one line of a list.
    """
    hits = fts_index.search(index, ["camping"])
    assert rels(hits)[0] == "family/camping/about.md"
    assert "family/groceries/todos.md" in rels(hits)


def test_a_hit_says_which_of_the_query_keywords_it_matched(index):
    """The signal for "is the answer even in here".

    A question whose keywords all land on one page is a different
    situation from one where the best hit only matched a single generic
    word, and the caller cannot tell those apart from a BM25 score
    alone -- the score is only comparable inside one result set, so it
    says nothing about whether the fact is in the vault. Each hit
    reports its own coverage so that decision can be made on evidence.
    """
    hits = {h.rel: h for h in
            fts_index.search(index, ["camping", "tent", "telescope"])}
    assert set(hits["family/camping/about.md"].matched) == {"camping", "tent"}
    assert set(hits["marge/notes/gift-ideas.md"].matched) == {"telescope"}
    assert set(hits["family/groceries/todos.md"].matched) == {"camping"}


def test_model_supplied_keywords_are_never_read_as_query_syntax(index):
    """The keywords come from a model, so they are untrusted input.

    FTS5 has an operator language -- `NOT`, `OR`, `*`, quotes, column
    filters. A model that answers `C++` or `NOT` or a stray quote must
    produce a search, not a syntax error and not an inverted query.
    """
    for hostile in (["NOT"], ['tent" OR "x'], ["C++"], ["*"], ["-"],
                    ["body:tent"], ["("]):
        fts_index.search(index, hostile)  # must not raise

    # Read as the operator it looks like, `NOT tent` would exclude the
    # one page the family is asking about. Read as a word, it is just
    # another term, and the tent page still comes back.
    assert "family/camping/about.md" in rels(
        fts_index.search(index, ["NOT", "tent"]))
    # `title:tent` is a column filter in FTS5's grammar, and as one it
    # finds nothing: no page is titled "tent". Quoted, it is two words,
    # one of which is on the camping page.
    assert "family/camping/about.md" in rels(
        fts_index.search(index, ["title:tent"]))


def test_a_query_that_matches_nothing_returns_no_hits(index):
    """No results is an answer, not a failure."""
    assert fts_index.search(index, ["snowmobile"]) == []


def test_filters_narrow_the_same_way_the_regex_engine_does(index):
    """Parity with `search_memory`, because callers already rely on it.

    Persons and tags are OR within an axis and AND across axes. Scopes
    are path prefixes, where `None` means the whole vault and an empty
    list means nothing at all -- that is how the archivist denies an
    unknown sender access to personal buckets.
    """
    assert rels(fts_index.search(index, ["telescope"], persons=["homer"])) == []
    assert rels(fts_index.search(index, ["telescope"], persons=["marge"])) == [
        "marge/notes/gift-ideas.md"
    ]
    assert rels(fts_index.search(index, ["telescope"], tags=["camping"])) == []

    scoped = rels(fts_index.search(index, ["milk", "tent"], scopes=["family"]))
    assert scoped and all(r.startswith("family/") for r in scoped)
    assert fts_index.search(index, ["telescope"], scopes=[]) == []


def test_an_edited_page_is_reindexed_and_a_deleted_one_disappears(vault, index):
    """The index is a cache over a git checkout that moves under it.

    A rebuild has to pick up an edit, drop a removed page, and add a new
    one, without the stale text surviving in the index -- a search that
    still returns yesterday's sentence is worse than no index.
    """
    (vault / "marge/notes/gift-ideas.md").write_text(
        "---\ntitle: Gift ideas\ndate: 2026-09-02\n---\n\nA microscope instead.\n",
        encoding="utf-8")
    (vault / "family/groceries/todos.md").unlink()
    page(vault, "bart/notes/skateboard.md",
         title="Skateboard", body="The deck cracked.", persons=["bart"])

    fts_index.build_index(vault, index)

    assert fts_index.search(index, ["telescope"]) == []
    assert rels(fts_index.search(index, ["microscope"])) == [
        "marge/notes/gift-ideas.md"
    ]
    assert fts_index.search(index, ["milk"]) == []
    assert rels(fts_index.search(index, ["skateboard"])) == [
        "bart/notes/skateboard.md"
    ]


# ── staying current ─────────────────────────────────────────────────────

@pytest.fixture
def git_vault(vault: Path, git_commit) -> Path:
    """The vault as what it actually is in production: a git checkout.

    The memory stacklet keeps a clone of the Forgejo vault repo, and
    every change to it arrives as a commit -- `update_memory` writes
    through Forgejo and fast-forwards this copy. Nothing edits these
    files in place, which is the fact the freshness check below leans
    on.
    """
    subprocess.run(["git", "init", "-q", "-b", "main", str(vault)], check=True)
    for key, value in (("user.email", "test@famstack.local"),
                       ("user.name", "Test"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "-C", str(vault), "config", key, value],
                       check=True)
    git_commit(vault, "family/about.md",
               "---\ntitle: Family\ndate: 2026-09-01\n---\n\nThe household.\n",
               "seed")
    return vault


def test_a_page_that_arrives_in_a_commit_becomes_searchable(
        git_vault, tmp_path, git_commit):
    """The case that matters: somebody wrote something, and we can find it.

    A pull is how every change reaches this checkout, so "the index is
    current" means "the index has caught up with HEAD".
    """
    db = tmp_path / "index.sqlite3"
    fts_index.build_index(git_vault, db)
    assert fts_index.search(db, ["kayak"]) == []

    git_commit(git_vault, "family/camping/kayak.md",
               "---\ntitle: Kayak\ndate: 2026-09-14\n---\n\nThe kayak leaks.\n",
               "add kayak note")
    fts_index.build_index(git_vault, db)

    assert rels(fts_index.search(db, ["kayak"])) == ["family/camping/kayak.md"]


def test_an_unmoved_head_costs_nothing_to_confirm(git_vault, tmp_path):
    """A search must not pay for a scan when nothing can have changed.

    Statting every file to learn that none of them moved is work
    proportional to the vault, on every single search. The checkout's
    HEAD answers the same question in constant time, and it is a sound
    substitute precisely because nothing writes into this tree outside
    git: no commit, no change.

    The edit below is therefore deliberately invisible. That is the
    trade: a hand-edited working copy is not picked up until something
    commits. In production nothing hand-edits it, and `--vault`
    overrides are not checkouts at all, so they keep scanning.
    """
    db = tmp_path / "index.sqlite3"
    fts_index.build_index(git_vault, db)

    (git_vault / "family/camping/about.md").write_text(
        "---\ntitle: Camping trip\ndate: 2026-09-01\n---\n\nA kayak now.\n",
        encoding="utf-8")

    stats = fts_index.build_index(git_vault, db)
    assert (stats.added, stats.updated, stats.deleted) == (0, 0, 0)
    assert fts_index.search(db, ["kayak"]) == []


def test_a_vault_that_is_not_a_checkout_is_always_rescanned(vault, tmp_path):
    """The fallback the lab and `--vault` overrides run on.

    A directory that is not a clone has no HEAD to compare, so there is
    nothing to short-circuit on and the scan is the only way to know.
    """
    db = tmp_path / "index.sqlite3"
    fts_index.build_index(vault, db)

    (vault / "family/camping/about.md").write_text(
        "---\ntitle: Camping trip\ndate: 2026-09-01\n---\n\nA kayak now.\n",
        encoding="utf-8")

    stats = fts_index.build_index(vault, db)
    assert stats.updated == 1
    assert rels(fts_index.search(db, ["kayak"])) == ["family/camping/about.md"]


def test_one_unreadable_path_does_not_cost_every_other_result(vault, tmp_path):
    """The vault moves underneath a scan, so a bad path must not be fatal.

    A sync can delete a file between the moment the walk lists it and
    the moment it is read. One page that cannot be resolved is a page
    missing from the results; it is not a reason for the family to get
    an error instead of the other twelve.
    """
    (vault / "family/dangling.md").symlink_to(vault / "nowhere.md")

    db = tmp_path / "index.sqlite3"
    stats = fts_index.build_index(vault, db)

    assert stats.total == 4
    assert rels(fts_index.search(db, ["telescope"])) == [
        "marge/notes/gift-ideas.md"
    ]


def test_a_second_process_indexing_does_not_fail_the_search(vault, tmp_path):
    """The CLI and the archivist both reconcile, sometimes at once.

    SQLite serialises writers, and its default is to give up the
    instant the file is busy. Without a wait, one search landing while
    the other is mid-reconcile is an error rather than a short pause.
    """
    db = tmp_path / "index.sqlite3"
    fts_index.build_index(vault, db)

    holder = sqlite3.connect(db)
    holder.execute("BEGIN EXCLUSIVE")
    done: list[object] = []

    def reconcile() -> None:
        page(vault, "bart/notes/skateboard.md",
             title="Skateboard", body="The deck cracked.")
        done.append(fts_index.build_index(vault, db))

    worker = threading.Thread(target=reconcile)
    worker.start()
    time.sleep(0.2)
    holder.rollback()
    holder.close()
    worker.join(timeout=10)

    assert done, "the second indexer gave up instead of waiting"
    assert rels(fts_index.search(db, ["skateboard"])) == [
        "bart/notes/skateboard.md"
    ]


def test_an_unchanged_vault_costs_no_rewrites(vault, index):
    """Rebuild runs on every search, so a no-change scan has to be cheap.

    The stats say what the scan did. Nothing changed, so nothing should
    have been written -- if this reports work, the change detection is
    broken and every search pays a full reindex.
    """
    stats = fts_index.build_index(vault, index)
    assert (stats.added, stats.updated, stats.deleted) == (0, 0, 0)
    assert stats.total == 4
