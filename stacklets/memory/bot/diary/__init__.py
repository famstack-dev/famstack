"""The family diary: the memories room turned into dated entries, cards and pages.

The memories room is where a family talks to its future self: voice
memos to a child, a photo with a caption, a dinner-table conversation
someone hit record on. It is also a mess. Recordings arrive days after
they were made, one memo gets split across two files, a caption shows
up ninety seconds behind its picture, and the only statement of when
something happened is a sentence spoken inside the audio.

This package is the part of the compiler that does not touch the world:
given the room's events and a reading of each message, it works out
what happened, when, and renders it. Every I/O concern (paginating
Synapse, downloading audio, calling whisper, calling the model, writing
the memory and the wiki) lives in the command, `bot/cli/diary/`. That
split lets the hard parts (which messages form one entry, which date
wins) be tested without a rig.

    events --resolve--> messages --group_room--> message groups
           --compile_entries--> entries --to_card--> cards (the source)
           --to_entry--> entries --pages_for--> month, year and index pages

One module per step, imported by its name (`from .diary.card import Card`):

    texts       the words a family reads, in the household language
    household   the names this family uses, for whisper and for a card
    messages    `resolve`: the room's events as messages
    grouping    `group_room`: the message groups, one entry each
    dating      `date_for`: the day of an entry and how it was found
    entries     `compile_entries`: one entry per group; `verify_moments`
    extraction  what a model read out of an entry
    card        an entry as a card and its sidecar, and back
    plan        which files a compile writes
    pages       the diary's pages, rendered from the entries

The diary quotes, it does not invent. Words shown as someone's own (a
quote, a transcript) come from the recording unchanged. Narrative text
(summaries, chronicle paragraphs) is permitted and renders as narrative,
never as quotation. Every entry links to its source events, and the
audio stays the archival original. The classification step returns a
small record of facts rather than prose for the same reason: generated
text must never blend into quoted text.

The package holds no code: each module is imported by its own name, so
there is one way to reach every name.
"""
