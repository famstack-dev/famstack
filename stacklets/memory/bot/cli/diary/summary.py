"""The paragraph that opens each month of the diary.

    summaries = await month_summaries(entries, llm, summaries_cache, bucket="family",
                                 rebuild=False, only=None)    # "2026-10" -> paragraph

The one piece of writing on a diary page that is not the family's own.
The model writes it from the month's entries, quoted as recorded, and a
reply that is not prose is dropped. The paragraph is cached by the
month's digest, so a month that did not change is not written again.
"""

from __future__ import annotations

import re

from stack.ai.client import LLMError
from stack.ai.transcripts import TRANSCRIPTS

from ... import call_costs
from ...diary.pages import month_digest, month_key, pages_for, year_key
from stack.people.names import known_names

from .household_files import household_language
from .progress import err

# Two to four sentences.
_SUMMARY_TOKENS = 600
_SUMMARY_TIMEOUT_S = 180.0


# ── Recalling a month ─────────────────────────────────────────────────
#
# The one piece of writing on a diary page that is not the family's own.
# It opens a month and it is allowed to be warm, but it may not invent:
# the entries underneath are the record, and a summary that adds to them
# is a lie told about someone's childhood.


SUMMARY_PROMPT = """\
Write the opening paragraph of a family's diary page for {month}. Below
are that month's entries, quoted exactly as the family recorded them.

The people in this family are: {people}. No other family members
exist. A name in the entries that is not in this list is a mishearing:
leave it out.

Write two to four short sentences recalling that month, the way
someone in the family would remember it later. One moment per
sentence. Do not chain several events into one sentence with words
like "before", "while" or "and then". The summary is not a complete
account: pick at most four moments and let the rest go -- the entries
below the paragraph carry everything else.

Rules:
- Write in {language}. The diary belongs to a family that speaks it.
- Use only what the entries say. Never add an event, a feeling, a place
  or an outcome that is not in them.
- Keep every detail with the person the entry keeps it with. Do not move
  something one child did onto another child.
- Keep the direction of what happened. If one person did something for,
  to, or about another, do not swap them round.
- Write a warm chronicle in the third person: what the month had to
  tell. Prefer reported speech -- what people recorded, told and
  described -- over bare statements of fact. Use verbs, not
  nominalizations. Never a list of topics: pick the moments that
  matter and let the rest go.

  Wanted register (an invented example, not this family):
    "September had a lot to tell: Anna recorded the first tooth
    finally coming through, and around the kitchen table the summer
    at the lake came back to life -- including the shoe that stayed
    in the water."
  Not wanted: "In September Anna and Jonas reported on a holiday, a
  first tooth and a concert."
- An entry marked as a conversation has no speaker labels in its text.
  Name its participants and its topics. Never attribute a statement
  inside it to a named person.
- Never put a role in place of a name. When an entry does not say who
  else was there, name the people it could be, joined with "or", or
  leave them out.
- [unclear] marks a word that was not heard clearly. Never guess what
  it was, and never treat it as a person.
- Name people as the entries name them.
- Report what the entries report, and no more. Do not frame the month as
  an occasion, and do not describe an event the entries only mention in
  passing as though the family gathered for it.
- An entry marked "date unknown" happened at no stated time. Do not give
  it one.
- Plain, warm, specific. No marketing words. Never write "heartwarming",
  "cherished", "precious", "journey", "chapter", or a closing sentence
  about what the month meant.
- Return the paragraph and nothing else: no heading, no list, no
  preamble, no quotation marks around it.

Entries:
{evidence}
"""


def _unclear_words(entry) -> list[str]:
    """Words whisper did not hear clearly, for this entry's recordings.

    Read from the transcript records' quality data. The summariser is
    told these words are unreliable, so it cannot hang an event or a
    person on a misheard name.
    """
    words: list[str] = []
    for eid in entry.event_ids:
        record = TRANSCRIPTS.read(eid) or {}
        for w in (record.get("quality") or {}).get("low_words") or []:
            token = (w.get("word") or "").strip()
            if token and token not in words:
                words.append(token)
    return words[:5]


# What the month summary reads for an entry that has no words.
_KIND_ONLY = {"image": "(a photo)", "video": "(a video)", "file": "(a file)",
              "voice": "(a recording)"}


