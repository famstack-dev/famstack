"""The diary's pages: month, year and index, rendered from the entries (step 5).

    for page in pages_for(entries, bucket="family", room_id=room_id):
        publish(page.path, page.body)

The diary is a reading surface, not a report. It quotes and it links;
it never summarises. A model decided what date an entry carries and
what its card says about it; it never decides what the page quotes,
because the words on the page are the family's own. The words around
them come from `texts`, in the household language.
"""

from __future__ import annotations

import hashlib
import html
from datetime import date
from typing import NamedTuple

from .entries import DISTILL_MIN_WORDS, Entry
from .messages import kept_paths
from .texts import (and_list, counted, day_heading, kind_label, month_name, month_year, phrase)

# ── Step 5: render ────────────────────────────────────────────────────
#
# The diary is a reading surface, not a report. It quotes and it links;
# it never summarises. An LLM decided what date an entry carries and
# whether two files were one recording -- it never decided what the page
# says, because the words on the page are the family's own.

DIARY_DIR = "diary"


def _distills(entry: Entry) -> bool:
    """Whether the renderer shows the distilled view for this entry.

    Requires a gist and a long body. Without a gist the full text is
    the only faithful rendering. Short entries are already the right
    amount of detail. A message addressed to one person never
    distills, whatever its length: it is a personal message, and the
    diary posts it whole, framed by the addressee in its heading.
    """
    return (bool(entry.gist)
            and not (entry.addressee or "").strip()
            and len(entry.body.split()) >= DISTILL_MIN_WORDS)


def _permalink(room_id: str, event_id: str) -> str:
    return f"https://matrix.to/#/{room_id}/{event_id}"


def _entry_block(entry: Entry, *, room_id: str,
                 media: "dict[str, str] | None" = None) -> str:
    """One entry: who, what it was, and then their words untouched.

    `media` maps an event id to the site path of the file that message
    carried, for the entries whose file was archived. Resolving those
    paths is the caller's job (see `pages_for`); this module states
    where they go on the page and in what form.
    """
    # An addressee is rendered as the message names them ("Bart", "kids"),
    # not title-cased, so a group reads as a group. A message whose
    # addressee resolves to its own sender is a misread, not a dedication.
    to = (entry.addressee or "").strip()
    addressed = bool(to) and to.lower() != entry.sender.lower()
    lines = (_heading(entry, to if addressed else "") + _when_unknown(entry)
             + _words(entry, addressed=addressed) + _replies(entry)
             + _files(entry, media or {}) + _link_to_room(entry, room_id))
    return "\n".join(lines).rstrip()


def _heading(entry: Entry, to: str) -> list[str]:
    """The entry's heading and the line under it: who, for whom, what kind."""
    who = entry.sender.title()
    # How the date was derived is our concern, not the reader's, and it
    # would repeat under every entry on every page. Where it matters,
    # because we could not derive one, the callout below says so.
    if entry.title:
        # From a card: its title heads the entry, and who recorded it
        # and what it is follow on one line.
        by = f"{who}, {phrase('for')} {to}" if to else who
        lines = [f"### {entry.title}", f"*{by} · {kind_label(entry)}*", ""]
    else:
        heading = f"### {who} — {phrase('for')} {to}" if to else f"### {who}"
        lines = [heading, f"*{kind_label(entry)}*", ""]
    if entry.entry_id:
        # The heading's own anchor follows the title, which a correction
        # rewrites. This one is the card's id and does not change.
        lines.insert(0, f'<a id="{html.escape(entry.entry_id)}"></a>')
    return lines


def _when_unknown(entry: Entry) -> list[str]:
    """The callout for an entry whose day could not be found."""
    if entry.confidence != "uncertain":
        return []
    return [f"> [!warning] {phrase('when_unknown')}", f"> {entry.basis.capitalize()}.", ""]


def _words(entry: Entry, *, addressed: bool) -> list[str]:
    """The summary, then the family's words: whole, or distilled with the
    whole text folded below."""
    lines: list[str] = []
    # The summary opens the entry as narrative, above the family's words
    # and never in their place. A message spoken to one person gets none:
    # it is posted whole, with nothing generated in front of it.
    if entry.gist and not addressed:
        lines += [entry.gist, ""]

    if entry.body.strip() and _distills(entry):
        # The distilled view for long recordings: the summary above,
        # verified quotes, and the full transcript in a folded block.
        # verify_moments() guarantees each quote is an exact excerpt.
        for moment in entry.moments:
            lines += [f"> [!quote] {moment}", ""]
        lines += [f"> [!note]- {phrase('full_transcript')}"]
        lines += [f"> {line}" if line.strip() else ">"
                  for line in entry.body.strip().splitlines()]
        lines += [""]
    elif entry.body.strip():
        lines += [entry.body.strip(), ""]
    elif entry.kind == "voice" and not entry.comments:
        # A gated recording: the transcript was unusable and the words
        # stay off the page. The sentence tells the reader this is
        # deliberate. The audio link below stays the way to hear it.
        lines += [phrase("untranscribable"), ""]
    # A photo, clip or file without words shows only the file below: a
    # sentence saying words are missing would read as a placeholder.
    return lines


