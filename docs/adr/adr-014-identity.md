# ADR-014: One passkey login for all services (Pocket ID)

**Status:** Proposed, under private test
**Date:** 2026-09-18
**Supersedes:** the "real solution" section of ADR-001 (lldap + Authelia)

## Context

Every stacklet ships its own accounts. ADR-001 seeds them per service
through each upstream API and already calls that temporary. In daily
use it means one login per service per device, and password resets
per service. The family intranet (ADR-013) now puts every service under
one name space reachable from every device, so the last piece that
differs per service is the login.

The owner's requirement: log in by device. On your own device one tap,
on a foreign device a QR scan with your phone. No passwords.

## Decision

Run one OpenID Connect provider on the homeserver, Pocket ID, and make
every OIDC-capable service delegate its login to it.

- Passkeys only. Pocket ID has no password login. A passkey is a tap
  with Face ID or Touch ID on the device that holds it, and a QR scan
  through the phone on any other device. Both flows are built into iOS,
  Android, macOS and Windows.
- One user per person, created once. Services link by email address.
- The provider runs at home, at `id.<domain>`, next to the
  services it protects. It does not run on the satellite VPS: logins
  must work when the uplink is down, the data is family data, and the
  VPS stays stateless apart from the encrypted backup (ADR-013).
- Identity and network stay separate layers. The tailnet decides which
  device reaches a service. The passkey decides who the person is.

## Alternatives considered

| Option | Why not |
|---|---|
| lldap + Authelia (ADR-001 plan) | two services, LDAP plus a portal, passwords remain the primary factor; passkeys are an add-on, not the model |
| Authentik, Keycloak | full enterprise IdPs; far more surface and upkeep than a household needs |
| tsidp (tailnet identity as OIDC) | zero-click on the tailnet, but experimental upstream, untested with headscale, and logins would only work through the tunnel; worth an experiment on top later |
| Caddy header auth from tailnet identity | covers only services that accept trusted headers; most of ours do not |
| keep per-service accounts | the problem statement |

## Consequences

- Per-service seed scripts become obsolete once a service is linked.
- Pocket ID's SQLite file and `ENCRYPTION_KEY` join the irreplaceable
  set: without them every member re-registers and every service
  re-links. Both are covered by the backup stacklet.
- A new device means one passkey registration per person, then nothing.
- Passkey storage is the platform keychain (iCloud Keychain for the
  Apple households famstack targets, Google Password Manager on
  Android). A self-hosted vault is optional, for mixed households or
  people who want secrets under their own roof; it is not part of this
  decision.
- Kids need a device with biometrics or a phone to scan with.
- Passkeys for third-party websites are out of scope. Pocket ID cannot
  serve them; that is a vault (Vaultwarden), a separate decision.

## Rollout

1. Private stacklet, excluded from git. Test on a non-production
   machine per `stacklets/id/README.md`.
2. Enable on the homeserver. Adults register passkeys.
3. Link services one at a time, password login kept on until every
   member is linked. Order: photos, drive, docs, code, messages, chatai.
4. Turn off password login per service. Remove seed scripts.
5. Publish the stacklet and mark this ADR Accepted.

## Open items

- User provisioning from `users.toml` through the admin API.
- Caddy: the framework does not assemble `caddy.snippet` files yet.
  Until it does, the host entry is added to the reverse proxy by hand.
- Session length policy per service, default 30 days at the provider.
- tsidp experiment for zero-click on the tailnet.
