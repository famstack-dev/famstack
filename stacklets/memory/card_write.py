"""The one write of a note's card and its sidecar to the memory repo.

A note is two files (ADR-018): its card, what it says, and its sidecar,
where its sources came from and how the card was made. Every writer of a
note writes both through `commit_card`: the archivist when it files a
note and refiles it from the chat (in the bot runner, in-process), and
`stack memory reprocess --apply` on the host (`lib.write_card`).
Composing the texts is each caller's; the write is the memory's, so a
filing, a refile and a reprocess land the same way:

  - card and sidecar in one commit, so neither changes without the other;
  - each against the blob it was read at, so a write never undoes a
    change it did not see (a person's edit in Forgejo, a reply filed in
    the meantime);
  - a move as the removal of both files at the old path and their
    creation at the new one, in that same commit, which is how the
    card's history follows it (`stack.cards.history`).

Forgejo decides "changed since": it refuses a commit that names a blob a
file is no longer at, or creates a file that is there, and then writes
none of it (`ForgejoClient.change_files`). Only after a refusal are the
files read again, to say which of them moved (`CardChanged`), so a write
that goes through is one request.

    commit_card(client, "family", "memory", path, card, sidecar,
                card_sha=read["sha"], sidecar_sha=side["sha"],
                author=("homer", "homer@simpson"), message="reply: Tent is torn")

The committer is the client's (`ForgejoClient.committer`, else its
account), the author who the change is by (`lib.commit_author`, ADR-015),
and the message the caller's, its subject from
`stack.cards.commits.commit_subject`.

Imports the framework only, so the bot runner (as `memory.card_write`)
and the host (as `card_write`, beside `lib`) load this same file.
"""

from __future__ import annotations

from stack.cards.sources import sidecar_path
from stack.forgejo import FileChange, ForgejoClient, ForgejoError


class CardChanged(Exception):
    """A card or its sidecar is no longer the file the new text was made from.

    Nothing was written. `paths` are the files that moved since they were
    read, the card first.

        try:
            commit_card(client, owner, repo, path, card, sidecar, ...)
        except CardChanged as e:
            e.paths     # ('marge/notes/2026/10/tent-3a338e.md',)
    """

    def __init__(self, paths: tuple[str, ...]):
        super().__init__(f"{', '.join(paths)} changed since it was read")
        self.paths = paths


def commit_card(client: ForgejoClient, owner: str, repo: str, path: str, card: str,
                sidecar: str | None, *, card_sha: str, sidecar_sha: str,
                author: tuple[str, str], message: str, moved_from: str = "") -> None:
    """Write `card` at `path` and its `sidecar` beside it, in one commit.

    `card_sha` and `sidecar_sha` are the blobs the texts were made from,
    "" where there was no file: a new card, a note from before sidecars.
    `sidecar` None leaves the sidecar out: nothing at either sidecar path
    is written or removed. `moved_from` is the path the card was read at
    when it is written somewhere else; both shas are then of the files
    there, which the commit removes (the sidecar when `sidecar_sha` names
    one). `author` is `(name, email)`.

    Raises `CardChanged` when either file changed since it was read, and
    `ForgejoError` when Forgejo cannot be reached or refuses for any other
    reason. Either way nothing is written.
    """
    changes = _changes(path, card, sidecar, card_sha=card_sha, sidecar_sha=sidecar_sha,
                       moved_from=moved_from)
    try:
        client.change_files(owner, repo, changes, message=message,
                            author_name=author[0], author_email=author[1])
    except ForgejoError as refused:
        read_at = moved_from or path
        based_on = [(read_at, card_sha)]
        if sidecar is not None:
            based_on.append((sidecar_path(read_at), sidecar_sha))
        if moved := _moved_since(client, owner, repo, based_on):
            raise CardChanged(moved) from refused
        raise


# ── The commit ───────────────────────────────────────────────────────────

def _changes(path: str, card: str, sidecar: str | None, *, card_sha: str, sidecar_sha: str,
             moved_from: str) -> list[FileChange]:
    """The files one card write changes, in the order of the commit."""
    if not moved_from:
        changes = [_put(path, card, card_sha)]
        if sidecar is not None:
            changes.append(_put(sidecar_path(path), sidecar, sidecar_sha))
        return changes
    changes = [FileChange("delete", moved_from, sha=card_sha)]
    if sidecar is not None and sidecar_sha:
        changes.append(FileChange("delete", sidecar_path(moved_from), sha=sidecar_sha))
    changes.append(FileChange("create", path, card))
    if sidecar is not None:
        changes.append(FileChange("create", sidecar_path(path), sidecar))
    return changes


def _put(path: str, text: str, sha: str) -> FileChange:
    """`text` over the file read at `sha`, or as a new file when there was none."""
    return FileChange("update", path, text, sha=sha) if sha else FileChange("create", path, text)


# ── After a refusal ──────────────────────────────────────────────────────

def _moved_since(client: ForgejoClient, owner: str, repo: str,
                 based_on: list[tuple[str, str]]) -> tuple[str, ...]:
    """The paths in `based_on` whose file is no longer at the blob next to
    it ("" for none). Empty when they all are, or when Forgejo cannot be
    read: then the refusal is not explained, and stays Forgejo's."""
    try:
        return tuple(p for p, sha in based_on
                     if str((client.get_file(owner, repo, p) or {}).get("sha") or "") != sha)
    except ForgejoError:
        return ()
