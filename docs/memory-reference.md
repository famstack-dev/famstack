# Memory Reference

This file describes the memory stacklet's internals:

- card ids and the card graph;
- views, and how they compile;
- card edits and refiling a note;
- the curator's API.

The memory format (card types, fields and links) is in
[memory-format.md](memory-format.md). The stacklet contract is in [stack-reference.md](stack-reference.md). Core's
pages and configuration are in [core-reference.md](core-reference.md).

## What does the stack add to the memory format?

The memory format has a document of its own: [memory-format.md](memory-format.md). It describes these things for
anyone who reads a memory without the stack:

- what a card is, the card types and their fields;
- a note's sources and sidecar;
- media, views and the folder layout.

The JSON Schemas in `stacklets/memory/schemas/` define the format. This file describes what the stack provides around
that format:

- ids, links and lookups;
- the migrations that brought older files to the format;
- the interfaces that read and change cards.

---

## How does the stack find a card?

A card's path is not a stable name. Thus anything that refers to a card from outside it finds the card by an id: a
`/go/` link, a correction, the agent. A stored path is only a hint.

### Which ids name a card?

Every card carries a [`card_id`](#every-card-carries-a-card_id). Before every card carried one, each type was found by
an id of its own. Those links still resolve:

| Type | Card id | Link |
|---|---|---|
| document | `paperless_id` | `/go/docs/<paperless_id>`. It opens the document's card, or Paperless when no card has the number. |
| note, bookmark | `capture_id`: the event id of the message it was filed from | `/go/capture/<capture_id>`, opens the card's page |
| email thread | `resource`: the `mid:` URI of the thread's first message | none yet |
| diary card | `entry_id` | `/go/diary/<entry_id>`, opens the card's page |
| person | the file name, `<bucket>/people/<id>.md` | `/go/person/<slug>` |
| topic | its folder, `<bucket>/<slug>/`, whose card is `topic.md` | `/go/topic/<slug>` |
| list | none yet: its file name is its name | `/go/list/<slug>`, opens the list's card page: the family's list of that name, else a person's own |
| list archive | `<list>/<YYYY-MM>`, from `<bucket>/lists/archive/<list>/<YYYY-MM>.md`. It is part of its list. | none |
| correspondent | `slug`, else its file name, `<shared>/correspondents/<slug>.md` | none yet |
| note, bookmark written by hand | its file name (no message to name it by) | none |
| a page the wiki generates to navigate (a diary month, an index) | `index/<path>` | the wiki's page |

Any card opens at `/go/card/<card_id>`.

### Every card carries a `card_id`

Every card carries a `card_id` (ADR-016). Its form is in [memory-format.md](memory-format.md#the-card-id).

- `stack.cards.new_card_id` makes the id for every writer.
- A writer that rebuilds a card from its source keeps the id that the file has.
- `stack.cards.with_card_id` gives an id to a file that has none. Migration `memory/0002` did so for the cards that
  were already there.
- The curator gives an id to each file that is pushed without one, or with an id that another file carries. The file
  that was first committed later gets the new id.
- Readers take the id from `stack.cards.card_id_from`.

### How does the card graph name a card?

The **card graph** is behind `stack memory find`, `card` and `links`, and the cards API. It reads its cards from the
vault only (ADR-016). It never finds or opens these pages as cards: the navigation pages, and the views that the wiki
compiles into the brain.

- A card comes with the views that name it, the view about it first.
- A card is named by its `card_id` when it has one (`stack.cards.card_ref`).
- A card without one is named by the rule in the [table above](#which-ids-name-a-card) (`card_name`). That name still
  opens a card that got an id since.
- Every link that the graph draws names the card it reaches by its id.
- `card_kind` tells a list archive and a navigation page from the rest.

**Topics and buckets.** A topic is a folder of one bucket. A card belongs to the topic of the folder that it is filed
in, in its own bucket. A list's archive belongs to the list beside it. A slug in a card's `topics` is looked for in its
own bucket first.

Some buckets share a name from before card ids. For example, the family's camping folder and Homer's were both
`topic/camping`. Such a name opens the card whose id it spells, `topic-camping`.

### What does `/go/card/<card_id>` open?

`/go/card/<card_id>` opens any card on its page, `/card/<card_id>`. The page is read only, on every device of the home
network (see [core-reference.md](core-reference.md#what-does-a-card-page-show)). A person's card page and a topic's
card page link the page that the wiki compiles from it.

**Where bots post it.** Bots post the link wherever they link a card: a filing reply, a search hit, the reply to an
edit (`go_card`). For a card that has no id yet, they fall back to the link of the kind.

**One answer at both addresses.** `/go/card/<card_id>` goes straight to `/card/<card_id>`. The page decides which card
the id names. Thus a card id has one answer at both addresses.

**The older links.** The links in the [table above](#which-ids-name-a-card) keep resolving. A link that names one card
by another id opens the card's page at its id, in one redirect. These links are of that kind:

- `/go/capture/...`;
- `/go/diary/...`;
- `/go/docs/<paperless_id>`, when a document card has that number;
- `/go/list/<slug>`.

A shared topic's link opens the wiki. A person's link (`/go/person/<slug>`) opens their memory page. A link to a
person's own topic (`/go/topic/<member>/<slug>`) opens the topic's memory page, and its `todo` link opens the list
named after it. The wiki holds only what the family shares, because every paired device reads the same wiki.

**Lists.** A list's name with `*`, `?` or `[` in it names no list. A list's page shows these things:

- its sections with their items;
- an item ticked today, with who ticked it;
- its months in the archive.

**Errors.** These requests do not open a card:

| Case | Answer |
|---|---|
| The memory is not set up | A card link and a card's page answer 404, with a page that says so and links `/setup`. `/go/docs/<paperless_id>` then opens Paperless. |
| `/go/<card_id>` without `card/` | 404, because every link names its kind |

### What happens when a card's id changes?

A card whose id changes keeps the old one. A card that becomes another type is a new card with a new id. It lists the
ids that it had in `former_card_ids`, in the same commit. Every writer uses `stack.cards.card_identity` for this. Thus
a rebuild keeps both fields.

A former id still opens the card:

- `/go/card/<former id>` opens the card.
- `stack memory card <former id>` shows the card and says which id it was asked for.
- The card graph opens the card by a former id too (`stack memory links`, the cards API's `/card/` and `/links/`).
  The exception: another card carries that id as its own now.

The case today is a photo filed as a bookmark and refiled as a note (migration `docs/0001`).

### What does a removed card answer?

A card that was removed answers as gone. When no card carries an id, `stack.cards.lookup.removal_of` reads the memory
repo's history. It looks for the newest commit that deleted a file with that `card_id`.

| Case | Answer |
|---|---|
| A removed card, at `/card/<card_id>` and so at its link `/go/card/<card_id>` | 410, with a page that says who removed it and on which day |
| A removed card, in `stack memory card`, for an audience that could have read it | The same: who removed it and on which day |
| An id that no card ever carried | 404 |

The page does not show the commit's subject, because the subject can carry the title.

Core's tools server reads the memory repo read-only for this, with git in its image. It finds the cards themselves
through the memory's cards API. Opening a link writes nothing.

### Where are the lookups?

`stack.cards` lists the kinds found by id in the vault (`CARD_ID_FIELDS`). The lookups are in `stack.cards.lookup`:

| Function | Finds a card by |
|---|---|
| `find_card` | the id of a kind |
| `find_document` | a document's `paperless_id` |
| `find_by_card_id` | a `card_id`, or a former one |

The lookups scan the frontmatter of a copy of the vault, with no index that can go stale. For an id that no card
carries, they answer None and do not guess. Each reader runs them over the copy that it reads from:

- Core's tools server uses the brain, the copy that the wiki's input is made from. Thus a `/go/` link names a card that is there, and 404 otherwise.
- The archivist uses its own mount, before it applies a correction to a capture. While that copy has not caught up, it
  falls back to the path that the filing message recorded.

---

## What does the stack write into a card?

### How does a record link the cards it names?

A record links the cards it names (ADR-017 §3, §4, §10). `stack.cards.links` holds the rules:

- `persons` holds declared person ids.
- The meta block's links are derived from `persons`.
- The correspondent's card and the topic's card are written the first time a record names them, and never rewritten.

The archivist writes a correspondent's card with the classifier's `correspondent_aliases`. Paperless keeps its own
person tags.

**Links in the graph and the wiki.** The graph links a record to the people that its `persons` names, by id or by any
name that they go by. The wiki sends a link to a person's, a topic's or a correspondent's card to that card's view
(`quartz/plugins/vaultLinks.ts`).

**The diary.** The diary compile resolves the names that the model read to declared people
(`stack.cards.links.About`). It leaves a name that nobody goes by off the card, and logs it.

**Migrations.** The same rules brought the files written before to the same shape:

| Migration | What it did |
|---|---|
| `memory/0003` | People by id. `Person:` tags dropped. Links to a document that moved since, found by its Paperless id. |
| `memory/0004` | The cards of correspondents and topics |
| `memory/0007` | The diary cards that the compile still owned, signed again (`stack.cards.links.persons_by_id`). A diary card that a person changed kept its names. |
| `memory/0009` | A correspondent written by hand that named itself in `canonical` gets that name as its `title`. |
| `memory/0011` | The cards of an instance older than 0.3.0-beta.2 into the format, because the earlier migrations read them wrongly or skip them |

### How does a card tag a topic?

A card tags a topic by its id (ADR-017 §4). `stack.tags` holds the rule:

- A topic's id is written as the id. A topic's label in any of the ontology's languages is written as the id too.
- Case and spacing do not matter.
- Anything else is lowercased, with hyphens between its words.

The archivist writes every record's tags through this rule. It slugs a tag that it cannot read: `Moe's` becomes
`moe-s`. The diary compile does the same.

| Migration | What it did |
|---|---|
| `memory/0005` | Brought the files written before to the same shape. It left a tag that it cannot read as it is, and named it. |
| `memory/0010` | Brought the diary cards, which had held the topic's label. It signed them again where the compile still owned them, and left a person's card unsigned. |
| `memory/0013` | Took a person's private topic id off every card outside that person's bucket. Earlier runs of 0005, 0010 and 0011 read the topics of every bucket, and these migrations now read the topics of the card's place. A tag that a person added by hand stays, and the migration names the card. |

The label that a reader sees comes from the ontology in the instance's language. The card browser shows it.
`stack memory find`, memory search and the wiki find a topic by its id and by its label in every language.

### What sets a card's `kind`?

A card's `kind` is one of its type's kinds (ADR-017 §6, `stack.cards.CARD_KINDS`).

- The nightly move writes `kind: archive` on every list archive that it adds to (`stack.lists.as_archive`).
- The diary compile writes a diary card's `kind`.
- `memory/0006` gave the files written before their `kind`.

The renderer shows an archive's lines without a checkbox.

A diary card is signed with a digest of its text (`stack.digest`). A migration that changes a card that the compile
owns signs it again.

### What is a note's sidecar?

A note is its card and its sidecar (ADR-018, `stack.cards.sources`). The format is in
[memory-format.md](memory-format.md#a-notes-sources).

**What the sidecar records.** The sidecar's `made.code_commit` is `STACK_COMMIT`. Its template hashes are
`prompt_template`, `correction_template` and `conversation_template`. Each hashes a prompt's skeleton, with a fixed
placeholder for every value that varies per call. Thus a hash changes with a template's wording, never with the day or
the note.

**How a note is rebuilt.** Some notes are in their writer's own words. When such a note has corrections or edits, it is
rebuilt in two calls (ADR-018 §7). Corrections are replies or `--msg`. Edits are a person's edits of its clean version.

The capture prompt then reads the rebuilt text as the note, for the title, tags and persons. The first call depends on
what the note has:

| The note has | The first call |
|---|---|
| Corrections only | The correction prompt (`note_correction`) writes the note from the original's words and every correction. |
| Also an edit by hand | The card conversation (`card_conversation`), see below. |

The card conversation holds these messages:

1. the original's words;
2. the clean version before the first correction or edit, as the model's own answer;
3. one message for each correction or edit, in time order.

The rebuilt text is checked by the first filing's rule, less what an event replaced (`clean_words.kept_rebuilt`).

**How a note is written.** These surfaces write the card and the sidecar in one commit. They write through
[the curator](#the-memory-has-one-writer-the-curator), the memory's one writer:

- filing;
- a correction in the chat;
- `stack memory reprocess --apply`.

A refile reads the sources, never the interpretation, and appends to them.

**How a note is found.** A note is found by a source's id (`stack.cards.lookup.find_by_source_id`, through
`find_card("capture", …)`). Thus a correction to a chat message finds its note.

**Older notes.** Migration `memory/0008` converts every note filed before, photo notes included. It does so in one
commit and without a model call:

- The old sections become quotes.
- The fields move into the sidecar.
- Three things are kept, marked `reconstructed` and named:
  - a reply's missing time and id;
  - a photo that the media store has no record of;
  - words that carry prompt text.

### How is a card shown?

A card type has one renderer, and it writes markdown (ADR-016 §4, `stack.card_render`). `render(fields, body)` gives
three things:

- the card's title;
- one line of the facts that matter for its type;
- its body: the type's own sections, then the person's own words, quoted, then the replies.

Each field appears once:

- The heading, the meta block under it (`> **About** …`) and `Person:` tags are not shown.
- People are shown by name. `names` maps an id to a name.
- Due dates keep the form that the files write (`📅 2026-10-15`).
- The briefing callout becomes the card's summary, its facts and its to-dos. The to-dos are under one label in the
  household's language.

| Type | Line of facts | Body |
|---|---|---|
| `document` | date · people · correspondent · `document_type` · a link `#<paperless_id>` to the `resource` | summary, facts, to-dos, the document's text |
| `note`, `bookmark` | date · people (· the site, for a bookmark) | summary, facts, to-dos, media, then the paste and the caption, then the replies. A note filed from a person's words has the clean version of them instead of a summary, facts and to-dos. |
| `diary` | date · people · 🎤, 📷, 🎬 or 📎 for its `kind` | summary, facts, media, quotes, then the transcript or text, then the replies |
| `email` | date · people | each message under its heading (day and sender): summary, to-dos, its words |
| `list` | `kind` | the description, then the items as the file writes them. An archive's items have no checkbox. |
| `person`, `correspondent`, `topic` | other names · `@account` | what the family wrote |
| any other | none | the fields as a list, then the body |

Every surface shows that markdown:

| Surface | How it shows the card |
|---|---|
| `stack memory card` | Prints it. With `--json`, it returns it in `rendered`. |
| The cards API | Returns it in `rendered`. |
| The card browser | Shows it as the card's body. |
| The archivist | Its filing, capture and diary replies and its search hits show the card through it. |
| The chat | `stack.card_render.for_chat` turns media into a link. It turns a link into the memory repo into its label. The reason: the chat shows neither. |

The renderer reads the file and never changes it.

### How does the cards API serve media?

The cards API serves the media archive (`<data>/memory/media`) at `/media/<path>`. The archive holds the photos and
recordings that cards embed (`![[/media/2026/10/<event id>.png]]`).

- A file goes only to an audience that may read a card that embeds or links it.
- With no audience, the admin reads every file.
- Paths with `..` and anything outside the archive are refused.
- Images, audio, video and PDFs are sent as what they are. Any other file is sent as a download.

Each card in a list (`/find`) names in `media` the archive's files that it embeds or links. Thus a page can mark a note
made from a file without a read of the whole card. A card's page reads the `.json` that the archive keeps beside a file,
for the file's name, type and size.

---

## How are views compiled?

### How does a view follow its cards?

Views name their cards and follow them (ADR-016 §3 and §5, ADR-017 §7).

- `stack.cards.is_view` is the one rule that every reader uses to tell a view from a card. It still knows the older
  `generated: true`.
- A view's versions are `stack.cards.card_version`, the file's git blob id.
- The card graph attaches to a card every view whose `about` or `derived_from` names its `card_id`.

A view whose `compiled_from` holds another version of the card than the vault does is behind it. Then these things are
true:

- `stack memory card` says so on the view's line.
- The cards API gives the view `behind: true`.
- The card's own text counts.

### Which views does a push compile?

When a card is pushed, the curator compiles these views again:

- exactly the views whose `derived_from` names the card, before or after the change;
- the views that a new card would now be in: the people, topic and correspondent that it names, and its diary month.

The compile has two speeds:

| Speed | When | What it writes |
|---|---|---|
| At once, without the model | With the mirror (`stack memory wiki update ... --fast`, `diary pages --fast`) | Which cards belong, list items, dates and links. The model's last wording is renumbered to the cards as they are. |
| With the model | After the quiet window (`wiki_rebuild_quiet_secs`), once per burst | The text |

**Lists.** A change to a list or its archive is compiled at once only. The person and topic pages read a list's open
items from the list file, and the home page shows no list. Thus the model would write the same text again. A topic
page's text about its to-dos follows with its next model compile: a filing that touches it, or the nightly sweep.

**The home page and the person pages.** These pages are compiled at once only, from any push. The model writes their
text in the nightly sweep, because each of these calls holds the AI server for minutes. The home prompt reads every
summary that the family may read. A person page's prompt can take tens of thousands of tokens.

Their cards and links are current within seconds, and their text is up to a day old. The curator keeps the pages that
it left to the night until the nightly sweep. A person page that the model has not written yet says so in one line. A
changed person file still has the model write every person page.

To write these pages now, run `stack memory wiki update`. It writes them together with the pages that the latest
filings touched. `--home`, `--member <id>` and `--all` write the pages that they name now.

**The model's turn.** The model's compile runs in the background, one at a time. A push during the model's turn is
still compiled at once. The push then joins the next model compile.

The model is asked from the cards as they were. Its answer is rendered over each view as the view is when the answer
is written. The cards are read again under the brain's writer lock (`memory/bot/brain_lock.py`). Every writer of the
brain takes this lock, the commit included.

### When is the model asked again?

A compile with the model asks the model only for a view whose request changed. Each view keeps a **fingerprint** of
its request next to the model's answer. The fingerprint covers these things:

- the AI server, the model and the request settings;
- the prompt;
- the sources that the answer cites.

The fingerprint of a person page uses the prompt without the previous page, because that text is the model's own last
answer. It also contains the page layout stamp.

When the fingerprint is the same, the view uses its kept answer. This is what `--fast` does. The compile then prints
this line:

```
<page>: unchanged since the model wrote it, not asked again
```

The compile decides this under the brain's writer lock, because no other writer may change the view in between.

The model is asked again when one of these changes:

- What the view reads of a card: its title, date, summary, people, who filed it, or its link. Not every view reads
  all of these.
- The prompt itself: a card added or removed, a new prompt file, the date on the home page.
- The model's name, or the AI endpoint (`stack ai switch`).
- The layout stamp of a person page.

The model is also asked again in these cases:

- The kept answer cited a card that is gone.
- The kept answer does not pass the current checks of the page.
- The view was compiled before views kept a fingerprint.

The rest of a card does not ask the model again. This includes its body text and the fields that the view does not
read. A new model served under the same name rewrites nothing until one of the changes above occurs.

**The nightly sweep.** The nightly sweep asks the model for the home page every night, because the home page's prompt
contains the date. On a night with nothing filed, the person and topic pages do not ask the model.

### What if the home prompt is too long?

The home prompt stays inside the model's context window. The compile asks the AI server for the window once
(`stack.ai.context_window`). The prompt and about 2,048 tokens for the answer must fit in the window. If they do not
fit, the compile leaves out the oldest summaries until they fit. It then prints one line with the count:

```
home: left out the 12 oldest of 500 summaries ...
```

A prompt that fits is unchanged. The prompt is also unchanged when the server does not report a window.

### How does the curator learn of a push?

Forgejo tells the curator of each push to `family/memory` through a webhook. The webhook goes to
`http://stack-memory-curator:8090/push` (the cards API). `stack up memory` adds the webhook once. The code stacklet
allows webhooks to `stack-*` hosts (`FORGEJO__webhook__ALLOWED_HOST_LIST`). The poll stays as the fallback, and the
nightly sweep as the catch-all.

---

## How does a card change?

A card changes through one endpoint (`stacklets/memory/card_edit.py`). Every surface that changes an existing card is
a client of it: `stack memory card edit`, the card browser later, and the agent through the command. None of them
validates or commits on its own.

The memory stacklet serves the endpoint on the cards API. Core passes it on at `/cards/api/cards/<card_id>` on
`127.0.0.1:42002`. Core forwards the request's `Authorization`, `Content-Type`, `If-Match`, `Famstack-Actor` and
`Accept` headers. Core holds no token of its own (the bot runner reads core's `.env`).

| Request | What it does |
|---|---|
| `GET /cards/<card_id>` | The card as `{"card_id", "path", "fields", "body", "rendered"}`, with its version as the `ETag`. With `Accept: text/markdown`, the file itself. |
| `PATCH /cards/<card_id>` | `application/merge-patch+json` (RFC 7396) on `{"fields": ...}`. A `null` removes a field. A list is sent whole. |

### What does a change need?

A change needs three headers:

| Header | Value |
|---|---|
| `Authorization` | `Bearer <memory__CARDS_WRITE_TOKEN>`. Memory generates this secret. The curator checks it. The host CLI and the bot runner send it. |
| `If-Match` | The `ETag` that the change was made on. The `ETag` is the file's git blob id, which Forgejo calls its `sha`. |
| `Famstack-Actor` | A declared person with an account |

### What does the endpoint do?

1. It reads the file as the curator's clone holds it.
2. It applies the change.
3. It checks the fields against the type's JSON Schema (`stacklets/memory/schemas/cards/`).
4. It derives the meta block's `**About**` links from `persons`, and the title heading from `title`.
5. It commits the card through the card store, with the person as author (see
   [the curator](#the-memory-has-one-writer-the-curator)).

The commit's subject is `edit: <title>`. The body names each changed field with its new value. A rebuild reads the
change back from the card's history, as it reads a hand edit (ADR-018 §7). A change that changes nothing makes no
commit.

A person changes fields, with `PATCH`, and nothing else. Only `stack memory reprocess --apply` writes a whole card. It
writes the card and the sidecar in one commit. It writes through the curator's API, not through this endpoint.

### What does the endpoint answer?

| Status | Meaning |
|---|---|
| `200` | The card and its new `ETag` |
| `401` | No write token |
| `404` | No such card |
| `412` | The card changed since `If-Match`. Nothing is written. |
| `415` | Another media type |
| `422` | `application/problem+json` (RFC 9457) with `errors`, one `{"pointer", "field", "message", "allowed"}` per field. The causes: a field outside its schema, a field that no schema names, a changed `readOnly` field, or an actor who is no member. |
| `428` | No `If-Match` |
| `503` | The curator has no clone of the memory yet, or there is no Matrix server name to make a person's commit address |

### Where are the schemas checked?

The schemas are checked in two places:

| Where | How |
|---|---|
| The curator's card store, in the curator's container only | It validates every card that it writes with `jsonschema`. For each check it builds the `urn:famstack:vault` resource from the memory repo (`card_schema.py`). |
| The format check, on the host | The host is stdlib-only. Thus it reads the same files with the standard library. `card_fields.py` reads the field names of each card type. `sidecar_schema.py` reads `schemas/sidecar.json` with every keyword that this schema uses, and raises on a keyword that it does not know. |

The `urn:famstack:vault` resource holds three formats:

- `person_id`: the declared people;
- `topic_id`: the ontology and the folders of the topic cards that the readers of the card may read. A card in the shared folder gets the shared topic cards. A card in a person's folder also gets that person's topic cards (`stack.filing.vocabulary`);
- `tag`: a topic id, or a keyword checked as `format: keyword` by `stack.tags.is_tag`.

A test holds the host's reader to the verdict of `jsonschema`.

### How to change a card from the terminal

To change a card, run:

```
stack memory card edit <card_id> --set f=v --add f=v --remove f[=v] --as <person>
```

The command reads the card and its `ETag` through core's door. It builds the merge patch and sends it. `--add` and
`--remove` change a list, which is sent whole. `--remove f` removes the field. The command prints the change, or each
refused field with what it may hold.

---

## What do `/history` and `/overview` answer?

### `/history`

`/history` is the memory's activity, newest first, read from the repo's log (`stack.cards.activity`). Each change has
these fields:

- its time;
- who made it;
- whether it is the machine's own work (`machine`);
- its kind in a word: `filed`, `replied`, `corrected`, `edited`, `refiled`, `migrated`, and so on;
- the cards that it touched, each with its `/go` link path.

The kind comes from the subject prefix and the `Migration:` trailer, by the closed commit grammar of ADR-018 §6 in
`stack.cards.commits`.

- A card and its sidecar count once.
- A change over many cards lists the first few cards and a `count`. It also lists how many of each type it touched.
- A change that touches no card that the audience may read is left out.

### `/overview/<view>`

`/overview/<view>?audience=<person>` gives the sections of one page of the memory. It builds them from the cards, with
no model call (`stacklets/memory/overview/`). These are the views:

- `me`, `family`, `person/<id>`;
- `topic/<card_id>`, `sender/<card_id>`;
- `documents`, `documents/<kind>`, `kind/<type>`, `senders`;
- `everything` (the menu's counts).

Each view gives only the sections that its page shows. These are the sections:

- up next, lists, topics, what changed in 14 days, bookmarks;
- documents by kind, senders, the page's cards;
- the links tallied, and the counts by type.

 The package's
docstring describes each field.

The answer is built from the graph of the audience. Thus no item, link or count comes from a card that the audience may
not read. A list gives its title and how many items are open, not its items.

| Case | Answer |
|---|---|
| `me` | Needs exactly one person in the audience |
| `person/<id>` | Also the person's topics and the cards about the person |
| A person, topic or sender that the audience may not read, or that does not exist | `404` |
| A document kind with no document that the audience may read | `404` |

---

## How to check the format: `stack memory check-format`

`stack memory check-format` lists where the vault does not meet the format yet. The cards API's `/report` gives the
same list, and the card browser shows it. The list names these problems:

- cards without a `card_id`, per type;
- a `card_id` that two files carry;
- links to a file that the vault does not have;
- files with no frontmatter, or with a `type` that is no card type;
- tags that are neither a topic id nor a keyword;
- a topic card whose folder is a topic id of the ontology, which wins;
- a `kind` outside its type's set;
- a list archive without `kind: archive`;
- a diary card that still says `medium`;
- a note whose sources and sidecar do not pair up;
- a sidecar whose card is gone;
- a sidecar value that its schema does not allow;
- fields that a card's type's schema does not name.

A change to a card with such fields is refused until the fields are removed. To remove one, run
`stack memory card edit <id> --remove <field>`.

The check is the admin's. It reads every card and writes nothing. A vault that meets the format gives an empty report.

---

## How to refile a note: `stack memory reprocess`

`stack memory reprocess <card_id>` refiles one note under today's rules. It reads the note's sources, never its summary
(ADR-018).

### What does the model read?

A correction in the chat and `stack memory reprocess` build the sources in one place,
`stack.filing.refile.refile_sources`, in this order:

1. The sources that the card and its sidecar keep. An older note's sources are read the way migration `memory/0008`
   converts them.
2. Each reply in the thread of each of the note's messages that the card does not hold yet. The bot runner reads them
   as the archivist. The room is the one of its rooms that has the message's event. The thread is its `m.thread`
   relations. Bot accounts are left out.
3. The reply in the chat, or `--msg` as a `note` source.

A source is added unless the note holds a source with its id. The words are not compared, because two replies with
the same words are two messages. Sources are only appended: the chat replaces no quote. The model is given exactly
this list. The preview returns it in `sources`, each record with its `quote`. Thus a surface that shows a card's
sources shows what the model read.

**Every original.** The model reads the words of every original, in order, and sees every photo. Thus a note of a
photo, a voice message and a line is rebuilt from all three.

**The form comes from the card.** A card whose text is a `> [!summary]` callout stays a summary. Any other card is a
clean version, also an empty one, and is rebuilt as a clean version of any length.

**The people.** A correction whose answer names nobody keeps the people that the card is about.

### How does a correction in the chat refile a note?

The archivist reads the card, its sidecar and the card's history once, with their versions, before it asks the model.
It writes the rebuilt card and sidecar in one commit on those versions. When either file changed since (a person's
edit while the model answers, a second correction at the same time), the curator refuses the commit. The reply then
gets the failed mark, and nothing of the change is lost.

When the history cannot be read (any failure but "no such card"), nothing is written, because the refile would drop a
person's edits. The reply gets the failed mark with the reason.

### What does the preview do?

The preview writes nothing to the memory. It prints these things:

- the card as it is and as it would be, both through the card renderer;
- the diff of the card and its sidecar;
- a proposal id.

It keeps the proposal in `<data>/memory/proposals/`, outside the memory's git tree.

The preview reads the card and its sidecar through the curator, and keeps their versions with the proposal.

### What does `--apply` do?

`--apply <proposal_id>` commits exactly the previewed card and sidecar, with no second model call. It sends exactly
those files to the curator as one commit, each on the version that the preview read. When either file changed since,
the curator refuses the whole commit and nothing is written (`changed`).

The commit is `reprocess: <title>`, with `Replaces: <sha>`. Thus one `git revert` undoes it.

**Who commits.** A refile is a machine rebuild. The memory bot commits it, with `Requested-By: <person>` naming who
asked. The sidecar's `made.requested_by` names the same person: `--by <person>`, or `admin` (the admin at the terminal)
when none is given.

**A correction.** `--msg "<text>" --by <person>` is a correction:

- The refile reads it with the replies.
- The sidecar keeps it as a `note` source by that person.
- The commit is that person's, with their words in its body (ADR-018 §6).

### How does a refile keep what people did?

A refile keeps what people did to the note (ADR-018 §7).

**The card's history.** The card's history has one reader, the curator's card store (`CardStore.history`, through
`stack.cards.history.card_versions`). The host asks for it with `GET /cards/<card_id>/versions`. The archivist in the
bot runner asks with `GitMirror.card_history`, which answers None for a card that the curator does not know (404) and
raises for any other failure.

The history follows the card across moves by its `card_id`. A move is a person's rename or move in Forgejo or a clone.
The archivist keeps a note at the path that it filed it under.

- A move is one commit that removes the card at its old path and adds it at the new one.
- The old file is followed only when it held the same `card_id`.
- The commit that put the card at its path is read on its own, for the files that it removed. The exception is the
  card's filing. Thus a note that never moved costs no extra read.
- Another card that lived at a path that the card had is never read as its past.

`history_of` reads what it answers.

**Whose commit counts.** `stack.cards.commits.is_machine` decides whose commit counts.

- A person's commit that carries a source is a correction, read from the sources. Such commits are a filing, a reply,
  or a refile with `--msg`.
- Every other person's commit gives the change to the clean version. The refile reads it as a message of the card
  conversation.
- It also takes the change to the fields, as operations (`stack.cards.fields`).

| Operation | Field | How it is found |
|---|---|---|
| `add`, `remove` | A list | By set difference. Thus the model's other suggestions still come in. |
| `set`, `unset` | A value | The change itself |

**Applying the operations.** The host sends the history to the bot runner with the request: `prior` (the clean version
before the first correction or edit), `edits` and `ops`.

The bot runner applies the operations when it composes the
card (`GitMirror.capture_entry`). It applies them after the model, in order. It checks each operation against the card
schemas (`schemas/cards/`), which it reads with the standard library. The check covers these things:

- the field, `readOnly`, `required`, type, `minLength` and `pattern`;
- the instance's people, topics and tags.

An operation that no longer fits is left out and named. It is printed, and is in `left_out` in the JSON.

**Notes without corrections.** A note without corrections or edits is refiled from its words alone, in one call.

**A card edited by hand.** A card edited by hand may show no clean version, because it would only repeat the words. The
capture prompt first gives it one, and the conversation starts from that (`clean_words.starting_point`).

### When does a refile refuse?

A refusal gives the reason and a `refusal` code:

| Code | Case |
|---|---|
| `not-a-note` | The card is not a note. |
| `no-sources` | The note has no sources. It was written in the memory, not filed. |
| `words` | The note has no words. Or its quotes and sidecar sources do not pair up. Or its words carry prompt text from an older reprocess. |
| `no-card` | No card has the id. |
| `no-proposal` | The model half made no proposal. |
| `unreachable` | The curator cannot be asked. The preview and the apply both refuse. |
| `changed` | The card or its sidecar changed after the preview. Only the apply refuses. |

### What does `--json` return?

| Command | Fields |
|---|---|
| A preview | `card_id`, `path`, `proposal_id`, `current` and `proposed` (each with `file`, `rendered` and `sidecar`), the `diff`, `clean_rejected`, `sources` and `left_out` |
| An apply | `commit` and `replaces` |

`clean_rejected` says why the model's clean version was not used. It is `""` when the clean version was used or none
was needed.

### Where does the model half run?

The model half runs in the bot runner (`reprocess-note` in the archivist's CLI entrypoint). It runs through a mirror
that composes the files and writes nothing (`GitMirror.capture_entry`). The bot runner never writes to the memory for
this command.

---

## The memory has one writer: the curator

The **curator** is the memory's one writer (ADR-019). It holds the memory's clone (`<data>/memory/vault`) and the card
store over it (`stacklets/memory/cards/`). Every change to the memory goes through it:

- the archivist's filings, declarations and list folds;
- the diary and the list archive;
- the card editor;
- `stack memory write`, `list`, `person`, `ontology` and `reprocess --apply`;
- the migrations;
- the seed of a new memory, and the person files.

No other process writes the clone. Nothing writes the memory through Forgejo's API.

### Where is the curator's API?

The API is on the curator, and through core's door at `http://127.0.0.1:42002/cards/api` on this Mac:

| Request | What it does |
|---|---|
| `POST /commits` | one commit of the changes, with the write token |
| `POST /sync` | Pulls Forgejo's latest and pushes what waits, now, with the write token. It answers the commit that the clone stands at afterwards (`head`). |
| `GET /files/<path>` | a file and the version to write it on |
| `GET /tree/<dir>` | every file under a directory, likewise |
| `GET /paths[/<dir>]` | the paths alone, of a directory or of the whole memory |
| `GET /filed-from/<id>/<dir>` | the note filed from the message `<id>`, under the directory `<dir>` at any depth; 404 when none was |
| `GET /pending` | the commits Forgejo does not have yet, and how the last cycle went |
| `GET /cards/<card_id>/versions` | the card's history, followed across its moves |

### How is a capture found again?

A capture is found by the messages it was filed from, never by its words or its link. The filing asks the curator
(`GET /filed-from/<id>/<dir>`, `MemoryAPI.filed_from`), because the curator's clone has every write as soon as it
is made. The archivist's own copy can lag a write by minutes.

- A note answers when one of its sources of role `original` has the id. A reply's id finds nothing, because a reply
  corrects a card. It is not what the card was filed from.
- A note without a sidecar answers when its frontmatter is `type: note` with this `capture_id`.
- Only notes under `<dir>` count. The filing asks under `<entity>/notes`, the folder the capture files into: `marge`
  for a capture in her own room, `homer/camping` for a personal topic.

A note's first filing asks this before the model:

- When a card holds the message already, nothing is written. A restart, a replay or a pin right after a filing does
  this. The room is told about the card as it is: its path, `card_id`, people and tags. A hand edit of the card stays.
- Else the note is written at the first free name of its path: `<name>.md`, `<name>-2.md`, `<name>-3.md`. A name is
  free only when its sidecar is free too. The write has an empty base, so a file that appeared meanwhile makes the
  curator refuse it. The filing then asks again before it tries the next name.

A correction reaches its card by its path. A bookmark keeps the hash of its link as its identity, so a link pasted
again updates its entry. An email keeps its thread.

### What does a write hold?

A write names these things for each file:

- `base`: the version that the file was read at, its git blob id. It is `""` for a file that must not exist yet.
- `by`: the person or bot that the write is by.
- `via`: the bot that writes it.

The store makes the message from `kind`, `title`, `body` and `trailers`, by the commit grammar (`commit_subject`). The
person is the author (ADR-015). The bot is the committer (`<bot>@local`). An example write:

```json
{"kind": "chore(lists)", "title": "marge changed Camping", "by": "marge",
 "via": "memory-bot", "changes": [{"path": "family/lists/camping.md",
 "text": "...", "base": "<version>"}]}
```

### What does the curator check?

Every write is checked before it is committed (`card_check.CardCheck`):

- A markdown file with a `type` meets its type's schema, with the instance's people and topics.
- A card keeps its `card_id`, or lists the one that it had in `former_card_ids`. The exception: another file still
  carries that id. Such a file is a copy, which may get an id of its own.
- A note's sidecar carries the note's `card_id`.

A page written by hand, without a `type`, goes in as it is.

A migration's commit (its `Migration:` trailer) is checked for identity only. The reason: migrations bring cards into
the format in steps, and the migration chain's test checks the end.

Writing the same text again commits nothing.

### How does the curator push?

The curator pushes before it answers.

- With Forgejo down, the commit stays in the clone, pending. The next cycle or `POST /sync` pushes it.
- When Forgejo and the clone both moved, the clone's own commits are replayed on top of Forgejo's. Each commit keeps
  its author and committer.
- A replay that conflicts is put back. The clone is then reported diverged until a person decides.
- Nothing is ever pushed by force.

### What does the curator answer?

| Status | Body |
|---|---|
| `201` | `{"commit", "pending", "mirrored"}` |
| `200` | An empty commit: nothing changed |
| Other | A problem document (RFC 7807), with a `reason` |

| `reason` | Status | Meaning |
|---|---|---|
| `malformed` | 400 | The request is malformed. |
| `unauthorised` | 401 | No valid write token |
| `changed` | 409 | A file changed since it was read. The answer names the `paths`. |
| `refused` | 422 | The write is refused. |
| `invalid` | 422 | A check failed. `errors` holds one `{"path", "field", "message"}` per field. |
| `missing` | 503 | No clone yet |
| `failed` | 500 | The write failed. |

### How does a caller reach the API?

Every caller reaches the API through the framework's client, `stack.memory.MemoryAPI`:

| Caller | Constructor |
|---|---|
| A command | `from_host(config)` |
| One of memory's hooks or migrations | `from_hook(ctx)` |
| The curator's and the bot runner's containers | `from_container()` |

The archivist uses the client too, with its own account as `via`. The reason: the docs stacklet imports none of
memory's code.

**The first start.** The curator clones the memory itself. It gives a memory with no commit its seed as the first
commit. Its API answers from its first moment, before there is a clone. `stack up memory` waits for `GET /pending`
through core's door. The install asks the curator to sync once Forgejo has the repository.

### Who commits through `ForgejoClient`?

`ForgejoClient` still writes the brain's scaffold, until the brain moves into the curator too. Every commit that it
writes names its committer:

- the client's `committer`, when it has one: a name and an address, with no account needed;
- else the account that the client signs in as. The client asks Forgejo for it once (`GET /user`).

The author is the person that a change is by or for, when the caller names one (ADR-015). The memory bot has no Forgejo
account. The memory stacklet writes the scaffold with the admin's token through `memory_forgejo`, which names the
memory bot as committer.
