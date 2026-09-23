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
