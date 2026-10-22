"""stack memory write: replace a vault page, or file a new note, and say what that did.

Reading the vault has always been fs-shaped: the agent runs `read_file` on
`vault/family/camping/todos.md` and it works, because every model is trained
on it and nobody had to invent a retrieval verb. Writing had no counterpart,
so it grew domain verbs instead -- `topic <slug> todo strike "<item>" --by
<person>` -- and a model that can describe the right list perfectly still
could not perform twenty string-matched calls in a row to produce it.

This is the write counterpart. One page in, one page out, attributed. That
git and Forgejo are underneath is implementation detail, the same way
`/go/topic/camping/todo` hides where a page actually lives.

WHERE THE CONTENT COMES FROM
    Not argv. The agent reaches the host through a plaintext socket that
    splits on shlex, and a markdown document does not survive that. It writes
    the page into its own data directory instead -- already mounted
    read-write, no new transport -- and this command reads it from the host
    side of the same mount. `--from` takes an ordinary host path for a person
    at a terminal.

TWO WAYS TO SAY WHAT THE PAGE SHOULD BECOME
    By default the buffer holds the finished page. With `--patch` it holds a
    JSON list of the edits `apply_patch` produces, and they are applied here,
    to the document Forgejo hands back at this instant -- not to the copy the
    caller read some seconds ago.

    That difference is the reason `--patch` exists. A whole-page write asserts
    "the page is now this" and cannot tell that somebody changed it in the
    meantime: on the rig the archivist filed three items at 16:14:24 and the
    agent replaced the same page at 16:14:26 from an older read, and the
    archivist's work vanished with nothing reported. A patch applied against
    the current text either fits or says which line it could not find, and a
    caller that is told which line is a caller that can read the page again
    and retry. Whole-page writes stay for the cases that really are a rewrite
    (splitting one list into two), where there is no smaller thing to say.

WHAT COMES BACK
    Not "ok". For a list page, `stack.list_doc` compares before and after and
    reports what the edit actually did: ticked off, added, moved, reworded,
    and -- named in full, always -- removed. A caller that rewrote a page and
    silently dropped six items learns so immediately, which is the whole
    reason a primitive write is safe to hand to a model at all. Any other
    page gets the honest general answer, how many lines went each way.

    That same sentence is the commit subject. The vault's history is read --
    by a person scrolling Forgejo, and by anyone asking the agent what
    changed this week -- and a log of two hundred identical "updated
    todos.md" lines answers none of it. What the edit did is already known
    at the moment of writing, so it costs nothing to say it where it lasts.

A NEW NOTE IS FILED, NOT WRITTEN
    A page that does not exist yet, in a `notes/` folder, that is a note
    (`type: note`, or no type at all) is filed the way a note sent in the
    chat is: by the archivist's capture pipeline, the one `stack memory
    capture` runs. It writes the clean version of the words, quotes them
    under `## Sources` and keeps the sidecar (ADR-018), so the note can be
    refiled from its words later. Written as given, it would have none of
    that.

        stack memory write homer/notes/grill.md --by homer --from note.md
          Filed a new note: Grill needs charcoal
            homer/notes/2026/10/grill-needs-charcoal-3a338e.md (note-7f3a9c21e4)

    The folder is the bucket: `homer/notes/...` files into Homer's bucket,
    `family/camping/notes/...` into the camping topic. The pipeline names
    the file, so the answer gives the path it chose and the card id, which
    is the name that keeps working. The words are the body; frontmatter is
    not filed, because the pipeline takes no title or field from a caller
    and makes them as it does for a chat message.

    When the pipeline cannot be reached or files nothing, the command fails
    with its message and writes nothing. A plain page in its place would be
    a note without sources, reported as filed. A page that exists already,
    a `--patch`, and any page that is not a note are written as above.
"""

HELP = "Replace a page in the family memory vault, or file a new note"

import difflib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import body_only, update_memory  # noqa: E402

from stack.bot_runner import call  # noqa: E402
from stack.cards.commits import commit_subject  # noqa: E402
from stack.cards import CardIdError, card_id_from, with_card_id  # noqa: E402
from stack.frontmatter import FrontmatterError, parse as parse_frontmatter  # noqa: E402
from stack.links import go_card, go_page, public  # noqa: E402
from stack.people import PEOPLE_DIR, PersonError, read_person  # noqa: E402
from stack.list_doc import diff  # noqa: E402
from stack.lists import LISTS_DIR  # noqa: E402
from stack.page_patch import apply_edits  # noqa: E402

