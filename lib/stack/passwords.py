"""passwords: a family member's first password, and the one reader of it.

    from stack.passwords import new_password, of_people, stored

    new_password()                         # "otter-maple-cloud-lamp"
    stored(stck, "homer")                  # "otter-maple-cloud-lamp", or None
    of_people(stck, ["homer", "maggie"])   # {"homer": "otter-maple-cloud-lamp"}

**What it is.** Each person in the family has one password for the
services the stack makes accounts in: the chat, photos, documents, code.
It lives in `.stack/secrets.toml` as `global__USER_<ID>_PASSWORD`, and
every service makes the person's account with it. This module makes a
new person's first password and reads a stored one. Nothing else makes
one: `stack family set` (`stack.family.set_family`) calls `new_password`
for each person without a stored password, and stores it before any
service makes an account with it.

**Four words, not the username.** The first password used to be the
person's username, the first name in lowercase, which every device at
home could read from the family list. A first password is now four
words from EFF's short word list (`words/eff_short_wordlist_1.txt`,
1295 words), picked by the operating system's cryptographic random
source and joined by "-": about 41 bits. The chat and the photos app
ask for it once per device, and the services limit the sign-ins they
take, so guessing it over the network is out of reach. Four short words
can be read out and typed on a phone, which a random string of the same
strength cannot.

**A stored password is never changed here.** A person who has one keeps
it at every later `stack family set` and at every update, the old first
password (their username) included, because the services' accounts
take it. Changing a password is a command of its own.

**One reader.** `stored` is the only function that reads a person's
password for showing it, and it answers only for a person of the family
(`users.toml`). It runs on the Mac, where the secrets are: the `stack up`
banner calls it, and the host API's JSON command `passwords` answers with
`of_people`, which calls it for each person asked. The tools server has
no access to the secrets file; its pages ask the host API, and only for
the people a page may show (every person on the Mac's address, its own
person on a paired device, no one on a shared device). The host API
answers that command only to core's token. No password goes to the
chat: the Server room's messages never carry one.

    tools server page ──{"cmd": "passwords", "people": ["marge"]}──▶ host API
                                                                       │
                       ◀──{"passwords": {"marge": "fiber-tulip-…"}}── of_people
                                                                       │
                                                       stored ── .stack/secrets.toml

What it does not do: decide who may see a password (the pages and
`devices.Who.sees_password_of` decide that), or set a password in a
service (each service's own hooks do).
"""
from __future__ import annotations

import functools
import secrets
from pathlib import Path

from .users import load_users, password_key, user_id

__all__ = ["new_password", "stored", "of_people", "WORDS_FILE"]

# The word list ships next to this module, read only. Its header names
# the source, the license and the one change from the source.
WORDS_FILE = Path(__file__).parent / "words" / "eff_short_wordlist_1.txt"

# Four words of 1295: log2(1295) * 4 = 41.4 bits.
WORDS_PER_PASSWORD = 4
SEPARATOR = "-"


def new_password() -> str:
    """Four random words of the word list, joined by "-".

    Each word is picked on its own by `secrets.choice`, the operating
    system's cryptographic random source, so a word can repeat. The
    result is lowercase a to z and "-", which every service the stack
    makes accounts in takes as it is.

        new_password()   # "otter-maple-cloud-lamp"
    """
    words = _words()
    return SEPARATOR.join(secrets.choice(words) for _ in range(WORDS_PER_PASSWORD))


def stored(stck, person_id: str) -> str | None:
    """The password stored for the family member `person_id`, or None.

    `stck` is the instance's `Stack`; `person_id` is a username, as
    `users.user_id` gives it. None when `person_id` is no person of
    `users.toml` (the tech admin, a bot, a made-up name) or has no
    password stored. Reads only `global__USER_<ID>_PASSWORD`, never
    another secret, and changes nothing. A secrets file that cannot be
    read raises `SecretsUnreadable`, as every read of the store does.

        stored(stck, "homer")        # "otter-maple-cloud-lamp"
        stored(stck, "stackadmin")   # None: not a person of the family
    """
    # SAFETY: this is the one place that reads a person's password to show
    # it. It answers for the family's people only and reads one key, so no
    # caller reaches another secret through it. Callers decide who may see
    # the answer; keep every page that shows a password on this function.
    if person_id not in {user_id(u) for u in load_users(stck.instance_dir)}:
        return None
    return stck.secrets.get("global", password_key({"id": person_id}))


def of_people(stck, people) -> dict[str, str]:
    """The stored password of each family member in `people`, by id.

    The host API's `passwords` command answers with it. `people` is a
    list of usernames; anything else, an id that is no person of the
    family and a person without a stored password are left out.

        of_people(stck, ["homer", "stackadmin"])   # {"homer": "otter-maple-cloud-lamp"}
        of_people(stck, "homer")                   # {}: not a list
    """
    if not isinstance(people, list):
        return {}
    found = {p: stored(stck, p) for p in people if isinstance(p, str)}
    return {p: password for p, password in found.items() if password is not None}


# ── The word list ─────────────────────────────────────────────────────────

@functools.cache
def _words() -> tuple[str, ...]:
    """The list's words, in order: the last field of each line that is
    not a comment. Read once per process; the file ships with the code."""
    lines = WORDS_FILE.read_text(encoding="utf-8").splitlines()
    return tuple(line.split()[-1] for line in lines if line.strip() and not line.startswith("#"))
