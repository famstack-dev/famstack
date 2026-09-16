"""A ranked full-text index over the memory vault.

`search_memory` in `lib.py` walks every `*.md` in the vault with a
regex and sorts the matches by frontmatter date. That has no notion of
a better match: a shopping list that needs "batteries for the camping
lamp" and the page about the camping trip come back in whatever order
they were written, and the model upstream has to read both. It also
strips frontmatter before matching, so who a page is about and what it
is tagged with are invisible to the query, and it is byte-literal, so a
family typing "Kase" never reaches "Käse".

This module is the ranked alternative. One SQLite file next to the
vault holds the pages and an FTS5 index over them; BM25 orders the
results; the tokenizer folds diacritics. Frontmatter becomes columns,
which makes persons and tags both searchable and weightable instead of
noise to be stripped.

The index is a derived cache. It sits beside the vault working copy,
is never committed, and can be deleted at any time -- `build_index`
rebuilds whatever is missing and reconciles whatever moved, so the
authoritative answer is always the git checkout.

    build_index(vault, db) -> Stats     reconcile the index with the vault
    search(db, keywords, ...) -> [Hit]  rank pages against model keywords

Two design points worth knowing before reading the code:

**Keywords, not a regex.** The caller passes the 2-4 words the model
produced, not a pattern. They are untrusted text, so every token is
quoted before it reaches FTS5 -- otherwise a model answering `NOT` or
`C++` would either invert the query or fail to parse it.

**Every hit reports its own coverage.** `Hit.matched` names which of
the query's keywords that page actually contains. A BM25 score is only
comparable within one result set, so it cannot answer "is this fact in
the vault at all"; coverage can, and that is the input a caller needs
to decide between answering and saying it did not find anything.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import (  # noqa: E402
    _fm_list, _norm_tag, body_only, strip_diacritics, vault_local_head,
)
from stack.frontmatter import parse as parse_frontmatter  # noqa: E402


# Prefix matching is what lets "Geburtstag" reach "Geburtstagsfeier",
# but on a two-letter token it degenerates into matching most of the
# vault. Below this length a token has to match a whole word.
MIN_PREFIX_LEN = 3

# BM25 column weights: title, persons, tags, body. A word in the title
# or in the tags says the page is *about* that thing; the same word in
# the body may be a passing mention. Persons sits with tags because a
# name in the frontmatter is a fact about the page, not prose.
WEIGHTS = (8.0, 4.0, 4.0, 1.0)


@dataclass(frozen=True)
class Hit:
    """One ranked page, in the shape the CLI formatter already prints."""

    rel: str
    title: str
    date: str
    persons: List[str]
    tags: List[str]
    excerpt: str
    score: float
    matched: tuple[str, ...]


@dataclass(frozen=True)
class Stats:
    """What one reconcile pass did, for callers that log or test it."""

    added: int
    updated: int
    deleted: int
    total: int


# ── text normalisation ──────────────────────────────────────────────────

def fold(text: str) -> str:
    """Lower-case and strip combining marks, as `remove_diacritics 2` does.

    The tokenizer folds the indexed side; this folds the Python side so
    that coverage (`Hit.matched`) agrees with what the index matched.
    The mark-stripping half is `lib.strip_diacritics`, shared with the
    regex engine so both answer "Kase" and "Käse" the same way; the
    case half is safe to add here because these are tokens rather than
    a pattern.
    """
    return strip_diacritics(text).lower()


def tokens(text: str) -> List[str]:
    """Split folded text into the alphanumeric runs unicode61 would produce."""
    return [t for t in re.split(r"[^0-9a-z]+", fold(text)) if t]


# ── schema ──────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS docs(
  id INTEGER PRIMARY KEY,
  path TEXT UNIQUE,
  title TEXT,
  persons TEXT,
  tags TEXT,
  date TEXT,
  mtime REAL,
  sha1 TEXT,
  body TEXT,
  persons_norm TEXT,
  tags_norm TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
  title, persons, tags, body,
  content='docs', content_rowid='id',
  tokenize="unicode61 remove_diacritics 2"
);

CREATE VIRTUAL TABLE IF NOT EXISTS tri USING fts5(
  title, persons, tags, body,
  content='docs', content_rowid='id',
  tokenize="trigram remove_diacritics 1"
);
"""