# Where the agent leaves a page it wants written. Its data dir is bind-mounted
# read-write into the container at ~/.nanobot, so the container writes here and
# the host reads the same bytes with no transport in between.
_AGENT_BUFFER = "agent/.write-buffer"

# The diary builds its index, year and month pages from the cards under
# `diary/entries/` into the wiki's copy of the vault (`diary.pages_for`). A
# write to one of those paths lands in the family's own files instead, and
# the build skips a page it did not make, so the written text replaces the
# diary's page for good.
_DIARY_PAGE = re.compile(r"^[^/]+/diary/(?!entries/).+\.md$")

# The capture pipeline runs in the bot runner, beside the archivist, and is
# reached through the entrypoint `stack memory capture` dispatches to.
_CAPTURE = "/stacklets/docs/bot/cli_entrypoint.py"

# The receipt `stack memory capture` prints for a filing (`render_receipt`):
# a `Captured: <title>` line, then the path and the card id it was filed at.
_CAPTURED = re.compile(r"^Captured: (.*)$", re.MULTILINE)
_FILED_AT = re.compile(r"^\s+(vault|card):\s*(\S+)\s*$", re.MULTILINE)

_USAGE = ("usage: stack memory write <vault-path> --by <person> "
          "[--from <file>] [--patch] [--dry-run]\n"
          "  e.g. stack memory write family/camping/todos.md --by marge")


def _check_frontmatter(before: str, after: str, page: str) -> None:
    """Reject a write that breaks or drops the page's frontmatter."""
    if after.lstrip().startswith("---") and not after.startswith("---\n"):
        raise ValueError(
            f"{page} must start with '---' at column one; the write begins "
            "with whitespace before the frontmatter. Re-send the page with "
            "the frontmatter block exactly as you read it.")
    if before.startswith("---\n") and not after.startswith("---\n"):
        raise ValueError(
            f"{page} has frontmatter and this write drops it. Keep the "
            "frontmatter block exactly as you read it, then the body.")
    try:
        parse_frontmatter(after)
    except FrontmatterError as e:
        raise ValueError(
            f"{page}: frontmatter does not parse ({e}). Keep the "
            "frontmatter block exactly as you read it.") from e


def _keep_card_id(before: str, after: str, page: str) -> str:
    """The page with its card id: a new page gets one, a page that had one keeps it.

    A card id is made once (ADR-016), so a rewrite that drops it, or
    changes it, gets the old one back. A page that was there without one
    is written as it comes: the curator gives it its id, so a write changes
    only what it says. So is a page the rule cannot place (a `type` that is
    no card type), which the curator reports.
    """
    try:
        kept = card_id_from(parse_frontmatter(before))
        if before.strip() and not kept:
            return after
        return with_card_id(after, page, kept)
    except (CardIdError, FrontmatterError):
        return after


def _check_person(after: str, page: str) -> None:
    """Reject a person file the readers would skip.

    Every reader leaves out a file that breaks the person schema, so
    the write is where the family hears about it, not a log.
    """
    parts = page.split("/")
    if len(parts) != 3 or parts[1] != PEOPLE_DIR:
        return
    try:
        read_person(Path(page).stem, after)
    except PersonError as e:
        raise ValueError(
            f"{page} is a person file and {e}. It needs `type: person`, "
            "a `title` with the full name and, optionally, `aliases` as a "
            "list.") from e


