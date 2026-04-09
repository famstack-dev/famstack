# RFC-001: Dashboard Stacklet & Native Companion Apps

**Status:** Draft

**Date:** 2026-03-31

**See also:** [ADR-005: LAN Discovery via Bonjour](adr-005-bonjour-discovery.md)

---

## Problem

famstack has no visual interface. Everything goes through the CLI, which is
fine for the person who set it up — but not for the rest of the household.
Your partner can't check if photo backups are working. Your kid can't see
why Paperless is down. You can't glance at server health without opening a
terminal.

Three related pain points:

1. **No glanceable status.** There's no way to see "is everything healthy"
   without running `stack status`. An old iPad on the kitchen counter
   should be able to show this.

2. **No quick actions without SSH.** Starting a stopped stacklet or
   checking logs requires terminal access. A phone in your pocket should
   be enough for "restart docs, it's stuck again."

3. **No document capture flow.** Paperless is great once documents arrive,
   but getting them there means email forwarding or manual upload. A phone
   with a camera is the obvious capture device — scan, crop, send to
   Paperless, done.

---

## Proposal

Three components, each independent, sharing a common API layer:

```
┌─────────────────────────────────────────────────────────┐
│  famstack server (Mac Mini / Mac Studio)                │
│                                                         │
│  ┌─────────────┐  ┌──────────┐  ┌──────────┐           │
│  │ photos      │  │ docs     │  │ messages  │  ...      │
│  └─────────────┘  └──────────┘  └──────────┘           │
│                                                         │
│  ┌─────────────────────────────────────────┐            │
│  │ dashboard stacklet (port 42000)         │            │
│  │ FastAPI + Jinja2 + htmx + Alpine.js     │            │
│  │                                         │            │
│  │  /            → status wall (HTML)      │            │
│  │  /api/*       → JSON API                │            │
│  │  /api/ws      → SSE health stream       │            │
│  └─────────────────────────────────────────┘            │
│                                                         │
│  ┌─────────────────────────┐                            │
│  │ mDNS advertiser         │                            │
│  │ _famstack._tcp.local.   │                            │
│  └─────────────────────────┘                            │
└─────────────────┬────────────────┬──────────────────────┘
                  │                │
        LAN / .local              │
                  │                │
   ┌──────────────┴───┐    ┌──────┴──────────────┐
   │ Dashboard (web)   │    │ Companion (native)   │
   │ iPad on wall      │    │ macOS tray + mobile  │
   │ phone browser     │    │ SwiftUI              │
   │ any device        │    │                      │
   └──────────────────┘    └─────────────────────┘
```

### Component 1: Dashboard Stacklet

A web-based status wall optimized for old tablets and phones on the LAN.
Runs as a regular famstack stacklet — `stack up dashboard`. Serves HTML
that auto-refreshes, shows colored tiles per stacklet, and provides quick
actions behind a PIN.

### Component 2: macOS Companion (tray app)

A native SwiftUI menu bar app that shows server health at a glance and
provides start/stop/restart without opening a terminal. Lives in the
macOS menu bar. Discovers the server via Bonjour.

### Component 3: Mobile Companion (iOS, eventually Android)

A native app for phones and tablets. Two modes: dashboard (status +
actions) and scanner (VisionKit document capture → Paperless). Discovers
the server via Bonjour. Shares the SwiftUI codebase with the macOS tray
app where possible.

---

## Component 1: Dashboard Stacklet

### Why a stacklet

The dashboard eats its own dog food. It's defined by a `stacklet.toml`,
discovered by the CLI, started with `stack up dashboard`, health-checked
like everything else. No special infrastructure. If the dashboard is
down, `stack status` still works — the CLI is never displaced, only
complemented.

### Architecture

```
stacklets/dashboard/
├── stacklet.toml
├── docker-compose.yml          # Single container
├── app/
│   ├── server.py               # FastAPI, <300 lines — thin router
│   ├── routes/
│   │   ├── pages.py            # Server-rendered HTML (Jinja2)
│   │   ├── stacklets.py        # JSON: list, up, down, restart
│   │   ├── health.py           # JSON: aggregate health, SSE stream
│   │   ├── users.py            # JSON: user list (read-only for now)
│   │   └── stats.py            # JSON: per-stacklet metrics
│   ├── auth.py                 # PIN-based gate for actions
│   ├── static/
│   │   ├── alpine.min.js       # ~15 KB, vendored
│   │   ├── htmx.min.js         # ~14 KB, vendored
│   │   ├── lucide.min.js       # Icon set, vendored
│   │   └── style.css           # Hand-written, no build step
│   └── templates/
│       ├── base.html           # Shell: dark mode, auto-refresh meta
│       ├── home.html           # Status wall — the default view
│       ├── stacklet.html       # Per-stacklet detail + logs
│       └── admin.html          # Actions (behind PIN)
└── hooks/
    └── on_install.py           # Generate dashboard API key
```

