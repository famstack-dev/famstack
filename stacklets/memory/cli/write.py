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

WHAT COMES BACK
    Not "ok". For a list page, `stack.list_doc` compares before and after and
    reports what the edit actually did: ticked off, added, moved, reworded,
    and -- named in full, always -- removed. A caller that rewrote a page and
    silently dropped six items learns so immediately, which is the whole
    reason a primitive write is safe to hand to a model at all. The same
    sentence becomes the commit message, so history says what happened rather
    than "updated todos.md".
"""

HELP = "Replace a page in the family memory vault"

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import update_memory  # noqa: E402

from stack.list_doc import diff  # noqa: E402

# Where the agent leaves a page it wants written. Its data dir is bind-mounted
# read-write into the container at ~/.nanobot, so the container writes here and
# the host reads the same bytes with no transport in between.
_AGENT_BUFFER = "agent/.write-buffer"

_USAGE = ("usage: stack memory write <vault-path> --by <person> [--from <file>]\n"
          "  e.g. stack memory write family/camping/todos.md --by marge")


def run(args, stacklet, config):
    argv = list(args or [])
    actor, argv = _opt(argv, "--by")
    source, argv = _opt(argv, "--from")
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

    # Captured from inside the transform so the comparison is against the
    # canonical file Forgejo hands back, not a local clone that may lag.
    seen: dict[str, str] = {}

    def _replace(prior: str) -> str:
        seen["before"] = prior or ""
        return content if content.endswith("\n") else content + "\n"

    change = None
    result = update_memory(
        config, repo_path, _replace, actor=actor,
        message=f"chore(memory): {actor} updated {repo_path}",
    )
    if "error" in result:
        return result

    before = seen.get("before", "")
    if repo_path.endswith("todos.md"):
        change = diff(before, content)

    if not result.get("committed"):
        print(f"{repo_path} was already exactly this; nothing to commit")
        return {"ok": True, "committed": False, "path": repo_path}

    told = change.summary() if change else "page replaced"
    print(f"Wrote {repo_path} (by {actor})\n  {told}")
    return {
        "ok": True, "committed": True, "path": repo_path, "by": actor,
        "summary": told,
        "destructive": bool(change and change.destructive()),
        "removed": list(change.removed) if change else [],
    }


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
