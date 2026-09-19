# Document access model for the docs stacklet

Status: planned, not built. 2026-09-18. Depends on the id stacklet
(passkey login), because accounts only arrive in Paperless through it.

## Problem

Paperless shows a user what they own or were granted. Accounts created
by the passkey login start with nothing, not even the right to load the
UI (403 on the settings endpoint). Today the docs stacklet fixes that
with one group, `all_documents`, that every new account joins: view and
change on the whole archive, a workflow that grants the group on every
consumed document, and a one-time grant over the existing archive.

That is right for adults and wrong for children.

## Target model

Adults see everything. Children see their own documents. Roles come
from `users.toml`, the same source the id stacklet builds accounts
from, so nothing is maintained twice.

| Group | Rights | Members |
|---|---|---|
| `own_documents` | load the UI, upload, view and edit own documents, view the vocabulary | everyone; default group for new logins |
| `all_documents` | view and change on every document, edit the vocabulary, saved views | `role = "admin"`, by username |

Admins are in both. Groups are named by what they grant, never by who
is in them (`parents` becomes wrong the day a grown child gets the same
rights).

"Own documents" for a child has two sources, both automatic:

1. Documents the child uploads. Paperless makes the uploader the owner.
2. Documents about the child. The stacklet already seeds one person tag
   per family member. One workflow per person, generated from
   `users.toml`: document tagged with that person's tag, grant view to
   that person. The archivist tags anyway, so nothing new is taught.

Adults see children's uploads too, through the existing consumption
workflow that grants `all_documents` on every document.

Not covered on purpose: documents of one adult hidden from the other.
A `private` tag with its own workflow can come later if wanted.

## Alternative considered

Roles as Pocket ID user groups, synced into Paperless through a groups
claim (`PAPERLESS_SOCIAL_ACCOUNT_SYNC_GROUPS`). Makes the identity
provider the single source of truth, but moves role management into a
second UI and every service would need the same claim mapping. The
users file already is the source. Revisit when a service cannot read it.

## Work

All in `stacklets/docs/permissions.py` and its hooks, behind the same
gate as today (`OIDC_CLIENT_ID` set). About two hours.

| Step | Effort |
|---|---|
| `own_documents` group with its rights; `all_documents` unchanged | 20 min |
| Membership by role, matched by username against `users.toml` | 20 min |
| `PAPERLESS_SOCIAL_ACCOUNT_DEFAULT_GROUPS` switched to `own_documents` | 5 min |
| Per-person tag workflows, created and kept from `users.toml`, reusing the seeded tag ids | 45 min |
| Guide and README | 15 min |
| Test: a document tagged for one member is visible to that member and not to another | 10 min |

Risks: the tag workflow needs the person tag to exist first, so it runs
after `seed_person_tags`; Paperless may reject a workflow that names a
missing tag. Both show on the first `stack up docs`.

## Migration

`all_documents` stays. Add `own_documents`, make it the default, put
admins into `all_documents` by role, generate the person workflows.
Existing admin accounts see no change. A member's first login lands in
`own_documents` only.
