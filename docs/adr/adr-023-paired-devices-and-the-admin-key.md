# ADR-023: Paired Devices and the Admin Key

## Status
Accepted, 2026-11-12.

## Context

Every device on the home network reached famstack's pages on port 42000: the
dashboard, the family memory, every `/go` link and the setup page. The tools
server decided "this request comes from the Mac" by the request's Host header.
Any device can set that header. The setup page then showed every person's
password. The dashboard's changes (start a stacklet, set the family) answered
every device. The host API answered every container without a token.

A family's home network also carries guests' phones. The Mac that runs famstack
often has no screen. A sign-in with a password on famstack's own pages does not
fit a family. Nobody wants to type a password on each phone, and a shared
password reaches everyone who heard it.

This ADR answers four questions:

1. How does famstack tell the Mac from every other device?
2. How does famstack know which person looks at a page, without a password?
3. Where may a person's password show?
4. What is trusted, and what are the known limits?

The decisions keep the numbers of the design they come from. Numbers that are
missing were decided elsewhere or were dropped.

## Decision

### Two addresses

famstack answers on two addresses:

| Address | Who reaches it | What it does |
|---|---|---|
| The Mac's address, `127.0.0.1:42002` | a browser with the admin key (D23), on the Mac or on a laptop through an ssh tunnel | the setup, every change to the server, every person's password, the list of devices |
| The home address, port 42000 (or the family's domain) | every device on the home network | the family's pages for paired devices, without changes |

**D6.** The setup, starting and stopping services, the family and every person's
password are only on the Mac's address. `access.here(request)` is true only for a
request that arrived through the Mac's listener and carries the admin key.

A route that changes the server declares `access.mac_only`. On the home address
such a route answers 403: JSON to a post, and the page "This happens on the Mac
that runs famstack" to a browser. Nothing reads a header to decide what only the
Mac may do.

### Pairing a device

A paired device is a browser that famstack knows. It belongs to a person or to
the household (a shared device, for example a screen in the living room).

**D1.** A phone is paired once, by a QR code from the Mac. famstack's own pages
ask for no password and no PIN.

The Mac's address makes a pairing code for one person. An admin's paired device
can make one too (D20). The code has 128 random bits and
works once, for 10 minutes. Only the tools server's memory keeps it. Its QR code
holds `<LINK_BASE_URL>/app?pair=<code>`. The phone that opens it gets a device
key for that person.

The device key is a random key of 256 bits in the cookie `stack-device`. The
cookie is HttpOnly and SameSite=Lax. Over HTTPS it is also Secure. Each visit
renews it for 400 days. The Mac keeps only the key's SHA-256 hash.

On the home
address, a browser without a valid key gets "This browser does not know you
yet" on every path. Only the health check and the pages' styles and fonts
answer without a key. While the family is empty, a browser gets "famstack is
being set up".

**D2.** Every other browser joins by a short code (8 letters and digits,
"K7MP-4XQD"), the device flow of RFC 8628. Its page shows the code and the same
code as a QR code.

The code only names the request. The waiting browser keeps a secret of its own in
the cookie `stack-join`. Only that browser gets a device key when the request is
allowed. The request lives 10 minutes, in memory only.

A
person's paired device allows a request for that person. An admin's paired
device or the Mac allows it for anyone.

**D3.** The page allows, never the tap on a link. The approver sees the browser,
the time and the code before "Allow". Only an admin or the Mac ends a request
that someone else may be waiting on.

**D4.** An admin can add a shared device, with a name ("Living room"). A shared
device reads what the family reads and shows no person's password.

**D5.** A device stays paired until someone removes it, on the Mac or on another
device of the same person. Its key stops counting at once. A person removed from
the family loses their devices.

**D9.** The list of devices has one writer: `stack devices` on the host. It
writes `.stack/devices/devices.toml`. For each device the file holds the key's
hash, the person and the device's name. It also holds when the device was paired
and last seen.

The tools server
reads the list through a read-only mount of `.stack/devices`. It changes the list
only through the host API. `devices.who(request)` is the one reader. It answers
a person, the household, the Mac, or nobody.

**D10.** The tools server sends only the fields of a waiting browser to the host
API's `join_notice`. The fields are the browser and the system from a fixed list,
the code and the time. `stack devices notice` writes the text of the chat message. No text from a
browser reaches the chat.

**D11.** The family wiki keeps its own port (42070, or its own name in domain
mode). Core stands at its door. A listener of core asks `devices.who` and
forwards a paired device's request to the wiki's container, which publishes no
port.

**D12.** The Mac's own browser pairs itself for the home address with "Use this
browser for the family's pages", as a person or as Shared.

**D15.** A card's link is `/go/card/<card id>` and its page is
`/card/<card id>`. The path behind a link can change without breaking a message,
because the chat holds `/go` links only.

**D16.** A browser sends a cookie only to the host name that set it. Every link
famstack makes therefore uses the host of `LINK_BASE_URL`, because a paired
browser's key applies only there. A browser that came by another name of the Mac
is told which address knows it.

**D20.** Every admin hears of every new device. One chat message (`m.text`) in
the Server room mentions each admin, because a mention rings a phone. famstack
sends at most one such message in 10 minutes. It names the browser that started
it, and only that browser's page says that the admins heard of it.

A device that a person's own device allowed is announced too. An admin's paired device can
show the "Add a phone" QR code for another person, as the Mac does.

### A Mac without a screen

**D21.** `stack devices pair <person>` prints the pairing QR code and its address
in a terminal, also over ssh. It waits until the phone opened the code. The
command line keeps no state of its own, because the command asks core's tools
server on the Mac's address for the code. `stack install --family` ends with this
QR code for the first admin.

**D22.** The admin's work stays on the Mac's address. A laptop reaches it
through an ssh tunnel (`ssh -L 42002:127.0.0.1:42002 <mac>`), then
`127.0.0.1:42002` in the laptop's browser. Screen sharing also works.

**D24.** After an update, the family may have no paired device. Then the restart
that applies the update ends with the first admin's QR code (in a terminal only)
and the command for the others. It also names how to open the Mac's own pages.
The Server room hears once, without a code or a link, where to pair.

### The admin key

**D23.** The Mac's listener alone does not prove the Mac, because the containers
of the stack reach `127.0.0.1:42002` through `host.docker.internal`. The Mac's
address therefore also needs the admin key.

- `stack` writes a random admin key into `.stack/admin-key` (mode 0600). Its
  SHA-256 hash goes into `.stack/devices/admin-key.sha256`.
- Only core's bot runner mounts `.stack`. It holds every secret already. The
  tools server mounts only `.stack/devices`. The agent gets its own secrets
  through its environment.
- Three things open the Mac's address once with the key: the menu bar app's
  "Setup…", the installer's last line and `stack devices admin-link`. The key
  sets the cookie `stack-admin`. The cookie is HttpOnly and SameSite=Strict, with
  no Domain and the Path `/`. Each visit renews it for 400 days.
- `admin-link` also prints the key. Without the key, the Mac's address answers
  401 with a page that says how to get it. The page has a field to paste the key.
  After ten wrong keys in a minute, it waits.
- The Mac's address is `http://127.0.0.1:42002` in every link famstack makes. A
  request to it by another name (`localhost:42002`) is redirected to
  `127.0.0.1:42002`. The key's cookie never reaches another service, because
  famstack opens each of them on the Mac at `localhost:<port>`.

### Passwords

**D7.** famstack never changes a stored password, also not at an update. Each new
person gets four random words from EFF's short word list 1, joined by "-". The
list has 1295 words, because "yo-yo" is left out: it holds the joiner. Changing
a password stays in each app.

**D8.** No password goes into the chat. The Server room says that the password
is on the setup page on the Mac.

**D18.** `stack.passwords.stored(person_id)` on the host is the one reader of a
person's password. It reads only that person's key. The tools server mounts no
secret. It asks the host API's `passwords` command for the people a page may
show. It never logs or caches the answer.

A password shows in these places:

| Where | Whose |
|---|---|
| The Mac's address | every person's on the setup's family step, the admin's on the service cards |
| A person's own paired device | that person's, only on the sign-in guide, and once at the end of pairing |
| A shared device | nobody's |
| The chat | nobody's |

A page that holds a password sends `Cache-Control: no-store`.

D13 and D19 (a password change in every service at once, and its record) were
dropped. They are not part of this ADR.

### The host API's callers

**D14.** Each stacklet that calls the host API has a token of its own, generated
like its other secrets (`[env] generate`). Core's token opens the JSON surface.
The agent's token opens only the plaintext surface with its allowlist. A request
without a token, or with an unknown one, runs nothing.

### Checks in the browser

Each listener has an allowlist of host names, against DNS rebinding. Every write
must carry an Origin of the same listener. Every answer carries a Content
Security Policy with `script-src 'self'` and `frame-ancestors 'none'`, and the
headers `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`. A
cap on live streams keeps worker threads free for the `/go` links.

### Link previews in the chat

**D17.** Synapse's link previews refuse the private and local address ranges, on
a new install and on the update. A link to famstack's pages gets no preview,
because Synapse is not a paired device. An empty list of refused ranges is the
installer's and is replaced. A list with entries is the admin's and is kept.

## Consequences

**What famstack trusts:**

| Who | Trusted |
|---|---|
| A browser with the admin key on the Mac's address | everything |
| The containers of the stack, extensions in the bot runner included | what their stacklet does. The bot runner can read the stack's secrets. |
| A paired device | as much as its person. A shared device: as much as the household. |
| Every other device on the home network | nothing but "This browser does not know you yet" |

**Known limits:**

- In port mode the home address is plain HTTP. A device that records the Wi-Fi's
  traffic could copy a device key or a chat password. WPA2 or WPA3 keep strangers
  off the Wi-Fi. Domain mode gives HTTPS (ADR-003).
- An install from an earlier release can have old first passwords: a person's
  first name. Each person keeps it until they change it in each app. famstack
  does not name these people.
- A service that someone opens by hand at `127.0.0.1:<port>` on the Mac receives
  the admin key's cookie.

**Not in this ADR:**

- A person who acts as themselves on a shared device, with a short PIN.
- A change of a password in every service at once.
- Passkeys and single sign-on for the services. They need HTTPS and a domain.

## Considered

- **A sign-in with the chat's password on famstack's pages.** Every phone would
  need the password, and famstack's pages would hold a login for each person.
- **One family PIN.** Anyone who heard it reads everything, and nobody can be
  removed alone.
- **The device's MAC address.** famstack never sees it: the containers see
  Docker's address. Phones change it per network. Any device on the Wi-Fi can copy
  it.
- **Passkeys (WebAuthn).** They do not work in port mode, because they need HTTPS
  and a real domain.
- **The Mac's listener as the only proof.** It holds for browsers, but not for
  containers. Containers reach `127.0.0.1` through `host.docker.internal` (D23).