def _replies(entry: Entry) -> list[str]:
    """Each reply, quoted under who added it."""
    lines: list[str] = []
    for who_replied, text in entry.comments:
        lines += ['> [!quote] ' + phrase('replied').format(who=who_replied.title())]
        lines += [f"> {line}" for line in text.strip().splitlines()]
        lines.append("")
    return lines


def _files(entry: Entry, media: "dict[str, str]") -> list[str]:
    """The archived files, shown the way their kind is worth showing.

    A recording, a photo and a clip are the memory itself and belong on
    the page; anything else is a download and gets a link, because an
    embedded document pushes the rest of the day off the screen. The
    embed syntax is Obsidian's, which the wiki turns into the right
    player by reading the extension off the path.
    """
    lines: list[str] = []
    for path in kept_paths(entry.event_ids, media):
        if entry.kind in ("voice", "image", "video"):
            lines += [f"![[{path}]]", ""]
        else:
            lines += [f"[{phrase('kept_file')}]({path})", ""]
    return lines


def _link_to_room(entry: Entry, room_id: str) -> list[str]:
    """The link to the entry's first message in the room."""
    if not (room_id and entry.event_ids):
        return []
    label = {"voice": phrase("link_voice"), "image": phrase("link_image"),
             "video": phrase("link_video")}
    return [f"[{label.get(entry.kind, phrase('link_other'))}]"
            f"({_permalink(room_id, entry.event_ids[0])})", ""]


def year_key(on: date) -> str:
    return on.strftime("%Y")


def month_key(on: date) -> str:
    """A month's own slug, which is its number.

    The year is already the folder, so the file is `03.md` rather than
    `2026-03.md`: it reads as `/diary/2026/03`, and numbering sorts the
    explorer chronologically where month names would sort April before
    March.
    """
    return on.strftime("%m")


def month_digest(entries) -> str:
    """A fingerprint of everything a month's summary was written from.

    The summary is cached against this, so it is recomputed exactly when
    the month's content moves and not otherwise. It covers what the
    summariser is shown -- who, when, and the words, including remarks
    attached later -- so a memo surfacing months after the fact rewrites
    the page it lands on, while an untouched month keeps its paragraph
    word for word rather than being quietly reworded every night.
    """
    parts = []
    for entry in sorted(entries, key=lambda e: (e.on, e.at)):
        parts.append("\x1f".join([
            ",".join(entry.event_ids), entry.on.isoformat(),
            entry.confidence, entry.sender, entry.body,
            "|".join(f"{who}:{text}" for who, text in entry.comments),
        ]))
    return hashlib.sha256("\x1e".join(parts).encode("utf-8")).hexdigest()


def _by_year(entries) -> "dict[str, list[Entry]]":
    out: dict[str, list[Entry]] = {}
    for entry in entries:
        out.setdefault(year_key(entry.on), []).append(entry)
    return out


def _by_month(entries) -> "dict[str, list[Entry]]":
    out: dict[str, list[Entry]] = {}
    for entry in entries:
        out.setdefault(month_key(entry.on), []).append(entry)
    return out


def _recorded_by(entries) -> list[str]:
    """Who captured a year's entries, in order of first appearance.

    Senders only. A sender is a Matrix account and is therefore a fact;
    an addressee is the model's reading of who was spoken to, and a
    landing page is the wrong place for a guess -- it reads as a roster
    of the household. Addressees stay on the entries themselves, where
    a misread is visible next to the words that caused it.
    """
    seen: list[str] = []
    for entry in entries:
        name = (entry.sender or "").strip()
        pretty = name.title() if name.islower() else name
        if pretty and pretty not in seen:
            seen.append(pretty)
    return seen


def render_month(entries, *, room_id: str = "", summary: str = "",
                 media: "dict[str, str] | None" = None) -> str:
    """A month of entries, grouped by the day they happened.

    `summary` is an optional paragraph recalling the month. It opens
    the page as narrative. Material quoted below it is word-for-word
    from the recordings. The full promise is stated once, on the
    diary's front page, not on every month.

    `media` is the archived file for each event that has one, keyed by
    event id, in the form a page addresses it by.

    Entries whose date could not be recovered are still shown on the day
    they surfaced, under a heading that says as much. Hiding them would
    lose the memory to protect the timeline, which is the wrong trade for
    a diary.
    """
    if not entries:
        return phrase("no_entries")

    lines = [f"# {month_year(entries[0].on)}", ""]
    if summary.strip():
        lines += [summary.strip(), ""]

    current: date | None = None
    for entry in entries:
        if entry.on != current:
            current = entry.on
            # The heading is the day. An entry that is unsure of its
            # date says so in its own block -- putting "week of" in the
            # heading would cast that doubt over every other entry
            # filed the same day.
            lines += [f"## {day_heading(entry.on)}", ""]
        lines += [_entry_block(entry, room_id=room_id, media=media), ""]

    return "\n".join(lines).rstrip() + "\n"


