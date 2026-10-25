"""One file of the memory, changed by a host command through the curator.

`stack memory write`, `list` and `person add` each change one file: they
read it, transform its text, and write the result as the person who asked.
The read and the write go to the curator (`memory_api.MemoryAPI`), which
holds the memory's clone and is its only writer (ADR-019): the text is the
clone's, the write is made on the version that was read, and the curator
refuses it when the file changed in between.

    update_memory(config, "family/lists/camping.md", tick_off, actor="marge",
                  kind="chore(lists)", describe=lambda before, after: ("marge ticked off Tent", ""))
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from memory_api import CuratorDown, MemoryAPI, Refused, put, remove

# The text before and after (`""` for a removed file), to the commit's
# title and body: a caller that can only say what its transform did once
# it has met the current text says it here.
Describe = Callable[[str, str], tuple[str, str]]


def still_to_follow(*, pending: bool, mirrored: bool) -> str:
    """What a written change still waits for, as lines to print under it.

    The curator has the change either way. Forgejo may not have it yet
    (`pending`: it waits in the curator's clone, while Forgejo is down for
    example), and the wiki and the agent's mount may be a beat behind
    (`mirrored` False). Both are said as a delay, never as a doubt: a
    person or the agent who reads anything short of "written" writes the
    change again, and the second write is a duplicate nobody asked for.
    """
    lines = []
    if pending:
        lines.append("Forgejo does not have it yet; `./stack memory pull` says what waits and why.")
    if not mirrored:
        lines.append("The wiki and the vault mount catch up shortly.")
    return "".join(f"\n  {line}" for line in lines)


def update_memory(config: Mapping, repo_path: str, transform: Callable[[str], str | None], *,
                  actor: str, kind: str, describe: Describe) -> dict:
    """Commit `transform` of the file at `repo_path` as `actor`; the
    framework's envelope.

    `transform` takes the file's text ("" when there is none) and answers
    the new text, or None to remove the file; a `ValueError` refuses the
    change with its message. Text that does not change commits nothing.
    `kind` is the commit grammar's prefix, and `describe` gives the title
    and body the curator makes the message from.

    `{"ok": True, "committed": False}` when nothing changed; `{"ok": True,
    "committed": True, "path", "pending", "mirrored"}` when it did, where
    `pending` means Forgejo does not have it yet and `mirrored` that the
    wiki and the agent's mount already show it (False: they catch up);
    `{"error": ...}` with the curator's own words otherwise.
    """
    memory = MemoryAPI.from_host(config)
    try:
        current = memory.file(repo_path)
        prior = current.text if current else ""
        merged = transform(prior)
        if merged == prior or (merged is None and current is None):
            return {"ok": True, "committed": False}
        title, body = describe(prior, merged or "")
        base = current.version if current else ""
        change = remove(repo_path, base=base) if merged is None else put(repo_path, merged, base=base)
        written = memory.commit([change], by=actor, kind=kind, title=title, body=body)
    except (ValueError, Refused, CuratorDown) as e:
        return {"error": str(e)}
    if not written.changed:
        return {"ok": True, "committed": False}
    return {"ok": True, "committed": True, "path": repo_path, "pending": written.pending,
            "mirrored": written.mirrored}
