# Memory Reference

This file describes the memory stacklet's internals: card ids, the card graph, views and how they compile, card
edits, refiling a note and the curator's API. The memory format (card types, fields and links) is in
[memory-format.md](memory-format.md). The stacklet contract is in [stack-reference.md](stack-reference.md). Core's
pages and configuration are in [core-reference.md](core-reference.md).

## Cards, views and the curator

**The memory format has a document of its own:
[memory-format.md](memory-format.md).** What a card is, the card types,
their fields, a note's sources and sidecar, media, views and the folder
layout are described there for anyone reading a memory without the stack,
and defined by the JSON Schemas in `stacklets/memory/schemas/`. This
section is what the stack provides around that format: ids, links,
lookups, the migrations that brought older files to it, and the
interfaces that read and change cards.

A card's path is not a stable name, so anything that refers to a card from
outside it (a `/go/` link, a correction, the agent) finds the card by an
id. A stored path is only a hint. Before every card carried a `card_id`,
each type was found by an id of its own, and those links still resolve:

| Type | Card id | Link |
|---|---|---|
| document | `paperless_id` | `/go/docs/<paperless_id>`, opens the document's card; Paperless when no card has the number |
| note, bookmark | `capture_id`: the event id of the message it was filed from | `/go/capture/<capture_id>`, opens the card's page |
| email thread | `resource`: the `mid:` URI of the thread's first message | none yet |
| diary card | `entry_id` | `/go/diary/<entry_id>`, opens the card's page |
| person | the file name, `<bucket>/people/<id>.md` | `/go/person/<slug>` |
| topic | its folder, `<bucket>/<slug>/`, whose card is `topic.md` | `/go/topic/<slug>` |
| list | none yet: its file name is its name | `/go/list/<slug>`, opens the list's card page: the family's list of that name, else a person's own |
| list archive | `<list>/<YYYY-MM>`, from `<bucket>/lists/archive/<list>/<YYYY-MM>.md`; part of its list | none |
| correspondent | `slug`, else its file name, `<shared>/correspondents/<slug>.md` | none yet |
| note, bookmark written by hand | its file name (no message to name it by) | none |
| a page the wiki generates to navigate (a diary month, an index) | `index/<path>` | the wiki's page |

The card graph behind `stack memory find`, `card` and `links`, and the
cards API, reads its cards from the vault only (ADR-016); navigation pages
and the views the wiki compiles into the brain are never found or opened
as cards. A card comes with the views that name it, the one about it
first. A card is named by its `card_id` when it has one
(`stack.cards.card_ref`); one without is named by the rule in the table
above (`card_name`), and that name still opens a card that has an id
since. Every link the graph draws names the card it reaches by its id. A
topic is a folder of one bucket, so a card belongs to the topic of the
folder it is filed in, in its own bucket, and a list's archive to the
list beside it; a slug in a card's `topics` is looked for in its own
bucket first. Where buckets share a name from before card ids (the
family's camping folder and Homer's were both `topic/camping`), the name
opens the card whose id it spells, `topic-camping`.
`card_kind` tells a list archive and a navigation page from the rest.

**Views name their cards and follow them** (ADR-016 §3 and §5, ADR-017
§7). `stack.cards.is_view` is the one rule every reader uses to tell a
view from a card, and still knows the older `generated: true`. A view's
versions are `stack.cards.card_version`, the file's git blob id. The card
graph attaches to a card every view whose `about` or `derived_from` names
its `card_id`. A view whose `compiled_from` holds another version of the
card than the vault does is behind it: `stack memory card` says so on the
view's line, the cards API gives the view `behind: true`, and the card's
own text counts.

