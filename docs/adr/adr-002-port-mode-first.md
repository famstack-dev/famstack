# ADR-002: Port mode first, Caddy when you're ready

**Status:** Accepted

**Date:** 2026-03-14

---

## Context

The simplest famstack setup should work without DNS configuration, reverse
proxies, or any networking knowledge. You run `stack up photos` and access
it from any device on your LAN.

## Decision

Caddy is opt-in, activated when `domain` is set in stack.toml.

**Port mode (domain empty):**
- Services bind to `0.0.0.0:<port>` — reachable from the network
- URLs shown as `hostname:port` (e.g. `mac-arthur.local:42010`)
- Caddy does not start, core only runs Watchtower
- Zero DNS setup required

**Domain mode (domain set):**
- Caddy starts and assembles the Caddyfile from stacklet snippets
- Services bind to `127.0.0.1:<port>` — only Caddy reaches them
- URLs shown as `service.domain` (e.g. `photos.home.internal`)
- Requires wildcard DNS entry in router

## Migration path

Switching from port mode to domain mode:

1. Set `domain = "home.internal"` in stack.toml
2. Configure wildcard DNS in router (`*.home.internal` → server IP)
3. Run `stack up <stacklet>` on each enabled stacklet

`stack up` is idempotent — it re-renders `.env`, restarts containers
with updated port bindings, assembles the Caddyfile, and starts Caddy.
`docker compose up -d` automatically recreates containers when the
port binding changes from `0.0.0.0` to `127.0.0.1`.

No data loss, no manual migration steps.

## Port convention

All stacklet ports live in the `42xxx` range (see concept.md for the
full table). This avoids collisions with common dev tools.
