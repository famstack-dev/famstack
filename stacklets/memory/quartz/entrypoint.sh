#!/bin/sh
# entrypoint.sh — keep the vault in sync from Forgejo, then serve it.
#
# Quartz `--serve` watches /vault and rebuilds on change, but the working
# copy only advances when we pull. A background loop pulls family/memory
# on a short interval, so a new commit — an archivist mirror, a seed
# push, or a hand-edit in the Forgejo web UI — shows up within
# VAULT_SYNC_INTERVAL seconds and Quartz rebuilds it for free. A Forgejo
# push webhook could replace this loop later; polling keeps it
# dependency-free and is cheap (the rebuild is sub-second).
set -e

INTERVAL="${VAULT_SYNC_INTERVAL:-20}"

# The working copy is cloned on the host and bind-mounted here, so git
# sees a different owner than the container user. Trust it explicitly,
# otherwise git refuses to operate on "dubious ownership".
git config --global --add safe.directory /vault

# Background sync, kept as cheap as possible. Each tick compares the
# local HEAD against the remote with `git ls-remote` — a single
# lightweight ref query, no object transfer, no working-tree touch — and
# only pulls when the remote has actually moved. So an idle tick (the
# common case) costs one tiny request and never triggers a rebuild; the
# fetch + fast-forward happens only when there is genuinely a new commit.
#
# Never fatal: if the clone isn't ready yet (first boot) or Forgejo is
# briefly unreachable, the tick is skipped and retried next time while
# the site keeps serving what is already on disk.
(
  while true; do
    sleep "$INTERVAL"
    local_head=$(git -C /vault rev-parse HEAD 2>/dev/null) || continue
    remote_head=$(git -C /vault ls-remote origin HEAD 2>/dev/null | cut -f1)
    if [ -n "$remote_head" ] && [ "$local_head" != "$remote_head" ]; then
      git -C /vault pull --quiet --ff-only 2>/dev/null || true
    fi
  done
) &

# Quartz in the foreground; its file watcher turns each pulled change
# into a rebuild. Port 8080 is the in-container port Caddy and the
# compose port mapping target.
exec npx quartz build --serve --directory /vault --port 8080
