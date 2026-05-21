# Infra runbooks

Symptom-driven entries for diagnosing and recovering from incidents
in the infra stacklet's AdGuard Home + Caddy + host networking
surface. Each file starts from a user-visible signal (browser error,
log pattern, command failure) and walks to root cause and fix.

## Index

| Symptom | Runbook |
|---------|---------|
| AdGuard DoH upstreams all timing out at 30s; cable clients lose DNS | [dns-doh-timeouts.md](dns-doh-timeouts.md) |
| Outbound TCP fails instantly with `Can't assign requested address` (EADDRNOTAVAIL); multiple unrelated services degrade at once | [tcp-port-exhaustion.md](tcp-port-exhaustion.md) |

## Conventions

- One file per symptom or scenario. Filename describes the symptom
  in kebab-case (`dns-doh-timeouts.md`, `tcp-port-exhaustion.md`).
- Each entry follows **Symptom → Quick diagnosis → Cause → Fix →
  Prevent recurrence**. Every block runnable as-is by an agent or
  operator — no placeholders without an explicit `# replace this`
  note.
- If a runbook references another, link to it.

## When to add a new entry

After any incident that took more than 15 minutes to diagnose. The
cost of the next occurrence is paid by whoever is on call. Write the
runbook before the memory fades.
