# photos — Immich

Family photo library and mobile backup, running on your own hardware.

Immich replaces Google Photos and iCloud — automatic mobile backup, shared albums,
face recognition, and CLIP-based search. All data stays on your Mac.

## What it runs

- `immich-server` — the main app (API + web UI)
- `immich-machine-learning` — face recognition and smart search (CPU, no GPU required)
- `redis` (valkey) — job queue and caching
- `postgres` — the database (with pgvector for ML search)

## Enable

```bash
stack up photos
```

That's it. `stack up` creates data directories, generates the database password,
renders the `.env` file, and starts all containers. No manual config needed.

## Access

- Web UI: `http://photos.home.internal` (or `http://localhost:2283` in port mode)
- Mobile app: search "Immich" in the App Store / Play Store

## First run

Open the web UI and create the admin account. Then optionally:

```bash
stack photos seed
```

This creates accounts for everyone in `users.toml`.

## Single sign-on

With an OIDC provider installed (see `docs/stack-reference.md`, "Single
Sign-On (OIDC)"), `stack up photos` turns on "Sign in with family
account" on the login page and in the mobile app. Accounts link by
email: the address at the provider must equal the one on the Immich
account.

`hooks/on_start_ready.py` writes five keys of Immich's OAuth settings
through the admin API: on, issuer, client id, client secret, button
text. Every other setting stays as set in the admin UI, and the
password login stays on. Without a provider nothing is written, so a
login set up by hand in the admin UI is kept.

## The stack's own account (incubating)

### What is the stack's own account?

The **stack's own account** is an Immich account that only famstack uses.
Each family member shares their library with it, as an Immich partner.
Its API key can then find the photos of every family member. For example,
a photo search in the chat finds Homer's and Marge's photos together.

The account can only read. Immich refuses to delete, archive or change a
photo of a family member through a partner share.

> [!WARNING]
> **Do not use this feature for photos you cannot lose.** It is incubating.
> It can change or disappear in any release.

### How to switch it on

1. Add this line to the `[photos]` section of `stack.toml`:

   ```toml
   [photos]
   stack_account = true
   ```

2. Start the photos stacklet:

   ```bash
   ./stack up photos
   ```

3. Restart core when the output tells you to:

   ```bash
   ./stack restart core
   ```

   Core reads the key of the account only when it starts.

`stack up photos` then does these steps:

- It creates the account. The account is not an admin. Its name is the
  `[core] name` of the stack, for example `famstack`. Its profile picture
  is the famstack icon.
- It keeps the password in `.stack/secrets.toml` as
  `photos__STACK_ACCOUNT_PASSWORD`.
- It creates an API key and keeps it as `photos__API_KEY`. A key that an
  admin put there by hand is replaced, and the output says so.
- It shares the library of each person in `users.toml` who has an Immich
  account. The `stacklets` line of the person does not matter.

Each later `stack up photos` shares the library of a person who is new in
`users.toml`. It changes nothing else. The output names each person who
has no Immich account.

To do only these steps without `stack up`, run:

```bash
./stack photos setup --stack-account
```

### What does the family see?

Each family member sees the account in their partner list in Immich.
Nobody sees more photos than before.

A family member can remove the account from their partner list. The stack does
not add the account again, because it shares each library one time only.
The output of `stack up photos` names these family members.

### How a family member shares a library by hand

The stack shares a library as the family member, with the password the
stack gave them. A family member who changed their password in the Immich
app must share by hand. `stack up photos` names these family members.

To share a library, the family member does these steps in Immich:

1. Open **Account Settings**.
2. Open **Partner Sharing**.
3. Add the stack's account as a partner.

### How to share an album with the stack

A partner share does not show the albums of a family member. To let the
stack read an album, share the album with the stack's account in Immich.

### How to switch it off

1. Set the switch to `false` in `stack.toml`, or remove the line.
2. Start the photos stacklet:

   ```bash
   ./stack up photos
   ```

`stack up photos` removes the account and its key. The photos and albums
of the family members stay as they are.

### Imports and the stack's key

When the switch is on, `stack photos import` and `stack photos album` use
`photos__IMPORT_API_KEY` only. This is because photos that the stack's key
uploads belong to the stack's account, not to a family member.

When the switch is off, they also use a key in `photos__API_KEY`, as
before.

## Data

Stored in `~/famstack-data/photos/`:
- `library/` — uploaded photos (back this up)
- `postgres/` — database (must be on SSD)

## Updating

Watchtower handles patch updates automatically at 3am. To restart manually:

```bash
stack restart photos
```

## Removing

```bash
stack destroy photos
```

This stops containers, removes state, and **deletes all data** in `~/famstack-data/photos/`.