# The word index and the substring index, in the order results are fused.
INDEXES = ("fts", "tri")

# Reciprocal rank fusion constant. 60 is the value from the original
# paper and the one the retrieval handover names for tier 2; nothing
# here is tuned to this corpus.
RRF_K = 60


def _connect(db: Path) -> sqlite3.Connection:
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    # Two processes reconcile this file: the CLI on the host and the
    # archivist in its container. SQLite serialises writers and, left
    # alone, gives up the instant the file is busy -- which would turn
    # "the other one is mid-reconcile" into an error rather than a
    # short wait.
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.executescript(SCHEMA)
    return conn


# ── indexing ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _Doc:
    """A vault page reduced to the columns the index stores."""

    path: str
    title: str
    persons: str
    tags: str
    date: str
    mtime: float
    sha1: str
    body: str
    persons_norm: str
    tags_norm: str


def _read(md_path: Path, rel: str, mtime: float) -> Optional[_Doc]:
    """Turn one file into a row, or None when it cannot be read.

    An unreadable page is skipped rather than fatal: the vault is a
    working copy that a sync may be rewriting underneath us, and one
    bad file must not cost the family every other search result.
    """
    try:
        text = md_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    fm = parse_frontmatter(text) if text.startswith("---\n") else {}
    persons = _fm_list(fm, "persons")
    tags = _fm_list(fm, "tags")
    body = body_only(text)
    return _Doc(
        path=rel,
        title=str(fm.get("title") or md_path.stem),
        persons=" ".join(persons),
        tags=" ".join(tags),
        date=str(fm.get("date") or ""),
        mtime=mtime,
        sha1=hashlib.sha1(text.encode("utf-8")).hexdigest(),
        body=body,
        # Wrapped in pipes so a SQL `LIKE '%|homer|%'` cannot match
        # "homerette" -- the same reason `search_memory` normalises
        # scope prefixes with a trailing slash.
        persons_norm="|" + "|".join(p.lower() for p in persons) + "|",
        tags_norm="|" + "|".join(_norm_tag(t) for t in tags) + "|",
    )


def _fts_delete(conn: sqlite3.Connection, row: sqlite3.Row) -> None:
    """Retract a row from both indexes using its *old* column values.

    External-content FTS5 keeps no copy of the text, so it cannot work
    out what to remove on its own. Handing it the current values is the
    documented protocol; handing it the new ones would leave the old
    terms in the index, and the page would keep answering searches for
    a sentence it no longer contains.
    """
    for table in INDEXES:
        conn.execute(
            f"INSERT INTO {table}({table}, rowid, title, persons, tags, body) "
            "VALUES('delete', ?, ?, ?, ?, ?)",
            (row["id"], row["title"], row["persons"], row["tags"],
             row["body"]),
        )


def _fts_insert(conn: sqlite3.Connection, doc_id: int, doc: _Doc) -> None:
    for table in INDEXES:
        conn.execute(
            f"INSERT INTO {table}(rowid, title, persons, tags, body)"
            " VALUES(?,?,?,?,?)",
            (doc_id, doc.title, doc.persons, doc.tags, doc.body),
        )


