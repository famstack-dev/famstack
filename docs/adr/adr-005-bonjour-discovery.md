# ADR-005: LAN Discovery via Bonjour

**Status:** Accepted

**Date:** 2026-04-01

**See also:** [RFC-001: Dashboard & Companion Apps](rfc-001-dashboard-and-companion-apps.md),
[ADR-003: Managed DNS](adr-003-managed-dns.md)

---

## Context

famstack needs a way for companion apps (macOS tray, iOS, web dashboard)
to find the server on the local network without manual IP entry.

ADR-003 solves the pretty-URL problem for browsers (`photos.griswolds.famstack.family`).
This ADR solves the complementary problem: **how does a native app discover
the server in the first place?** Before DNS is configured, before the user
knows the server's IP, before anything is typed into a settings screen —
the app should just find it.

Apple's Bonjour (mDNS + DNS-SD) is the standard answer on home networks.
Every Apple device supports it natively. Android supports it via `NsdManager`
(less reliable, but functional). It requires zero infrastructure — no DNS
server, no router config, no internet connection.

### Prior art

**PicoMLX's BonjourPico** broadcasts `_pico._tcp` with a persistent server
UUID in the TXT record. When the server's IP changes (DHCP lease renewal,
Wi-Fi reconnect), the UUID stays the same. Clients pair to the UUID, not
the address. This is the pattern we adopt.

**OMLX** does not use Bonjour — it relies on the user knowing the server
address. This works for developers but not for family members.

**Apple ecosystem:** AirPlay (`_airplay._tcp`), AirPrint (`_ipp._tcp`),
HomeKit (`_hap._tcp`) all use this exact pattern. famstack joins a
well-understood family of LAN services.

---

## Decision

The famstack API server advertises itself via Bonjour using a persistent
server identity. Companion apps discover it automatically on first launch
and re-discover it when the network changes. A QR code provides fallback
for devices that can't do mDNS (older Android, some corporate networks).

---

## Protocol

### Service type

```
_famstack._tcp.local.
```

One instance per famstack server. The service name is the server's
human-readable name from `stack.toml`:

```
Arthur's famstack._famstack._tcp.local.
```

### Port

The API server port, default `42000`. Companion apps connect to this
port for all API calls.

### TXT record

The TXT record carries metadata that clients need before making their
first API call:

| Key | Example | Purpose |
|---|---|---|
| `uuid` | `a1b2c3d4-e5f6-7890-abcd-ef1234567890` | Persistent server identity |
| `version` | `0.1.0` | famstack version (for compatibility checks) |
| `name` | `Arthur's famstack` | Human-readable display name |
| `api` | `/api` | API base path (future-proofing) |

**TXT record size:** ~150 bytes. Well within the DNS-SD 8900-byte limit
and the practical 400-byte recommended maximum.

### Server UUID

The UUID is generated once during `stack init` and stored in
`.famstack/server.toml`:

```toml
# .famstack/server.toml (gitignored)
uuid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
created = "2026-04-01T10:30:00Z"
```

It never changes. Not on IP change. Not on hostname change. Not on
`stack.toml` edits. Not on OS upgrade. Only a full `stack uninstall`
followed by `stack init` generates a new one.

This is the anchor that companion apps pair to. When the server moves
to a different IP (DHCP renewal, network switch, migration to new
hardware with the same data), the companion app re-discovers the same
UUID via Bonjour and reconnects transparently.

---

## Server-side implementation

### Advertising

The API server registers the Bonjour service on startup using Python's
`zeroconf` library:

```python
import socket
import uuid
from zeroconf import Zeroconf, ServiceInfo

def advertise(stack):
    server_uuid = stack.server_uuid()  # reads from .famstack/server.toml
    local_ip = get_local_ip()

    info = ServiceInfo(
        type_="_famstack._tcp.local.",
        name=f"{stack.config.name}._famstack._tcp.local.",
        addresses=[socket.inet_aton(local_ip)],
        port=stack.api_port,
        properties={
            "uuid": server_uuid,
            "version": stack.version,
            "name": stack.config.name,
            "api": "/api",
        },
    )

    zc = Zeroconf()
    zc.register_service(info)
    return zc  # caller holds reference, unregisters on shutdown
```

