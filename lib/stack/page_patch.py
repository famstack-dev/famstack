"""Apply a model's structured edits to a page, without a filesystem.

`apply_patch` is the tool nanobot advertises to the model as the default way
to change a file, and the model reaches for it accordingly. Its edits are
ordinary text substitutions -- find this exact string, put that one there --
which it normally performs against a file on disk. A family memory page is
not on disk: the agent sees a read-only projection, and the real document
lives in the family's git store behind `stack memory write`.

So this is the same operation with the filesystem taken out: text in, edits
in, text out. Pure, so the write path can run it wherever the *current*
document actually is.

WHY THAT MATTERS MORE THAN IT SOUNDS
    A whole-page write says "the page is now this", and means it even if
    somebody changed the page a second ago. Two writers on one message is not
    hypothetical: the archivist filed three items into a list at 16:14:24 and
    the agent rewrote the same page at 16:14:26, having read it ten seconds
    before the archivist's write existed. The rewrite won, silently, because
    a whole-page write has no way to notice.

    A patch cannot lose that way, but only if it is applied to the current
    document rather than the stale one the model read. Then "old_text not
    found" stops being a nuisance and becomes the useful answer: the line you
    meant to change is not there any more, so look again. That is why this
    function exists separately from the tool, and why the write path calls it
    against freshly-read content instead of applying it agent-side.

WHY THE SEMANTICS ARE COPIED RATHER THAN CHOSEN
    Match `nanobot.agent.tools.apply_patch` exactly -- a `replace` whose
    `old_text` is not unique is an error, an `add` appends -- because the model
    was trained against those rules and is told them in the tool description.
    A store that quietly did something friendlier (replacing the first of three
    matches, say) would be a second dialect of a tool the model thinks it
    already knows, and the difference would surface as data loss.
"""

from __future__ import annotations

from dataclasses import dataclass


class PatchError(ValueError):
    """An edit that cannot be applied to this text, said so the model can fix it."""


@dataclass(frozen=True)
class Edit:
    """One substitution: replace `old_text` with `new_text`, or append it."""

    action: str
    new_text: str
    old_text: str = ""


def edits_from(raw) -> list[Edit]:
    """Read the tool's own edit dicts, rejecting the malformed ones by name.

    Takes what `apply_patch` was handed rather than a cleaned-up shape, so the
    validation lives in one place instead of being half-done at each caller.
    """
    out: list[Edit] = []
    for item in raw or []:
        if not isinstance(item, dict):
            raise PatchError("each edit must be an object")
        action = item.get("action")
        if action not in ("replace", "add"):
            raise PatchError(f"unknown edit action: {action!r} (expected replace or add)")
        new_text = item.get("new_text")
        if new_text is None:
            raise PatchError(f"new_text required for {action}")
        old_text = item.get("old_text") or ""
        if action == "replace" and not old_text:
            raise PatchError("old_text required for replace")
        out.append(Edit(action=action, new_text=str(new_text), old_text=str(old_text)))
    if not out:
        raise PatchError("must provide edits")
    return out


def apply_edits(text: str, edits) -> str:
    """The page as those edits leave it, or raise `PatchError` saying why not.

    Edits apply in order and each sees the one before it, matching the tool:
    the model can replace a line and then append below it in a single call.
    """
    out = (text or "").replace("\r\n", "\n")
    for edit in (edits if edits and isinstance(edits[0], Edit) else edits_from(edits)):
        if edit.action == "add":
            out = _append(out, edit.new_text)
        else:
            out = _replace_once(out, edit.old_text, edit.new_text)
    if out and not out.endswith("\n"):
        out += "\n"
    return out


def _replace_once(text: str, old: str, new: str) -> str:
    """Substitute `old`, insisting it occurs exactly once.

    Ambiguity is refused rather than resolved. On a list, "- [ ] Milch" may
    well appear under two headings, and picking one for the model is how the
    wrong item gets ticked off with nobody the wiser.
    """
    old = old.replace("\r\n", "\n")
    at = text.find(old)
    if at < 0:
        raise PatchError(
            f"old_text not found on the page: {_excerpt(old)}. The page may have "
            f"changed since you read it -- read it again and patch what is there now."
        )
    if text.find(old, at + 1) >= 0:
        raise PatchError(
            f"old_text appears more than once on the page: {_excerpt(old)}. "
            f"Include enough surrounding lines to name just the one you mean."
        )
    return text[:at] + new.replace("\r\n", "\n") + text[at + len(old):]


def _append(text: str, addition: str) -> str:
    """Add text at the end, never welded onto an unterminated last line."""
    extra = addition.replace("\r\n", "\n")
    if text and extra and not text.endswith("\n") and not extra.startswith("\n"):
        text += "\n"
    return text + extra


def _excerpt(text: str, limit: int = 60) -> str:
    """A one-line quotation of a snippet, short enough to read in an error."""
    flat = " ".join(text.split())
    return repr(flat if len(flat) <= limit else flat[:limit] + "...")