def build_index(vault: Path, db: Path) -> Stats:
    """Reconcile the index with the vault, and report what changed.

    This runs before every search, so the cost when nothing changed is
    the cost that matters. It is answered in two steps.

    **The checkout's HEAD, when there is one.** The vault is a clone,
    and every change to it arrives as a commit: `update_memory` writes
    through Forgejo and fast-forwards this copy, and nothing else
    writes these files at all. So a HEAD that has not moved is proof
    that no page has changed, in constant time. Measured on a vault of
    8000 pages: 14 ms to read HEAD against 350 ms to stat every file.

    **A scan, when there is no HEAD to trust.** A `--vault` override or
    a fixture directory is not a clone, so there is nothing to
    short-circuit on. Then mtime decides, and a page whose mtime moved
    is hashed before it is re-indexed, because a checkout rewrites
    mtimes on files whose content is identical.

    The HEAD is stored in the same transaction as the rows it
    describes. A reconcile that dies halfway leaves both behind, and
    the next search does the work again -- the index is never half
    updated while claiming to be current.

    What this cannot do is make the *clone* current; that is
    `refresh_vault_if_stale`, and the caller's concern. When the remote
    is unreachable the index faithfully reflects a stale vault, which
    is the existing contract for reads and not something an index can
    fix.
    """
    conn = _connect(db)
    try:
        head = vault_local_head(vault)
        if head is not None:
            stored = conn.execute(
                "SELECT value FROM meta WHERE key = 'head'").fetchone()
            if stored is not None and stored["value"] == head:
                total = conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
                return Stats(added=0, updated=0, deleted=0, total=int(total))

        known = {
            row["path"]: row
            for row in conn.execute(
                "SELECT id, path, title, persons, tags, body, mtime, sha1 "
                "FROM docs"
            )
        }
        added = updated = 0
        seen: set[str] = set()

        for md_path in sorted(vault.rglob("*.md")):
            try:
                if not md_path.is_file():
                    continue
                mtime = md_path.stat().st_mtime
            except OSError:
                # A sync can delete a page between the walk listing it
                # and this reading it. One page missing from the
                # results beats an error instead of the other results.
                continue
            rel = str(md_path.relative_to(vault))
            seen.add(rel)
            row = known.get(rel)
            if row is not None and row["mtime"] == mtime:
                continue

            doc = _read(md_path, rel, mtime)
            if doc is None:
                continue
            if row is None:
                cur = conn.execute(
                    "INSERT INTO docs(path, title, persons, tags, date, mtime,"
                    " sha1, body, persons_norm, tags_norm)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?) RETURNING id",
                    (doc.path, doc.title, doc.persons, doc.tags, doc.date,
                     doc.mtime, doc.sha1, doc.body, doc.persons_norm,
                     doc.tags_norm),
                )
                _fts_insert(conn, int(cur.fetchone()[0]), doc)
                added += 1
                continue

            if row["sha1"] == doc.sha1:
                # Same bytes, new mtime. Store the timestamp so the next
                # scan short-circuits, but leave the index alone.
                conn.execute("UPDATE docs SET mtime=? WHERE id=?",
                             (mtime, row["id"]))
                continue

            _fts_delete(conn, row)
            conn.execute(
                "UPDATE docs SET title=?, persons=?, tags=?, date=?, mtime=?,"
                " sha1=?, body=?, persons_norm=?, tags_norm=? WHERE id=?",
                (doc.title, doc.persons, doc.tags, doc.date, doc.mtime,
                 doc.sha1, doc.body, doc.persons_norm, doc.tags_norm,
                 row["id"]),
            )
            _fts_insert(conn, int(row["id"]), doc)
            updated += 1

        gone = [row for path, row in known.items() if path not in seen]
        for row in gone:
            _fts_delete(conn, row)
            conn.execute("DELETE FROM docs WHERE id=?", (row["id"],))

        if head is not None:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES('head', ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (head,))
        conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        return Stats(added=added, updated=updated, deleted=len(gone),
                     total=int(total))
    finally:
        conn.close()


# ── querying ────────────────────────────────────────────────────────────

