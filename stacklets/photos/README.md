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
