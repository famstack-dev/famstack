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

THE THREE WAYS A MODEL CHANGES A FILE
    nanobot offers `write_file`, `apply_patch` and `edit_file`, and shimming
    only one leaves the others aimed at a read-only mount. So:

    `write_file` replaces the page. Right for a genuine rewrite -- splitting
    one list into two, tidying the lot -- where the change is the whole shape
    and there is no smaller thing to say.

    `apply_patch` sends its edits to the store, which applies them to the page
    as it currently stands. Right for everything narrow, and safer than a
    rewrite: matching `old_text` against live text is what turns a lost race
    into a message about one line instead of a silent overwrite.

    `edit_file` is refused, and says which of the two to use. It is a
    single-file `apply_patch` with a different spelling, and a third path
    would be a third thing to keep correct for no capability gained.

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

import json
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


def _run(page: str, payload: str, *flags: str) -> str:
    """Hand the buffer to the memory stacklet and relay its answer verbatim."""
    _BUFFER.parent.mkdir(parents=True, exist_ok=True)
    _BUFFER.write_text(payload, encoding="utf-8")
    result = subprocess.run(
        ["stack", "memory", "write", page, "--by", _actor(), *flags],
        capture_output=True, text=True, timeout=120,
    )
    return (result.stdout or result.stderr or "").strip() or "(no answer from memory)"


def write_page(page: str, content: str) -> str:
    """Replace a page with the text the caller assembled."""
    return _run(page, content or "")


def patch_page(page: str, edits: list, *, dry_run: bool = False) -> str:
    """Apply structured edits to a page, matched against its current text.

    The edits travel to the store rather than being applied here, so they meet
    the page as it is now. A caller who read the page ten seconds ago and lost
    a race gets told which line no longer exists, instead of quietly reverting
    whoever won it.
    """
    flags = ("--patch", "--dry-run") if dry_run else ("--patch",)
    return _run(page, json.dumps(edits), *flags)


def _patched_away(page: str) -> str:
    """What a partial-edit tool answers when aimed at a vault page."""
    return (f"{page} is a family memory page and is edited whole, not patched. "
            f"Read it with read_file, then call write_file on the same path "
            f"with the complete new contents.")


def _split_by_page(edits) -> tuple[dict, list]:
    """Sort a patch into vault edits, grouped per page, and everything else.

    One call may legitimately touch a page and an ordinary file, and may touch
    two pages, so neither is treated as an error. Grouping by page keeps each
    page's edits in one store call, which is what makes them apply together.
    """
    mine: dict[str, list] = {}
    theirs: list = []
    for edit in edits or []:
        page = vault_page(edit.get("path")) if isinstance(edit, dict) else ""
        if page:
            mine.setdefault(page, []).append(edit)
        else:
            theirs.append(edit)
    return mine, theirs


def install() -> None:
    """Point the native write tools at the vault's own write path.

    Every `execute` here is `async def` because nanobot's are: the tool loop
    awaits the result, so a sync shim returns a `str` into an `await` and the
    call dies with a TypeError the model reports as a broken tool.
    """
    from nanobot.agent.tools.apply_patch import ApplyPatchTool
    from nanobot.agent.tools.filesystem import EditFileTool, WriteFileTool

    original_write = WriteFileTool.execute
    original_edit = EditFileTool.execute
    original_patch = ApplyPatchTool.execute

    async def execute_write(self, path=None, content=None, **kwargs):
        page = vault_page(path)
        if not page:
            return await original_write(self, path=path, content=content, **kwargs)
        try:
            return write_page(page, content or "")
        except Exception:
            # Never let a routing bug look like a successful write. The model
            # must see a failure it can report rather than a silent no-op.
            _log.exception("vault write failed for %s", page)
            return (f"Could not write {page}: the memory store did not accept it. "
                    "Nothing was changed.")

    async def execute_edit(self, path=None, **kwargs):
        page = vault_page(path)
        if not page:
            return await original_edit(self, path=path, **kwargs)
        return _patched_away(page)

    async def execute_patch(self, edits=None, dry_run=False, **kwargs):
        # apply_patch is advertised to the model as the *default* editor, so a
        # page edit lands here first. Its edits are ordinary substitutions, so
        # they carry to the store unchanged -- and arrive better off, because
        # there they are matched against the page as it currently stands.
        mine, theirs = _split_by_page(edits)
        if not mine:
            return await original_patch(self, edits=edits, dry_run=dry_run, **kwargs)

        answers = []
        if theirs:
            answers.append(await original_patch(self, edits=theirs,
                                                dry_run=dry_run, **kwargs))
        for page, page_edits in mine.items():
            try:
                answers.append(patch_page(page, page_edits, dry_run=dry_run))
            except Exception:
                _log.exception("vault patch failed for %s", page)
                answers.append(f"Could not patch {page}: the memory store did "
                               f"not accept it. Nothing was changed.")
        return "\n".join(answers)

    WriteFileTool.execute = execute_write
    EditFileTool.execute = execute_edit
    ApplyPatchTool.execute = execute_patch