### Manifest

```toml
id          = "dashboard"
name        = "Dashboard"
description = "Family server status wall"
version     = "0.1.0"
category    = "infrastructure"
port        = 42000
build       = true
requires    = ["core"]

[env]
generate = ["API_KEY"]

[env.defaults]
FAMSTACK_SOCKET = "/var/run/famstack/stack.sock"

[health]
url = "http://localhost:42000/api/health"
```

### The API layer

The dashboard container talks to the host's `stack` CLI. Two options,
in order of preference:

**Option A: Unix socket.** A tiny FastAPI server on the host listens on
`/var/run/famstack/stack.sock`, bind-mounted into the container. The
dashboard calls it. The socket server wraps `lib/stack` calls in JSON.
Clean separation, no Docker-in-Docker.

**Option B: Host CLI exec.** The container bind-mounts the `stack` binary
and Docker socket. Calls `stack list --json`, `stack up photos`, etc. as
subprocess. Simpler to implement, messier boundary.

Option A is the right answer. The socket server becomes the shared API
that both the dashboard and companion apps consume. It's the same pattern
as Docker's own API — a Unix socket on the host, exposed over the network
when needed.

### The API surface

```
GET  /api/status                → system overview (what `stack status` returns)
GET  /api/stacklets             → list with state, health, ports
GET  /api/stacklets/:id         → detail: config, health, stats
GET  /api/stacklets/:id/logs    → last N log lines
GET  /api/stacklets/:id/stats   → service-specific metrics (photo count, etc.)
POST /api/stacklets/:id/up      → start (requires PIN)
POST /api/stacklets/:id/down    → stop (requires PIN)
POST /api/stacklets/:id/restart → restart (requires PIN)
GET  /api/health/stream         → SSE: health changes as they happen
GET  /api/health                → dashboard's own health check
```

The stats endpoint is the most interesting. Each stacklet can declare
what metrics it exposes:

```toml
# stacklets/photos/stacklet.toml
[dashboard]
icon = "camera"
color = "blue"
stats_url = "http://localhost:42010/api/server-info"
stats_map = { photos = "$.photos", videos = "$.videos", usage = "$.usage" }
label = "{photos} photos · {usage}"
```

The dashboard doesn't know Immich's API. It reads the manifest, fetches
the URL, extracts values via JSONPath, formats the label. New stacklets
get stats on the dashboard by adding a `[dashboard]` section — no
dashboard code changes needed.

### Design for old tablets

**Not a SPA.** Server-rendered HTML with progressive enhancement. The
baseline works with JavaScript disabled (auto-refresh via `<meta>`
tag). With JS enabled, htmx handles partial page updates and Alpine.js
adds interactivity.

**Constraints:**
- No build step. No webpack, no Node, no npm. Raw CSS, vendored JS.
- Touch targets ≥ 48px. Old tablets, fat fingers.
- Dark mode default. Ambient display shouldn't light up a room.
- Total page weight < 200 KB. An iPad Air 2 (2014) should feel snappy.
- Works on iOS Safari 15+, Chrome 90+, Firefox 90+.
- No WebSocket dependency. SSE for real-time, polling as fallback.

**Auto-dim.** After 5 minutes of no interaction, dim the display and
cycle through a slow rotation of stacklet summaries. Prevents screen
burn on OLED tablets. Tap to wake.

### PIN-based access

The dashboard is read-only by default. Anyone on the LAN can see status.
Mutating actions (start, stop, restart) require a 4-6 digit PIN set
during `stack up dashboard`. The PIN is stored in secrets.toml.

No full auth system. No login page. No sessions to manage. The
threat model is "family LAN" — the PIN prevents accidental taps,
not determined attackers. Full RBAC waits for the identity stacklet
(ADR-001).

---

## Component 2: macOS Companion (Tray App)

### Why native, why Swift