When a card is pushed, the curator compiles again exactly the views
whose `derived_from` names it before or after the change, and the ones a
new card would now be in (the people, topic and correspondent it names,
its diary month), in two speeds. At once, with the mirror, without the
model (`stack memory wiki update ... --fast`, `diary pages --fast`):
which cards belong, list items, dates and links, with the model's last
wording renumbered to the cards as they are. After the quiet window
(`wiki_rebuild_quiet_secs`), once per burst, with the model. A change
to a list or its archive is compiled at once only: the person and topic
pages read a list's open items from the list file and the home page
shows no list, so the model would write the same text again. A topic
page's text about its to-dos follows with its next model compile (a
filing that touches it, or the nightly sweep). The home page and the
person pages are compiled at once only, from any push. The model writes
their text in the nightly sweep, because each of these calls holds the AI
server for minutes. The home prompt reads every summary the family may
read. A person page's prompt can take tens of thousands of tokens.
Their cards and links are current within seconds, and their text is up
to a day old. The curator keeps the pages it left to the night until
the nightly sweep. `stack memory wiki update` writes them now, with the
pages the latest filings touched. `--home`, `--member <id>` and `--all`
write the pages they name now. A changed person file still has the model
write every person page. A person page that the model has not written
yet says so in one line. The model's compile runs in the background,
one at a time. A push during the model's turn is still compiled at once.
The push then joins the next model compile. The model is asked from the
cards as they were. Its answer is rendered over each view as the view
is when the answer is written. The cards are read again under the
brain's writer lock (`memory/bot/brain_lock.py`). Every writer of the
brain takes this lock, the commit included.

A compile with the model asks the model only for a view whose request
changed. Each view keeps a fingerprint of its request next to the model's
answer. The fingerprint covers the AI server, the model and the request
settings. It also covers the prompt and the sources that the answer
cites.

The fingerprint of a person page uses the prompt without the previous
page, because that text is the model's own last answer. It also contains
the page layout stamp.

When the fingerprint is the same, the view uses its kept answer. This is
what `--fast` does. The compile then prints `<page>: unchanged since the
model wrote it, not asked again`. The compile decides this under the
brain's writer lock, because no other writer may change the view in
between.

The model is asked again when one of these changes:

- What the view reads of a card: its title, date, summary, people, who
  filed it, or its link. Not every view reads all of these.
- The prompt itself: a card added or removed, a new prompt file, the
  date on the home page.
- The model's name, or the AI endpoint (`stack ai switch`).
- The layout stamp of a person page.

The model is also asked again in these cases:

- The kept answer cited a card that is gone.
- The kept answer does not pass the current checks of the page.
- The view was compiled before views kept a fingerprint.

The rest of a card does not ask the model again. This includes its body
text and the fields that the view does not read. A new model served
under the same name rewrites nothing until one of the changes above
occurs.

The nightly sweep asks the model for the home page every night, because
the home page's prompt contains the date. On a night with nothing filed, the
person and topic pages do not ask the model.

The home prompt stays inside the model's context window. The compile asks
the AI server for the window once (`stack.ai.context_window`). The prompt
and about 2,048 tokens for the answer must fit in the window. If they do
not fit, the compile leaves out the oldest summaries until they fit. It
then prints one line with the count (`home: left out the 12 oldest of
500 summaries ...`).

A prompt that fits is unchanged. The prompt is also unchanged when the
server does not report a window.

Forgejo
tells the curator of each push to `family/memory` through a webhook to
`http://stack-memory-curator:8090/push` (the cards API), which `stack up
memory` adds once; the code stacklet allows webhooks to `stack-*` hosts
(`FORGEJO__webhook__ALLOWED_HOST_LIST`). The poll stays as the fallback,
and the nightly sweep as the catch-all.

