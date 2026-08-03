"""Let the agent edit a vault page with the tool every model already knows.

The agent could read the family's vault with `read_file` and change nothing in
it. Writing meant `stack memory topic <slug> todo strike "<item>" --by <person>`,
once per item, matched by substring. Asked to tidy a list, a real agent produced
the correct final document in chat, grouped and deduplicated exactly as asked,
and then failed to perform the twenty calls that would have made it so. It
claimed success instead.

So this routes `write_file` on a vault page to `stack memory write`. The model
does what it is good at, rewriting a document it can see whole, and the plumbing
underneath (which store, whose name on the commit, what actually changed) stays
where the caller does not have to think about it.

WHY WRITE AND NOT EDIT
    `EditFileTool` is refused on vault paths rather than translated. A family
    list is fifteen lines; the model can hold all of it, and whole-document
    reasoning is the point when the task is "split this in two" or "tick off
    the ones I marked". A surgical patch would also have to be applied here
    against a read-only mount and re-uploaded, which is the same write with
    extra steps and a new way to be wrong.

WHY THE BUFFER FILE
    The container reaches the host over a plaintext socket that splits on
    shlex, and a markdown document does not survive that. The agent's data dir
    is already bind-mounted read-write, so the page is written there and the
    host command reads the same bytes off its side of the mount. No new
    transport, no quoting.

WHAT THE MODEL READS BACK
    Whatever the CLI says, verbatim -- "ticked off 2: ..." or "REMOVED 1:
    Campingstuehle mitbringen". Not "ok". An edit that quietly loses six items
    is the failure worth engineering against, and the cheapest defence is that
    the model is told, in the tool result, exactly what it just did.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

_log = logging.getLogger("agent.runtime.vault_write")

# The page the model addresses as `vault/...` is the same relative path the
# memory stacklet knows; only the prefix differs.
_PREFIX = "vault/"
# Host and container see this same file through the agent's data-dir mount.
_BUFFER = Path.home() / ".nanobot" / ".write-buffer"


def vault_page(path: str | None) -> str:
    """The repo-relative page a tool path names, or "" if it is not one.

    Only markdown under the vault routes to the memory store. Everything else
    (the agent's own workspace notes, scratch files) keeps stock behaviour.
    """
    text = (path or "").strip().lstrip("/")
    for marker in (_PREFIX, "workspace/vault/"):
        if marker in text:
            text = text.split(marker, 1)[1]
            break
    else:
        return ""
    return text if text.endswith(".md") else ""


def _actor() -> str:
    """Whoever the agent is speaking with this turn, for the commit."""
    try:
        import brief
        return getattr(brief, "speaking_with", "") or "someone"
    except Exception:
        return "someone"


def write_page(page: str, content: str) -> str:
    """Hand a rewritten page to the memory stacklet and relay its answer."""
    _BUFFER.parent.mkdir(parents=True, exist_ok=True)
    _BUFFER.write_text(content or "", encoding="utf-8")
    result = subprocess.run(
        ["stack", "memory", "write", page, "--by", _actor()],
        capture_output=True, text=True, timeout=120,
    )
    return (result.stdout or result.stderr or "").strip() or "(no answer from memory)"


def install() -> None:
    """Point the native write tools at the vault's own write path."""
    from nanobot.agent.tools.filesystem import EditFileTool, WriteFileTool

    original_write = WriteFileTool.execute
    original_edit = EditFileTool.execute

    def execute_write(self, path=None, content=None, **kwargs):
        page = vault_page(path)
        if not page:
            return original_write(self, path=path, content=content, **kwargs)
        try:
            return write_page(page, content or "")
        except Exception:
            # Never let a routing bug look like a successful write. The model
            # must see a failure it can report rather than a silent no-op.
            _log.exception("vault write failed for %s", page)
            return (f"Could not write {page}: the memory store did not accept it. "
                    "Nothing was changed.")

    def execute_edit(self, path=None, **kwargs):
        page = vault_page(path)
        if not page:
            return original_edit(self, path=path, **kwargs)
        return (f"{page} is a family memory page and is edited whole, not "
                f"patched. Read it, then call write_file on the same path with "
                f"the complete new contents.")

    WriteFileTool.execute = execute_write
    EditFileTool.execute = execute_edit