A menu bar app needs to feel instant. Web wrappers (Electron, Tauri)
add overhead and don't integrate with macOS conventions. PyObjC works
but you're fighting two abstractions. SwiftUI's `MenuBarExtra` (macOS
13+) is purpose-built for this — a working tray app is ~40 lines.

The investment is small: learn enough Swift to build a single-purpose
app. The skills transfer directly to the mobile companion (Component 3),
because SwiftUI runs on macOS, iOS, and iPadOS from the same codebase.

### What it does

```
┌──────────────────────────────┐
│ ● famstack         ▶ 3/4 up │   ← menu bar icon + summary
├──────────────────────────────┤
│ ● Photos      42010  ▶ Stop │
│ ● Docs        42020  ▶ Stop │
│ ● Messages    42030  ▶ Stop │
│ ○ AI          —      ▶ Start│
├──────────────────────────────┤
│ Disk: 234 GB / 1 TB         │
│ Uptime: 14 days              │
├──────────────────────────────┤
│ Open Dashboard...            │
│ Settings...                  │
│ Quit                         │
└──────────────────────────────┘
```

- **Green/red dots** per stacklet. Glanceable.
- **One-click start/stop.** Calls the API, shows spinner, updates dot.
- **Open in browser.** Click a stacklet name → opens its web UI.
- **Notifications.** Optional: alert when a stacklet goes unhealthy.
- **Auto-discovery.** Finds the server via Bonjour (`_famstack._tcp.local.`).
  If the tray app runs on the server itself, connects to localhost.

### What it doesn't do

- No logs viewer. Open the dashboard for that.
- No configuration editing. Use the CLI or dashboard.
- No document scanning. That's the mobile companion's job.

The tray app is a remote control, not a cockpit.

### Tech details

| Aspect | Choice |
|---|---|
| Language | Swift 6 / SwiftUI |
| Min macOS | 14 (Sonoma) — `MenuBarExtra` stable |
| Discovery | `NWBrowser` (Network framework, Bonjour) |
| API client | `URLSession` → dashboard API |
| Binary size | ~3–5 MB |
| Distribution | DMG on GitHub releases, Homebrew cask, or Mac App Store |
| Signing | Developer ID + notarization (no App Store), or App Store review |
| Dependencies | Zero. Apple frameworks only. |

### Discovery and pairing

See [ADR-005: LAN Discovery via Bonjour](adr-005-bonjour-discovery.md) for
the full discovery protocol. Summary:

1. Server advertises `_famstack._tcp.local.` with a persistent server UUID
2. `NWBrowser` finds the service, connects automatically
3. Companion pairs to the UUID (survives IP/hostname changes)
4. Fallback: QR code scan or manual address entry

Subsequent launches: reconnect to stored UUID, re-resolve via Bonjour.

---

## Component 3: Mobile Companion

### The case for native

A PWA handles the dashboard use case adequately — but not document
scanning. iOS PWAs have persistent camera permission issues in
standalone mode, no access to VisionKit, and limited background
capabilities. For "scan a receipt and send it to Paperless," native
is the only path to a good experience.

The pragmatic answer: **build the mobile app in SwiftUI (iOS first),
evaluate cross-platform later.** Here's why:

- The macOS tray app is already SwiftUI. Shared networking, API client,
  and data models transfer directly.
- VisionKit (document scanning) is iOS-only and trivial to integrate
  in Swift (~20 lines). No cross-platform framework gives you this.
- famstack runs on macOS. The user already has an Apple developer
  account (or is close to getting one). The ecosystem alignment is free.
- Distribution via TestFlight covers family use (up to 10,000 testers,
  90-day builds). No App Store review needed for personal use.
- Android can come later. When it does, the API is stable and documented.
  A Kotlin or Flutter Android app can be built against the same endpoints.

### What it does

**Two modes, one app:**

**Dashboard mode** (what you see on the home screen):
- Same status wall as the web dashboard, but native and faster
- Pull-to-refresh health status
- Tap a stacklet → detail view with health, stats, quick actions
- Start/stop/restart behind Face ID or PIN
- Push notifications when a stacklet goes unhealthy (via APNs, routed
  through the famstack server)

**Scanner mode** (the killer feature):
- Tap the scan button → VisionKit document camera opens
- Automatic edge detection, perspective correction, multi-page
- Preview → confirm → upload to Paperless via the dashboard API
- Optional: on-device OCR preview (VNRecognizeTextRequest) before upload
- Tag suggestions based on document content (calls the AI stacklet)

### Shared code with macOS companion

