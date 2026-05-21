# infra: AdGuard Home and Caddy

DNS server with ad-blocking, and a reverse proxy that gives every stacklet a pretty URL. This is what makes `photos.home.internal` resolve to your Mac and serve the right service.

## What it runs

- `stack-infra-adguard`: DNS server with ad-blocking. Resolves `*.{domain}` to the server IP and filters ads, trackers, and malicious domains for every device on the LAN.
- `stack-infra-caddy`: reverse proxy. Routes incoming requests by hostname to the right container, terminates TLS, and (optionally) provisions Let's Encrypt certificates if you use a real domain.

## Enable

```bash
stack up infra
```

`stack up` creates the data directories, renders the Caddyfile from your `stack.toml`, starts both containers, and prints next-step hints. AdGuard runs its first-run wizard on port 3000.

## First run

Open `http://<server-ip>:3000` and walk through the AdGuard wizard.

1. **Admin user**: pick any username and password. You will only use this when changing AdGuard settings, not for daily browsing.
2. **Network interfaces**: accept the defaults (`0.0.0.0` for both web and DNS, port 53).
3. **DNS settings**: use the recommended config below. The wizard prefills some defaults; replace them entirely.

### Recommended DNS settings

Pick the set that matches your jurisdiction preference. Both options have been live-tested (each plain-DNS IP verified with `dig`, each DoH URL accepted by AdGuard's "Test upstreams" validator). Whichever you choose, paste the lists exactly into Settings > DNS settings after the wizard.

#### Option A: EU / EEA providers only

Strongest privacy posture for users who want no US legal jurisdiction over their resolver. All providers are based in the EU or EEA (Switzerland for Quad9, Sweden for Mullvad, Cyprus for AdGuard).

**Upstream DNS** (DoH):

```
https://dns10.quad9.net/dns-query
https://unfiltered.adguard-dns.com/dns-query
https://base.dns.mullvad.net/dns-query
```

**Bootstrap DNS** (plain DNS, IPv4 only):

```
9.9.9.10
149.112.112.10
94.140.14.140
94.140.14.141
```

Mullvad cannot appear in Bootstrap because they do not operate a plain-DNS endpoint (DoH/DoT only). Quad9 and AdGuard provide the redundancy here.

**Fallback DNS** (plain DNS):

```
9.9.9.10
94.140.14.140
```

#### Option B: Mixed EU + US (default, prioritises diversity and speed)

Adds Cloudflare's US-based resolver. Cloudflare has KPMG-audited no-logs claims and the fastest global anycast in most benchmarks. The trade-off is jurisdiction (US-based, subject to CLOUD Act).

**Upstream DNS** (DoH):

```
https://dns10.quad9.net/dns-query
https://cloudflare-dns.com/dns-query
https://unfiltered.adguard-dns.com/dns-query
```

**Bootstrap DNS** (plain DNS):

```
9.9.9.10
1.1.1.1
94.140.14.140
```

**Fallback DNS** (plain DNS):

```
9.9.9.10
1.1.1.1
94.140.14.140
```

### Settings that apply to both options

**Upstream mode**: Load balancing. Distributes queries across the three upstreams and avoids slow ones automatically. Switch to "Parallel requests" if you want lower tail latency at the cost of slightly more upstream bandwidth.

**Bootstrap IPv6**: drop any IPv6 entries unless you have confirmed working IPv6 outbound from the host. Hanging v6 dials look identical to DoH timeouts and are painful to diagnose later.

**Common pitfall**: `dns.cloudflare.com` is **not** a valid Cloudflare DoH endpoint. The correct hostname is `cloudflare-dns.com`. AdGuard's own validator (the "Test upstreams" button) will reject the wrong one.

**Verify before walking away**: hit "Test upstreams" in the AdGuard UI. All entries should turn green.

**Rate limit**: the default 20 req/s applies per `/24` subnet, so all your wired devices share 20 qps. On a busy LAN, raise it (50 to 100) or set the IPv4 subnet prefix length to 32 so the limit is per-device.

### DNS cache configuration

A separate panel further down the DNS settings page. Four knobs worth changing from defaults for a home LAN:

| Setting | Default | Recommended | Why |
|---------|---------|-------------|-----|
| Cache size | `4194304` (4 MB) | `16777216` (16 MB) | More entries held at once, fewer evictions when several devices are active. ~12 MB extra RAM on the host. Negligible. |
| Override minimum TTL | `0` | `300` | Some CDN and matchmaking endpoints publish 30-60 second TTLs. A 5-minute floor lets repeat lookups hit cache instead of round-tripping upstream. Trade-off: stale data possible for up to 5 minutes after a hostname's IP rotates. Fine at home; would not do it on a CDN edge. |
| Override maximum TTL | `0` | `0` (leave) | No real benefit changing. Upstream TTLs are rarely absurd. |
| Optimistic caching | off | **on** | When a cached entry is at its expiry boundary, AdGuard returns the still-cached response immediately and refreshes in the background. Removes the blocking cache-miss case where a client waits on an upstream round-trip. Briefly serves slightly stale answers after expiry. |

Optimistic caching is the setting that actually changes how the LAN feels. Cached lookups are already microseconds; the slow ones are the cache-miss-while-refreshing cases. Optimistic mode eliminates the blocking version of that.

### Validate before walking away

```bash
# AdGuard's own check: all three upstreams should be green
# Settings > DNS settings > "Test upstreams"

# From a cable client, resolve something internal and something external
nslookup photos.home.internal
nslookup google.com

# Watch the log for 60 seconds. No 30-second timeouts is healthy.
docker logs -f stack-infra-adguard | grep -iE "error|timeout"
```

## Access

- Setup wizard (first run only): `http://<server-ip>:3000`
- AdGuard admin (after setup): `http://<server-ip>:42080` or `http://dns.{domain}`
- Every other stacklet: `http://<stacklet>.{domain}`

## Point your router

Set the LAN's DNS server to the Mac's IP in the router's DHCP settings. Both wired and wireless clients then resolve through AdGuard. On a Fritz!Box: Home Network > Network > Network Settings > IPv4 Configuration > Local DNS server.

If you skip this step, only devices that manually point at the Mac's IP get the benefit. Most won't, so most of the household stays unfiltered.

## Data

Stored under `${ADGUARD_DATA_DIR}` and `${CADDY_DATA_DIR}` (defaults to `~/famstack-data/infra/`):

- `adguard/work/`: query logs and stats. Volatile, fine to wipe.
- `adguard/conf/AdGuardHome.yaml`: DNS configuration, filter lists, custom rules. Back this up.
- `Caddyfile`: reverse-proxy routes. Regenerated from `stack.toml` on every `stack up`.
- `caddy/`: TLS certificates and ACME state. Back this up if you use a real domain.

## Updating

Watchtower handles patch updates silently in the background. To restart manually:

```bash
stack restart infra
```

## Removing

```bash
stack destroy infra
```

This stops both containers, removes state, and deletes everything under `~/famstack-data/infra/`. Your router's DNS server setting does not change automatically; update it back to whatever it was before, or to your ISP's DNS, to keep the LAN online.
