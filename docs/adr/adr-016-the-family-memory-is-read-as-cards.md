# ADR-016: The Family Memory Is Read as Cards, Through One Interface

## Status
Proposed.

## Context
Everything the family keeps ends up as a file in the memory vault: captures,
diary entries, lists, people, topics, emails, and a card for every document
(the document itself stays in Paperless). Each file stands for one thing and
has a lasting id (`stack.cards`); together with the links between them they
are a graph of cards.

How that memory is read grew one command at a time. Today eight commands
read it, each in its own way:

| Command | Reads |
|---|---|
| `memory search` | full text over the brain, ranked by rare words |
| `memory ask` | `search`, then one model call over the hits |
| `memory find` / `card` / `links` | the cards as a graph: words, recency, links, git history |
| `memory topic` | a topic's compiled page |
| `memory person show` | a person's declared file |
| `memory history` | the vault's git log, by person and topic |
| `memory lookup` | a term against the ontology |

And three readers outside the CLI: core's tools server (`/go` links, through
`stack.cards.find_card`), the wiki (Quartz over the brain) and each agent
(the nanobot agent through its memory tool, file reads and these commands).

Four things follow from that:

- **Ids are defined twice, and some kinds have none.** `stack.cards` holds the
  id fields for captures and diary entries; the graph derived them again and
  added documents, people, topics and lists. Notes written by hand and
  correspondents have no lasting id: they are named by their file, which
  changes when the file is renamed.
- **The ontology is not used for reading.** `ontology.toml` names every topic
  and document type in German and English, with synonyms and keywords
  ("Versicherung", "insurance", "policy", "coverage"). `find` and `search`
  match words only, so a German question misses an English card: of 13
  German questions, `find` answered 5; with the same words in both languages,
  12 (`tools/agent-lab/retrieval_bench.py`). An agent can search in both
  languages; nothing makes it.
- **Facts are not readable.** `facts.toml` holds the household's rules,
  facts, habits and preferences. No read command shows them.
- **Who may read what is decided by one reader only.** A note filed in a room
  with one person goes into that person's bucket, one filed with two or more
  into the family's. `find`, `card` and `links` read that rule back with
  `--audience`; `search`, `history`, `list show` and the curator, which
  compiles the wiki's pages, do not.

## Decision

### 1. One read interface, over cards
The memory stacklet offers one read core, the card graph, with four
operations, and every reader goes through it:

| Operation | Answers |
|---|---|
| `find(words, type, since, audience)` | the cards that match, ranked by match × recency + links |
| `card(id, audience)` | one card: its text, its last changes, its links both ways |
| `links(id, depth, audience)` | the cards around one |
| `history(scope, by, since, audience)` | what changed, from the vault's log |

