# ADR-001: Per-service user seeding via API

**Status:** Experimental — may be removed before v0.1

**Date:** 2026-03-13

---

## Context

famstack needs a way to create user accounts across self-hosted services.
The first implementation (`stack photos seed`) creates Immich accounts by
calling its REST API directly. Each stacklet would ship its own seed script
in `cli/seed.py`, reading users from a central `users.toml`.

## Decision

We built it and tested it. It works — admin sign-up, user creation, and
idempotent re-runs all function correctly against Immich v2.5.6.

However, we're flagging this as experimental because the approach has
structural problems that get worse with scale.

## Problems

**API coupling.** Each seed script is tightly coupled to the upstream
service's API. We hit this immediately — Immich v2.5.6 returns 500 on
`/api/server/config` where earlier versions returned a clean response.
Every upstream version bump is a potential breakage, multiplied across
every stacklet that has a seed script.

**Admin password fragility.** If the admin account is created manually
through the web UI (common on first install), the password in `users.toml`
won't match and seed can't log in to create other users. There's no clean
recovery path — just a confusing error.

**Maintenance at scale.** One seed script for Immich is fine. Maintaining
seed scripts for Immich, Paperless, Forgejo, Matrix, and future services
means tracking N different APIs across N different release cycles. That's
the kind of work that quietly rots.

## The real solution

A central identity service (lldap + Authelia) where users are defined once
and services authenticate against LDAP. No per-service API calls, no
password sync issues, no upstream API coupling. Services that support LDAP
just work. Services that don't can fall back to manual account creation —
which is a one-time task, not an ongoing maintenance burden.

## What we're keeping for now

- `users.toml` as the user definition format — it's clean and will feed
  into the identity stacklet later
- The stacklet CLI plugin system (`stack <stacklet> <command>`) — that's
  solid architecture regardless of whether seed survives
- `stack photos seed` stays as experimental until the identity stacklet
  exists, then gets removed

## Decision outcome

Invest in the identity stacklet (lldap + Authelia) as the real v1 solution
for user management. Keep per-service seeding as a stopgap but don't
expand it to more stacklets.