def _match_expression(keywords: Sequence[str]) -> str:
    """Render model keywords as an FTS5 MATCH expression, or "" for none.

    Every token is wrapped in double quotes before it is joined. That is
    not cosmetic: unquoted, a model's `NOT`, `OR`, `*` or stray `"`
    would be read as the query language rather than as words, which
    turns a bad keyword into a silently inverted search or a syntax
    error. Quoted, the worst case is a word that matches nothing.
    """
    parts: List[str] = []
    for keyword in keywords:
        for token in tokens(keyword):
            star = "*" if len(token) >= MIN_PREFIX_LEN else ""
            parts.append(f'"{token}"{star}')
    return " OR ".join(parts)


def _trigram_expression(keywords: Sequence[str]) -> str:
    """The same keywords as substring searches, for the trigram index.

    No trailing `*`: a trigram index already matches anywhere inside a
    word, which is the point of consulting it. Tokens under three
    characters are dropped rather than passed through, because a
    trigram index cannot match them at all and they would only cost a
    scan.
    """
    parts: List[str] = []
    for keyword in keywords:
        for token in tokens(keyword):
            if len(token) >= MIN_PREFIX_LEN:
                parts.append(f'"{token}"')
    return " OR ".join(parts)


def covers(keyword: str, doc_tokens: set[str]) -> bool:
    """Does this page contain the keyword, under the same rules as MATCH?

    Public because coverage has to be computable for pages this module
    did not rank -- the retrieval lab scores the regex engine's hits the
    same way, and a second implementation of the rule would make that
    comparison meaningless.
    """
    kw_tokens = tokens(keyword)
    if not kw_tokens:
        return False
    for token in kw_tokens:
        if len(token) >= MIN_PREFIX_LEN:
            if any(d.startswith(token) for d in doc_tokens):
                return True
        elif token in doc_tokens:
            return True
    return False


def _scope_clause(scopes: Sequence[str]) -> tuple[str, List[str]]:
    """`rel LIKE 'family/%'` for each prefix, OR-combined.

    Prefixes gain a trailing slash first, so `marge` cannot reach into
    `margery/` -- same rule as `search_memory`, kept identical because
    the archivist relies on it to keep an unknown sender out of the
    personal buckets.
    """
    prefixes = [s if s.endswith("/") else f"{s}/" for s in scopes]
    clause = " OR ".join("docs.path LIKE ?" for _ in prefixes)
    return f"({clause})", [f"{p}%" for p in prefixes]


def _axis_clause(column: str, values: Iterable[str]) -> tuple[str, List[str]]:
    """OR within one frontmatter axis, against the pipe-wrapped column."""
    wanted = list(values)
    clause = " OR ".join(f"docs.{column} LIKE ?" for _ in wanted)
    return f"({clause})", [f"%|{v}|%" for v in wanted]


def _filters(
    persons: Optional[Sequence[str]],
    tags: Optional[Sequence[str]],
    scopes: Optional[Sequence[str]],
) -> tuple[List[str], List[object]]:
    """The frontmatter and scope narrowing, shared by both indexes."""
    where: List[str] = []
    params: List[object] = []
    if persons:
        clause, values = _axis_clause(
            "persons_norm", (p.lower() for p in persons))
        where.append(clause)
        params += values
    if tags:
        clause, values = _axis_clause("tags_norm", (_norm_tag(t) for t in tags))
        where.append(clause)
        params += values
    if scopes:
        clause, values = _scope_clause(scopes)
        where.append(clause)
        params += values
    return where, params


def _ranked(conn: sqlite3.Connection, table: str, expression: str,
            filters: tuple[List[str], List[object]],
            limit: int) -> List[sqlite3.Row]:
    """One index's best `limit` rows for this expression, best first."""
    where, filter_params = filters
    sql = (
        "SELECT docs.path, docs.title, docs.date, docs.persons, docs.tags,"
        " docs.body,"
        f" snippet({table}, 3, '', '', ' … ', 16) AS excerpt,"
        f" bm25({table}, ?, ?, ?, ?) AS score"
        f" FROM {table} JOIN docs ON docs.id = {table}.rowid"
        f" WHERE {' AND '.join([f'{table} MATCH ?', *where])}"
        " ORDER BY score LIMIT ?"
    )
    try:
        return conn.execute(
            sql, [*WEIGHTS, expression, *filter_params, limit]).fetchall()
    except sqlite3.OperationalError:
        # A MATCH expression this module built should always parse.
        # Staying quiet here keeps the failure mode identical to the
        # regex engine's, which returns [] on a pattern it cannot
        # compile rather than taking the caller down.
        return []