The CLI commands are thin surfaces over it. `search` becomes `find` (full text
is part of `find`'s match); `topic` and `person show` become `card` on a
`topic/` or `person/` id; `ask` stays and is `find` plus one model call;
`lookup` stays, as the ontology's own tool. Removed commands keep working for
one release and say which command replaced them.

### 2. Every card carries its own id: `card_id`
Every card has a `card_id` in its frontmatter: our own id for the card,
`<kind>-<value>`, made once when the card is first written and never
derived again.

| Kind | Example | Value at creation |
|---|---|---|
| note, bookmark, email, document, diary entry | `note-7f3a9c21e4` | ten random hex digits |
| person, topic, list | `person-homer`, `list-markt` | its slug |
| correspondent | `corr-springfield-elementary` | its slug |
| list archive | `list-markt-2026-10` | its list and month |

It stands next to the ids of where a card came from, which stay as they are:
`capture_id`, `entry_id`, `paperless_id`. Those name the source; `card_id`
names the card. A card's identity does not depend on a system famstack does
not control: Paperless numbers documents anew on a re-import, a Matrix event
id belongs to Matrix, a slug changes with a rename. Stored, the card id
outlives all three. Every reader reads one field instead of a rule per kind.

Every writer sets it (the archivist, capture, the diary, the curator for the
pages it compiles, the list and person commands). A writer that rebuilds a
card from its source (ADR-010) finds the existing card by its source id
(`stack.cards.find_card`) and keeps its `card_id`. A migration gives every
existing card its id, in one commit; the curator gives one to a card written
by hand, as the stack's commit. The name is `card_id`, not `id`: it is the
concept the stack reference already calls the card id, it sits with the other
`*_id` fields, and a plain `id` is read by other tools that open the same
files (Obsidian plugins, Quartz).

**A card whose id changes keeps the old one.** A note refiled as a document is
a new card with a new `card_id`; it lists the ids it had before in
`former_card_ids`, in the same commit as the change, so who changed it and when
stay in the history. Every reader resolves a former id to the current card:
`/go/note-7f3a9c21e4` redirects, `card` and the cards API answer with the
current card and say which id they were asked for. A card that was removed
answers as gone, with the commit that removed it, rather than not found: a
link in a chat's history keeps saying what became of it.

`/go/<card_id>` is the link to any card; the links in use today
(`/go/capture/…`, `/go/diary/…`, `/go/docs/…`, `/go/topic/…`) keep working.
Until every card has its id, readers name a card by the rule in `stack.cards`
(`card_ref`), as the card graph does now.

### 3. The ontology widens a search
`find` matches a word and every name, synonym and keyword the ontology gives
for the same topic or document type, in every language. "Versicherung" finds
the English insurance letter.

### 4. Facts are cards
Each row of `facts.toml` is a card, `fact/<kind>-<n>`, found by `find` and
opened by `card`. Facts are still written with `stack facts`.

### 5. One rule for who may read a card
A card in a person's own bucket is read only when that person is the whole
audience; the family's bucket is read by everyone. The rule lives in one
function, every read operation takes the audience, and the curator applies it
when it compiles a page: a page is made only from the cards its readers may
read. The CLI without an audience is the admin's and reads everything.

### 6. Writes stay as they are
Capture, the list verbs, `write`, `person add` and the diary compile keep
their commands. Every change is the person's commit (ADR-015).

### 7. Sources plug in as cards
A source (a chat room, the diary room, email, Paperless) delivers cards to
the vault through the front door. A document is the one source whose content
lives elsewhere: its card holds the summary, the correspondent and the link,
and `docs show` reads the document from Paperless. Reading never needs to
know which source a card came from.

## Order
1. `card_id` on every card: the writers, the migration, `former_card_ids` and
   its redirects, `/go/<card_id>`. Until then the graph names cards by the rule
   in `stack.cards` (done on #257).
2. The audience rule in one function; `history` and `list show` take it.
3. The curator compiles from readable cards only.
4. Ontology terms in `find`.
5. Facts as cards.
6. `search`, `topic` and `person show` onto the interface; the old commands
   point at the new ones for one release.

Steps 1 to 3 change no command's output for the admin. Step 3 changes compiled
pages: a person's page loses what it quoted from someone else's bucket.

## Consequences
- One place to make reading faster. The graph is built from the files and the
  whole git log on each call (1.4 s at 10,000 cards); an index can be added
  behind the interface later, for every reader at once.
- Agents learn four read commands instead of eight, and the same commands the
  admin uses.
- Privacy holds for every reader, not only the one that passes `--audience`.
- Removing `search`, `topic` and `person show` is a breaking change for
  scripts that call them; they keep working for one release.

## Considered
- **A search index (SQLite FTS) next to the files.** Faster, and a second copy
  of the truth that can go stale (the reason `stack.cards` scans). Deferred
  until the scan is too slow for a real family.
- **Embeddings in `find`.** With the graph, the best in the retrieval
  benchmark (12 of 13 German questions, 13 of 13 English), at 125 ms and a
  model per call. An option of `find` once step 4 is in;
  measured first against ontology terms, which cost nothing.

## Open
- Whether `ask` stays a memory command or becomes a skill of the agent.
- How a person's page is compiled for that person alone, from their own
  bucket, once step 3 leaves it with the family's cards only.
