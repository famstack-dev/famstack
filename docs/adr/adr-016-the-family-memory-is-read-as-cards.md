# ADR-016: The Family Memory Is Read as Cards, Through One Interface

## Status
Proposed.

## Context
Everything the family keeps ends up as a file in the memory vault: captures,
diary entries, lists, people, topics, emails, and a card for every document
(the document itself stays in Paperless). Each file stands for one thing and
has a lasting id (`stack.cards`); together with the links between them they
are a graph of cards.

The wiki shows pages compiled from those cards: a person's page from the
person and every record about them, a topic page from the topic's records,
a diary month from its entries (ADR-011: the memory repo is the source, the
brain a projection). Today both carry the same `type` and are read as the
same thing, so a person is two nodes of the graph and a page compiled from
twenty cards answers a search next to them.

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
`<type>-<value>`, made once when the card is first written and never
derived again. The prefix is the card's `type`; a card that becomes another
type is a new card with a new id (below).

| Type | Example | Value at creation |
|---|---|---|
| note, bookmark, email, document, diary | `note-7f3a9c21e4`, `diary-0b6e2d91aa` | ten random hex digits |
| person, topic, list, correspondent | `person-homer`, `list-markt`, `correspondent-springfield-elementary` | its slug |
| list archive | `list-markt-2026-10` | its list and month |

It stands next to the ids of where a card came from, which stay as they are:
`capture_id`, `entry_id`, `paperless_id`. Those name the source; `card_id`
names the card. A card's identity does not depend on a system famstack does
not control: Paperless numbers documents anew on a re-import, a Matrix event
id belongs to Matrix, a slug changes with a rename. Stored, the card id
outlives all three. Every reader reads one field instead of a rule per kind.

Every writer of a card sets it (the archivist, capture, the diary, the list,
person and topic commands). A writer that rebuilds a
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

**Two files with one `card_id` are one error, not two cards.** A note copied
by hand keeps the id it was copied with. The format check reports it, and the
curator gives the newer file (by its first commit) a new id, as the stack's
commit.

`/go/<card_id>` is the link to any card; the links in use today
(`/go/capture/…`, `/go/diary/…`, `/go/docs/…`, `/go/topic/…`) keep working.
Until every card has its id, readers name a card by the rule in `stack.cards`
(`card_ref`), as the card graph does now.

### 3. Cards and views
**A card is a file in the memory repo and stands for one thing.** Each thing
the memory knows about is exactly one card.

**A view is derived from one or more cards and is not a card.** The curator
projects cards into views (ADR-011); the brain is the set of views, and they
are compiled again at will. A view names the cards it was derived from
(`derived_from`, card ids) and, when it is about one thing, that thing's card
(`about`). It carries no `type` and no `card_id` (ADR-017).

| View | About | Derived from |
|---|---|---|
| a person (`homer/about.md`) | `person-homer` | the person card and every record about Homer |
| a correspondent | `correspondent-moe-s-tavern` | the correspondent card and its documents |
| a topic | `topic-itchy-scratchy-land` | the topic card and its records and lists |
| a diary month | | the month's diary entries |
| the home page, a folder listing | | the cards it lists |

So:

- **A person** is the declared file, `<shared>/people/<id>.md`, card
  `person-homer`. Records link to it, and their `persons` field holds person
  ids (`homer`), not display names (ADR-017). The same holds for a
  correspondent.
- **A topic** gets a card of its own in the memory repo, written when the
  topic is first filed into and never rewritten, like a person or a
  correspondent. Today a topic is only a folder and a compiled page.
- **A list archive** is a card of its own, `list-markt-2026-10`, with
  `kind: archive` and a `list` field naming the card of the list it belongs
  to.
- **The audience rule reaches views through `derived_from`:** a view may be
  shown to the readers of every card it was derived from, and no one else
  (§8).

**Search finds cards; reading uses views.** `find` searches the cards in the
vault and never returns a view: a hit is a card, which a reader can link and
a person can correct. `card` returns the card together with the views
derived from it. The agent reads those views as its summary: a person's or a
topic's view puts what many cards say into one text compiled for reading,
and views can be compiled for the agent as well as for the wiki. A view's
link opens it in the wiki.

A view's facts follow its cards within seconds; its written summary can be
a few minutes behind (§5). When a card in a view's `derived_from` changed
after the view was compiled, `card` says so, and the card's word counts over
the view's.

The file format of every card type and of views, their fields and their
links, is ADR-017.

### 4. Each card type has a schema and renderers
A card type is backed by a schema: the fields every card shares, the ones
the type adds, which are required, and the values allowed (ADR-017). Every
writer checks against it, and so does the format check.

