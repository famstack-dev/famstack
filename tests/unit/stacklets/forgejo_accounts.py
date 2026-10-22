"""The accounts a Forgejo stand-in signs requests in as.

Every commit the stack writes names its committer, the account the
client writes as, which the client asks Forgejo for once (`GET /user`).
A stand-in answers that the way Forgejo does: by the credentials the
request carries. A bot's token is its account; the admin's password is
the admin's, or the account an admin names in the `Sudo` header.

`forgejo_accounts` (the fixture in this directory's conftest) serves
`GET /api/v1/user` from this table on the test's `httpserver`.
"""

from __future__ import annotations

import json

from werkzeug.wrappers import Response

MEMORY_BOT = {"login": "memory-bot", "email": "memory-bot@local"}
ARCHIVIST_BOT = {"login": "archivist-bot", "email": "archivist-bot@local"}
STACKADMIN = {"login": "stackadmin", "email": "stackadmin@home.local"}

# The tokens the tests hand out, by the account each belongs to.
TOKENS = {"bottoken": MEMORY_BOT, "memory-bot": MEMORY_BOT, "existing-token": MEMORY_BOT,
          "fresh-bot-token": MEMORY_BOT, "archivisttoken": ARCHIVIST_BOT,
          "installtoken": STACKADMIN, "migration-token": STACKADMIN}
ACCOUNTS = {a["login"]: a for a in (MEMORY_BOT, ARCHIVIST_BOT, STACKADMIN)}


def doer(request) -> dict | None:
    """The account a request is made as, as Forgejo decides it; None when
    it signs in as nobody the stand-in knows."""
    auth = request.headers.get("Authorization", "")
    account = (TOKENS.get(auth.removeprefix("token ")) if auth.startswith("token ")
               else STACKADMIN if auth.startswith("Basic ") else None)
    if account is STACKADMIN and (sudo := request.headers.get("Sudo")):
        return ACCOUNTS.get(sudo)
    return account


def signed_in(request) -> Response:
    """`GET /user`: the account the request signs in as, or 401."""
    account = doer(request)
    if account is None:
        return Response(json.dumps({"message": "unauthorized"}), status=401)
    return Response(json.dumps(account), content_type="application/json")