def _evidence(entries) -> str:
    """A month's entries as the summariser sees them.

    Each line names who recorded it and when, because attribution is the
    thing the model gets wrong: without the date and the sender pinned to
    the words, a summary quietly reassigns a first tooth to the wrong
    child.
    """
    out = []
    for entry in entries:
        when = ("date unknown" if entry.confidence == "uncertain"
                else entry.on.strftime("%-d %B"))
        # Sender only. The addressee is the classifier's reading, and
        # feeding one generation's guess into another compounds it: a
        # nickname misread as a third person becomes a third person in
        # the prose. Whoever a memo is spoken to is named in its words
        # anyway, where the model can read it as evidence.
        who = entry.sender.title()
        # The mode separates safe attribution from unsafe: a monologue
        # has one speaker (the sender); a conversation carries no
        # speaker labels inside its text.
        if entry.kind == "voice" and entry.mode == "dialogue":
            role = (f"{who} recorded a conversation "
                    f"(who said which line is unknown)")
        elif entry.kind == "voice":
            role = f"{who} spoke"
        else:
            role = who
        body = entry.body.strip() or _KIND_ONLY.get(entry.kind, "(an entry)")
        for sender, text in entry.comments:
            body += f"\n  {sender.title()} replied: {text.strip()}"
        # Unclear words are removed from what the summariser sees.
        # Telling the model a word is unreliable does not stop it from
        # using it; a word it never sees cannot become a person.
        for token in _unclear_words(entry):
            body = re.sub(rf"\\b{re.escape(token)}\\b", "[unclear]", body)
        out.append(f"- [{when}] {role}: {body}")
    return "\n".join(out)


def _month_page(in_month, *, bucket: str) -> str:
    """The path of the page a month's paragraph opens, as the brain holds it:
    the month's page among the diary's pages (`pages_for`), in `bucket`."""
    [month] = [page for page in pages_for(in_month, bucket=bucket)
               if page.view == "diary-month"]
    return f"{bucket}/{month.path}"


async def _summarise(entries, llm, *, bucket: str) -> str:
    """A paragraph recalling one month, or "" if the model cannot.

    Temperature 0, so a recompile of an unchanged month reads the same
    way. A failure returns empty and the page falls back to its factual
    opening -- a diary missing its introduction is fine, a diary whose
    introduction changes wording every night is not.
    """
    month = entries[0].on.strftime("%B %Y")
    prompt = SUMMARY_PROMPT.format(
        language=household_language(), month=month,
        people=", ".join(known_names()) or "unknown",
        evidence=_evidence(entries))
    try:
        text = await llm.complete("writer", prompt, temperature=0,
                                  max_tokens=_SUMMARY_TOKENS,
                                  timeout=_SUMMARY_TIMEOUT_S,
                                  on_cost=call_costs.recorder(
                                      "diary-month", _month_page(entries, bucket=bucket)))
    except LLMError as e:
        err(f"  could not summarise {month}: {e}")
        return ""
    # A model that answers with a heading or a bulleted list has ignored
    # the brief; the opening is prose or it is nothing.
    cleaned = " ".join(text.strip().split())
    if cleaned.startswith(("#", "-", "*")):
        err(f"  discarded a non-prose summary for {month}")
        return ""
    return cleaned


async def month_summaries(entries, llm, summaries_cache, *, bucket: str, rebuild: bool,
                     only: "list[str] | None") -> dict[str, str]:
    """The paragraph opening each month, by month (`2026-10`).

    The cached one when the month's entries are the ones it was written
    from; otherwise the model's, and without `llm` the last one written.
    A month outside `only` is never sent to the model.
    """
    months: dict[str, list] = {}
    for entry in entries:
        key = f"{year_key(entry.on)}-{month_key(entry.on)}"
        months.setdefault(key, []).append(entry)

    summaries = {}
    for key, in_month in sorted(months.items()):
        if only is not None and key not in only:
            summaries[key] = summaries_cache.latest(key)
            continue
        digest = month_digest(in_month)
        cached = "" if rebuild else summaries_cache.get(key, digest)
        if cached or llm is None:
            summaries[key] = cached or summaries_cache.latest(key)
            continue
        err(f"  summarising {in_month[0].on.strftime('%B %Y')}")
        summaries[key] = await _summarise(in_month, llm, bucket=bucket)
        if summaries[key]:
            summaries_cache.put(key, digest, summaries[key])
            summaries_cache.save()
    summaries_cache.save()
    return summaries
