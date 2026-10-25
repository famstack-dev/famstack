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
    stack memory diary --preview 3       the 3 newest entries' cards, as they
                                         are and as the current prompts
                                         make them; writes nothing
    stack memory diary --rebuild         write every card again under the
                                         current prompts
    stack memory diary --limit 20        only the newest 20 messages; always
                                         a dry run, it writes and removes
                                         no card and publishes no page

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

    The model never rewrites a card on its own. A compile writes a card
    for a new entry, and writes a card again only when its entry
    changed: a message joined it, its words were edited, a reply came.
    New prompts after an update, a new topic or a new person change no
    entry, so no card. Each card records the prompts it was made under
    (`prompt_template`), and a compile says how many were made under
    older ones. `--rebuild` writes those again under the current
    prompts; `--preview` shows what that does to the newest first.

    The words themselves are never rewritten. Transcription and reading
    both run on the AI stacklet configured for this household.

PREVIEW BEFORE A REBUILD
    `--preview N` reads the N newest entries (people's entries, never the
    archivist's posts) under the current prompts, and shows each one's
    card as it is in the memory and as `--rebuild` would write it: title,
    description, people, topics, summary, facts, quotes and words, with
    what changed marked - and +, and whether the next compile writes it
    or keeps the card on file. It asks the model once for each entry not
    read under the current prompts yet. It writes nothing: no card, no
    commit, no page. It does keep the readings it paid for, so the
    rebuild that follows does not pay for them again. With `--json`, both
    cards of each entry.

Runs inside `stack-core-bot-runner` (it has the whisper client, the LLM
client, and the brain working copy); this is a thin docker-exec, the
same shape as `stack memory wiki`.
"""

HELP = "Compile the memories room into the family diary"

import sys

from ._common import dispatch, dispatch_report


def run(args, stacklet, config):
    argv = sys.argv[3:]
    if "--preview" not in argv:
        return dispatch("diary", *argv)
    report = dispatch_report("diary", *argv)
    if "error" not in report:
        print(render_preview(report))
    return report


# ── The preview, for people ───────────────────────────────────────────

# The parts of a card a person reads, in the order the card shows them.
_FIELDS = ("date", "title", "description", "people", "topics", "summary",
           "facts", "quotes", "replies", "words")
_STATES = {
    "new": "new, no card on file yet",
    "changed": "changed",
    "unchanged": "unchanged",
    "kept": "kept, a person changed this card and the compile leaves it",
}
# What the next compile does with a card that a rebuild changes.
_NEXT_COMPILE = {
    "writes": "the next compile writes it",
    "keeps": "the compile keeps the card on file until --rebuild",
}
# An unchanged recording can run to pages; one line of it names it.
_WORDS_SHOWN = 100


def render_preview(report: dict) -> str:
    """The preview's report as text: per entry, each field of the card on
    file and of the card the compile makes, `-` and `+` where they differ."""
    entries = report.get("entries") or []
    lines = [f"The {len(entries)} newest diary entries, read with the current "
             "prompts. Nothing was written."]
    for entry in entries:
        before, after = entry.get("before"), entry["after"]
        state = _STATES.get(entry["state"]) or str(entry["state"])
        if entry["state"] in ("new", "changed"):
            state += "; " + _NEXT_COMPILE.get(entry.get("next_compile", ""), "")
        lines += ["", f"{after['date']}  {after['title']}  ({state})", f"  {entry['path']}"]
        for field in _FIELDS:
            lines += _field(field, before.get(field) if before else None, after.get(field))
    return "\n".join(lines)


def _field(name: str, old, new) -> list[str]:
    """One field: its value once when it stayed, the old and the new when not."""
    old_lines, new_lines = _as_lines(old), _as_lines(new)
    if old is not None and old_lines == new_lines:
        if name == "words" and old_lines:
            shown = " ".join(old_lines)
            old_lines = [shown if len(shown) <= _WORDS_SHOWN else shown[:_WORDS_SHOWN] + "..."]
        marked = [f"  {ln}" for ln in old_lines]
    else:
        marked = [f"- {ln}" for ln in old_lines] + [f"+ {ln}" for ln in new_lines]
    label = f"  {name:<12}"
    return [(label if i == 0 else " " * len(label)) + ln for i, ln in enumerate(marked)]


def _as_lines(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [ln for ln in str(value).splitlines() if ln.strip()]