A card type can have renderers attached, one per output format: a markdown
layout, an HTML layout, a text layout for the chat. A renderer presents a
card's information and never changes it. A surface picks the format it
needs (the card browser HTML, a bot's chat message text, a view markdown)
and uses the type's renderer for it. A type without a renderer for that
format falls back to a generic one: the title, the fields as a list, then
the body. Renderers belong to the type, not to a single card, so every card
of a type looks the same on every surface.

This removes three findings of the card review at the source: headers that
repeat the fields, due dates written in two languages, and a title shown
twice. The links Obsidian needs stay in the file.

### 5. Corrections belong to cards; views follow
A correction is made to a card, never to a view or a source. Wherever a
person makes it (a reply to a filing in the chat, a reply in a diary thread,
the agent through `stack memory write`, a hand edit in Forgejo or Obsidian),
it lands as that person's commit on the card, found by its `card_id`, with
`Correction:` and `Set:` (ADR-015). It is attached through git; the card
holds no list of its changes.

- **A regenerate keeps it.** A card rebuilt from its source gets its
  corrections applied again (ADR-015).
- **Views follow, at once.** When a card changes, the curator compiles again
  exactly the views whose `derived_from` names it, topic views included. It
  learns of a change when it is pushed: Forgejo notifies it of every push,
  which covers every writer, a push from a clone included, and polling stays
  only as the fallback. A view is compiled at two speeds: what needs no
  model (the items of a list, dates, links, which cards belong to it)
  within seconds of the push; the text a model writes after a quiet
  window, so a burst of filings costs one model call. The nightly sweep
  stays as the catch-all.
- **Nobody corrects a view.** A correction aimed at a view (a reply to a
  topic summary) goes to the card the wrong statement came from, found among
  the view's `derived_from`. When the agent cannot tell which card that is,
  it asks.
- **The source stays as it was.** The chat message, the PDF in Paperless and
  the email are never rewritten; the card says what they mean.

### 6. The ontology widens a search
`find` matches a word and every name, synonym and keyword the ontology gives
for the same topic or document type, in every language. "Versicherung" finds
the English insurance letter.

### 7. Facts are cards
Each row of `facts.toml` is a card, `fact-<kind>-<n>`, found by `find` and
opened by `card`. Facts are still written with `stack facts`.

### 8. One rule for who may read a card
A card in a person's own bucket is read only when that person is the whole
audience; the family's bucket is read by everyone. The rule lives in one
function, every read operation takes the audience, and the curator applies it
when it compiles a view: a view is made only from the cards its readers may
read, and its `derived_from` says which those were. The CLI without an
audience is the admin's and reads everything.

**The agent never chooses its audience.** The agent's runtime sets it from
the room the turn belongs to: the room's members, bots excluded. The model
cannot leave it out or change it, and a read the agent makes never goes out
without one. A prompt that asks the model to keep something private is not
the rule; the code is.

**Two buckets, stated as a limit.** Where a card is filed follows the same
membership: one person in the room files into that person's bucket, two or
more into the family's. A room with two of five people (the parents' room)
therefore files for everyone, and the children's agent can read it. The user
guide says so. Narrower audiences (a card readable only by the people of its
room) can come later without losing anything: the room each card was filed
from is in the Matrix timeline, so a reprocess can rebuild them.

### 9. Writes stay as they are
Capture, the list verbs, `write`, `person add` and the diary compile keep
their commands. Every change is the person's commit (ADR-015).

### 10. Sources plug in as cards
A source (a chat room, the diary room, email, Paperless) delivers cards to
the vault through the front door. A document is the one source whose content
lives elsewhere: its card holds the summary, the correspondent and the link,
and `docs show` reads the document from Paperless. Reading never needs to
know which source a card came from.

## Order
1. `card_id` on every card, one card per thing (§3): the writers, the
   migration, `former_card_ids` and its redirects, `/go/<card_id>`. The
   migration is the one ADR-017 needs for links and person ids; both run as
   one. Until then the graph names cards by the rule in `stack.cards`.
2. The audience rule in one function; `history` and `list show` take it, and
   the agent's runtime sets it.
3. The curator compiles views from readable cards only, with
   `derived_from`, and compiles a view again when one of its cards is
   pushed, in two speeds (§5).
4. Ontology terms in `find`.
5. Facts as cards.
6. `search`, `topic` and `person show` onto the interface; the old commands
   point at the new ones for one release.

Steps 1 to 3 are the base of 0.5, step 4 follows when there is time, steps 5
and 6 come after 0.5. Steps 1 to 3 change no command's output for the admin.
Step 3 changes views: a person's view loses what it quoted from someone
else's bucket.

**The card browser comes first and checks each step.** Core's tools server
shows the card graph on the server Mac (#257), and the same graph answers as
JSON through the cards API and `memory card`. It reports what the steps are
meant to remove: cards without a `card_id`, two files with one id, a thing
that is two cards, links to nothing. The admin reads it in the browser, an
agent reads it as JSON, and a step is done when its report is empty on the
test instance. It is also the first surface built on renderers (§4): its
HTML layout per card type is where they start.

## Consequences
- One place to make reading faster. The graph is built from the files and the
  whole git log on each call (1.4 s at 10,000 cards); an index can be added
  behind the interface later, for every reader at once.
- Agents learn four read commands instead of eight, and the same commands the
  admin uses.
- Privacy holds for every reader, not only the one that passes `--audience`.
  It holds for the agent only where the runtime can name the room of a turn.
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
- How a person's view is compiled for that person alone, from their own
  bucket, once step 3 leaves it with the family's cards only.
- How the wiki uses the renderers. It is rendered by Quartz, in TypeScript;
  most likely the curator writes the rendered cards into the views.
- Whether a correction to a document card (its date, its correspondent) also
  goes to Paperless through the archivist, so the two agree.