def render_year(entries, *, bucket: str) -> str:
    """A year's landing page: its months, and who is in them.

    Deterministic. Counting entries and naming the people who appear is
    reading, not summarising, so nothing here needs a model and nothing
    here can drift between runs.
    """
    year = entries[0].on.strftime("%Y")
    lines = [f"# {year}", ""]

    n = len(entries)
    people = _recorded_by(entries)
    opening = phrase("year_opening").format(
        count=counted(n, "entry_one", "entry_many"))
    if people:
        opening += f", {phrase('recorded_by')} {and_list(people)}"
    lines += [opening + ".", "", f"## {phrase('months_h')}", ""]

    for key, month in sorted(_by_month(entries).items()):
        label = month_name(month[0].on)
        count = len(month)
        lines.append(
            f"- [{label}](/{bucket}/{DIARY_DIR}/{year}/{key}): "
            f"{counted(count, 'entry_one', 'entry_many')}")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_index(entries, *, bucket: str) -> str:
    """The diary's front door: what it is, and a way into every year.

    Links are root-based (`/<bucket>/diary/2026/about`), like every link
    the wiki generates: the wiki resolves links from the vault root, where
    a page-relative `2026/about` names no page.
    """
    lines = [
        f"# {phrase('diary_title')}",
        "",
        phrase("index_intro"),
        "",
    ]
    if not entries:
        # An empty diary is the one moment a family needs to be told
        # how to fill one. This page is where the wiki's diary link
        # lands, so it has to answer "what now" rather than report a
        # count of zero.
        lines += [phrase("getting_started"), ""]
        return "\n".join(lines).rstrip() + "\n"

    lines += [f"## {phrase('years_h')}", ""]
    for key, year in sorted(_by_year(entries).items(), reverse=True):
        count = len(year)
        months = len(_by_month(year))
        lines.append(
            f"- [{key}](/{bucket}/{DIARY_DIR}/{key}/about): "
            f"{counted(count, 'entry_one', 'entry_many')} "
            f"{phrase('across')} "
            f"{counted(months, 'month_one', 'month_many')}")
    lines.append("")

    # No note here about entries we could not date. It is a system
    # caveat in our own vocabulary, and the front door of a family's
    # diary is the wrong place for it. Each affected entry already
    # carries the warning on the page where it is read.
    return "\n".join(lines).rstrip() + "\n"


def home_link(bucket: str) -> str:
    """The diary's pointer, for a page that wants to link to it.

    Both the path and the wording live here, so the linking page needs
    to know neither. `bucket` is the shared bucket the caller prefixes
    every diary path with; see `pages_for`.
    """
    return (f"> [!tip] [{phrase('diary_title')}](/{bucket}/{DIARY_DIR}/about)\n"
            f"> {phrase('home_teaser')}")


class Page(NamedTuple):
    """One diary page: where it goes, what it says, and the entries it shows.

    `view` is the kind of view it is (ADR-017 §7): `diary-front`,
    `diary-year` or `diary-month`.
    """

    path: str
    body: str
    title: str
    view: str
    entries: list


def pages_for(entries, *, bucket: str, room_id: str = "",
              summaries: "dict[str, str] | None" = None,
              media: "dict[str, str] | None" = None,
              ) -> list[Page]:
    """Every page the diary publishes, as `Page`s.

    Three levels, because a diary outlives its first year: the root
    names the years, a year names its months, and a month holds the
    entries. Breadcrumbs come free from the path.

    A folder's own page is `about.md`, not `index.md`, matching every
    other entity in this wiki. That convention exists for a reason:
    Quartz serves a folder URL through its folder-page layout, which
    renders no body here, so an `index.md` would be a page whose
    contents nobody can read.

    Paths are relative to the shared bucket, which the caller prefixes
    -- the bucket is named in config (`family`, `office`, a surname).
    `bucket` is that name, needed only for the links between these pages,
    which the wiki resolves from the vault root.

    `summaries` and `media` arrive the same way and for the same
    reason: both are the result of work this module cannot do (a model
    call; a download and a file write), handed in keyed by month and by
    event id so rendering stays a pure function of its inputs.
    """
    out = [Page(f"{DIARY_DIR}/about.md", render_index(entries, bucket=bucket),
                phrase("diary_title"), "diary-front", list(entries))]
    for year, in_year in sorted(_by_year(entries).items()):
        out.append(Page(
            f"{DIARY_DIR}/{year}/about.md", render_year(in_year, bucket=bucket), year,
            "diary-year", in_year,
        ))
        for month, in_month in sorted(_by_month(in_year).items()):
            out.append(Page(
                f"{DIARY_DIR}/{year}/{month}.md",
                render_month(in_month, room_id=room_id,
                             summary=(summaries or {}).get(f"{year}-{month}", ""),
                             media=media),
                month_year(in_month[0].on), "diary-month", in_month,
            ))
    return out
