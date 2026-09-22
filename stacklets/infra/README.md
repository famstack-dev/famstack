# infra: AdGuard Home and Caddy

DNS server with ad-blocking, and a reverse proxy that gives every stacklet a pretty URL. This is what makes `photos.home.internal` resolve to your Mac and serve the right service.

**Beta.** It works, but how HTTPS and your own sites are configured can still change between releases. Back up `~/famstack-data/infra/` before you upgrade.

## What it runs

- `stack-infra-adguard`: DNS server with ad-blocking. Resolves `*.{domain}` to the server IP and filters ads, trackers, and malicious domains for every device on the LAN.
- `stack-infra-caddy`: reverse proxy. Routes incoming requests by hostname to the right container and, with a DNS provider configured, serves them over HTTPS with certificates from Let's Encrypt.

## Enable

Set a domain in `stack.toml`, plus a DNS provider if you want HTTPS:

```toml
[core]
domain       = "home.example.family"
dns_provider = "hetzner"     # or "cloudflare"; empty serves plain HTTP
```

Then:

```bash
stack up infra
```

`stack up infra` builds the Caddy image with the DNS provider plugins (a minute or two the first time). If HTTPS is on and no token is stored yet, it asks for the provider's API token (see [TLS](#tls)). Then it writes the Caddyfile and starts both containers.

From then on the Caddyfile follows the stacklets. Every `stack up`, `stack down` and `stack destroy` rewrites `~/famstack-data/infra/Caddyfile` from the `caddy.snippet` of each stacklet that is up, and reloads Caddy. Each stacklet is served at `<id>.<domain>` (`photos.home.example.family`), and core's home and `/go` links at the bare domain. A name no running stacklet claims answers 404.

### DNS records

Every device has to resolve the bare domain and every name under it to the Mac. That takes two records, because a wildcard does not cover the bare name:

| Name | Type | Value |
|---|---|---|
| `*.home.example.family` | A | `192.0.2.10` (the Mac's LAN IP) |
| `home.example.family` | A | `192.0.2.10` |

Add both as DNS rewrites in AdGuard (Filters > DNS rewrites). That is what the LAN asks once the router points at AdGuard. Add them at the DNS provider too, for devices that bypass AdGuard, such as a browser with its own secure DNS or iCloud Private Relay. Some routers drop public DNS answers that point at a private address (DNS rebind protection); the AdGuard rewrites are not affected.

Give the Mac a fixed LAN IP with a DHCP reservation on the router, or every record goes stale when it changes.

## TLS

With `dns_provider` set, Caddy serves every service over HTTPS with two certificates from Let's Encrypt: a wildcard for `*.<domain>`, which every stacklet's subdomain uses, and one for the bare domain. It proves you own the domain by writing a TXT record through the provider's API (a DNS-01 challenge), so nothing on your LAN has to be reachable from the internet. The domain has to be registered, with its DNS zone hosted at that provider.

### Provider and token

| `dns_provider` | Token |
|---|---|
| `hetzner` | A Hetzner Cloud API token with Read & Write access, created in the Hetzner Console project that holds the zone (Security > API tokens). A token from the old DNS Console at dns.hetzner.com does not work. |
| `cloudflare` | A Cloudflare API token with `Zone.Zone:Read` and `Zone.DNS:Edit`, limited to this zone. |

### Where the token lives

In `.stack/secrets.toml` as `infra__DNS_API_TOKEN`, never in `stack.toml`. It reaches the Caddy container as the environment variable `DNS_API_TOKEN` and does not appear in the Caddyfile. Nothing prints it; `stack infra dns-token` reports its length only.

The first `stack up infra` asks for it in a terminal. Without a token, or with a provider the image has no plugin for, `stack up infra` stops and says what to do.

### Rotating it

Create the new token at the provider, then:

```bash
stack infra dns-token     # paste the new token; input is hidden
stack up infra            # restarts Caddy with it
```

Revoke the old token at the provider afterwards. Certificates Caddy already holds stay valid; the token is only used when a certificate is issued or renewed. Switching provider works the same way: change `dns_provider`, store the new provider's token, `stack up infra`.

Certificates and the ACME account live in `~/famstack-data/infra/caddy/`. Keep that directory. Without it Caddy requests every certificate again, and Let's Encrypt limits how many it issues per domain per week.

## Your own sites

Services that are not stacklets (a dashboard, a Docker UI) go in `~/famstack-data/infra/Caddyfile.local`. Only one proxy can own ports 80 and 443, so they cannot keep a Caddyfile of their own. The file is yours: the stack appends it after the stacklets' sites and never writes it.

```
status.{$STACK_DOMAIN} {
    reverse_proxy homepage:3000
}
```

Backends are container names on the `stack` network, or `host.docker.internal:<port>` for something running on the Mac itself. Run `stack up infra` after an edit. A name that a running stacklet also serves makes Caddy refuse the new config and keep the old one, and the command prints a warning.

## First run

AdGuard's setup wizard listens on the Mac only. Open `http://localhost:42081` there, or from another computer tunnel to it first with `ssh -L 42081:localhost:42081 you@<mac>` and open the same address. Walk through the wizard.

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

- Setup wizard (first run only): `http://localhost:42081` on the Mac
- AdGuard admin (after setup): `https://dns.<domain>`, or `http://localhost:42080` on the Mac
- Every other stacklet: `https://<stacklet>.<domain>`

Without a DNS provider, every address is `http://` instead.

## Point your router

Set the LAN's DNS server to the Mac's IP in the router's DHCP settings. Both wired and wireless clients then resolve through AdGuard. On a Fritz!Box: Home Network > Network > Network Settings > IPv4 Configuration > Local DNS server.

If you skip this step, only devices that manually point at the Mac's IP get the benefit. Most won't, so most of the household stays unfiltered.

## Data

Stored under `${ADGUARD_DATA_DIR}` and `${CADDY_DATA_DIR}` (defaults to `~/famstack-data/infra/`):

- `adguard/work/`: query logs and stats. Volatile, fine to wipe.
- `adguard/conf/AdGuardHome.yaml`: DNS configuration, filter lists, custom rules. Back this up.
- `Caddyfile`: reverse-proxy routes, rewritten from the stacklets' snippets whenever a stacklet starts or stops. Do not edit it.
- `Caddyfile.local`: your own sites, if you have any. Back this up.
- `caddy/`: TLS certificates and ACME state. Back this up if you use HTTPS.

## Updating

Watchtower handles patch updates silently in the background. To restart manually:

```bash
stack restart infra
```

## Removing

```bash
stack destroy infra
```

This stops both containers, removes state, and deletes everything under `~/famstack-data/infra/`, including certificates and `Caddyfile.local`. The stored DNS API token is removed too; revoke it at the provider. Your router's DNS server setting does not change automatically; update it back to whatever it was before, or to your ISP's DNS, to keep the LAN online.