Structured as Swift packages (inspired by PicoMLX's composable ecosystem)
so each layer is independently testable and reusable:

```
famstack-apple/
├── Packages/
│   ├── FamstackAPI/                   # Swift package
│   │   ├── FamstackClient.swift       # URLSession-based API client
│   │   └── Models.swift               # Stacklet, Health, User structs
│   ├── FamstackDiscovery/             # Swift package
│   │   ├── BonjourBrowser.swift       # NWBrowser wrapper
│   │   └── ServerIdentifier.swift     # Persistent UUID pairing
│   └── FamstackUI/                    # Swift package
│       ├── StackletRow.swift          # Reusable status row
│       ├── HealthDot.swift            # Green/yellow/red indicator
│       └── StatsLabel.swift           # "12,431 photos" formatter
│
├── macOS/
│   ├── MenuBarApp.swift               # MenuBarExtra scene
│   └── TrayMenu.swift                 # The dropdown menu
│
├── iOS/
│   ├── DashboardView.swift            # Status wall (tablet + phone)
│   ├── StackletDetailView.swift       # Per-stacklet detail
│   ├── ScannerView.swift              # VisionKit integration
│   └── SettingsView.swift             # Server address, PIN, notifications
│
├── Shared/
│   ├── Keychain.swift                 # PIN + API key storage
│   └── Notifications.swift            # Local + push notification helpers
│
└── famstack-apple.xcodeproj           # Single Xcode project, two targets
```

The Swift packages (`FamstackAPI`, `FamstackDiscovery`, `FamstackUI`) are
independent — they could be consumed by third-party apps or community
projects without pulling in the full Xcode project.

Estimated shared code: ~60%. The API client, models, discovery, and
many UI components are identical. Platform-specific code is mostly
layout (menu bar vs full screen) and scanner (iOS only).

### Distribution

| Method | Effort | Reach |
|---|---|---|
| TestFlight | Low — upload every 90 days | Family (up to 10k testers) |
| Ad Hoc | Medium — collect UDIDs | Family (up to 100 devices) |
| App Store | High — Apple review | Anyone |
| macOS DMG | Trivial — GitHub releases | Anyone with a Mac |

Start with TestFlight for iOS and DMG for macOS. If famstack grows
a community, App Store can come later.

### Android strategy

Not now, but the door is open. The dashboard API is the contract — any
client that speaks JSON over HTTP works. When Android demand appears,
the options are:

- **Kotlin + Jetpack Compose.** Native, best scanning (ML Kit).
  Recommended if someone in the community knows Android.
- **Flutter.** Cross-platform, but by then the iOS app exists in Swift
  and rewriting it in Dart is waste. Better as a standalone Android app.
- **PWA.** The web dashboard already works on Android Chrome. For
  scanning, use `<input type="file" capture>` and do perspective
  correction on the server. Good enough for many users.

---

## Server-Side Foundation: The famstack API

All three components share a common API. This is the real deliverable —
the clients are relatively simple once the API exists.

### Implementation

A lightweight HTTP/JSON server that wraps `lib/stack` functions. Runs on
the host (not in a container) as a long-lived process, managed by launchd.

```python
# api/server.py — the entire server is ~200 lines

from fastapi import FastAPI
from lib.stack.stack import Stack

app = FastAPI()
stack = Stack(root=REPO_ROOT)

@app.get("/api/stacklets")
def list_stacklets():
    return stack.list_all()

@app.post("/api/stacklets/{sid}/up")
def start(sid: str):
    stack.up(sid)
    return {"ok": True}

# ... health, stats, logs, SSE stream
```

### mDNS / Bonjour discovery

The API server advertises itself on the LAN via Bonjour so companion apps
can find it without manual IP entry. The full protocol is specified in
[ADR-005: LAN Discovery via Bonjour](adr-005-bonjour-discovery.md).

Key properties broadcast in the TXT record:

```
_famstack._tcp.local.
  uuid     = "a1b2c3d4-..."    # persistent, survives IP changes
  version  = "0.1.0"
  name     = "Arthur's famstack"
  api_port = 42000
```

Native apps (macOS, iOS) discover via `NWBrowser`. Web clients use
`.local` hostname resolution (works on Apple devices, inconsistent on
Android — QR code fallback handles this).

### Authentication

Three tiers, matching the threat model:

| Tier | Access | Auth |
|---|---|---|
| Read | Status, health, stats | None (LAN-only) |
| Action | Start, stop, restart | PIN (4-6 digits) |
| Admin | Destroy, user management | Full API key |

The API key is generated during `stack up dashboard` and stored in
secrets.toml. The PIN is a convenience layer — it hashes to the API key
with a reduced permission set.

---

## Discovery and Onboarding

How does a new device find the famstack server? Full protocol in
[ADR-005](adr-005-bonjour-discovery.md). Summary:

### Flow

```
1. Install app (or open browser)
2. App searches for _famstack._tcp.local. via Bonjour (NWBrowser)
3. Found? → Read server UUID + address from TXT record
   Not found? → Show "Scan QR code" or "Enter address"
4. App stores the server UUID in Keychain (persistent pairing)
5. Dashboard shows a QR code at /onboard containing:
   { "uuid": "a1b2c3d4-...", "host": "mac-arthur.local",
     "port": 42000, "key": "..." }
6. Phone scans QR → paired, UUID + API key stored in Keychain
```

The UUID is the stable identity. If the server's IP or hostname changes
(DHCP lease renewal, network switch), the companion app re-discovers the
same UUID via Bonjour and reconnects — no manual reconfiguration.

Apple devices (macOS, iOS, iPadOS) get zero-config discovery. Android
devices and browsers use the QR code, which transfers address + API key
in one step.

---

## What OMLX Got Right (and What We Adapt)

This RFC was informed by studying the OMLX project (a local LLM server
for Apple Silicon with a built-in admin dashboard). Here's what we take
and what we leave:

### Take

- **Vendored static assets.** OMLX bundles Alpine.js, Tailwind, fonts —
  no CDN, works offline. We do the same with htmx + Alpine.js + a
  hand-written CSS file (no Tailwind build step needed).

- **Settings editable from the UI.** OMLX lets you change config from
  the dashboard, persisted to disk. We expose `stack.toml` editing
  for safe fields (timezone, update schedule, AI model) — not all fields.

- **Per-model stats in the dashboard.** OMLX shows memory, request
  count, loaded state per model. Our `[dashboard]` manifest section
  generalizes this — any stacklet can declare its stats, the dashboard
  renders them generically.

- **Homebrew-managed lifecycle.** OMLX uses `brew services` for
  start/stop/restart. Our macOS tray app can ship via Homebrew cask.

- **Native macOS integration (PyObjC menu bar).** OMLX proves a tray
  app is valuable. We go further with SwiftUI for a cleaner result and
  direct transfer to iOS.

### Leave

- **3,600-line server.py.** OMLX's entire API is one file. We split
  into focused route modules from day one.

- **Monkey-patching for extensibility.** OMLX patches `sys.modules`
  at runtime. We use the manifest system — stacklets declare their
  dashboard metadata in `stacklet.toml`, no code patching.

- **Feature creep.** OMLX's dashboard grew into a model downloader,
  quantizer, benchmarker, and chat client. Our dashboard shows status,
  provides quick actions, and stops. The CLI and dedicated web UIs
  (Immich, Paperless, Element) handle everything else.

- **Single API key auth.** OMLX has one key + sub-keys. We use
  PIN-based tiers because the audience includes family members who
  shouldn't need to manage API keys.

---

## What PicoMLX Got Right (and What We Adapt)

PicoMLX is a Swift-native ecosystem for local AI on Apple Silicon. Where
OMLX informed our dashboard design, PicoMLX informs the companion app
architecture.

### Take

- **Composable Swift packages.** PicoMLX ships ~15 independent Swift
  packages (API client, Bonjour discovery, Markdown renderer, vector DB)
  that compose into apps. We structure the companion the same way —
  `FamstackAPI`, `FamstackDiscovery`, `FamstackUI` as standalone packages.
  Each is testable and reusable independently.

- **BonjourPico's discovery pattern.** Their `_pico._tcp` service
  broadcasts a persistent server UUID in the TXT record, surviving IP
  and hostname changes. We adopt this directly for `_famstack._tcp` —
  see [ADR-005](adr-005-bonjour-discovery.md).

- **Mac App Store for menu bar apps.** Pico AI Homelab ships free on the
  App Store. This proves a server-management tray app passes Apple review.
  Easier distribution than DMG + notarization for non-technical users.

- **iMCP's app-plus-CLI pattern.** Their MCP bridge uses a sandboxed
  macOS app for UI/permissions and a bundled CLI for protocol work,
  connected via Bonjour on localhost. Useful if the famstack tray app
  ever needs to bridge macOS sandbox boundaries.

### Leave

- **The inference stack.** PicoMLX's core is LLM serving. We don't need
  MLXKit, PicoVector, or the RAG pipeline — famstack manages services,
  not models. The `ai` stacklet handles inference separately.

- **Multiple servers on different ports.** Pico runs one LLM server per
  port. Our multi-stacklet model is already richer — one API server
  manages all stacklets through a single endpoint.

---

## Phasing

### Phase 0: API server (foundation)

- HTTP/JSON wrapper around `lib/stack`
- Unix socket on host, optionally TCP on LAN
- mDNS advertisement via zeroconf
- PIN + API key auth
- `stack api` command to start/manage
- **Enables:** everything below

### Phase 1: Dashboard stacklet

- Server-rendered HTML status wall
- Stacklet tiles with health dots and stats
- Log viewer (last 100 lines, auto-scroll)
- Quick actions behind PIN
- QR code onboarding for mobile
- `[dashboard]` section in stacklet manifests
- **Value:** old tablet on the wall shows family server health

### Phase 2: macOS tray app

- SwiftUI `MenuBarExtra` with stacklet status
- Bonjour auto-discovery
- Start/stop/restart from the menu
- Click-to-open stacklet web UIs
- Notifications on health changes
- **Value:** server admin sees health without leaving current work

### Phase 3: iOS companion

- Dashboard view (shared SwiftUI components from Phase 2)
- VisionKit document scanner → Paperless upload
- Push notifications for health alerts
- Face ID / PIN for actions
- TestFlight distribution
- **Value:** scan documents from your phone, monitor server from anywhere on LAN

### Phase 4 (future): Expand

- Android companion (Kotlin or PWA, depending on demand)
- Dashboard widgets: storage trends, backup timeline, recent activity
- Stacklet-specific dashboard plugins (photo carousel, document inbox)
- Integration with agent notifications (agents.md Phase 1)

---

## Trade-offs and Open Questions

**Dashboard in a container vs on the host.** A container is cleaner
(consistent with other stacklets) but needs a way to call the host's
`stack` CLI. The Unix socket approach solves this — the API server runs
on the host, the dashboard container connects via bind-mounted socket.
The alternative (Docker-in-Docker or bind-mounting the Docker socket)
is messy and a security surface we don't need.

**SwiftUI-only vs cross-platform mobile.** Going SwiftUI-first means no
Android for a while. But famstack's server runs on macOS, the primary
audience has Apple devices, and VisionKit (the killer scanning feature)
is iOS-only. Flutter or React Native would give Android coverage at the
cost of worse scanning, a new language to learn, and a separate codebase
from the macOS tray app. The pragmatic choice is: native Apple first,
API-stable so Android can follow.

**How much config editing in the dashboard?** Starting conservative:
read-only for most config, editable for timezone, update schedule, and
AI model selection. Full `stack.toml` editing is risky (one bad domain
change could lock out all services). The CLI remains the tool for
structural changes.

**Scanner as a separate app?** A focused "famstack Scanner" app would be
simpler, smaller, and could ship faster. But discovery, auth, and API
client code would be duplicated. A single app with two tabs (Dashboard +
Scanner) is better — shared foundation, less to install, one pairing
step. If the app grows too large, the scanner can split off later.

**Push notifications without a relay.** APNs requires a server-side
component to send pushes. Options: (a) the famstack server runs a
small APNs sender (requires Apple Developer certificate on the server),
(b) local notifications triggered by background refresh (less reliable
on iOS), (c) Matrix notifications via Element (already works, no new
infrastructure). Start with (c) — Matrix is the notification backbone
per agents.md. Add (a) only if Matrix proves insufficient.

**Apple Developer Program cost.** $99/year is required for any iOS
distribution (TestFlight, Ad Hoc, or App Store). For a family project
this is a real cost. The macOS tray app does NOT require this — it
can be distributed as a DMG with notarization. The dashboard (web)
requires nothing. Only the iOS companion triggers this cost.

---

## Success Criteria

- A family member who has never used a terminal can see whether photo
  backups are working by looking at a tablet on the wall.
- A scanned receipt goes from phone camera to Paperless in under 10
  seconds without leaving the companion app.
- The server admin can restart a stuck stacklet from the macOS menu bar
  in two clicks.
- Adding `[dashboard]` metadata to a new stacklet's manifest is all
  that's needed to appear on the dashboard — no dashboard code changes.
