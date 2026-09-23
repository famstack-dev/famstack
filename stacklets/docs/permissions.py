"""Document access groups in Paperless.

Paperless shows a user only what they own or were granted, and a user
without any group may not even load the UI (403 on the settings
endpoint). Accounts that arrive through single sign-on (the OIDC provider)
start with no group. This module keeps one group, named by what it
grants rather than by who is in it:

  all_documents  view and change on the whole archive, plus the rights
                 a member needs to work with it.

Paperless puts new social accounts into it through
PAPERLESS_SOCIAL_ACCOUNT_DEFAULT_GROUPS; this module covers accounts
that existed before, and drift. Two more pieces make "all documents"
true: a workflow that grants the group on every document as it is
consumed, and a one-time grant over the documents that already exist
(share_archive). A more restricted group (own_documents) is added when
the first account that must not see everything arrives.

Superusers (the technical admin) are left alone. Idempotent; runs on
every 'stack up docs'.
"""

import json
import urllib.error
import urllib.request

GROUP = "all_documents"
WORKFLOW = "Share new documents with all_documents"

# What a family member may do. Documents are theirs to add, fix and
# remove. Tags, correspondents, document types and custom fields are
# shared vocabulary that the archivist also classifies against, so a
# member can add and rename entries but not delete one: a delete removes
# it from every document at once. Storage paths decide where files land
# on disk and stay an admin decision, so view only. Workflows are view
# only because this module's own workflow is what keeps "all documents"
# true. Saved views, notes and share links are personal, in full.
# `uisettings` is what the web UI loads first; without it the UI fails
# with 403. `logentry` shows a document's history.
PERMISSIONS = [
    f"{a}_{m}"
    for m, actions in {
        "document": "view add change delete",
        "tag": "view add change",
        "correspondent": "view add change",
        "documenttype": "view add change",
        "storagepath": "view",
        "customfield": "view add change",
        "savedview": "view add change delete",
        "uisettings": "view change",
        "note": "view add change delete",
        "sharelink": "view add delete",
        "paperlesstask": "view",
        "workflow": "view",
        "logentry": "view",
    }.items()
    for a in actions.split()
]


def _call(url, token, method, path, body=None):
    req = urllib.request.Request(
        f"{url}/api{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Token {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def _paged(url, token, path):
    # Paperless pages every list. `next` is an absolute URL, and `_call`
    # takes a path under /api, so only that part is kept.
    out, page = [], f"{path}{'&' if '?' in path else '?'}page_size=500"
    while page:
        d = _call(url, token, "GET", page)
        out += d["results"]
        nxt = d.get("next")
        page = nxt.split("/api", 1)[1] if nxt else None
    return out


def ensure_workflow(url, token, group_id, step):
    """Grant the group view and change on every document at consumption.

    Paperless makes a new document visible to its owner only. A workflow
    is the one place that runs for every document, whichever way it
    arrives.

    Workflows have no stable key, so the name is the identity. An
    existing one is left alone, also when an admin has edited it in the
    UI; renaming it there makes this create a second one.
    """
    flows = _paged(url, token, "/workflows/")
    if any(w["name"] == WORKFLOW for w in flows):
        return
    _call(url, token, "POST", "/workflows/", {
        "name": WORKFLOW, "order": 0, "enabled": True,
        # trigger type 1 = consumption started. Sources 1, 2, 3 are the
        # consume folder, the API (which the archivist bot uses) and mail.
        # Paperless requires a filter; "*" matches every file name
        "triggers": [{"type": 1, "sources": [1, 2, 3], "filter_filename": "*"}],
        # action type 1 = assignment
        "actions": [{"type": 1, "assign_view_groups": [group_id], "assign_change_groups": [group_id]}],
    })
    step(f"Paperless workflow '{WORKFLOW}' created")


def share_archive(url, token, step=None):
    """One-time grant of view and change to the group over every
    existing document.

    Documents consumed before the workflow existed have owner-only
    permissions. This runs once, when the group is created, and never
    again: from then on the workflow covers new documents, and a repeat
    would restore grants an admin removed on purpose. `merge` adds the
    group and keeps owners and other grants as they are.
    """
    step = step or (lambda m: None)
    groups = _call(url, token, "GET", "/groups/?page_size=100")["results"]
    group = next((g for g in groups if g["name"] == GROUP), None)
    if group is None:
        step(f"Group '{GROUP}' missing, run ensure_group first")
        return
    ids = [d["id"] for d in _paged(url, token, "/documents/?fields=id")]
    # In batches, so one request stays small on a large archive.
    for i in range(0, len(ids), 500):
        _call(url, token, "POST", "/documents/bulk_edit/", {
            "documents": ids[i:i + 500], "method": "set_permissions",
            "parameters": {"set_permissions": {"view": {"users": [], "groups": [group["id"]]},
                                               "change": {"users": [], "groups": [group["id"]]}},
                           "merge": True},
        })
    step(f"{len(ids)} documents shared with '{GROUP}'")


def ensure_group(url: str, token: str, step=None) -> bool:
    """Keep the group as this module defines it and every member in it.

    Returns True when the group was created in this run, so the caller
    can do the one-time archive grant exactly once.

    The group is named by what it grants, so its permissions are part of
    this code: an edit in the Paperless UI is reset on the next
    `stack up docs`. Output names only what changed, so a run with
    nothing to do prints nothing.

    Errors are reported, not raised. A failed permission sync must not
    stop `stack up docs`: Paperless and its login still work, and the
    next run tries again.
    """
    step = step or (lambda m: None)
    created = False
    try:
        groups = _call(url, token, "GET", "/groups/?page_size=100")["results"]
        group = next((g for g in groups if g["name"] == GROUP), None)
        if group is None:
            group = _call(url, token, "POST", "/groups/", {"name": GROUP, "permissions": PERMISSIONS})
            created = True
            step(f"Paperless group '{GROUP}' created")
        elif sorted(group["permissions"]) != sorted(PERMISSIONS):
            _call(url, token, "PATCH", f"/groups/{group['id']}/", {"permissions": PERMISSIONS})
            step(f"Paperless group '{GROUP}' permissions updated")

        ensure_workflow(url, token, group["id"], step)

        # Every account that is not a superuser joins the group. Accounts
        # created by the single sign-on login get it from Paperless
        # (PAPERLESS_SOCIAL_ACCOUNT_DEFAULT_GROUPS); this covers local
        # accounts from before, which otherwise have no group and cannot
        # load the UI. Superusers see everything already, and the
        # technical admin stays out of member groups.
        # One page: a household has far fewer than 200 accounts.
        users = _call(url, token, "GET", "/users/?page_size=200")["results"]
        for u in users:
            if u.get("is_superuser") or group["id"] in (u.get("groups") or []):
                continue
            _call(url, token, "PATCH", f"/users/{u['id']}/", {"groups": sorted(set(u.get("groups") or []) | {group["id"]})})
            step(f"Paperless user {u['username']} added to '{GROUP}'")
    except urllib.error.HTTPError as e:
        step(f"Paperless permissions: {e.code} {e.read().decode()[:160]}")
    except urllib.error.URLError as e:
        step(f"Paperless permissions skipped: {e}")
    return created