**A record links the cards it names** (ADR-017 §3, §4, §10).
`stack.cards.links` holds the rules: `persons` as declared person ids, the
meta block's links derived from them, and the correspondent's and the
topic's card written the first time a record names them, never rewritten.
The archivist writes a correspondent's card with the classifier's
`correspondent_aliases`. Paperless keeps its own person tags. The same
rules brought the files written before to the same shape: `memory/0003`
(people by id, `Person:` tags dropped, and links to a document that moved
since, found by its Paperless id), `memory/0004` (the cards of
correspondents and topics) and `memory/0009` (a correspondent written by
hand, which named itself in `canonical`, gets that as its `title`).
`memory/0011` brings the cards of an instance older than 0.3.0-beta.2 into
the format, because the earlier migrations read them wrongly or skip them. The
graph links a record to the people its `persons` names, by id or by any
name they go by, and the wiki sends a link to a person's, a topic's or a
correspondent's card to that card's view (`quartz/plugins/vaultLinks.ts`). The diary
compile resolves the names the model read to declared people
(`stack.cards.links.About`) and leaves a name nobody goes by off the card,
logged. `memory/0007` brought the diary cards the compile still owned to
that shape and signed them again (`stack.cards.links.persons_by_id`); a
diary card a person changed kept its names.

**A card tags a topic by its id** (ADR-017 §4). `stack.tags` holds the
rule. A topic's id or its label in any of the ontology's languages, in any
case and spacing, is written as the id; anything else is lowercased with
hyphens between its words. The archivist writes every record's tags
through it, slugging a tag it cannot read (`Moe's` becomes `moe-s`), and
so does the diary compile; `memory/0005` brought the files written before
to the same shape, leaving a tag it cannot read as it is and naming it,
and `memory/0010` the diary cards, which had held the topic's label:
signed again where the compile still owned them, a person's left
unsigned. The label a reader
sees comes from the ontology in the instance's language: the card browser
shows it, and `stack memory find`, memory search and the wiki find a topic
by its id and by its label in every language.

**A card's `kind` is one of its type's** (ADR-017 §6,
`stack.cards.CARD_KINDS`). The nightly move writes `kind: archive` on
every list archive it adds to (`stack.lists.as_archive`), the diary
compile writes a diary card's `kind`, and `memory/0006` gave the files
written before theirs. The renderer shows an archive's lines without a
checkbox. A diary card is signed with a digest of its text
(`stack.digest`), and a migration that changes a card the compile owns
signs it again.

`stack memory check-format` (and the cards API's `/report`, which the
card browser shows) lists where the vault does not meet the format yet:
cards without a `card_id`, per type; a `card_id` two files carry; links to
a file the vault does not have; files with no frontmatter or a `type` that
is no card type; tags that are neither a topic id nor a keyword; a topic
card whose folder is a topic id of the ontology, which wins; a `kind`
outside its type's set, a list archive without `kind: archive` and a diary
card that still says `medium`; a note whose sources and sidecar do not
pair up, and a sidecar whose card is gone; a sidecar value its schema does
not allow; fields a card's type's schema does not name, which a change to
the card is refused for until they are removed (`stack memory card edit
<id> --remove <field>`). It is the admin's, read over every card, and
writes nothing. A vault that meets the format gives an empty report.

