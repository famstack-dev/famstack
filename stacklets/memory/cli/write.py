"""stack memory write — replace a vault page, and say what that did.

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
"""

HELP = "Replace a page in the family memory vault"

import difflib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import update_memory  # noqa: E402

from stack.list_doc import diff  # noqa: E402
from stack.page_patch import apply_edits  # noqa: E402

# Where the agent leaves a page it wants written. Its data dir is bind-mounted
# read-write into the container at ~/.nanobot, so the container writes here and
# the host reads the same bytes with no transport in between.
_AGENT_BUFFER = "agent/.write-buffer"

_USAGE = ("usage: stack memory write <vault-path> --by <person> "
          "[--from <file>] [--patch] [--dry-run]\n"
          "  e.g. stack memory write family/camping/todos.md --by marge")


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

    buffer = Path(source).expanduser() if source else data_dir / _AGENT_BUFFER
    try:
        content = buffer.read_text(encoding="utf-8")
    except OSError as e:
        return {"error": f"nothing to write: cannot read {buffer} ({e})"}
    if not content.strip():
        return {"error": "nothing to write: the page is empty"}

    actor = actor.strip().split(":")[0].lstrip("@") or "someone"

    if as_patch:
        try:
            edits = json.loads(content)
        except json.JSONDecodeError as e:
            return {"error": f"--patch expects a JSON list of edits ({e})"}

    # Captured from inside the transform so the comparison is against the
    # canonical file Forgejo hands back, not a local clone that may lag. For
    # a patch that is not merely bookkeeping: `prior` is the text the edits
    # are matched against, which is what makes a concurrent write visible
    # instead of silently overwritten.
    seen: dict[str, str] = {}

    def _replace(prior: str) -> str:
        seen["before"] = prior or ""
        after = apply_edits(prior or "", edits) if as_patch else content
        seen["after"] = after if after.endswith("\n") else after + "\n"
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

    before, after = seen.get("before", ""), seen.get("after", "")
    change = diff(before, after) if repo_path.endswith("todos.md") else None
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

    print(f"Wrote {repo_path} (by {actor})\n  {told}")
    return {
        "ok": True, "committed": True, "path": repo_path, "by": actor,
        "summary": told,
        "destructive": bool(change and change.destructive()),
        "removed": list(change.removed) if change else [],
    }


def describe(before: str, after: str, repo_path: str) -> str:
    """One line saying what this edit did to this page.

    A list can be described in the family's own terms -- ticked off, added,
    removed -- because we know what a list is. Any other page gets the honest
    general answer rather than a fabricated one: how much text went each way.
    Vague beats wrong in a commit subject somebody will read back later.
    """
    if repo_path.endswith("todos.md"):
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
    line = f"chore(memory): {actor} {told} in {_where(repo_path)}"
    if len(line) <= _SUBJECT_MAX:
        return line
    return f"chore(memory): {actor} updated {repo_path}\n\n{told}"


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
