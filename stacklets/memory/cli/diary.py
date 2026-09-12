"""stack memory diary — compile the memories room into the family diary.

The memories room is where a family records things for its future self:
voice notes to a child, a photo with a caption, a dinner conversation
someone hit record on. This reads the whole room back and publishes it
as diary pages in the wiki, in the words it was recorded in.

    stack memory diary                   compile and publish
    stack memory diary --dry-run         print the pages, write nothing
    stack memory diary --room memories   read a different room
    stack memory diary --burst-window 1  tighten sync-burst detection

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

NOTHING IS SUMMARISED
    The model is asked to read each message, never to rewrite one. What
    lands on the page is what was said. Transcription and reading both
    run on the local AI stacklet; the room's contents never leave the
    box.

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