def run(args, stacklet, config):
    argv = list(args or [])
    actor, argv = _opt(argv, "--by")
    source, argv = _opt(argv, "--from")
    as_patch = "--patch" in argv
    preview = "--dry-run" in argv
    paths = [a for a in argv if not a.startswith("-")]

    if len(paths) != 1 or not actor:
        return {"error": _USAGE}

    data_dir = Path(config["data_dir"]) if config and config.get("data_dir") else None
    if data_dir is None:
        return {"error": "no data_dir configured"}

    # The agent addresses pages the way it reads them, under `vault/`. Strip it
    # so the caller's mental model and the repo path can differ without the
    # caller having to know they do.
    repo_path = paths[0].strip().removeprefix("vault/").lstrip("/")
    if not repo_path.endswith(".md"):
        return {"error": f"{repo_path!r} is not a page (expected a .md path)"}
    if _DIARY_PAGE.match(repo_path):
        return {"error": (
            f"{repo_path} is a page the diary builds from its entries, and a write "
            "here would cover it. An appointment goes on the calendar with "
            "list_edit (add with due); a diary entry is changed in its own card "
            "under diary/entries/.")}

    buffer = Path(source).expanduser() if source else data_dir / _AGENT_BUFFER
    try:
        content = buffer.read_text(encoding="utf-8")
    except OSError as e:
        return {"error": f"nothing to write: cannot read {buffer} ({e})"}
    if not content.strip():
        return {"error": "nothing to write: the page is empty"}

    actor = actor.strip().split(":")[0].lstrip("@") or "someone"

    edits = None
    if as_patch:
        try:
            edits = json.loads(content)
        except json.JSONDecodeError as e:
            return {"error": f"--patch expects a JSON list of edits ({e})"}

    # What this page would file as a note, if there is no page here yet.
    note = None if as_patch else _note(repo_path, content)

    # Captured from inside the transform so the comparison is against the
    # canonical file Forgejo hands back, not a local clone that may lag. For
    # a patch that is not merely bookkeeping: `prior` is the text the edits
    # are matched against, which is what makes a concurrent write visible
    # instead of silently overwritten.
    seen: dict[str, str] = {}

    def _replace(prior: str) -> str:
        seen["before"] = prior or ""
        # Whether the page exists is decided here, against what Forgejo
        # holds now. A new note is filed by the capture pipeline below, so
        # this hands back what is there: no change, and no commit.
        if note and not seen["before"]:
            seen["new_note"] = "yes"
            return seen["before"]
        after = apply_edits(prior or "", edits) if as_patch else content
        seen["after"] = after if after.endswith("\n") else after + "\n"
        # The wiki refuses a page whose frontmatter does not parse, so a
        # bad write silently drops the page from the family's view. This
        # seam sees every mutation, so the check lives here. ValueError
        # becomes the error message the caller relays to the model.
        _check_frontmatter(seen["before"], seen["after"], repo_path)
        seen["after"] = _keep_card_id(seen["before"], seen["after"], repo_path)
        _check_person(seen["after"], repo_path)
        if _is_list(repo_path):
            _check_list_write(diff(seen["before"], seen["after"]), as_patch=as_patch)
        # A preview still wants the *current* page to compare against, so it
        # takes the same trip and then hands back what was already there:
        # an unchanged file is a no-op, and a no-op does not commit.
        return seen["before"] if preview else seen["after"]

    # `update_memory` turns a transform's ValueError into an error envelope,
    # and PatchError is one -- so a patch that no longer fits arrives here as
    # a message, not an exception. Name the page it was meant for; the rest of
    # the sentence already says which line and what to do.
    result = update_memory(
        config, repo_path, _replace, actor=actor,
        message=lambda before, after: _commit_message(
            actor, repo_path, describe(before, after, repo_path)),
    )
    if "error" in result:
        if as_patch and isinstance(result.get("error"), str):
            result = {"error": f"could not patch {repo_path}: {result['error']}"}
        return result
    if note and seen.get("new_note"):
        words, bucket = note
        if preview:
            return _preview_note(repo_path, bucket, actor)
        return _file_note(repo_path, words, bucket, actor, config)

    before, after = seen.get("before", ""), seen.get("after", "")
    change = diff(before, after) if _is_list(repo_path) else None
    told = describe(before, after, repo_path)

    if preview:
        print(f"Would write {repo_path} (by {actor}); nothing committed\n  {told}")
        return {
            "ok": True, "committed": False, "preview": True, "path": repo_path,
            "summary": told,
            "destructive": bool(change and change.destructive()),
            "removed": list(change.removed) if change else [],
        }

    if not result.get("committed"):
        print(f"{repo_path} was already exactly this; nothing to commit")
        return {"ok": True, "committed": False, "path": repo_path}

    # Forgejo has the commit; the agent's mount and the wiki are fed by the
    # curator and may be a beat behind. Say that as a delay, never as a
    # doubt: a model that reads anything short of "done" writes the page
    # again, and the second write is a duplicate nobody asked for.
    mirrored = bool(result.get("mirrored"))
    lag = "" if mirrored else "\n  The wiki and the vault mount catch up shortly."
    # The touched page's wiki link, so the reply can cite it. A card is
    # linked by the card id in its frontmatter, which `_replace` has
    # already checked parses.
    home_url = (config or {}).get("home_url", "")
    url = public(go_page(repo_path, parse_frontmatter(after)),
                 f"{home_url}/go" if home_url else "")
    link = f"\n  {url}" if url else ""
    print(f"Wrote {repo_path} (by {actor})\n  {told}{link}{lag}")
    return {
        "ok": True, "committed": True, "path": repo_path, "by": actor,
        "summary": told, "mirrored": mirrored,
        "destructive": bool(change and change.destructive()),
        "removed": list(change.removed) if change else [],
    }


