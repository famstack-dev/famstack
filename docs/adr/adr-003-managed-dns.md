# ADR-003: Managed DNS with famstack-owned domain

**Status:** Proposed (v2)

**Date:** 2026-03-15

---

## Context

famstack's zero-config port mode works but produces ugly URLs
(`192.0.2.10:42020`). Domain mode requires the user to configure
wildcard DNS on their router — a real barrier for non-technical users.

We want pretty URLs with valid HTTPS and zero networking knowledge.

## Idea

famstack operates a domain (e.g. `famstack.family`). Each user registers
a unique subdomain. DNS records point to the user's private LAN IP. Caddy
obtains real TLS certificates via DNS-01 challenge — no public port
exposure needed.

```
photos.griswolds.famstack.family  →  192.0.2.10  (LAN only)
docs.griswolds.famstack.family    →  192.0.2.10  (LAN only)
```

Valid HTTPS, trusted by every browser, works from any device on the home
network.

## How it works

### Registration

```bash
stack register griswolds
```

The CLI calls a small registration API (hosted on famstack.dev or similar):
1. Checks `griswolds` is available
2. Creates DNS records via Cloudflare API:
   - `*.griswolds.famstack.family` → user's LAN IP
3. Stores the subdomain in `stack.toml`
4. Configures Caddy with the DNS-01 challenge provider

### DNS resolution

The wildcard DNS record points to a **private IP** (e.g. `192.0.2.10`).
This means the domain only resolves to something useful from inside the
user's home network. From the outside, it resolves to a non-routable
address — effectively useless. This is a feature, not a bug: famstack
is local-only by design.

### TLS certificates

Caddy requests a wildcard certificate for `*.griswolds.famstack.family`
using the DNS-01 ACME challenge. This works because:

- DNS-01 doesn't require inbound connections (unlike HTTP-01)
- The ACME server verifies domain ownership by checking a TXT record
- Caddy's Cloudflare DNS plugin can create that TXT record automatically
- The certificate is issued by Let's Encrypt and trusted everywhere

The user gets real HTTPS without opening any ports to the internet.

### IP changes

LAN IPs are typically stable (DHCP leases are long, and most users set
static IPs for servers). If the IP changes:

- `stack ip-update` pushes the new IP to the DNS API
- Or: a lightweight cron job does this automatically (like dynamic DNS)

## What famstack needs to operate

| Component | Cost | Notes |
|-----------|------|-------|
| Domain (`famstack.family` or similar) | ~$10/year | One-time purchase |
| Cloudflare DNS (free tier) | $0 | API for programmatic record management |
| Registration API | Minimal | Small service: check availability, create DNS records |
| Caddy DNS-01 plugin | $0 | Open source, already exists for Cloudflare |

## User experience

```bash
# One-time setup
stack register griswolds
# → Registering griswolds.famstack.family...
# → DNS configured, requesting TLS certificate...
# → Done! Your services are now available at:
# →   https://photos.griswolds.famstack.family
# →   https://docs.griswolds.famstack.family

# Day-to-day
stack list
# → photos   ● online   https://photos.griswolds.famstack.family
# → docs     ● online   https://docs.griswolds.famstack.family
```

## Three tiers of URL complexity

| Tier | Setup required | URL example | When |
|------|---------------|-------------|------|
| Port mode | Nothing | `http://192.0.2.10:42010` | v0.1 (now) |
| Managed DNS | `stack register` | `https://photos.griswolds.famstack.family` | v2 |
| Custom domain | Router DNS + stack.toml | `https://photos.home.internal` | Power users |

Each tier is a superset — you can always fall back to the simpler one.

## Open questions

- **Domain choice**: `famstack.family`, `famstack.dev`, `famstack.io`?
- **Abuse prevention**: rate limiting on registration, reserved names
- **Revocation**: what happens when a user abandons their subdomain?
- **Privacy**: the DNS records reveal the user's LAN IP structure to
  anyone who queries the domain. This is low-risk (private IPs are
  non-routable) but worth noting.
- **Caddy build**: DNS-01 providers require a custom Caddy build with the
  plugin compiled in. Ship a pre-built binary or Docker image?

## Decision

Park for v2. The infrastructure is simple and cheap, the UX is
compelling, and it solves the "pretty URLs without networking knowledge"
problem completely. Build it after port mode and the identity stacklet
are proven.