def _fuse(lists: Sequence[Sequence[sqlite3.Row]],
          limit: int) -> List[tuple[sqlite3.Row, float]]:
    """Reciprocal rank fusion over one or more ranked lists.

    RRF combines rankings without needing their scores to mean the same
    thing, which matters here: a BM25 over words and a BM25 over
    character trigrams are not on one scale and averaging them would be
    arithmetic on unrelated numbers. Each list contributes `1/(k+rank)`,
    so a page both indexes like beats a page only one of them found.
    """
    scores: dict[str, float] = {}
    rows: dict[str, sqlite3.Row] = {}
    for ranking in lists:
        for position, row in enumerate(ranking, start=1):
            path = row["path"]
            scores[path] = scores.get(path, 0.0) + 1.0 / (RRF_K + position)
            rows.setdefault(path, row)
    ordered = sorted(scores.items(), key=lambda kv: -kv[1])[:limit]
    return [(rows[path], score) for path, score in ordered]


def search(
    db: Path,
    keywords: Sequence[str],
    persons: Optional[Sequence[str]] = None,
    tags: Optional[Sequence[str]] = None,
    scopes: Optional[Sequence[str]] = None,
    limit: int = 20,
    substrings: bool = False,
) -> List[Hit]:
    """Rank the vault against `keywords`, best first.

    The filters carry the same contract as `search_memory`: persons and
    tags are OR within an axis and AND across axes, `scopes=None` means
    the whole vault and `scopes=[]` means nothing at all. An empty
    keyword list returns nothing rather than everything -- a caller who
    got no keywords out of the model has no query, and returning the
    newest twenty pages would look like an answer.

    `substrings` also consults the trigram index and fuses the two
    rankings. The word index matches from the front of a word, which
    reaches "Geburtstagsfeier" from "Geburtstag" but not from "Feier";
    German puts the noun the family asks with at either end of a
    compound, so the substring index covers the half prefixes cannot.
    It costs a second query and a larger index, which is why it is a
    parameter rather than the default until the measurement says
    otherwise.
    """
    if scopes is not None and not scopes:
        return []
    expression = _match_expression(keywords)
    if not expression:
        return []

    filters = _filters(persons, tags, scopes)
    conn = _connect(db)
    try:
        word_hits = _ranked(conn, "fts", expression, filters, limit)
        if not substrings:
            scored = [(row, -float(row["score"])) for row in word_hits]
        else:
            tri_expression = _trigram_expression(keywords)
            tri_hits = (_ranked(conn, "tri", tri_expression, filters, limit)
                        if tri_expression else [])
            scored = _fuse([word_hits, tri_hits], limit)
    finally:
        conn.close()

    hits: List[Hit] = []
    for row, score in scored:
        doc_tokens = set(tokens(
            " ".join((row["title"], row["persons"], row["tags"], row["body"]))))
        hits.append(Hit(
            rel=row["path"],
            title=row["title"],
            date=row["date"] or "",
            persons=row["persons"].split() if row["persons"] else [],
            tags=row["tags"].split() if row["tags"] else [],
            excerpt=(row["excerpt"] or "").strip(),
            # Higher is better either way: bm25() is negative and was
            # flipped, RRF is already a positive sum. The two are not
            # on one scale, so a score is only comparable inside one
            # result set -- which is why coverage exists below.
            score=score,
            matched=tuple(k for k in keywords if covers(k, doc_tokens)),
        ))
    return hits