### IP address selection

`get_local_ip()` returns the primary LAN interface address. Strategy:

1. Open a UDP socket to a non-routable address (`10.255.255.255:1`)
2. Read the socket's own address — this is the interface the OS would
   use to reach the LAN
3. Fall back to `socket.gethostbyname(socket.gethostname())`

This avoids hardcoding interface names (`en0`, `en1`) which vary across
Mac models and docking configurations.

### Re-advertisement on network change

When the LAN IP changes (cable plugged in, Wi-Fi reconnect), the
Bonjour service must be re-registered with the new address. Two
approaches:

**Option A: zeroconf's `InterfaceChoice.All`.** Registers on all
interfaces. The OS handles failover. Simple, but advertises on
interfaces we may not want (VPN, Docker bridge).

**Option B: NetworkMonitor.** Use `SCNetworkReachability` (via PyObjC)
or poll the IP every 30 seconds. On change, unregister and re-register.
More control, slightly more code.

Start with Option A. Restrict to specific interfaces only if Docker
bridge or VPN advertisement causes confusion.

---

## Client-side implementation

### Apple devices (macOS, iOS, iPadOS)

Use `NWBrowser` from the Network framework. Available on macOS 10.15+,
iOS 13+.

```swift
import Network

class FamstackDiscovery: ObservableObject {
    @Published var servers: [FamstackServer] = []
    private let browser: NWBrowser

    init() {
        let params = NWParameters()
        params.includePeerToPeer = true
        browser = NWBrowser(for: .bonjour(type: "_famstack._tcp", domain: nil),
                           using: params)
    }

    func start() {
        browser.browseResultsChangedHandler = { results, changes in
            self.servers = results.compactMap { result in
                guard case .service(let name, let type, let domain, _) = result.endpoint else {
                    return nil
                }
                return FamstackServer(name: name, type: type, domain: domain)
            }
        }
        browser.start(queue: .main)
    }
}
```

On discovery, the app resolves the service to get the IP, port, and TXT
record. The UUID from the TXT record is stored in Keychain. On subsequent
launches, the app browses for the same UUID — if the IP changed, it
still reconnects.

### Android devices

Use `NsdManager` (available since API 16):

```kotlin
val nsdManager = getSystemService(Context.NSD_SERVICE) as NsdManager
nsdManager.discoverServices("_famstack._tcp", NsdManager.PROTOCOL_DNS_SD, listener)
```

Android's mDNS implementation is less reliable than Apple's — some
devices have buggy resolvers, and discovery can take 5-10 seconds.
The QR code fallback is more important on Android.

### Web browsers

Browsers cannot perform mDNS discovery. The web dashboard has two paths:

1. **Direct URL.** User navigates to `http://mac-arthur.local:42000` or
   the server's IP. Works on Apple devices (Safari resolves `.local` via
   mDNS). Inconsistent on Android browsers.

2. **QR code.** The dashboard is already running (the user got to it
   somehow). For onboarding *other* devices, the dashboard shows a QR
   code at `/onboard` containing the server's address and an API key.
   Another phone scans it, bookmarks the URL, adds to home screen.

---

## QR code onboarding

The QR code is the universal fallback. It works on every device with a
camera, requires no mDNS support, and transfers the API key in one step.

### QR payload

```json
{
  "famstack": 1,
  "uuid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "host": "mac-arthur.local",
  "ip": "192.0.2.10",
  "port": 42000,
  "key": "fs_ak_..."
}
```

| Field | Purpose |
|---|---|
| `famstack` | Schema version. Lets the client know this is a famstack QR code. |
| `uuid` | Server identity for persistent pairing. |
| `host` | `.local` hostname (preferred, human-readable). |
| `ip` | Fallback IP for devices that can't resolve `.local`. |
| `port` | API port. |
| `key` | API key with "action" tier permissions (start, stop, restart, view). Not admin. |

### QR code display

The dashboard shows the QR code at `GET /onboard`:

- Full-screen, high-contrast, works in a dim room
- Auto-generates a scoped API key on display (not the admin key)
- Key can be revoked from the dashboard later
- Regenerate button if the key is compromised

