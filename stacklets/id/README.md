# id — Pocket ID

Passkey-only OpenID Connect provider. One login for every family
service. Status: private stacklet under test. See ADR-014.

## What it does

| Piece | Where |
|---|---|
| Login page | `https://id.<domain>` |
| Container | `stack-id-server`, port 42100 on localhost |
| Data | `{data_dir}/id/data` (SQLite + uploads) |
| Secrets | `ENCRYPTION_KEY`, `STATIC_API_KEY` in `secrets.toml` |

## First run, step by step

1. `./stack up id`. The install hook creates the data and onboarding
   dirs, the health check waits for `/healthz`, then provisioning runs.
2. Add the host to the reverse proxy. The framework does not assemble
   `caddy.snippet` files yet, so add the equivalent of
   `stacklets/id/caddy.snippet` to your Caddy config by hand and
   reload Caddy. The container must be on the `stack` network.
3. Accounts. With `provision_users = true` under `[id]` in `stack.toml`,
   every person in `users.toml` gets a Pocket ID account (admins from
   `role = "admin"`) and a one-time login link in
   `{data_dir}/id/onboarding/<username>.txt`, mode 600, valid 7 days.
   Open your own link, register a passkey, then register a second one
   elsewhere. Show the others their link as a QR code from Users in the
   admin UI, or send the file's content over a private channel.
   Without the switch, create people in the admin UI; the setup page
   does not exist, because the static API key already created a
   service admin.
4. Clients. Every stacklet with an `[oidc]` table in its manifest gets
   a client on every `stack up id`. Credentials land in the secrets
   store as `id__CLIENT_<STACKLET>_ID` and `_SECRET`. Run `stack up id`
   after adding such a stacklet, then `stack up <stacklet>`.

## Declaring a stacklet as OIDC client

```toml
[oidc]
name      = "Documents"
callbacks = ["{url}/accounts/oidc/pocketid/login/callback/"]

[env.defaults]
OIDC_ISSUER        = "{id_url}"
OIDC_CLIENT_ID     = "{id__CLIENT_DOCS_ID}"
OIDC_CLIENT_SECRET = "{id__CLIENT_DOCS_SECRET}"
```

`{url}` in a callback is that stacklet's public URL. Without the id
stacklet the three env values render empty; the compose file must
treat an empty client id as "no SSO", for example with
`${OIDC_CLIENT_ID:+...}`. The docs stacklet is the reference.

## Connect a service

Each service is one OIDC client in Pocket ID (Admin, OIDC Clients, Add)
plus the OIDC settings on the service side. Link accounts by email:
the email in Pocket ID must equal the email of the existing account in
the service, or the service creates a duplicate.

| Service | Where to configure | Notes |
|---|---|---|
| photos (Immich) | Administration, Settings, Authentication, OAuth | verified 2026-09-18: issuer `https://id.<domain>`, scope `openid email profile`, RS256, storage label claim `preferred_username`; the Immich user's email must equal the Pocket ID email; keep password login on until every user is linked |
| drive (Nextcloud) | app `user_oidc`, Administration, OpenID Connect | set "Use unique user id" off so `username` maps to the existing account |
| docs (Paperless) | automated: `[oidc]` in its manifest, env rendered from the secrets store | existing users link once via My Profile, or by verified email; signups off; disable local login later |
| code (Forgejo) | Site administration, Authentication sources, OAuth2 | "Account linking: auto" |
| messages (Synapse) | `oidc_providers` in homeserver.yaml | Element shows "Continue with Pocket ID"; existing users need `allow_existing_users: true` |
| chatai (Open WebUI) | env `OAUTH_CLIENT_ID` etc. | `OAUTH_MERGE_ACCOUNTS_BY_EMAIL=true` |

Do one service at a time. After each: log in as an admin and as a
member, confirm the same account as before, then move on.

## Test plan before this goes to production

Run on a machine that is not the family server; the rig shares ports
and container names with production.

1. `tests/integration/stacktests up id`
2. Setup page reachable, first admin created, passkey registered.
3. Create the Simpsons from `users.toml` by hand, one-time links work on
   a phone.
4. `stacktests up photos`, connect it as above, log in as Homer via
   passkey, confirm it is the seeded Homer account, not a new one.
5. Stop the identity container. Confirm photos still serves logged-in
   sessions and the password fallback still works.
6. Backup: `./stack backup` archives `id/data`. Restore into an
   empty data dir, start, log in with the same passkey.
7. Watchtower: confirm the `v2` tag exists and a patch pull keeps data.

## Known open points

- Paperless access model for children (own documents only):
  `docs/design/docs-access-model.md`, planned.

- Account provisioning is opt-in (`[id] provision_users = true`); the
  reference install keeps it off until its `users.toml` is current.
- Existing accounts are never updated (email, admin flag). Only new
  usernames are created.
- `login_field = "passkey"` makes the CLI print "Login <admin> / <admin>"
  after `stack up`. Wrong for a passkey service; the CLI needs a way to
  say "no password login". Framework change, small.

## Note: no setup page

`STATIC_API_KEY` makes Pocket ID create a service admin user at first
start, and with one user present `/setup` redirects to the login page
for good. The stacklet relies on that: accounts come from provisioning
or the admin UI, never from `/setup`. On a fresh install turn on
`provision_users` before the first `stack up id`, or the first human
admin has no way in.

## Trap: client group restriction

A client has a group restriction switch and a group list. Switch on with
an empty list denies everyone; the log says `access_denied` after a
successful passkey. Keep the switch off unless the service must be
limited to a group, and then put the people into that group first.

## Verified on first start (2026-09-18)

- `ghcr.io/pocket-id/pocket-id:v2` resolves, version 2.14.0.
- The framework's 32-char urlsafe secret is accepted as `ENCRYPTION_KEY`.
- `/healthz` answers 204, `/setup` and `/login` answer 200 on the
  container port within seconds of start.