**A note is its card and its sidecar** (ADR-018, `stack.cards.sources`, the
format in [memory-format.md](memory-format.md#a-notes-sources)). The
sidecar's `made.code_commit` is `STACK_COMMIT`, and its template hashes
(`prompt_template`, `correction_template`, `conversation_template`) hash a
prompt's skeleton, with a fixed placeholder for every value that varies
per call, so they change with a template's wording, never with the day or
the note. A note in its writer's own words that has corrections (replies,
or `--msg`) or a person's edits of its clean version is rebuilt in two
calls (ADR-018 §7), the capture prompt reading the rebuilt text as the
note for the title, tags and persons. With corrections alone, the
correction prompt (`note_correction`) writes it from the original's words
and every correction. A note a person also edited by hand is asked the
card conversation (`card_conversation`): the original's words, the clean
version before the first correction or edit as the model's own answer,
and one message per correction or edit in time order. The rebuilt text is
checked by the first filing's rule, less what an event replaced
(`clean_words.kept_rebuilt`). Filing, a correction in the chat and
`stack memory reprocess --apply` write card and sidecar in one commit,
through the curator, the memory's one writer (below); a refile reads the
sources, never the interpretation, and appends to them. A note is found by a source's id
(`stack.cards.lookup.find_by_source_id`, through `find_card("capture", …)`),
so a correction to a chat message finds its note. Migration `memory/0008`
converts every note filed before, photo notes included, in one commit and
without a model call: the old sections become quotes, the fields move into
the sidecar, and a reply's missing time and id, a photo the media store
has no record of and words that carry prompt text are kept, marked
`reconstructed` and named.

**A card type has one renderer, and it writes markdown** (ADR-016 §4,
`stack.card_render`). `render(fields, body)` gives a card's title, one
line of the facts that matter for its type, and its body: the type's own
sections, then the person's own words, quoted, then the replies. Each
field appears once: the heading, the meta block under it (`> **About** …`)
and `Person:` tags are not shown, people are shown by name (`names` maps
an id to one), and due dates keep the form the files write
(`📅 2026-10-15`). The briefing callout becomes its summary, its facts
and its to-dos, the last under one label in the household's language.

| Type | Line of facts | Body |
|---|---|---|
| `document` | date · people · correspondent · `document_type` · `[#<paperless_id>](<resource>)` | summary, facts, to-dos, the document's text |
| `note`, `bookmark` | date · people (· the site, for a bookmark) | summary, facts, to-dos, media, then the paste and the caption, then the replies; a note filed from a person's words has the clean version of them instead of a summary, facts and to-dos |
| `diary` | date · people · 🎤, 📷, 🎬 or 📎 for its `kind` | summary, facts, media, quotes, then the transcript or text, then the replies |
| `email` | date · people | each message under its heading (day and sender): summary, to-dos, its words |
| `list` | `kind` | the description, then the items as the file writes them; an archive's without a checkbox |
| `person`, `correspondent`, `topic` | other names · `@account` | what the family wrote |
| any other | none | the fields as a list, then the body |

Every surface shows that markdown: `stack memory card` prints it (and
returns it in `rendered` with `--json`), the cards API returns it in
`rendered` and the card browser shows it as the card's body, and the
archivist's filing, capture and diary replies and its search hits show
the card through it. In the chat, `stack.card_render.for_chat` turns
media into a link and a link into the memory repo into its label, since
the chat shows neither. The renderer reads the file and never changes it.

The cards API also serves the media archive (`<data>/memory/media`) at
`/media/<path>`, for the photos and recordings cards embed
(`![[/media/2026/10/<event id>.png]]`). A file goes only to an audience
that may read a card embedding or linking it; with no audience, the admin
reads every file. Paths with `..` and anything outside the archive are
refused. Images, audio, video and PDFs are sent as what they are; any
other file as a download. Each card in a list (`/find`) names the
archive's files it embeds or links in `media`, so a page can mark a note
made from a file without reading the card whole. A card's page reads the
`.json` the archive keeps beside a file for the file's name, type and
size.

**A card changes through one endpoint** (`stacklets/memory/card_edit.py`).
Every surface that changes an existing card is a client of it: `stack
memory card edit`, the card browser later, the agent through the command.
None validates or commits on its own. The memory stacklet serves it on
the cards API; core passes it on at `/cards/api/cards/<card_id>` on
`127.0.0.1:42002`, with the request's `Authorization`, `Content-Type`,
`If-Match`, `Famstack-Actor` and `Accept` headers, and holds no token of
its own (the bot runner reads core's `.env`).

| Request | What it does |
|---|---|
| `GET /cards/<card_id>` | The card as `{"card_id", "path", "fields", "body", "rendered"}`, its version as the `ETag`; the file itself with `Accept: text/markdown` |
| `PATCH /cards/<card_id>` | `application/merge-patch+json` (RFC 7396) on `{"fields": ...}`; a `null` removes a field, a list is sent whole |

A change needs `Authorization: Bearer <memory__CARDS_WRITE_TOKEN>` (a
secret memory generates; the curator checks it, the host CLI and the bot
runner send it), `If-Match` with the `ETag` it was made on, and
`Famstack-Actor` naming a declared person with an account. The `ETag` is
the file's git blob id, which Forgejo calls its `sha`. The endpoint reads
the file as the curator's clone holds it, applies the change, checks the
fields against the type's JSON Schema (`stacklets/memory/schemas/cards/`),
derives the meta block's `**About**` links from `persons` and the title
heading from `title`, and commits it through the card store, the person
as author (below). The subject is `edit:
<title>` and the body names each changed field with its new value; a
rebuild reads the change back from the card's history, as it reads a
hand edit (ADR-018 §7). A change that changes nothing makes no commit.

A person changes fields, with `PATCH`, and nothing else. A whole card is
written only by `stack memory reprocess --apply`, card and sidecar in one
commit, through the curator's API (below), not this endpoint.

Answers: `200` the card and its new `ETag`; `401` no write token; `404`
no such card; `412` the card
changed since `If-Match` (nothing written);
`415` another media type; `422` `application/problem+json` (RFC 9457)
with `errors`, one `{"pointer", "field", "message", "allowed"}` per field,
for a field outside its schema, a field no schema names, a `readOnly`
field changed, or an actor
who is no member; `428` no `If-Match`; `503` before the curator has its
clone of the memory, or without a Matrix server name to make a person's
commit address.

**The schemas are checked in two places.** The curator's card store
validates every card it writes with `jsonschema` (below), in the curator's
container only, building the `urn:famstack:vault` resource from the memory
repo for each check
(`card_schema.py`): `person_id` (the declared people), `topic_id` (the
ontology and the topic cards' folders) and `tag` (a topic id, or a keyword checked as `format:
keyword` by `stack.tags.is_tag`). The format check runs on the host as
well, which is stdlib-only, so it reads the same files with the standard
library: `card_fields.py` the field names each card type has, and
`sidecar_schema.py` `schemas/sidecar.json` with every keyword that schema
uses, raising on one it does not know. A test holds that reader to
`jsonschema`'s verdict.

`stack memory card edit <card_id> --set f=v --add f=v --remove f[=v] --as
<person>` reads the card and its `ETag` through core's door, builds the
merge patch (`--add` and `--remove` change a list, sent whole; `--remove f`
removes the field), sends it, and prints the change or each refused field
with what it may hold.

`/history` is the memory's activity, newest first, read from the repo's
log (`stack.cards.activity`): each change with its time, who made
it, whether it is the machine's own work (`machine`), its kind in a word
(`filed`, `replied`, `corrected`, `edited`, `refiled`, `migrated`, ...;
from the subject prefix and the `Migration:` trailer, by the closed
commit grammar of ADR-018 §6 in `stack.cards.commits`) and the
cards it touched, each with its
`/go` link path. A card and its sidecar count once; a change over many
cards lists the first few, a `count` and how many of each type. A change
touching no card the audience may read is left out.

`/overview/<view>?audience=<person>` gives the sections of one page of the
memory, built from the cards with no model call (`stacklets/memory/overview/`).
The views are `me`, `family`, `person/<id>`, `topic/<card_id>`,
`sender/<card_id>`, `documents`, `documents/<kind>`, `kind/<type>`, `senders`
and `everything` (the menu's counts). Each view gives only
the sections its page shows: up next, lists, topics, what changed in 14
days, bookmarks, documents by kind, senders, the page's cards, the links
tallied and the counts by type. The package's docstring describes each
field. The answer is built from the graph of the audience, so no item,
link or count comes from a card the audience may not read. A list gives
its title and how many items are open, not its items. `me` needs exactly
one person in the audience. `person/<id>` also gives the person's topics
and the cards about the person. A person, topic or sender that the
audience may not read, or that does not exist, gives `404`. A document kind
with no document that the audience may read gives `404` too.

**Every card carries a `card_id`** (ADR-016; its form in
[memory-format.md](memory-format.md#the-card-id)).
`stack.cards.new_card_id` makes it for every writer; a writer that
rebuilds a card from its source keeps the one the file has.
`stack.cards.with_card_id` gives one to a file that has none: migration
`memory/0002` did so for the cards already there, and the curator does it
for each file pushed without one, or with one another file carries (the
file first committed later gets a new id). Readers take the id from
`stack.cards.card_id_from`.

**`/go/card/<card_id>` opens any card on its page**, `/card/<card_id>`, read
only, on every device of the home network. A person's and a topic's card
page links the page the wiki compiles from it. Bots post the link wherever
they link a card (a filing reply, a search hit, the reply to an edit;
`go_card`), and fall back to the link of the kind for a card that has no
id yet. `/go/card/<card_id>` goes straight to `/card/<card_id>`, and the
page decides which card the id names, so a card id has one answer at both
addresses. The links in the table keep resolving: a link that names one
card by another id (`/go/capture/...`, `/go/diary/...`,
`/go/docs/<paperless_id>` when a document card has that number, and
`/go/list/<slug>`) opens the card's page at its id, in one redirect. A
list's name with `*`, `?` or `[` in it names no list. Topic and person
links open the wiki. A list's page shows its sections with their items,
an item ticked today with who ticked it, and its months in the archive. When
the memory is not set up, a card link and a card's page answer 404 with
a page that says so and links `/setup`; `/go/docs/<paperless_id>` then
opens Paperless. Every link names its kind: `/go/<card_id>` without
`card/` answers 404.

**A card whose id changes keeps the old one.** A card that becomes another
type is a new card with a new id: it lists the ids it had in
`former_card_ids`, in the same commit (`stack.cards.card_identity`, which
every writer uses, so a rebuild keeps both fields). `/go/card/<former id>` opens
the card, and `stack memory card <former id>` shows it and says which id it
was asked for. The card graph opens it by a former id too (`stack memory
links`, the cards API's `/card/` and `/links/`), unless another card
carries that id as its own now. A photo filed as a bookmark and refiled as a note
(migration `docs/0001`) is the case today.

**A card that was removed answers as gone.** When no card carries an id,
`stack.cards.lookup.removal_of` reads the memory repo's history for the newest
commit that deleted a file with that `card_id`. The card's page,
`/card/<card_id>`, and so its link `/go/card/<card_id>`, then answers 410 with a page that says who removed it and on which day (not the
commit's subject, which can carry the title), and `stack memory card` says
the same to an
audience that could have read the card. An id no card ever carried is a
404. Core's tools server reads the memory repo read-only for this, and git
in its image; cards themselves are found through the memory's cards API.
Opening a link writes nothing.

The kinds found by id in the vault are listed in `stack.cards`
(`CARD_ID_FIELDS`); the lookups are in `stack.cards.lookup`: `find_card` by
the id of a kind, `find_document` by a document's `paperless_id`,
`find_by_card_id` by a `card_id` or a former one.
They scan the frontmatter of a copy of the vault, with no index to go
stale, and answer None for an id no card carries rather than guessing. Each reader runs it over the copy it reads
from: core's tools server over the wiki's copy, so a `/go/` link names a
card that is there (404 otherwise); the archivist over its own mount,
before it applies a correction to a capture, falling back to the path
the filing message recorded while that copy has not caught up.

**`stack memory reprocess <card_id>` refiles one note under today's
rules**, from its sources, never its summary (ADR-018). They are
`memory.note_card.note_sources`: the sources the card and its sidecar keep
(an older note's read the way migration `memory/0008` converts it), then
each reply in its message's thread the card does not hold yet (read in the
bot runner as the archivist: the room is the one of its rooms that has the
original's event, the thread its `m.thread` relations, bot accounts left
out), then `--msg` as a `note` source. Sources are only appended: the chat
replaces no quote. The model is given exactly this list, and the preview
returns it in `sources`, each record with its `quote`, so a surface that
shows a card's sources shows what the model read.

The preview writes nothing to the memory: it prints the card as it is and
as it would be (both through the card renderer), the diff of the card and
its sidecar and a proposal id, and keeps the proposal in
`<data>/memory/proposals/`, outside the memory's git tree. `--apply
<proposal_id>` commits exactly the previewed card and sidecar, with no
second model call, as one commit `reprocess: <title>` with `Replaces:
<sha>`, so one `git revert` undoes it. A refile is a machine rebuild: the
memory bot commits it, with `Requested-By: <person>` naming who asked,
and the sidecar's `made.requested_by` names the same person: `--by
<person>`, or `admin` (the admin at the terminal) when none is given.
`--msg "<text>" --by <person>` is a correction: the refile reads it with
the replies, the sidecar keeps it as a `note` source by that person, and
the commit is theirs, with their words in its body (ADR-018 §6).

A refile keeps what people did to the note (ADR-018 §7). The card's
history has one reader, the curator's card store (`CardStore.history`,
through `stack.cards.history.card_versions`), which the host and the
archivist in the bot runner ask for it (`GET /cards/<card_id>/versions`;
`GitMirror.card_history` in the archivist). It follows the card across
moves by its `card_id` (a person's rename or move in Forgejo or a clone;
the archivist keeps a note at the path it filed it under): a move is one
commit that removes the card at its
old path and adds it at the new one, and the old file is followed only
when it held the same `card_id`. The commit that put the card at its path
is read on its own for the files it removed, unless it is the card's
filing, so a note that never moved costs no extra read. Another card that lived at a path the
card had is never read as its past. `history_of` reads what it answers.
Whose commit counts is `stack.cards.commits.is_machine`; a person's commit that
carries a source (a filing, a reply, a refile with `--msg`) is a
correction, read from the sources. From every other person's commit it
takes the change to the clean version, a message of the card
conversation, and the change to the fields, as operations (`stack.cards.fields`):
`add` and `remove` for a list by set difference, so the model's other
suggestions still come in, `set` and `unset` for a value. The host sends
the history to the bot runner with the request (`prior`, the clean
version before the first correction or edit, `edits` and `ops`); the
operations are applied in the bot runner when the card is composed
(`GitMirror.capture_entry`), after the model, in order, each checked against the
card schemas (`schemas/cards/`, read with the standard library: the
field, `readOnly`, `required`, type, `minLength`, `pattern`, and the
instance's people, topics and tags). One that no longer fits is left out
and named: printed, and `left_out` in the JSON. A note without
corrections or edits is refiled from its words alone, one call. A card
edited by hand that shows no clean version
(one that would only repeat the words) is given one by the capture prompt
first, and the conversation starts from that (`clean_words.starting_point`).

Refused, with the reason and a `refusal` code: a card that is not a note
(`not-a-note`) or a note with no sources, written in the memory rather than
filed (`no-sources`); a note without its words, a
card whose quotes and sidecar sources do not pair up, or words that carry
prompt text from an older reprocess (`words`); no card with the id
(`no-card`); a model half that made no proposal (`no-proposal`). Preview
and apply both refuse when the curator cannot be asked (`unreachable`),
and the apply when the card or its sidecar changed after the preview
(`changed`). With
`--json` a preview returns `card_id`, `path`, `proposal_id`, `current` and
`proposed` (each `file`, `rendered` and `sidecar`), the `diff`,
`clean_rejected` (why the model's clean version was not used, "" when it
was or none was needed), `sources` and `left_out`; an apply returns `commit` and
`replaces`. The model half runs in the bot runner (`reprocess-note` in the
archivist's CLI entrypoint), through a mirror that composes the files and
writes nothing (`GitMirror.capture_entry`); the bot runner never writes to
the memory for this command.

The preview reads the card and its sidecar through the curator and keeps
their versions with the proposal. The apply sends exactly those files to
the curator as one commit, each on the version the preview read; when
either changed since, the curator refuses the whole commit and nothing is
written (`changed`).

**The memory has one writer, the curator** (ADR-019). It holds the
memory's clone (`<data>/memory/vault`) and the card store over it
(`stacklets/memory/cards/`), and every change to the memory goes through
it: the archivist's filings, declarations and list folds, the diary, the
list archive, the card editor, `stack memory write`, `list`, `person`,
`ontology` and `reprocess --apply`, the migrations, the seed of a new
memory and the person files. No other process writes the clone, and
nothing writes the memory through Forgejo's API. Its API, on the curator
and through core's door at `http://127.0.0.1:42002/cards/api` on this Mac:

| Request | What it does |
|---|---|
| `POST /commits` | one commit of the changes, with the write token |
| `POST /sync` | pull Forgejo's latest and push what waits, now, with the write token; answers the commit the clone stands at afterwards (`head`) |
| `GET /files/<path>` | a file and the version to write it on |
| `GET /tree/<dir>` | every file under a directory, likewise |
| `GET /paths[/<dir>]` | the paths alone, of a directory or of the whole memory |
| `GET /pending` | the commits Forgejo does not have yet, and how the last cycle went |
| `GET /cards/<card_id>/versions` | the card's history, followed across its moves |

A write names, for each file, the version it was read at (its git blob
id, `""` for a file that must not exist yet), the person or bot it is by
(`by`) and the bot that writes it (`via`). The store makes the message
from `kind`, `title`, `body` and `trailers` by the commit grammar
(`commit_subject`), with the person as author (ADR-015) and the bot as
committer (`<bot>@local`):

    {"kind": "chore(lists)", "title": "marge changed Camping", "by": "marge",
     "via": "memory-bot", "changes": [{"path": "family/lists/camping.md",
     "text": "...", "base": "<version>"}]}

Every write is checked before it is committed (`card_check.CardCheck`): a
markdown file with a `type` meets its type's schema, with the instance's
people and topics; a card keeps its `card_id`, or lists the one it had in
`former_card_ids`, unless another file still carries that id (a copy, which
may get an id of its own); a note's sidecar carries the note's `card_id`. A page
written by hand, without a `type`, goes in as it is. A migration's commit
(its `Migration:` trailer) is checked for identity only: migrations bring
cards into the format in steps, and the migration chain's test checks the
end. Writing the same text again commits nothing.

The curator pushes before it answers. With Forgejo down, the commit stays
in the clone, pending, and the next cycle or `POST /sync` pushes it. When
Forgejo and the clone both moved, the clone's own commits are replayed on
top of Forgejo's, each keeping its author and committer; a replay that
conflicts is put back, and the clone is reported diverged until a person
decides. Nothing is ever pushed by force. Answers: `201` with `{"commit",
"pending", "mirrored"}`, or `200` with an empty commit when nothing
changed; otherwise a problem document (RFC 7807) whose `reason` is
`malformed` (400), `unauthorised` (401), `changed` (409, with the
`paths`), `refused` or `invalid` (422; `invalid` with `errors`, one
`{"path", "field", "message"}` per field), `missing` (503, no clone yet)
or `failed` (500).

Every caller reaches the API through the framework's client,
`stack.memory.MemoryAPI` (`from_host(config)` in a command, `from_hook(ctx)`
in one of memory's hooks or migrations, `from_container()` in the curator's
and the bot runner's containers). The archivist uses it too, with its own
account as `via`, since the docs stacklet imports none of memory's code. The curator clones the memory itself and gives a
memory with no commit its seed as the first commit. Its API answers from
its first moment, before there is a clone: `stack up memory` waits for
`GET /pending` through core's door, and the install asks it to sync once
Forgejo has the repository.

Every commit `ForgejoClient` writes (the brain's scaffold, until the brain
moves into the curator too) names its committer: the client's `committer`
(a name and an address, no account needed) when it has one, else the
account the client signs in as, which it asks Forgejo for once (`GET
/user`). The author is the person a change is by or for, when the caller
names one (ADR-015). The memory bot has no Forgejo account: the memory
stacklet writes the scaffold with the admin's token through
`memory_forgejo`, which names the memory bot as committer.
