"""The card store: the one writer of the family memory.

The memory is a git repository. Forgejo holds it, the curator holds a clone
of it on the data disk, and the host, the bot runner and the curator read
that clone. The store is the only thing that changes it: a write becomes one
commit in the clone, made the way every commit to the memory is made, and
is pushed to Forgejo before the store answers (ADR-019 §3, §4).

    store = CardStore(Path("/data/memory/vault"), remote="curator", server_name="simpson")

    camping = store.read("family/lists/camping.md")
    written = store.commit(
        [Put(camping.path, camping.text.replace("Tent", "Tent for four"), base=camping.version)],
        by="marge", kind="chore(lists)", title="marge changed Camping")
    written.sha        # the commit; "" when the text was there already
    written.pending    # True while Forgejo does not have it yet

The curator holds the one store of its clone. Everything else reaches it
over the curator's HTTP API, which `StoreAPI` answers: `POST /commits`,
`GET /pending`, `GET /cards/<id>/versions`.

**Reading.** `read(path)` gives a file as the last commit left it, with its
version, and `card(card_id)` the card that carries an id, now or as a
former one. A version is the git blob id of a file's bytes, which is also
the `sha` Forgejo's contents API gives a file, so a version read from
either is good for a write. `history(path, card_id)` gives every version of
a card, oldest first, followed across its moves: what a refile reads a
person's edits from. Other processes ask the curator for it
(`GET /cards/<id>/versions`).

**Writing.** `commit(changes, by=, kind=, title=)` writes `Put`s and
`Remove`s as one commit:

- Each change names the version its text was made from (`""` for a file
  that must not exist yet). When a file moved on since, nothing is written
  and `CardChanged` names it: a write never undoes a change it did not see.
- Writing what is there already commits nothing.
- A file the clone holds with changes that were never committed (someone
  edited the clone on disk) is not overwritten: the write is refused.
- The store makes the message: `commit_subject(kind, title)`, then `body`,
  then `trailers` as `Key: value` lines. A kind outside the memory's
  commit grammar is refused.
- `by` is the person or bot the change is by, and becomes the author by
  the one rule for it (`commit_author`, ADR-015). `via` is the bot account
  that writes it, and becomes the committer: the memory bot unless another
  bot writes on someone's behalf. Readers tell a person's change from the
  machine's by both names (`is_machine`).
- A change made in Forgejo in the meantime is pulled in first, and the
  write is made on top of it.

**Reaching Forgejo.** A commit is pushed before `commit` returns. A push
Forgejo refuses because it moved on is rebased onto it and pushed again;
when Forgejo cannot be reached, or refuses for another reason, the commit
stays in the clone, `written.pending` is True, and `sync()`, which the
curator runs every cycle, pushes it. Nothing is ever forced and the clone is
never reset: histories that cannot be joined stop the pushing until a
person decides, and `sync()` says so. `pending()` lists what Forgejo does
not have yet.

**Errors** are `StoreError`s whose text is meant for a person: the API
answers it, the CLI prints it, a bot says it in the chat.

- `CardChanged`: a file moved on since it was read; read it again.
- `Refused`: the write cannot be made as asked (a path outside the memory,
  a kind the history cannot place, no server name for a person's address).
- `CloneMissing`: there is no clone yet; `stack up memory` makes it.
- `StoreError` itself: git failed; its text says how.
"""

from ._api import StoreAPI
from ._store import (
    CardChanged,
    CardInvalid,
    CardStore,
    CloneMissing,
    File,
    Pending,
    Put,
    Refused,
    Remove,
    StoreError,
    Sync,
    Written,
)

__all__ = ["CardChanged", "CardInvalid", "CardStore", "CloneMissing", "File", "Pending", "Put", "Refused",
           "Remove", "StoreAPI", "StoreError", "Sync", "Written"]
