"""stack memory diary — compile the memories room into the family diary.

The memories room is where a family records things for its future self:
voice notes to a child, a photo with a caption, a dinner conversation
someone hit record on. This reads the whole room back, files one card
per entry in the memory vault, and publishes the diary pages in the wiki
from those cards, in the words it was recorded in.

    stack memory diary                   compile and publish
    stack memory diary --dry-run         print the pages, write nothing
    stack memory diary letters           read a different room
    stack memory diary --burst-window 1  tighten sync-burst detection
    stack memory diary --force           read it all again from scratch

WHEN IT RUNS
    The curator compiles the diary on its nightly sweep, so new
    recordings reach the wiki overnight without anyone asking. Running
    it by hand does the same thing sooner.

    Every run is a full pass over the room rather than an append. It has
    to be: a reply to a memo from March can arrive in September, an edit
    can land on a year-old note, and a remark can turn out to be about a
    photo from last spring. A compiler that walked forward from where it
    last stopped would never attach any of them. It stays cheap because
    what each recording and each reading cost is kept against the
    message it belongs to, so a nightly pass pays only for what is new.

WHAT IT RECOVERS
    Matrix stamps an event with the time the server received it, never
    the time it was recorded. A memo made on a walk and synced three
    days later carries the sync date, which would file it under the
    wrong week forever. So the compiler prefers a date spoken inside the
    recording ("today is the sixteenth") over the timestamp, falls back
    to the timestamp for anything sent live, and refuses to guess for a
    recording that synced late without saying its own date -- those are
    filed under the week they surfaced and marked as unrecovered on the
    page.

    The habit that prevents the third case costs nothing: say the date
    at the start of the recording.

CARDS ARE THE RECORD
    Each entry becomes a card under `family/diary/entries/` in the
    memory vault: the words as they were recorded, the files, and a
    title, summary, facts, people and topics a model read out of it.
    The diary pages are compiled from the cards alone, so a correction
    made on a card (in Forgejo or Obsidian) shows on the pages. A card
    someone edited is never overwritten; the run lists it as kept.

    The words themselves are never rewritten. Transcription and reading
    both run on the AI stacklet configured for this household.

Runs inside `stack-core-bot-runner` (it has the whisper client, the LLM
client, and the brain working copy); this is a thin docker-exec, the
same shape as `stack memory wiki`.
"""

HELP = "Compile the memories room into the family diary"

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import dispatch  # noqa: E402


def run(args, stacklet, config):
    argv = sys.argv[3:]
    return dispatch("diary", *argv)