### QR code scanning

**Native apps:** Use the device camera to scan. Parse the JSON, store
UUID + key in Keychain, connect immediately.

**Web (phone browser):** The user scans with their phone's camera app.
The QR code can optionally encode a URL (`http://192.0.2.10:42000/pair?key=...`)
that opens the dashboard in the browser and stores the key in localStorage.

---

## Relationship to ADR-003 (Managed DNS)

ADR-003 gives famstack pretty URLs with HTTPS (`photos.griswolds.famstack.family`).
This ADR gives famstack zero-config device discovery (`_famstack._tcp.local.`).

They solve different problems and coexist:

| | ADR-003 (Managed DNS) | ADR-005 (Bonjour) |
|---|---|---|
| **Solves** | Pretty URLs, valid HTTPS | App-to-server discovery |
| **Requires** | Internet (DNS registration) | Nothing (LAN only) |
| **Used by** | Browsers accessing stacklet web UIs | Native companion apps |
| **When** | After `stack register` | Immediately, on any LAN |

A user in port mode (no domain configured) still gets full Bonjour
discovery. A user with managed DNS gets both. The flows are independent.

When managed DNS is active, the Bonjour TXT record can include the
registered domain as an additional field:

```
domain = "griswolds.famstack.family"
```

Companion apps can then construct stacklet URLs as
`https://photos.griswolds.famstack.family` instead of
`http://192.0.2.10:42010`.

---

## Security considerations

**Bonjour is LAN-only.** mDNS queries don't cross routers. The service
is only visible to devices on the same network segment. This matches
famstack's threat model (trusted home LAN).

**The TXT record is not secret.** It contains the server name, UUID,
version, and API base path. None of these are credentials. The API key
is never broadcast — it's transferred via QR code (one-time, in-person)
or set manually.

**UUID is not authentication.** Knowing the UUID lets you find the
server, not control it. All mutating API calls still require PIN or API
key authentication (RFC-001).

**Rogue advertisement.** A malicious device on the LAN could advertise
a fake `_famstack._tcp` service. The companion app would discover it
and potentially send API requests to it. Mitigation: the app pairs to a
specific UUID on first connection. If a different UUID appears, the app
warns the user rather than silently switching. This is the same trust
model as Bluetooth pairing.

---

## Implementation plan

### Phase 0 (with API server)

1. Generate server UUID during `stack init`, store in `.famstack/server.toml`
2. Add `zeroconf` as an optional dependency of the API server
3. Advertise `_famstack._tcp.local.` on API server startup
4. Unregister on shutdown

### Phase 1 (with dashboard)

5. Add `/onboard` page with QR code
6. QR code contains UUID + host + IP + scoped API key
7. Add key revocation to the dashboard

### Phase 2 (with macOS tray app)

8. Implement `FamstackDiscovery` Swift package using `NWBrowser`
9. First-launch flow: discover → pair → store UUID in Keychain
10. Reconnect flow: browse → match UUID → resolve → connect

### Phase 3 (with iOS companion)

11. Same discovery package, shared from Phase 2
12. Add QR scanning as onboarding alternative
13. Add Android documentation for `NsdManager` integration

---

## Open questions

**Multiple famstack servers on one LAN.** Uncommon but possible (dev +
production, or two family members each running their own). The companion
app should show a picker if multiple `_famstack._tcp` services are
discovered. The UUID ensures the app reconnects to the right one.

**Docker network interfaces.** Bonjour advertised on all interfaces
(`InterfaceChoice.All`) may appear on Docker bridge networks. This is
harmless (no client will be on the Docker bridge) but clutters the
service registry. Monitor and restrict to physical interfaces if needed.

**mDNS on corporate/guest networks.** Some networks block mDNS traffic.
The QR code fallback handles this. The companion app should show a clear
message: "Can't find your server automatically. Scan the QR code on
your dashboard, or enter the address manually."

**IPv6.** Bonjour supports IPv6 natively. The `ServiceInfo` can include
both IPv4 and IPv6 addresses. No special handling needed — `NWBrowser`
resolves to whichever address the OS prefers. Worth testing on
IPv6-only networks (rare in homes, but increasingly common).