# ── A new note ───────────────────────────────────────────────────────────

def _note(repo_path: str, page: str) -> tuple[str, str] | None:
    """(words, bucket) when `page` would be a note at `repo_path`, else None.

    A note is a page in a `notes/` folder whose frontmatter says
    `type: note` or says nothing of what it is (ADR-017 §6). Its words are
    the body. A page whose frontmatter cannot be read is no note: the page
    write refuses it and says what is wrong.
    """
    bucket = _notes_bucket(repo_path)
    if bucket is None:
        return None
    try:
        fields = parse_frontmatter(page)
    except FrontmatterError:
        return None
    body = body_only(page)
    if page.lstrip().startswith("---") and body == page:
        return None       # frontmatter that is not a closed block at column one
    if str(fields.get("type") or "note").strip() != "note":
        return None
    words = body.strip()
    return (words, bucket) if words else None


def _notes_bucket(repo_path: str) -> str | None:
    """The bucket a page in a `notes/` folder files into, None for any other page.

    A bucket's notes are in `<bucket>/notes/`, a topic's in
    `<bucket>/<topic>/notes/` (`stack.vault.topic_of`), and the bucket is
    what `stack memory capture --bucket` takes: `homer`, `family/camping`.
    """
    parts = repo_path.split("/")
    for depth in (1, 2):
        if len(parts) > depth + 1 and parts[depth] == "notes":
            return "/".join(parts[:depth])
    return None


def _preview_note(repo_path: str, bucket: str, actor: str) -> dict:
    """What a dry run of a new note says. Filing is all the pipeline does,
    so a preview cannot ask it what the note would become."""
    told = f"would file a new note in {bucket} through the capture pipeline"
    print(f"Would file {repo_path} as a new note (by {actor}); nothing filed\n  {told}")
    return {"ok": True, "committed": False, "preview": True, "path": repo_path,
            "summary": told, "destructive": False, "removed": []}


def _file_note(repo_path: str, words: str, bucket: str, actor: str, config) -> dict:
    """File `words` as `actor`'s note in `bucket`, as a note sent in the chat is.

    `stack memory capture` in the bot runner does the filing, and its
    receipt says where. Anything short of a receipt with a path is an
    error, and nothing else is written in its place.
    """
    try:
        receipt = call(_CAPTURE, "capture", "--by", actor, "--bucket", bucket, "--", words)
    except RuntimeError as e:
        return {"error": f"{repo_path} was not filed as a note: {e}"}
    filed = _filed(receipt)
    if not filed.get("vault"):
        said = receipt.strip() or "the capture answered nothing"
        return {"error": f"{repo_path} was not filed as a note: {said}"}

    path, card_id, title = filed["vault"], filed.get("card", ""), filed["title"]
    home_url = (config or {}).get("home_url", "")
    url = public(go_card({"card_id": card_id}), f"{home_url}/go" if home_url else "")
    link = f"\n  {url}" if url else ""
    told = f"filed a new note: {title}"
    # The pipeline named the file, so the caller hears where it went rather
    # than reading the path it asked for. The curator carries the
    # archivist's commit on to the wiki and the vault mount on its own.
    print(f"Filed a new note: {title} (by {actor})\n  {path} ({card_id or 'no card id'})"
          f"{link}\n  The wiki and the vault mount catch up shortly.")
    return {
        "ok": True, "committed": True, "path": path, "card_id": card_id, "by": actor,
        "summary": told, "mirrored": False, "destructive": False, "removed": [],
    }


