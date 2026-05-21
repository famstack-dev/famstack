# AdGuard DoH upstreams all timing out at 30s

## Symptom

AdGuard log shows repeated entries like:

```
[error] dnsproxy: exchange failed
  upstream=https://dns.google:443/dns-query
  duration=30.00s
  err="net/http: request canceled while waiting for connection
       (Client.Timeout exceeded while awaiting headers)"
```

Browsers on cable-attached devices show `DNS_PROBE_FINISHED_NXDOMAIN` or similar. WiFi devices (especially Apple) may look fine because iCloud Private Relay routes most external DNS around AdGuard.

## Quick diagnosis

Run on the host, not in the container:

```bash
# 1. Is bootstrap DNS reachable?
docker exec adguard nslookup dns.google 9.9.9.10

# 2. Can the host open a plain TCP/443 connection outbound?
nc -w 5 -zv 8.8.8.8 443
```

Decision table:

| Bootstrap nslookup | `nc` to 8.8.8.8:443 | Conclusion |
|--------------------|---------------------|------------|
| Fails | Anything | ISP or network is blocking plain DNS to Quad9. Switch bootstrap to mixed providers (Quad9 + Cloudflare + Google). |
| Works | Connects in <1s | Real DoH issue. Continue with "Cause A" below. |
| Works | Hangs until timeout | IPv6 dead path. See "Cause B". |
| Works | `Can't assign requested address` (EADDRNOTAVAIL) | TCP port exhaustion on the host. Jump to [tcp-port-exhaustion.md](tcp-port-exhaustion.md). |

## Cause A: broken DoH endpoint or unreachable upstream

A misconfigured DoH URL, or an upstream the local ISP is selectively filtering.

Common pitfalls in the AdGuard upstream list:

- `https://dns.cloudflare.com/dns-query` is **not** a valid Cloudflare DoH endpoint. The correct one is `https://cloudflare-dns.com/dns-query`. AdGuard's own validator (Settings > DNS settings > "Test upstreams") will reject the wrong one.
- Duplicate entries skew the load-balancer weights but otherwise behave like one entry.
- IPv6 bootstrap entries (`2620:fe::10`, etc.) only help when the host has working IPv6 outbound. Drop them otherwise.

## Cause B: IPv6 dead path

Container resolves AAAA records for the DoH host, dials over IPv6, and the dial hangs because there is no v6 route out of the LAN. The error wording `request canceled while waiting for connection` is the giveaway.

Fix by either:

- Removing IPv6 entries from Bootstrap DNS.
- Forcing v4-only outbound on the AdGuard container in `docker-compose.yml`:

  ```yaml
  sysctls:
    - net.ipv6.conf.all.disable_ipv6=1
  ```

## Fix

1. Switch upstreams to plain DNS over UDP to confirm recovery:
   ```
   9.9.9.9
   1.1.1.1
   8.8.8.8
   ```
2. Verify a client device can resolve `google.com` again.
3. Move back to a cleaned DoH list (correct URLs, no duplicates).
4. Set fallback DNS to at least two plain-DNS servers across providers.

## Prevent recurrence

- Keep upstream mode on **Load balancing** so slow upstreams are avoided automatically.
- Validate every URL via "Test upstreams" after any change.
- Keep at least one plain-DNS fallback so a DoH outage does not take down the LAN.
