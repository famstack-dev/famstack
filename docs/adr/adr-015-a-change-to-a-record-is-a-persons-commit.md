# ADR-015: A Change to a Record Is a Person's Commit

## Status
Proposed.

## Context
A record is a file in the family memory that a bot derived from a source: a
document, a note, a bookmark, an email thread, a diary card. Records are
regenerated when the source is replayed (ADR-010), for example after a better
model or prompt. People also change them, in three ways:

- a reply in chat ("it's ceramics", "that was on Tuesday"), applied by a bot;
- the agent, through `stack memory write --by <person>`;
- by hand, in Forgejo's web editor or in a clone opened in Obsidian.

Today each record type treats those changes differently:

- **Notes and bookmarks** keep the replies of their thread in the entry, under
  the replier's name, and a reprocess folds them back in.
- **Diary cards** apply a correction once and are written unsigned from then
  on: the card is the family's, and the compile never touches it again
  (`stacklets/memory/bot/diary_card.py`, `render(family_owned=True)`). The
  correction's words are kept by the vault commit, not on the card. The card is
  frozen: no later improvement reaches it.
- **Documents and emails** are rewritten whole by a reprocess. A hand edit is
  lost without a trace.

A fingerprint on every record was considered, so that a writer rewrites a file
only while it still matches. It protects an edit by freezing the file, the same
cost the diary pays today, for an edit path nobody has used yet.

Git already records who changed a file, when, in what order and how. It cannot
tell today: the bots write each commit with the family member as both author
and committer (`lib/stack/forgejo.py`, `payload["committer"]`), so a filing
the archivist made for Homer looks exactly like an edit Homer made. On a test
instance, all of the last 300 commits had the same author and committer.

## Decision
**A change to a record is a commit by a person. Git is the change record; there
is no second store.**

1. **Author and committer say who.** The author is the family member the
   change is by or for. The committer is the account that wrote the commit.
   Bots commit as their own account (`archivist-bot`, `memory-bot`) and keep
   the family member as author. Forgejo's file API takes the two separately.
   A family member's address is `<person id>@<Matrix server name>`
   (`marge@simpson`) on every path that writes, mirroring their Matrix id, so
   no real email address enters the memory repo; a bot's is its Forgejo
   account's. Without a server name the write is refused, never signed with a
   made-up address (amended 2026-10-22).
2. **A commit whose committer is a person is a person's change.** That covers
   an edit in Forgejo's web editor and a push from a clone, with nothing for
   the person to add.
3. **A bot that writes a person's change says so** with a trailer naming what
   asked for it:

   ```
   correct: Abe's Birthday Lunch

   This lunch was on Saturday 15 March 2025, not in April.

   Correction: $Hk2…:simpson
   Set: date: 2025-03-15
   ```

   `Correction:` carries the event id of the chat message, or the command when
   there is none (`Correction: stack memory write`). The message body holds the
   person's words. `Set: <field>: <value>` lines hold the structured result
   where the change has one (a date, the people, the title).
4. **Everything else a bot commits is derived** and may be replaced by a
   regenerate: a filing, a reprocess, a compile, a rename.
5. **A regenerate keeps the person's changes.** It derives the record from its
   source again, then applies the person's commits to that file since its first
   derivation, oldest first:
   - a commit with `Correction:` is applied again: `Set:` lines exactly, the
     words through the model with the new reading;
   - a commit without one (a hand edit) is merged onto the new output, as git
     merges two branches, with the derived version the person edited as the
     base. On a conflict the person's version wins, the file is left as it is,
     and `stack doctor` names it.
6. **Diary cards stop being frozen.** A corrected card is regenerated like any
   other record, with its corrections applied again. The digest no longer
   decides ownership.

Rejected:
- A fingerprint per file: it freezes what it protects.
- A list of changes in the frontmatter: the file grows with every change and
  repeats what git already holds.
- `Co-authored-by:`: it says two people wrote a commit, not which part is whose,
  and is meant for people working together.

## Order
1. Bots commit as their own account (`lib/stack/forgejo.py`).
2. Bots add `Correction:` and `Set:` to the changes they write for a person:
   thread corrections, diary corrections, `stack memory write`.
3. A migration (#220) records the commit where this starts. Before it, author
   and committer do not say who, so only the commits that were already a
   person's change by their subject (`correct:` on a diary card) count; every
   other older commit counts as derived.
4. Reprocess and the diary compile read the person's commits of a file and
   apply them again. The diary's freeze goes.

## Consequences
- A person's change survives every regenerate, or the file is kept and named.
  Nothing is lost without a trace.
- Who changed what is in the history every git tool shows ("authored by Marge,
  committed by archivist-bot"), so the files keep their meaning without
  famstack.
- The history of the family memory must never be rewritten (no squash, no force
  push): it now carries the changes, not only their past.
- A regenerate reads the file's history, one request per file. A file renamed
  since a change is followed by its card id, not its path.
- Lists, declared people and facts are state, not records. They are written
  directly and never regenerated, and this decision does not change them.

## Open
- Whether a push from a clone keeps the person as committer depends on the
  clone's git settings. A clone set up by famstack sets it; one a family member
  made by hand might not.
- Replaying words through the model can read them differently on a new
  version. If that shows up in practice, more changes get `Set:` lines.