def _filed(receipt: str) -> dict[str, str]:
    """`title`, `vault` and `card` from a capture receipt; {} when it filed nothing."""
    captured = _CAPTURED.search(receipt)
    if not captured:
        return {}
    return {"title": captured[1].strip(), **dict(_FILED_AT.findall(receipt[captured.end():]))}


def _check_list_write(change, *, as_patch: bool) -> None:
    """Refuse a write to a list page that does what only `list_edit` should.

    Writing a list page is for restructuring it: rewording an item, moving
    it under another heading, renaming a section. Adding or ticking off an
    item through it skips what the list commands do (the duplicate check,
    the item's section, the day of the tick), and an agent that took this
    route put Joghurt on a list twice and its coffee under bread. A whole-
    page rewrite also never legitimately reopens or removes an item
    (measured 2026-09-15: rewrites drop [x] marks, and prompt rules do not
    reliably repair them); a patch may, on purpose.
    """
    items = [i.text for i in change.added] + list(change.struck)
    if items:
        raise ValueError(
            f"this write adds or ticks off items: {'; '.join(items)}. Item "
            "changes go through list_edit (add, tick), which keeps their "
            "sections and finds duplicates. Write a list page only to "
            "restructure it.")
    lost = list(change.reopened) + list(change.removed)
    if lost and not as_patch:
        raise ValueError(
            f"this rewrite reopens or removes items: {'; '.join(lost)}. A "
            "restructure keeps every item and every [x]. Resend the full "
            "page with them unchanged. To reopen or remove an item on "
            "purpose, use list_edit.")


def _is_list(repo_path: str) -> bool:
    """A list page: a topic's `todos.md`, or a file under `<bucket>/lists/`."""
    return repo_path.endswith("todos.md") or f"/{LISTS_DIR}/" in repo_path


def describe(before: str, after: str, repo_path: str) -> str:
    """One line saying what this edit did to this page.

    A list can be described in the family's own terms -- ticked off, added,
    removed -- because we know what a list is. Any other page gets the honest
    general answer rather than a fabricated one: how much text went each way.
    Vague beats wrong in a commit subject somebody will read back later.
    """
    if _is_list(repo_path):
        return diff(before, after).summary()
    plus, minus = _line_delta(before, after)
    if not (plus or minus):
        return "no change"
    return f"changed +{plus}/-{minus} lines"


def _line_delta(before: str, after: str) -> tuple[int, int]:
    lines = difflib.unified_diff((before or "").splitlines(),
                                 (after or "").splitlines(), n=0, lineterm="")
    plus = sum(1 for ln in lines if ln.startswith("+") and not ln.startswith("+++"))
    # `unified_diff` is a generator, so it is spent; re-run it for the other side.
    lines = difflib.unified_diff((before or "").splitlines(),
                                 (after or "").splitlines(), n=0, lineterm="")
    minus = sum(1 for ln in lines if ln.startswith("-") and not ln.startswith("---"))
    return plus, minus


# Git's own convention, and Forgejo truncates past roughly this in a list view.
_SUBJECT_MAX = 72


def _where(repo_path: str) -> str:
    """The place a subject line names: a topic, or whose page it is.

    Not the full path. Git already records which file changed, so spelling
    `family/camping/todos.md` in the subject spends the line's whole budget
    on something the commit says twice -- and pushes an ordinary tick-off
    over the limit. The curator has always said "in camping"; match it.
    """
    parts = repo_path.rsplit("/", 2)
    return parts[-2] if len(parts) > 1 else parts[-1].removesuffix(".md")


def _commit_message(actor: str, repo_path: str, told: str) -> str:
    """The commit subject, with the detail moved below it when it is long.

    A removal names every item it lost, deliberately, so this is exactly the
    case that overflows a subject line. Nothing is dropped: the long form
    moves into the body, where git and Forgejo both still show it.
    """
    line = commit_subject("chore(memory)", f"{actor} {told} in {_where(repo_path)}")
    if len(line) <= _SUBJECT_MAX:
        return line
    return commit_subject("chore(memory)", f"{actor} updated {repo_path}") + f"\n\n{told}"


def _opt(argv, flag):
    """Pull `--flag value` out of argv, returning (value, remaining)."""
    out, value, i = [], None, 0
    while i < len(argv):
        if argv[i] == flag and i + 1 < len(argv):
            value = argv[i + 1]
            i += 2
            continue
        out.append(argv[i])
        i += 1
    return value, out
