#!/bin/sh
# entrypoint.sh — serve the vault as the family wiki.
#
# Pure view: Quartz `--serve` watches /vault and rebuilds on change.
# The working copy is kept in sync by the curator sidecar (the vault
# keeper — it pulls from Forgejo on its poll tick; see
# `../bot/curator.py`), so this container never touches git and the
# vault is mounted read-only. If the curator is down, the wiki keeps
# serving what is on disk — stale, never broken.
set -e

# Quartz in the foreground; its file watcher turns each pulled change
# into a rebuild. Port 8080 is the in-container port Caddy and the
# compose port mapping target.
exec npx quartz build --serve --directory /vault --port 8080
