"""stack memory diary: compile the memories room into the family diary.

The memories room is already full. Somebody has been recording voice
memos to their kids for months, and nothing has ever read them back.
This command is the reader: it walks the room's whole history, decodes
every recording, works out when each one actually happened, and
publishes the result as diary pages in the family wiki.

It is a batch, not a listener. The room is the source of truth and it
only grows, so re-running recompiles the same history into the same
pages rather than appending to something stateful. That makes the
command safe to run again after a bad model day, and it means the
expensive half is cached rather than repeated: transcripts live in
`TRANSCRIPT_DIR`, keyed by event id, shared with the bots.

    stack memory diary                      compile and publish
    stack memory diary --dry-run            print the pages, publish nothing
    stack memory diary letters              a different room
    stack memory diary --group-window 1     see below
    stack memory diary --force              re-read everything from scratch
    stack memory diary --rebuild            write every card again under the
                                            current prompts (a compile
                                            otherwise writes a card again
                                            only when its entry changed)
    stack memory diary --retranscribe       decode all recordings again
                                            (after a whisper config or
                                            vocabulary change)
    stack memory diary --preview 3          the 3 newest entries' cards, as
                                            they are on file and as
                                            --rebuild makes them now, as
                                            JSON (the host renders it);
                                            writes nothing but the reading
                                            caches
    stack memory diary --limit 20           only the newest 20 messages,
                                            always a dry run: writes no
                                            card, removes none, publishes
                                            no page
    diary pages [--fast] [--month 2026-10]  the pages again from the records
                                            on file, no room read (the
                                            curator, after a push)

WHY THE GROUP WINDOW IS A KNOB
    What a person sends in one go is one entry, and a pause of 5 minutes
    (or a message "--") starts the next (`stack.message_groups`). In a
    real room separate moments are hours apart. A *replayed* test corpus
    compresses those hours to seconds and needs a window of about a
    second, or every run of one sender becomes one entry. So the caller
    can pick, in seconds.

ENTRIES ALREADY ON FILE
    A message on a card keeps that card's grouping, on every compile
    and on `--rebuild`, whatever rule made the card; no new message
    joins it, except a reply in its threads. Only messages on no card
    are grouped by the rule above.

A GO STILL OPEN WAITS
    A group that may still take messages when the compile runs (its
    last message is less than the window old, and no "--" ended it) gets
    no card and no notice; a later run files it whole. `STACK_NOW` sets
    the moment for a test (`stack.clock`).

A dry run publishes no pages but still writes the transcript, reading
and summary caches, and still keeps the originals it reads. None of
those are output: discarding them would make a preview run as expensive
as the compile that follows, and a preview whose pages embed nothing
would not be a preview of what publishing produces.

Runs inside `stack-core-bot-runner`: it has the whisper client, the LLM
client, the brain working copy, and the Matrix admin credentials. The
host-side wrapper is a thin docker-exec, like `stack memory wiki`.

One module per step, in the order a compile runs them:

    options          the command line
    room             the room through the admin API, its files kept
    transcribe       each recording's words, decoded once
    reading          the model's reading of each message (the day it names)
    household_files  the family's names, topics and language, from its files
    entry_reading    the model's reading of an entry for its card
    cards            each entry's card, read only where it needs it
    store            the cards in the memory, through the curator
    summary          the paragraph opening each month
    projection       the pages from the cards on file (`diary pages`)
    preview          `--preview n`
    report           the JSON report the archivist posts from
    correction       a family member's correction (`diary correct`)
    compile          `run`, the steps above in order
    progress         the progress lines on stderr

The pure half (grouping, dating, the card format, the pages' text) is
the package `bot/diary/`.
"""

from __future__ import annotations

from .compile import run
from .correction import correct
from .projection import pages

__all__ = ["HELP", "correct", "pages", "run"]

HELP = "Compile the memories room into the family diary"
