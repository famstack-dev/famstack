# Admin Guide

famstack is an opinionated, batteries-included stack built on established open source projects (Immich, Paperless, Matrix, MLX). The glue is the part that matters: everything is wired together and reachable through chat, so your family actually uses it instead of just you.

This guide is for the person who runs the server: hardware, install, the stacklets, day-to-day operations, troubleshooting. One page on purpose. Search it with `Cmd+F`.

Living with famstack rather than running it? The [User Guide](user-guide.md) covers everything your family does in chat: filing documents, saving links, voice memos, asking questions.

If you get stuck, jump into [Discord](https://discord.gg/hfutdmmfBe) or open an issue on [GitHub](https://github.com/famstack-dev/famstack/issues).

[![famstack install walkthrough on YouTube](https://img.youtube.com/vi/2trwwigKyEY/maxresdefault.jpg)](https://www.youtube.com/watch?v=2trwwigKyEY)

Prefer to watch? [Full install walkthrough on YouTube](https://www.youtube.com/watch?v=2trwwigKyEY): 19 minutes, clone to a working document archive with mobile access. The whole [famstack channel](https://www.youtube.com/@famstacker) has the short clips too.

> ⚠️ **Local network only.** famstack runs plain HTTP and opens only for the devices you pair with it (see [Security](#security)). Never install it on a machine reachable from the public internet: no VPS, no forwarded ports, no DMZ. For access from outside the house, use a VPN (Tailscale works well and is free for families).

---

## Quick Start

If you already have [Homebrew](https://brew.sh):

```bash
git clone https://github.com/famstack-dev/famstack.git
cd famstack
./stack
```

The installer checks the dependencies and starts core. Your browser then opens the dashboard. It asks who is at home and adds each stacklet with you.

To use the terminal instead, run one command at a time. Start with Messages, because the bots need it:

```bash
./stack up messages   # Matrix and Element X (family chat)
./stack up photos      # Immich (photo library, mobile backup)
./stack up docs        # Paperless-ngx (document archive with OCR)
./stack up ai          # Local AI (oMLX and Whisper)
./stack up code        # Forgejo (private git server)
./stack up memory       # The memory of your family brain. The curated knowledge.
```

Verify:

```bash
./stack status
./stack list
```

If you do not have Homebrew yet, see [Install](#install) below.

---

## Before you start: stable IP/LAN address

> Do this before you sign anyone in on a phone.

famstack is a server. Every phone, every Immich app, every Element X session points at the Mac's IP on your home network. If your router hands the Mac a different DHCP lease next week, every device stops connecting and you reconfigure the household.

Pick one, in order of preference:

1. **DHCP reservation on the router (best).** Reserve the Mac's current IP to its MAC address. Five minutes, never breaks again.
2. **Manual static IP on the Mac.** System Settings > Network > Details > TCP/IP > Configure IPv4: Manually. Choose an address inside your subnet but outside the router's DHCP range.
3. **Bonjour name as a fallback.** Use `<mac-name>.local` (System Settings > General > Sharing > Local hostname) if your router lets mDNS through. Less reliable than option 1 or 2.

If the IP changes later, every phone has to be repointed. Save yourself the family meeting.

---

## Hardware

famstack runs on Apple Silicon. Whisper and oMLX use Metal GPU acceleration. Intel Macs technically run Docker, but the AI parts will be unusably slow.

| Resource | Minimum | Recommended | Why |
|----------|---------|-------------|-----|
| CPU | Apple Silicon (M1) | M2 or newer | Metal GPU is mandatory for the AI stacklet. |
| RAM | 16 GB | 32 GB or more | See the RAM note below. |
| Disk | 60 GB free | 256 GB+ free | Software is small. Photo library and AI models are not. |
| Network | Local LAN | Wired ethernet for the host | Stable host link helps phone uploads. |

### What does the memory of the Mac change?

A **memory profile** says what a Mac of one memory size gets. The installer reads the memory of the Mac and writes the chat model of its profile as `[ai] default` in `stack.toml`:

| Memory | Profile | Chat model | Speech at install | oMLX memory |
|--------|---------|------------|-------------------|-------------|
| Less than 24 GB | `small` | Qwen3.5-9B 4-bit (5.8 GB) | off | 9 GB ceiling, model unloaded after 10 idle minutes |
| 24 to 31 GB | `medium` | Qwen3.5-9B 4-bit | on | oMLX's own settings |
| 32 GB or more | `large` | Qwen3.6-35B-A3B 4-bit | on | oMLX's own settings |

Qwen3.6-35B-A3B gives the best quality, also for German. Qwen3.5-9B is fine for English. Its German is weaker, especially when it classifies long, formal documents. The smaller profiles stay on Qwen3.5, because Qwen3.6 has no small variant.

#### What does a 16 GB Mac get?

A Mac with less than 24 GB gets the `small` profile. The profile changes four things:

1. **Speech is off.** The installer writes `speech = false` under `[ai]` in `stack.toml`. Then `./stack up ai` installs and starts no speech-to-text server, because that server keeps about 1.6 GB loaded.
2. **oMLX has a memory ceiling of 9 GB and reads long prompts in chunks.** Each `./stack up ai` gives these settings to the oMLX that the stack installs. The ceiling keeps the chat model under the GPU limit of a 16 GB Mac. A memory guard that you set in oMLX's settings stays.
3. **oMLX unloads the chat model after 10 idle minutes.** The memory then goes back to Docker and macOS. The next request loads the model again. An idle timeout that you set in oMLX's settings stays.

The oMLX app and an AI server on another computer keep their own settings, because famstack changes nothing that it did not install. When your own AI server is an oMLX, the setup page shows these settings as a recommendation.
4. **The agent (beta) asks before its first start.** The agent sends the AI long prompts. On a 16 GB test Mac, a prompt of 6,000 tokens took 4 minutes. Other services moved to swap meanwhile. The default answer is no, and a no starts nothing.

#### What does speech off cost?

With speech off, the stack keeps voice messages but does not transcribe them. The stack also gives no spoken answers.

To turn speech on, set `speech` under `[ai]` in `stack.toml`:

```toml
[ai]
speech = true
```

Then run:

```bash
./stack up ai
```

An instance that was installed before the memory profiles has no `speech` key and keeps speech on. On a small Mac, `./stack doctor` then warns that speech keeps about 1.6 GB loaded. The warning names the line that turns speech off.

#### How do I treat the Mac as a different size?

Set the profile under `[core]` in `stack.toml`:

```toml
[core]
profile = "small"
```

The values are `small`, `medium` and `large`. A value that you set in `stack.toml`, such as `[ai] default`, wins over the profile. The details are in the [stack reference](stack-reference.md#memory-profiles).

For a household that uses German or another non-English language, 32 GB is the sweet spot. Documents then get better tags, transcripts and summaries. 16 GB works, but you will feel the difference. You can always switch models later by editing `[ai] default` in `stack.toml` and running `./stack setup ai`.

The `chatai` stacklet (Open WebUI) is also memory-hungry, and it is deprecated. On 16 GB Macs, skip it. Local AI still works for document classification and voice transcription without a chat UI.

### A note on disk

What actually fills the disk:

- Docker images: roughly 5 to 8 GB across all stacklets.
- Whisper model: 1.5 GB (large-v3-turbo).
- LLM model: 2.5 GB (9B 4-bit) up to 25 GB (35B 8-bit).
- Photo library: depends on you. Plan for what you would back up to iCloud, plus about 20 percent overhead for Immich thumbnails.
- Documents: small. Even 10 years of household paperwork rarely exceeds a few GB after OCR.

`data_dir` defaults to `~/famstack-data` but accepts any path. If your internal SSD is tight, point it at an external drive in `stack.toml` before running `./stack up photos` for the first time.

### Keep the Mac awake

A sleeping Mac runs nothing: no bot answers, no photo uploads, no scheduled command. Set it never to sleep on its own, and keep it on power:

```bash
sudo pmset -a sleep 0
```

The screen may still turn off. `./stack doctor` warns while the Mac is set to sleep, also when something like `caffeinate` holds it awake for now. The stack runs as your user, so the Mac also has to be logged in after a restart: switch on automatic login in System Settings > Users & Groups (macOS offers it only while FileVault is off).

---

## Install

### 1. Install Homebrew

One dependency: **[Homebrew](https://brew.sh)**.

Installing Homebrew pulls in the Xcode Command Line Tools (which gives you `git` and `python3`), and the famstack installer uses `brew` to install OrbStack and, if you enable the AI stacklet, oMLX plus its build dependencies. So if you have Homebrew, you have everything you need.

If it is not installed:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Follow the post-install instructions to add `brew` to your shell `$PATH`. Open a fresh terminal afterwards.

You do not need to install OrbStack, Docker, oMLX, cmake or anything else by hand. The installer handles them.

### Free ports

famstack uses the 420xx range to avoid conflicts with anything you might already run:

| Port | Service |
|------|---------|
| 42000 | core (the home address: links, the dashboard and the memory's pages, to read) |
| 42002 | core (the Mac's address, on this Mac only) |
| 42010 | photos (Immich) |
| 42020 | docs (Paperless-ngx) |
| 42030 | messages (Element web) |
| 42031 | messages (Synapse, Matrix homeserver) |
| 42040 | code (Forgejo) |
| 42050 | chatai (Open WebUI) |
| 42060 | ai (oMLX) |
| 42061 | ai (LM Studio, optional GGUF backend) |
| 42062 | ai (Whisper) |
| 42070 | core (the family wiki's door: paired devices only) |

If a port is taken, stop the offender or switch to `domain` mode (see [stack-reference.md](stack-reference.md)).

### 2. Clone the repo

Pick a directory, e.g. `~/server/famstack`. Do not put it inside iCloud Drive or Dropbox. The file watchers will fight Docker.

```bash
mkdir -p ~/server && cd ~/server
git clone https://github.com/famstack-dev/famstack.git
cd famstack
```

Optional: pin to the latest tag instead of `main`. `main` is the working branch and is usually fine, but tags are what we test.

```bash
git fetch --tags
git checkout $(git describe --tags --abbrev=0)    # latest release tag
```

The current tags are listed on the [releases page](https://github.com/famstack-dev/famstack/releases).

Sanity check:

```bash
ls -l stack
./stack --help
```

`./stack` is a 4-line bash wrapper that boots the Python CLI from `lib/`. It uses your system `python3`, no virtualenvs, no pip install. That is intentional.

### 3. Run the installer

```bash
./stack
```

What it does, in order:

1. **Asks to start.** It says what it will do and waits for Enter. If you answer `n`, it stops before it writes or starts anything.
2. **Checks Homebrew.** If missing, prints the install one-liner and waits.
3. **Checks Docker.** If missing, offers to install OrbStack via Homebrew. If installed but not running, asks you to start it and waits.
4. **Writes config.** `stack.toml` (central config, gitignored, yours to edit) with the time zone of this Mac and a language proposed from it, `en` or `de`; and `.stack/secrets.toml` with the generated admin password (treat it like a password manager export). Nothing else is asked.
5. **Brings up `core`.** The tools server with the dashboard, the bot runner and the auto-updates.
6. **Installs the `famstack` command.** A small script in Homebrew's `bin` directory that runs this checkout's `./stack`, so `famstack status` works from any directory. If another command called `famstack` is already on your PATH, the installer leaves it alone and the final screen says `./stack` instead.
7. **Opens the dashboard** on this Mac, `http://127.0.0.1:42002/dashboard`, with the [admin key](#what-is-the-admin-key), and prints this link. Port 42002 is the Mac's address: core publishes it on this Mac only. Other devices use the home address, port 42000. Only the Mac's address shows the family's passwords, and only to a browser that holds the admin key.

The rest happens in the browser. The dashboard asks your name, greets you, and asks who else is at home: your family's name, everyone in it (the first two marked as parents; tap a name to change that, to choose words like sister or brother for them, or to say who looks after the server), and your family's own web address if you have one. It names your chat server, which can never change: `@homer:simpsons.com` with the address, `@homer:simpson` without one. Your browser's language becomes the family's language if famstack speaks it. Everyone you add gets an account in each service you add, with their first name, lowercase, as username and four random words as first password (see [Security](#how-are-first-passwords-made-and-changed)). Then the dashboard opens, with your family at the top; add the chat and the rest from there.

The browser writes the family the way `./stack family set` does: `users.toml` and `stack.toml`. Only the Mac's address takes changes: setting the family, starting a service, the setup questions and the AI's downloads. Other devices on the home network see the dashboard and each service's state. When they open the setup, a page tells them to use the Mac.

Total time on a fresh Mac with a decent connection: 5 to 10 minutes.

If something fails partway through, fix the underlying issue (usually Docker not running) and run `./stack` again. The installer is idempotent, and opens the dashboard again.

#### Without a terminal: install from a family file

A script, a provisioning tool or a test installs without questions from a file that describes the family, in TOML or JSON (the format is in [stack-reference.md](stack-reference.md#the-family-as-one-file-stack-family)):

```toml
name   = "Simpson"
domain = "simpsons.com"   # optional: the family's own web address

[[people]]
name  = "Homer Simpson"
words = "he"

[[people]]
name  = "Marge Simpson"
words = "she"
```

```bash
./stack install --family simpsons.toml --yes
```

It checks Homebrew and Docker and stops with a plain message when one is missing, writes `stack.toml` (time zone and language detected), the admin password and the family, starts core, and prints where the dashboard is. A family file with problems is refused before anything is written, with every problem named. Without `--yes` it asks for one confirmation, and refuses to run without a terminal. Running it again changes nothing. Messages is not started: set it up from the dashboard, or with `./stack up messages`.

Writing `stack.toml` and `users.toml` yourself before the first `./stack up` keeps working as before.

### 4. Sign in (browser)

On the Mac that runs famstack, open the address the installer printed: `http://localhost:42030` in port mode. You should see Element.

Element runs in a browser only over HTTPS or on `localhost`. In port mode the Mac's LAN address is plain HTTP, so on any other computer the browser shows "does not support this browser" and Element never finishes loading. Other computers and phones use the Element apps instead (below), or you set up domain mode with HTTPS.

- **Username**: your first name, lowercase.
- **Password**: your first password, on the setup page on the Mac (see [Security](#how-are-first-passwords-made-and-changed)).

To choose a password of your own, change it in Element > Settings > Account.

You will see the Family space with three rooms:

- **Family Room**: where the family talks. Photos, voice memos, life.
- **Memories**: a place to store your memories, voice diaries, stories, funny moments. With the `memory` stacklet it becomes the family diary (see [Memory](#memory-memory-optional)).
- **Server Room**: where the server talks back. Status, alerts, install confirmations. Only admins are in it.

`./stack up docs` adds a fourth, **Documents Room** (see [Documents](#documents-docs)).

### 5. Connect your phones

The browser login is enough to verify the install. The point of famstack is that your family uses it from their pockets. Five minutes per device.

#### Element X (chat)

Element X is the modern Matrix client for a Whats-App like chat. Built by the same team that builds Synapse. Install it from the App Store or Play Store. Do not install plain "Element" (older client, still works but no longer recommended).

Sign-in flow:

1. Open Element X. Tap **Sign in**.
2. When asked for the homeserver, tap **Edit** or **Other** and enter:

   ```
   http://<mac-lan-ip>:42031
   ```

   Type it in full, with `http://` at the front: without it the app tries HTTPS and cannot connect. That is the chat port (`42031`), not the Element web port (`42030`). The apps reach Synapse on this port.

3. Element X warns the connection is not HTTPS. Correct. famstack runs unencrypted on your LAN by default, because trusting your own router is fine and Let's Encrypt does not issue certs for `192.168.x.x`. Confirm and continue. (For HTTPS, set up `domain` mode and Caddy will issue certs. See [stack-reference.md](stack-reference.md).)
4. Username: first name, lowercase. Password: the person's first password, from the sign-in guide on their paired phone (see [Security](#how-are-first-passwords-made-and-changed)).
5. Set up encryption. First device on the account: **Reset identity**. Subsequent devices: **Verify** with the QR-code flow from an already-signed-in session.
6. Allow notifications when prompted. Without this, you do not get pings for new messages.
7. Turn on threads: **Settings > Labs > Threads**, then restart the app. The archivist answers in threads and takes corrections there, and the diary cards sit in the thread under each memory. Without it, Element X shows those replies as a flat list. It is a beta in Element X, and works.

Verify: send a message from the phone. It should appear in the browser session within a second. Reverse it. If both directions work, you are done. If only one direction works, it is almost always a notification permission issue, not a Matrix issue.

Repeat for every family member. Each one logs in with their own first-name account.

#### Element (other computers)

The browser on another computer cannot run Element in port mode (see [Sign in](#4-sign-in-browser)). Download the Element desktop app from [element.io/download](https://element.io/download) instead (the "Desktop" section; there is no Element X for computers):

1. Open Element and click **Sign in**.
2. Next to the homeserver, click **Edit** and enter the server in full, with `http://` at the front:

   ```
   http://<mac-lan-ip>:42031
   ```

3. Sign in with your first name, lowercase, and your password.

#### Immich (photos)

Once you bring up the photos stacklet (next section), install **Immich** on every phone:

1. Open the Immich mobile app.
2. Server URL: `http://<mac-lan-ip>:42010`.
3. Sign in with the family member's first name and password.
4. Enable **Background backup**. Photos sync automatically from then on.

This is the part that replaces iCloud Photos for the family.

#### Phone troubleshooting

- **"Cannot reach server"**: phone is on a different Wi-Fi network than the Mac. Guest Wi-Fi often isolates clients. Switch to the same network.
- **"Server not Matrix"**: `messages` stacklet not healthy. Check `./stack status`, restart with `./stack restart messages`.
- **Notifications stop after a day**: phone killed Element X in the background. iOS: Background App Refresh on for Element X. Android: exempt it from battery optimization.
- **Mac changed IP**: revisit [Before you start](#before-you-start-stable-lan-address). DHCP reservation on the router fixes this for good.

---

## Security

This part tells you which devices famstack trusts and how a device gets in. It also tells you what only the Mac can do, how first passwords work and what famstack does not protect.

### What does famstack trust?

famstack trusts three things:

| Who | Trusted | What it can do |
|---|---|---|
| The Mac that runs famstack | Yes | Everything: the setup, every change, every person's password |
| The stack's containers, extensions included | Yes | What their stacklet does. An [extension](#extensions) can read the stack's secrets. |
| A [paired device](#how-does-a-device-get-paired) | As much as its person | Open the family's pages and its own person's sign-in |
| Every other device on the home network | No | Nothing but the page **This browser does not know you yet** |

A **paired device** is a phone, a laptop or a screen that famstack knows. It belongs to a person, or to the household as a shared device. A paired device opens these pages without a password: the dashboard, the card pages, the family wiki and every `/go` link. Its lock screen protects it. It never asks for a password or a PIN.

A guest's phone on your Wi-Fi is not paired. A web page in a family member's browser is not paired either. famstack is not reachable from outside the home network. Keep it that way (see the warning at the top of this guide).

### How does a device get paired?

There are two ways. A phone is usually paired by a QR code from the Mac. Any other browser joins by its code, and someone who is already paired allows it.

To pair a phone from the Mac:

1. On the Mac, click famstack in the menu bar, then **Setup…**. Or run `./stack devices admin-link` and open its link.
2. In **Your family**, click **Add a phone** next to the person.
3. Open the camera on the person's phone and point it at the QR code.

The phone opens **This phone is now Homer's**, with the chat app for the phone and the person's own sign-in. The code works once, for 10 minutes. The setup's phone step does the same for your own phone.

To pair a phone on a Mac without a screen, connect to the Mac with ssh. Then run:

```bash
./stack devices pair homer
```

The command prints the QR code and its address in the terminal. It waits until the phone opened the code. `./stack install --family` ends with this code for the first admin.

Every other browser (a laptop, a tablet, a phone with no camera at hand) joins by its code:

1. Open the home address in the new browser. It shows **This browser does not know you yet**, a code such as `K7MP-4XQD` and the same code as a QR code.
2. Allow it from a paired phone. Point the phone's camera at the QR code. Or tap **Add a device** in the top bar and type the code.
3. On the page **Add this browser?**, compare the code with the new screen. Then tap **Allow**.

The new browser goes on by itself. The request ends after 10 minutes, and the code works once.

Who can allow a new browser:

| Who | For whom |
|---|---|
| A person's paired device | That person only |
| An admin's paired device | Anyone in the family, or **Shared** with a name |
| The Mac's **Devices** page | Anyone in the family, or **Shared** with a name |
| A shared device, or a browser that is not paired | Nobody |

A **shared device** belongs to the household, for example the screen in the living room. Only an admin adds it: choose **Shared** under **Allow as**, then **Name this device**. Its top bar shows that name. It shows no person's password.

Every admin's phone rings when a device asks to join. famstack sends a message to the Server room that mentions each admin:

```
A browser waits to join famstack: Chrome on a Mac, since 21:14, code K7MP-4XQD. If it belongs to the family: Allow it
```

Tap **Allow it** on your own paired phone. The link opens famstack's page with the browser, the time and the code. The tap alone allows nothing. Compare the code with the screen in front of you, or with what the family member tells you. Then choose the person under **Allow as** and tap **Allow**. The page answers only on an admin's paired device or on the Mac.

famstack sends at most one such message in 10 minutes. The Server room also hears of each device that is added: "Homer added a device: Chrome on a Mac, allowed from Homer's iPhone."

To use the Mac's own browser for the family's pages, open **Devices** on the setup page. Then click **Use this browser for the family's pages**.

### How is a device removed?

Remove a device on the Mac or on another device of the same person. The device's key stops working at once.

- **On the Mac:** open **Devices** on the setup page and click **Remove** next to the device.
- **On a paired device:** open **Your devices** in the top bar and tap **Remove** next to the device.
- **From the terminal:** run `./stack devices list` to get the device's id. Then run `./stack devices remove <id>`.

Remove a lost phone. A new password does not help, because the phone opens famstack with its key. When you remove a person from the family, famstack removes the person's devices too.

### What happens only on the Mac?

The setup, starting and stopping services, the family and every person's password are only on the Mac's address. The Mac's address is `http://127.0.0.1:42002`. No other device on the network can reach it, because core publishes it on the Mac only. The Mac's address also asks for the admin key, because the stack's containers can reach it too.

famstack decides "on the Mac" from two things: the address that a request arrives on, and the admin key. It never decides from what the request says about itself. On the home address, a change answers with the page **This happens on the Mac that runs famstack**.

### What is the admin key?

The **admin key** is a random key that opens the Mac's address in a browser. famstack makes it on the first start of core and keeps it in `.stack/admin-key`. Only the Mac's user can read this file. No container gets the key. Core's tools server gets only its hash.

A browser gets the key once, in a link. The link sets a cookie and then removes the key from the address bar. The browser keeps the cookie for 400 days. These open the link for you:

- the menu bar app's **Setup…**;
- the last line of `./stack`, the installer;
- `./stack devices admin-link`, which prints the link and the key under it.

To open the Mac's address in another browser, run:

```bash
./stack devices admin-link
```

Then open the link. Or open `http://127.0.0.1:42002` and paste the key into the field on the page. The page counts wrong keys. After 10 wrong keys in a minute, it refuses every key until the minute is over.

The cookie is for `127.0.0.1` only. The other services never get the cookie, because famstack opens them at `localhost`. Do not open a service at `127.0.0.1:<port>` by hand, because that service then gets the cookie too.

To make a new key, delete `.stack/admin-key` and run `./stack devices admin-link`. Every browser then needs the new link.

### How do I reach the Mac's address without a screen?

Use one of these two ways:

- **An ssh tunnel.** On your laptop, run the command below. Then run `./stack devices admin-link` on the Mac over ssh, and open its link in the laptop's browser.

  ```bash
  ssh -L 42002:127.0.0.1:42002 <mac>
  ```

  The tunnel reaches the Mac's `127.0.0.1`, and the link gives the laptop's browser the admin key. Close the ssh connection when you are done.
- **Screen sharing.** Connect with macOS screen sharing and use the Mac's browser.

To pair the first phone without a screen, use `./stack devices pair <person>` over ssh (see [How does a device get paired?](#how-does-a-device-get-paired)).

### How are first passwords made and changed?

The chat, photos, documents and code apps have their own sign-in. famstack makes an account for each person. The account's **first password** is four random words, for example `otter-maple-cloud-lamp`. `./stack family set`, `./stack install --family` and the setup page make it for each new person. famstack never changes a password that is already stored.

The first password shows in these places:

| Where | Whose |
|---|---|
| The setup page on the Mac | Every person's, in the family step. The service cards show the admin's. |
| A person's own paired device | Only that person's. **Your sign-in** on a service card opens it, with **Copy**. |
| A shared device | Nobody's |
| The terminal | The output of `./stack up` and `./stack messages setup` |
| The Server room | Nobody's. The room says that the password is on the setup page on the Mac. |

To change a password, change it in the app itself:

| App | Where |
|---|---|
| Chat (Element) | Element on the Mac (`localhost:42030`): Settings > Account |
| Photos (Immich) | Immich in the browser: your picture (top right) > Account Settings > Password |
| Documents (Paperless) | Paperless in the browser: your name (top right) > My Profile |
| Code (Forgejo) | Forgejo in the browser: your picture (top right) > Settings > Account |

famstack does not learn of a password that you change in an app. The setup page and the sign-in guide still show the first password. Under it they say **Changed a password in an app? Then that one counts there.**

> ⚠️ **On an install from beta.1 or earlier, change each person's first password in each app.** On those installs each person's first password is their first name, and every device at home can read the usernames. famstack does not name the people who still have it. The update to beta.2 changes no password.

### What are the known limits?

- **The home address is plain HTTP in port mode.** The chat's own sign-in on port 42031 is plain HTTP too. A device that records the Wi-Fi's traffic could copy a device key or a chat password.
- **Your Wi-Fi keeps strangers out.** Use WPA2 or WPA3 with a strong Wi-Fi password, because only a device on the Wi-Fi can read its traffic. Give guests a guest network.
- **Domain mode gives HTTPS.** With your own domain and a DNS provider, Caddy gets certificates and every page uses HTTPS (see [ADR-003](adr/adr-003-managed-dns.md)).
- **A paired device is as trusted as its person.** A lost phone with no lock screen opens the family's pages. Remove it (see [How is a device removed?](#how-is-a-device-removed)).
- **A key belongs to one host name.** A phone paired on `simpson.local` is not paired on the Mac's IP address. famstack writes every link with one host name. A link that you type with another name of the Mac opens **This browser does not know you yet**.

---

## Stacklets

Each stacklet except `core` and `messages` is opt-in. Mix and match as you like. `messages` is technically optional, but without it the whole point of famstack (operating from your phone) goes away.

famstack also ships a lightweight bot runtime. Bots are tiny helpers that automate things in your family chat: filing documents, transcribing voice memos, managing the stack itself. Some are also experimenting with [nanobot](https://github.com/HKUDS/nanobot)-based agent flows.

### Messages (`messages`)

Matrix homeserver (Synapse) plus the Element web client. Your family's private chat, and the messaging backbone of famstack.

**Why Matrix?** Element X has native apps for every platform: iOS, Android, macOS, Windows, browser. Install once per device and forget about it. Matrix was built for federated public servers with full E2E encryption; we do not need any of that for a local setup, and the config options are complex. famstack hides that complexity. We did not find a better free messenger covering all platforms. (See [ADR-004](adr/adr-004-messaging-backend-and-abstraction.md).)

```bash
./stack up messages    # the installer does not start it
```

`./stack up messages` creates the Family space with Family Room, Memories and Server Room, and seeds an account for every family member. Each person signs in with their first name in lowercase and their first password (see [Security](#how-are-first-passwords-made-and-changed)).

| | |
|---|---|
| Element web port | `42030` |
| Synapse port (mobile clients) | `42031` |
| Data | `~/famstack-data/messages/synapse/`, `~/famstack-data/messages/postgres/` |

Useful commands:

```bash
./stack messages users
./stack messages send <room> "<message>"
./stack messages send --to <member> "<message>"
./stack messages room list
./stack messages setup
```

`messages users` lists each account and the app of each of its sign-ins, for example "Element X Android" or "Element Web". With `--json`, every sign-in also has a kind: `phone`, `computer`, `browser`, or empty when the chat server does not say which app it is.

Commands that take a room name it by its alias: `famchat` is Family Room, `memories` is Memories, `famstack` is Server Room and `documents` is Documents Room. For example, `./stack messages send famstack "Back up tonight"` posts to Server Room.

`messages send` posts as the system account by default; `--as agent`
posts as the agent, `--as <member>` as that family member. `--to <member>`
sends to the member's private chat with the sender, the room with just the
two of them. When there is none, nothing is sent and the command fails:
the stack does not create rooms for a message.

An encrypted room, such as a private chat started in Element X, gets the
message encrypted. It comes from a device of the sending account's own,
listed in Element as `<account> (command line)`, which runs in the core
bot runner: core has to be up (`./stack up core`). For the agent and the
bots that device is signed with the account's own keys, so Element shows
it as verified. A family member's command-line device shows as unverified,
because their keys stay on their phone.

### Photos (`photos`)

Immich-based photo library, with native mobile apps and automatic background upload.

**Why Immich?** It is the most complete self-hosted photo solution: face recognition, maps, memories, albums, shared libraries. Native iOS and Android apps with background sync. Your family does not need to change their habits. They take photos, photos sync.

```bash
./stack up photos
```

Install the Immich app on every phone, point it at `http://<mac-lan-ip>:42010`, log in. Done. Every family member gets their own account and library.

| | |
|---|---|
| Port | `42010` |
| Data | `~/famstack-data/photos/library/`, `~/famstack-data/photos/postgres/` |

#### Bulk import

Import media from a mounted drive, NAS share, or local folder. The importer tracks progress per source path, so imports can be paused, resumed, and interleaved across multiple sources.

```bash
./stack photos import --source /Volumes/files/Bilder/2020   # scan a folder
./stack photos import --status                               # show all sources + progress
./stack photos import --proceed 100                          # upload next 100 files
./stack photos import --proceed 10 --dry-run                 # preview, no upload
./stack photos import --proceed 10 --verify                  # spot-check EXIF first
./stack photos import --proceed 500 --force                  # skip hash dedup (faster)
./stack photos import --album "Vacation 2023"                # upload into a specific album
./stack photos import --proceed 500 --album-per-year         # auto-bucket by EXIF year
```

Deduplication is two-layered: a local SHA-256 hash ledger skips files already imported in previous runs, and Immich's server-side check catches files uploaded via the mobile app or web UI. The hash step can bottleneck large imports — use `--force` to skip it when you know there are no duplicates (Immich still deduplicates server-side). Switching `--source` starts a new cursor; going back to a previous source picks up where you left off.

**Auto-sort by year.** For unsorted photo dumps, `--album-per-year` reads the EXIF date from each file, groups by year, and creates one album per year automatically. Falls back to filename patterns (`20180415_...`) when EXIF is missing. Files without a detectable year go into "Unknown Year".

**WhatsApp images.** Phone backups often contain hundreds of WhatsApp forwarded images (`IMG-*-WA*`) that pollute the timeline. After importing, archive them in one pass:

```bash
./stack photos album archive --pattern whatsapp
```

This hides them from the timeline, detaches them from albums, and keeps the files in the library. Use `unarchive` to put them back on the timeline. It does not restore the albums, because Immich keeps no record of them.

#### Album management

Bulk operations on albums and assets. Archive junk, move assets between albums, or clear an album — all from the command line.

```bash
./stack photos album archive --pattern whatsapp             # archive WhatsApp images
./stack photos album archive --pattern '*.gif'              # archive all GIFs
./stack photos album archive --from "Imported"              # archive an entire album
./stack photos album archive --from "Imported" --pattern whatsapp  # combine both
./stack photos album unarchive --pattern whatsapp           # back on the timeline, albums are not restored
```

Archive detaches assets from albums by default. With `--pattern`, an asset leaves every album. With `--from`, an asset leaves the named album only. Use `--keep-albums` to keep album memberships.

```bash
./stack photos album copy  --from "Imported" --to "Family"  # copy all assets
./stack photos album clear --from "Imported"                 # remove all from album
./stack photos album move  --from "Imported" --to "2024"     # copy + clear in one pass
```

Copy, clear, and move accept `--pattern` to filter by filename glob (e.g. `--pattern '*.jpg'`). The alias `whatsapp` expands to `IMG-*-WA*`.

Album names are matched case-insensitively. `--from` accepts a part of an album's name. `--to` means the album with exactly that name, and creates the album if it does not exist. A command refuses a name that two albums have, because it cannot show which album it changed. A `move` refuses a target that is its source.

All commands support `--dry-run` to preview and `--api-key` to use a specific user's key (defaults to the import key from `secrets.toml`).

### Documents (`docs`)

Paperless-ngx archive with OCR and structured classification. Receipts, letters, contracts, tax documents. Search across everything by content.

**Why Paperless?** Gold standard for self-hosted document management. Mature project, large community. famstack makes the document inbox available on every phone through the chat: photograph a receipt, send it to the Documents Room, and it gets filed.

```bash
./stack up docs
```

The archivist bot creates a room called Documents Room in your chat. AI classifies and tags incoming documents when the `ai` stacklet is running. Without `ai`, documents are still stored and searchable by OCR text, just not auto-tagged. Type `show 42` in the Documents Room to read a document; type any term to search.

On first setup, famstack seeds Paperless with common document categories and types in your configured language. The LLM picks from these when classifying, so tags stay consistent. See [`stacklets/docs/taxonomy.toml`](../stacklets/docs/taxonomy.toml).

**Who sees what.** When family members sign in to Paperless through single sign-on, every account that is not a superuser joins the `all_documents` group: it sees the whole archive and may add, change and delete documents. The vocabulary (tags, correspondents, document types, storage paths) is shared with every member: entries have no owner, so everyone sees them by name. Members may add and rename tags, correspondents and document types, but not delete them. An entry a member creates in the Paperless UI belongs to that member, and stays private until they share it.

**Memory vault.** With the `code` stacklet up, every filed document and every captured URL or pasted note also lands as a Markdown file in your Forgejo repo `family/memory`. Documents and correspondents live under the shared bucket (`family/` by default, configurable via `stack.toml [core] shared_bucket`). Personal captures route to the sender's own bucket: Homer's pastes end up under `homer/notes/` or `homer/bookmarks/`, Marge's under hers. Paperless stays the canonical store; the memory vault is the browsable, git-versioned human view. Install the [`memory` stacklet](#memory-memory-optional) to seed the ontology and render the vault as the family wiki.

| | |
|---|---|
| Port | `42020` |
| Data | `~/famstack-data/docs/paperless/`, `~/famstack-data/docs/postgres/`, `~/famstack-data/docs/consume/` |

Useful commands:

```bash
./stack docs show <id>                     # current state
./stack docs classify <id>                 # re-run AI tagging
./stack docs reformat <id>                 # re-run OCR to clean markdown
./stack docs reprocess <id>                # full pipeline, respects bot.toml
./stack docs mirror <id>                   # publish to Forgejo mirror
./stack docs tags                          # list tags with document counts
./stack docs tags merge <from> <to>        # merge duplicate tags
./stack docs tags prune --lang <de|en>     # drop unused seeded tags
```

Every write command accepts `--dry` (or `--dry-run`) to preview. `reprocess` is clean-slate: running it twice does not accumulate old tags.

### AI (`ai`)

Local AI engine. Powers document classification, voice transcription, and text-to-speech. The heavy one.

**Why oMLX?** Runs MLX-native models directly on Apple's Metal GPU with a smart SSD caching layer. Faster than Ollama on Apple Silicon, and serves models larger than your RAM by spilling to disk. (See [ADR-009](adr/adr-009-managed-ai-provider.md) and the [benchmarks](https://famstack.dev/guides/mlx-vs-gguf-part-2-isolating-variables/).)

```bash
./stack up ai
```

famstack gets its AI in one of two ways. The setup page and the first `./stack up ai` offer the same two ways:

| Way | What it is | How to start it |
|-----|------------|-----------------|
| **famstack installs oMLX** (recommended) | famstack installs oMLX from Homebrew, runs it on port 42060 and looks after its models. | `./stack ai switch managed` in the terminal of the Mac. It takes 20 to 30 minutes. |
| **Your own AI server** | Any server with the OpenAI API: the oMLX app, an oMLX on another Mac, Ollama, LM Studio or a hosted service. famstack changes nothing on that server. | The address and the key on the setup page, or `./stack ai switch <address> --key <key>` |

famstack is tested and tuned for local models with oMLX. We recommend it.

A Mac with less than 24 GB starts with speech off. Then the first start downloads and builds nothing for speech. See [What does a 16 GB Mac get?](#what-does-a-16-gb-mac-get)

#### How do I let famstack install oMLX?

The setup page shows this way under **famstack installs oMLX**. The command runs in the terminal, because Homebrew and macOS ask for permissions there.

1. Open the terminal on the Mac that runs famstack.
2. Go to the famstack folder.
3. Run the command:

```bash
./stack ai switch managed
```

The command installs oMLX with Homebrew when it is missing, and starts it. It downloads the models of your memory profile. At the end it asks the AI one short question. The AI tile on the setup page follows the run, and the page shows **Your AI is ready** when the AI answered.

famstack runs oMLX in a folder of its own, `~/famstack-data/ai/omlx`. Its launchd service is `dev.famstack.omlx`. The folder holds the settings, the port and the API key of famstack's oMLX. The oMLX app keeps its own folder, `~/.omlx`, and famstack does not change it. Both use the models folder `~/.omlx/models`, because then a model is downloaded only once.

The command never prints the API key. The key is `[ai] openai_key` in `stack.toml`.

Quit the oMLX app before you run the command, because two AI servers on one Mac's graphics chip can crash the Mac. If the oMLX app runs, the command stops before it installs or starts anything, and it changes nothing. The AI tile on the setup page shows the same message. `./stack up ai` and `./stack ai key new` do the same check.

Homebrew installs the oMLX release that its tap offers. This can be a release candidate. The run prints the release that it installed. If that release cannot answer, the last step of the run says so. The setup page then shows **Your AI does not answer**.

If the Mac has no Homebrew, the command stops and says how to install it. If the run fails halfway, the stack keeps the AI server that it used before. The output says why.

With speech on, the page then shows **Your Mac learned to speak**. Click **▶ Play** to hear the AI's welcome in this browser, on the Mac or on a phone. Click **Yes** when you heard it. The AI then introduces itself, and the page shows its words while it speaks.

To hear the AI in the terminal, run `./stack ai hello` (the welcome) or `./stack ai intro` (the introduction). They play on the speakers of this Mac.

#### How do I use my own AI server?

Your own AI server can be the oMLX app on this Mac or a server elsewhere. For a server on another machine or a hosted service, see [AI on another machine, or a hosted provider](#ai-on-another-machine-or-a-hosted-provider). To connect a server on the setup page:

1. Open `/setup` on the Mac and go to the step **Your AI**.
2. Under **Use your own AI server**, click **Enter a server**.
3. Enter the address of the server. The field starts with `http://localhost:8000/v1`, the address of the oMLX app.
4. Enter its API key, if it needs one. The oMLX app shows its key under **Settings**, **Security**, **API Key**.
5. Click **Connect**.
6. If the server has more than one model, choose the model for reading and answering. Then click **Connect** again.

The fields show only on the Mac that runs famstack, because the page uses plain HTTP on the home network.

**Connect** does the same as `./stack ai switch <address> --key <key>`. It checks that the server answers and picks its chat model. Then it asks the server one short question. It downloads nothing and changes nothing on the server. If the server does not answer or refuses the key, the page says so. Then `stack.toml` does not change.

If the server has no speech-to-text model, voice messages need a speech server (`--whisper`).

In the terminal, the first `./stack up ai` asks **Set up oMLX?**. Answer no. Then enter the address and the API key of your server.

#### Which settings does famstack recommend for an oMLX?

When your own server is an oMLX, the setup page shows the settings that famstack recommends for this Mac. They show under **Your AI is ready**. famstack asks the server whether it is an oMLX when it asks its first question. famstack does not change these settings on your server. Set them yourself in the oMLX app, because the server is yours.

| Setting | Small Mac (less than 24 GB) | Larger Mac |
|---------|-----------------------------|------------|
| Chat model | `mlx-community/Qwen3.5-9B-MLX-4bit` | the chat model of the memory profile |
| Speech models | none, speech is off | the speech-to-text model and the house voice |
| Memory guard | custom, 9 GB | oMLX's own setting |
| Chunked prefill | on | oMLX's own setting |
| Idle unload | after 10 minutes without a request | oMLX's own setting |

See [What does a 16 GB Mac get?](#what-does-a-16-gb-mac-get) for the reasons.

#### What if the AI does not answer?

A server can list its models and still fail every answer. For example, an oMLX app built for another macOS fails like this. The setup page then shows **Your AI does not answer**, with the server's own error. It offers these ways on:

| Button or link | What it does |
|----------------|--------------|
| **Check again** | Asks the AI one short question again. For the oMLX that famstack runs, it starts the AI's run. |
| **Enter another server** | Opens the form of your own AI server. |
| **Let famstack install oMLX instead** | Shows `./stack ai switch managed`. Only for a server that famstack does not run. |

To ask the AI in the terminal, run `./stack ai check`.

#### What happens without an AI server?

When no AI server runs on the Mac, the first `./stack up ai` installs oMLX. The first start takes 20 to 30 minutes:

1. Asks you to confirm the local engine. If you run another AI server (for example Ollama), answer no. A yes installs a second server next to it. Then see [AI on another machine](#ai-on-another-machine-or-a-hosted-provider).
2. Taps and trusts the `jundot/omlx` Homebrew tap, then installs oMLX (see below).
3. Installs `cmake` and `ffmpeg` if missing.
4. Clones and builds whisper.cpp with Metal support (1 to 2 minutes of compilation).
5. Downloads the Whisper large-v3-turbo model (1.5 GB).
6. Downloads the chat model of your memory profile (2.5 to 25 GB).
7. Starts oMLX as a Homebrew service, Whisper as a launchd service.
8. Downloads the speech model of the house voice, `[ai] language`, into oMLX: Kokoro for English, Qwen3-TTS for German.

The AI runs no container. Up to 0.4 it ran a speech container, `stack-ai-speech`. On an updated Mac, the next `./stack up ai` removes that container and its image.

**About the Homebrew tap.** oMLX is not in Homebrew's own repositories, it is published through the `jundot/omlx` tap. Homebrew 6 refuses to run formulas from outside taps until you trust them, so `stack up ai` trusts this one for you and says so while it does it. That is a real widening of what Homebrew will execute on your Mac, which is why it is called out rather than buried in the install log. To take it back:

```bash
brew untrust jundot/omlx
```

Trusted taps are recorded in `~/.homebrew/trust.json` (or under `$XDG_CONFIG_HOME/homebrew/` if you set it). On Homebrew 5 and older there is no trust gate and the step is skipped.

**Maintaining oMLX by hand.** famstack installs it with `--with-grammar`, which
brings in xgrammar. That is what makes the model return structured JSON, and
document classification depends on it: without xgrammar, filing still happens
but every document arrives with no tags, no correspondent and no summary.

Upgrading is safe. Homebrew records the options a formula was built with and
reuses them, so `brew upgrade omlx` keeps grammar. You can check what yours was
built with:

```bash
grep -o '"--with-grammar"' /opt/homebrew/Cellar/omlx/*/INSTALL_RECEIPT.json
```

Uninstalling is what loses it, because that receipt goes with it. If you ever
remove and reinstall oMLX yourself, pass the flag again:

```bash
brew install omlx --with-grammar
```

Two failures worth recognising. `brew` refusing to load the formula at all
means the tap is no longer trusted, so trust it again as above. And a
`bad interpreter: .../bin/python3.11: no such file or directory` from the
`omlx` command means its virtualenv broke, usually after a Python upgrade;
`brew reinstall omlx` rebuilds it. `stack up ai` reports a missing oMLX
rather than starting and failing a health check with no stated cause.

Why native instead of Docker? Metal GPU acceleration does not pass through Docker on macOS cleanly. Roughly 10x performance difference. Docker for orchestrated services, native for the GPU work.

Once up, voice messages in chat get transcribed automatically and the document classifier starts using the LLM.

| | |
|---|---|
| oMLX port | `42060` |
| Whisper port | `42062` |
| Data | `~/famstack-data/ai/` (Whisper, TTS), `~/.omlx/models/` (LLMs) |

Useful commands:

```bash
./stack ai models
./stack ai download <model-id>
./stack ai switch <url>
./stack ai say "<text>" [--voice <name>]
./stack setup ai
```

To switch LLM models: edit `[ai] default` in `stack.toml` (alternatives are listed as commented lines), then `./stack setup ai`.

#### What if a voice model is missing?

Every later `./stack up ai` downloads the speech model of the house voice when it is still missing. When the download fails, it warns with the model's name. Until the model is there, `./stack ai say` reads a text in another language in the house voice.

To add the German voice, run:

```bash
./stack ai download mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit
```

To add the English voice, run:

```bash
./stack ai download mlx-community/Kokoro-82M-bf16
```

The stack downloads these models only into an oMLX. For another speech server, such as one in `[ai] speech_url`, download the models there.

#### AI on another machine, or a hosted provider

Everything that uses AI talks to one OpenAI-compatible address, `[ai] openai_url`. The engine `./stack up ai` installs is one option. If your models run on another computer in the house (oMLX, Ollama, LM Studio, anything that speaks the OpenAI API and listens on the network), or you use a hosted provider, skip `./stack up ai` and point the stack there:

```bash
./stack ai switch 192.168.1.20:11434                      # Ollama on another machine
./stack ai switch https://api.example.com --key sk-...    # a hosted provider
./stack ai switch localhost:8888                          # an AI app you run on this Mac
./stack ai switch managed                                 # the engine the stack runs on this Mac
```

A second Mac running famstack's AI stacklet in port mode serves both chat and voice to the network, oMLX on port 42060 and Whisper on 42062. Its API key is its own `[ai] openai_key`, `local` unless changed:

```bash
./stack ai switch 192.168.1.20:42060 --key local --whisper 192.168.1.20:42062
```

The command checks that the server answers, picks a model it has (`--model <id>` when it lists several), writes `[ai]` in `stack.toml`, and names the running stacklets to restart. It installs nothing and restarts nothing. `./stack list` then shows the AI stacklet as `remote`, with the server's name, and as degraded when that server stops answering.

When the server is an oMLX on another machine, the first `./stack up ai` asks before it downloads a model onto it. This is true for the chat model, the speech-to-text model and the voice. The question names that machine, and the default answer is no. The stack does not change the settings of a server that it does not manage.

**Voice messages** have their own address, because not every AI server can transcribe:

- `--whisper <url>` names a speech server. It gets every voice message, whatever the AI server offers. A second Mac running the AI stacklet serves one on port 42062.
- Without one, the AI server transcribes, if it has a speech-to-text model loaded. The command looks for one and warns when there is none.
- `--whisper ai` removes the speech server again.

**Speaking aloud** (`./stack ai say`, the agent's voice messages) goes to the AI server too, with its key, in the stack's own voices: Kokoro's `af_heart` for English and the stack's German voice, cloned with Qwen3-TTS from a clip it ships. Each needs its model in oMLX; `./stack ai say` reads a text whose language's model is missing in the house voice. `[ai] speech_url` (with `[ai] speech_key`) names another speech server, and any server that answers OpenAI's speech API will do once `[ai.voices]` names its model and voice for each language:

```toml
[ai]
speech_url = "http://192.168.1.20:8000"
speech_key = "..."

[ai.voices.en]
model = "tts-1"
voice = "alloy"

[ai.voices.de]
model = "tts-1"
voice = "onyx"
```

An entry replaces that language's voice whole; a language without one keeps the stack's own. `clip = "stacky-de"` clones the stack's German voice on a server that can (oMLX), and is sent only when set. `./stack ai say --voice <name>` knows the voices in force. `./stack up ai` downloads the models of these voices into the engine the stack runs, when a model is one of the stack's own or written as a Hugging Face repo (`mlx-community/...`); a server the stack does not run is left alone. A speech server in your home is recommended: a hosted one hears everything the stack says to your family.

A short phrase that is said often, such as "Moment.", can play without a request to the speech server. Add `--keep`: the first time, the stack synthesizes the phrase, plays it and keeps the audio. After that, the phrase plays from the kept audio, also when the speech server is busy or stopped. `--prepare` makes and keeps the audio and plays nothing. Kept audio is for one voice and its model, and another voice or a changed model is synthesized again. The stack keeps it in `~/famstack-data/ai/voice-snippets/<language>/`, one file for each phrase. The file name is the phrase's words and a short hash, for example `de/gern-geschehen-91b0e4aa.mp3`, so you can find and play a phrase. You can delete that folder, because the stack makes the audio again when it is needed. Without `--keep` or `--prepare`, the stack keeps nothing.

**What leaves the house.** With a hosted provider, the text of your documents, notes, chat questions and voice messages is sent to that provider and processed by whoever runs it. The command warns whenever an address is outside your home network; machines on your LAN or your Tailscale network count as home.

**The engine the stack manages.** `./stack ai switch managed` moves the stack to the engine the ai stacklet runs on this Mac. It runs `./stack up ai`, which installs oMLX when it is missing. When that fails, the stack keeps the previous server.

Once `[ai]` names a server, `./stack up ai` asks no question and keeps the server you chose. After a switch to your own server, `./stack up ai` installs only speech-to-text on this Mac. That is also the setup for an AI app that you run on this Mac: `./stack ai switch localhost:<port>`, then `./stack up ai`. `./stack list` then shows the ai row as `localhost`. After a switch to a remote server, `./stack down ai` stops a local engine that still runs, and frees its memory.

### ChatAI (`chatai`) deprecated

Open WebUI: a ChatGPT-style interface for your local LLM.

**Deprecated in 0.4.0, removed in 0.5.0.** It still works, and `./stack up chatai` says so on every start. Asking the AI now happens in the family chat, through Stacky, the `agent` stacklet (beta).

**Why Open WebUI?** Most polished open source chat UI. Multiple models, conversation history, file uploads. Feels like ChatGPT but runs locally.

**The tradeoff:** another container using RAM. Skip on 16 GB Macs. AI still works for document classification and voice transcription without a chat UI.

```bash
./stack up chatai
```

| | |
|---|---|
| Port | `42050` |
| Data | `~/famstack-data/chatai/` |

### Code (`code`) optional

Forgejo: a community fork of Gitea. Lightweight private git server.

**Why Forgejo?** Simple, fast, low resource usage. Useful if you want the docs stacklet's "mirror to git" feature, or somewhere to version-track household text files.

```bash
./stack up code
```

| | |
|---|---|
| HTTP port | `42040` |
| SSH clone port | `222` (macOS uses 22 for itself) |
| Data | `~/famstack-data/code/` |

### Memory (`memory`) optional

The persistent layer of the household brain: a curated knowledge vault, rendered as a browsable family wiki. Needs the `code` stacklet.

**Why a wiki?** Filed documents are answers waiting for questions. The memory stacklet turns what the archivist files into a wiki of your family life: a home page, a page per family member, topic pages, correspondents. Everything lives as Markdown in the `family/memory` Forgejo repo, so every change is a git commit you can review or revert. Prefer Obsidian? Clone the repo and open it as a vault; the wiki is just a nicer pair of eyes on the same files.

```bash
./stack up memory
```

Setup seeds the vault with three things: the classification ontology (the topics and document types the archivist files against), household facts, and a hand-curated correspondents layer with aliases, so "Springfield Insurance" and "Springfield Ins. Co." resolve to the same page instead of becoming duplicates.

The wiki maintains itself. A curator sidecar (`stack-memory-curator`) watches the vault: when new filings settle it regenerates the pages of the family members involved plus the home page (a couple of LLM calls, a few minutes after the burst); a changed list updates the topic pages it belongs to and the pages of the people its items are for within seconds, without asking the model. Once a night it rebuilds everything — topic pages, cross-references, the lot — while the GPU has nothing better to do. To bring the wiki up to date now instead of waiting, `./stack memory wiki update` regenerates what the latest filings touched and `./stack memory wiki update --all` runs the whole nightly rebuild; both return when the pages are written. `--member`, `--topic` and `--home` regenerate just those pages, and `[memory]` in `stack.toml` holds the knobs (see [Configuration](#configuration)). Edits happen in Forgejo (every wiki page links to its source), so the commit log doubles as the household's learning history.

**Who the family is.** Every person the family knows has a file of their own in the vault, `family/people/<id>.md`: the full name, the other names the family uses, and the chat account if they have one.

```markdown
---
type: person
title: Bart Simpson
aliases:
  - Bartholomew
account: bart
---
```

Each `./stack up memory` adds a file for every account in `users.toml` that has none, so an update reaches an existing install with a restart of the memory stacklet. An existing file is never rewritten: it is the family's. Add someone without an account, such as a baby, with `./stack memory person add "Maggie Simpson" --alias Margaret --as marge`: the file is named after the first name, `--id` gives another when two people share one, and a name that already belongs to someone is refused. Add further names a person goes by to their file. A person with an account is a member of the household. The wiki gives every person a page under the names their file gives, and its home page lists the members. The archivist files a document under a person by any of their names ("Margaret" on a birth certificate is Maggie) and creates a `Person:` tag for a person without an account the first time a document names them. Transcription and the diary listen for every name in the files. A file the wiki cannot read is named in the curator's log (`docker logs stack-memory-curator`) and left out, and no page is removed until it reads again. A change to any file rebuilds the person pages within a few minutes, or right away with `./stack memory wiki update --home --members`. Edit the files in Forgejo, in Obsidian, or with `./stack memory write family/people/<id>.md --by <you> --from <file>`.

**The family diary.** Everything posted in the Memories room becomes a diary entry. Each night the curator transcribes the new recordings, has the AI read each entry (a title, a summary, the people, topics from the ontology, facts), writes one card per entry to the vault under `family/diary/entries/`, and rebuilds the diary pages in the wiki from those cards. At 07:30 local time the archivist posts each new card as a quiet notice in the thread under its memory; the first time, one notice in the room says how to correct a card, and after two weeks without a new memory one notice invites the next, once per quiet stretch (`diary_job_at` in the archivist's `bot.toml` sets the time; it catches up after a night the Mac slept through). A family member corrects a card by replying in its thread, typed or spoken; the correction is committed to the vault under their name, and a corrected or hand-edited card is never overwritten by a later run. The archivist says nothing else in the Memories room. The user guide has the family's side: [The Memories room](user-guide.md#the-memories-room).

**Renaming a topic.** The archivist names a topic after its room. For example, the room "Thema: Nähprojekte" makes the folder `nahprojekte`. To give the topic another id and title, run:

```bash
./stack memory topic rename nahprojekte naehprojekte "Nähprojekte"
```

- The title is optional. Without it, the topic keeps its title.
- The command moves the topic's folder, its list and the list's archive in one commit. Every card of the bucket that had the old id gets the new id.
- The topic room continues to work. The archivist files into the new folder, and the agent knows the room's topic.
- `--bucket homer` renames a topic of Homer's own. Its list and the list's archive are in the family's lists, and they move with it. If the family has its own topic with the same id, the list stays with the family's topic. `--dry-run` shows the change and writes nothing.
- The command lists the links into the old folder that people wrote by hand. Correct these links yourself, because the command does not change text that people wrote.

| | |
|---|---|
| Wiki port | `42070` (domain mode: `memory.<your-domain>`; `wiki.<your-domain>` redirects there) |
| Data | `~/famstack-data/memory/vault/` (a checkout of Forgejo `family/memory`) |

Useful commands:

```bash
./stack memory search <term>      # full-text query over the vault
./stack memory wiki update             # bring the latest filings into the wiki, and wait
./stack memory wiki update --all       # the nightly rebuild now: diary, sources, every page
./stack memory wiki update --member homer   # regenerate one page
./stack memory ontology           # sync the vault ontology with the shipped seed
./stack memory lookup <text>      # which topic or doctype does a term resolve to?
./stack memory topic rename <old> <new> [<title>]   # give a topic another id
./stack memory correspondents     # inspect the correspondent layer
./stack memory pull               # have the curator take Forgejo edits now; says what waits for Forgejo
./stack memory diary              # take in the Memories room now instead of tonight
./stack memory diary --dry-run    # show what would be written, write nothing
./stack memory prompt             # the vocabulary the models are given, per prompt
```

---

## Extensions

### What are extensions?

An **extension** is a [stacklet](creating-stacklets.md) that is not part of famstack. You install it in addition to the [stacklets that famstack ships](#stacklets).

For example, the [**Hue lights**](https://github.com/famstack-dev/famstack-extensions/tree/main/hue) extension controls Philips Hue lamps. famstack does not include it, because only families with a Hue Bridge need it.

The [**Web search**](https://github.com/famstack-dev/famstack-extensions/tree/main/web) extension searches the internet through your own [SearXNG](https://docs.searxng.org/). It also answers a question from the search results, with the sources.

Each installed extension has its own folder in `~/famstack-extensions/`. After the install, an extension operates like all other stacklets:

- `./stack list` shows it.
- `./stack up <id>` starts it (see [Lifecycle](#lifecycle)).

### Where to get extensions

| Source | Review | Install with |
|---|---|---|
| The [famstack-extensions catalog](https://github.com/famstack-dev/famstack-extensions) | The famstack project reviews each extension before it goes into the catalog. | `./stack extensions add <id>` |
| Any other git repository | Nobody at famstack reviews the code. Anyone can publish an extension. | `./stack extensions add <git-url>` |

To see the extensions in the catalog and the extensions that you installed, run:

```bash
./stack extensions list
```

When the catalog has a newer version of an installed extension, the list shows **update available**. To install the newer version, see [How to update an extension](#how-to-update-an-extension).

### How to install an extension

**From the catalog**, give the id of the extension:

```bash
./stack extensions add hue
```

**From a different git repository**, give the URL of the repository:

```bash
./stack extensions add https://git.example.org/ann/kettle
```

Some repositories contain more than one extension. For these, give the id of the extension after the URL:

```bash
./stack extensions add https://git.example.org/ann/tools kettle
```

If you do not give an id, `add` asks which extension to install. `add` never installs all extensions of a repository.

**What `add` does:**

1. It copies the extension into `~/famstack-extensions/<id>/`. If `~/famstack-extensions/` does not exist, `add` creates it.
2. It writes the file `.source.toml` into the folder. This file records the repository, the commit and the path of the extension. It also records each file that `add` wrote. `./stack extensions list` and `./stack extensions update` use it.

`add` does not start the extension.

> **Next step:** read the README of the extension. `add` shows its path. Most extensions start with `./stack up <id>`. The Hue lights extension has no service to start. To use it, connect it to your Hue Bridge with `./stack hue setup`.

### How to update an extension

> ⚠️ **Do not change the files that `add` installed.** If you change one of them, `update` stops for this extension. Files that you add to the folder are kept. To change the code of an extension, see [Extensions that you develop](#extensions-that-you-develop).

> ⚠️ **Stop a running extension before you update it.** `update` deletes the old folder. A running container then reads deleted files, and the files that it writes during the update are lost.

To stop the extension, run:

```bash
./stack down hue
```

To update all extensions that you installed with `add`, run:

```bash
./stack extensions update
```

To update one extension, give its id:

```bash
./stack extensions update hue
```

`update` gets the newest version of each extension from the default branch of its repository. This is the version that `./stack extensions list` compares with. Extensions from the same repository share one download. Each extension then gets one line:

```
     toaster      installed by hand, not updated
  Fetching https://github.com/famstack-dev/famstack-extensions...
  ✓  hue          updated to 3c9d2f1
     Apply the new code with: stack up hue
     web          up to date
```

`updated to` shows the newest commit of the repository. This commit can be a change to a different extension.

**What `update` does:**

1. It compares the installed version with the newest version in the repository. If they are the same, the extension is **up to date**. A change to a different extension of the same repository is not an update.
2. It checks the files that `add` or the last `update` wrote. `.source.toml` records each of these files. If one of them is changed or missing, `update` stops for this extension.
3. It writes the new version into `~/famstack-extensions/.incoming/`. It does not copy a `.env` file that the repository contains, because `./stack up` writes this file.
4. It checks the files again, in case they changed while `update` asked a question.
5. It moves all other files of the old folder into the new version. For example, it keeps the `.env` file that `./stack up` writes. It also keeps a configuration file that the README asks for. If the new version has a folder with the same name as your folder, `update` moves your files into it.
6. It puts the new folder in the place of the old folder. Then it deletes the old folder.

If a step fails, `update` puts the old folder and your files back. If it cannot put a file back, the message shows where the file is. If you press Ctrl-C while `update` moves files, it completes the move first and then stops.

`update` does not start the extension. To start it with the new code, run:

```bash
./stack up hue
```

Do not use `./stack restart` for this. It does not load the new code of a bot.

An extension from a repository other than the catalog shows [the warning](#extensions-from-other-repositories) again, for the new version. Then `update` asks `Update <id>? [y/N]`. To update without the question, add `--yes`. The warning still shows.

**When `update` does not update an extension**, it shows the reason and does not change the folder. The other extensions continue.

| `update` refuses when | What to do |
|---|---|
| A file that `add` installed is changed or missing. `update` shows the first five. | Undo your change. Or move the folder away and install the extension again with `add`. |
| The new version has a file with the same path as a file that you added. | Move your file away, then run `update` again. |
| The extension is not installed. | Install it with `./stack extensions add <id>`. |
| You put the folder there yourself (no `.source.toml`). | Update it with your own tools, for example `git pull`. |
| The `.source.toml` of the extension does not list its files. | Move the folder away and install the extension again with `add`. |
| `update` cannot read a file or a folder in the extension. | Read the path in the message. Correct the permissions of that file. |
| The new version is not usable: its `stacklet.toml` cannot be read, its `id` changed, or it is not in the repository any more. | Ask the author of the extension. |
| `update` cannot get the repository. | Read the reason from git. Check the network and the address of the repository. |

If one extension is refused, the exit code is 1. If no extension has a `.source.toml`, `update` shows **No extensions installed with stack extensions add**.

### Extensions from other repositories

> ⚠️ **Install only extensions that you trust.** The [hooks](stack-reference.md#hook-reference) and [commands](stack-reference.md#cli-commands) of an extension run on your Mac with your user account. They can read the [secrets of the stack](#stacksecretstoml), for example the admin password.

Before `add` installs an extension from a repository other than the catalog, it shows a warning:

```
  ! kettle is not from the famstack catalog. Nobody at famstack
    has reviewed this code.

    Source   https://git.example.org/ann/kettle
    Commit   a81b0e4  2026-10-20  "feat: brew on a schedule"
    Brings   containers   kettle-api
             host hooks   on_install, on_start
             CLI plugin   stack kettle brew

    Its hooks and CLI plugin run on this Mac as you, with access to
    the stack's secrets. Install only code you trust.

  ? Install kettle? [y/N]
```

The warning shows:

- the repository and the commit of the extension;
- the parts of the extension that run code: containers, hooks, migrations, commands and bots.

The default answer is **no**. To install without the question, for example in a script, add `--yes`. The warning still shows.

Each `./stack up` of such an extension shows the repository that the extension came from.

### When `add` does not install an extension

`add` checks the extension before it writes files. If a check fails, `add` writes nothing and shows the reason.

| `add` refuses when | Reason |
|---|---|
| famstack ships a stacklet with the same id. | The stacklet of famstack has priority. The extension would never load. |
| An extension with the same id is already installed. | `add` shows the folder of the installed extension. |
| The `stacklet.toml` of the extension cannot be read, or it has no `id`, `name` or `description`. | famstack needs these fields to list and start the extension. |

### Extensions that you develop

You can also put a folder into `~/famstack-extensions/` yourself, for example a clone of an extension that you develop. For such a folder:

- There is no `.source.toml`.
- `./stack extensions list` shows it as **installed by hand**.
- `./stack extensions` never changes it.

### Backups of extensions

> **Note:** the backup does not include `~/famstack-extensions/`. See [Backups](#everything-else).

You can install an extension from a repository again at any time. Keep the extensions that you develop in a git repository.

### Use a different catalog

The address of the catalog is `extension_catalog` in the `[core]` section of [`stack.toml`](#stacktoml):

```toml
[core]
extension_catalog = "https://github.com/famstack-dev/famstack-extensions"
```

`add` installs extensions from this catalog without a warning. Change the address only to a catalog whose reviews you trust.

---

## Configuration

### stack.toml

The single config file. Generated by the installer. Yours to edit.

```toml
[core]
domain   = ""                    # empty = port mode (recommended to start)
host     = ""                    # empty = auto-detect LAN IP; "localhost" for one machine
data_dir = "~/famstack-data"     # where databases, uploads, media live
timezone = "Europe/Berlin"
language = "de"                  # "de" or "en", used for document tags and UI

[updates]
schedule = "0 0 3 * * *"         # Watchtower nightly image updates

[ai]
default = "mlx-community/Qwen3.5-9B-MLX-4bit"   # change to match your RAM
language = "en"                                   # the voice that reads answers aloud; can differ from [core]

[memory]
wiki_auto_rebuild = true         # curator refreshes member pages after filings
wiki_rebuild_quiet_secs = 180    # how long the vault must stay quiet first
wiki_nightly = "03:30"           # nightly full rebuild, local time ("" disables)
```

Key things to know:

- **domain**: leave empty to start. Services are reachable via `<host>:<port>`. Set a domain later for pretty URLs like `photos.home.example.family`, served by the infra stacklet's proxy. It needs two DNS records pointing at the Mac's LAN IP, `*.<domain>` and `<domain>` itself, because the wildcard covers neither the bare name's DNS nor its certificate. Add both as DNS rewrites in AdGuard, and at your DNS provider for devices that bypass AdGuard. Domain mode makes the Mac the DNS server of your whole network and takes some networking knowledge to set up; read [stacklets/infra/README.md](../stacklets/infra/README.md) first.
- **dns_provider**: with a domain, `"hetzner"` or `"cloudflare"` serves everything over HTTPS, with certificates Caddy obtains through the provider's API. Empty serves plain HTTP. The API token is stored with `./stack infra dns-token`, never in `stack.toml`.
- **host**: the hostname used in port-mode URLs and hints. Empty = auto-detect the LAN IP (right for households: phones need it). Set to `localhost` for a single-machine setup, or to a fixed name like `mac-mini.local` if the LAN IP keeps shifting.
- **data_dir**: where all persistent data lives. Back this up. Outside the git repo.
- **language**: asked during install, proposed from your timezone. `[core] language` is the family's language: the bots answer in it, document categories are seeded in it, voice messages are transcribed in it, and the archivist writes every filing's title, summary and facts in it, whatever language the document or capture is in (the original stays untouched). A room can choose another language, or keep the source's, with `!config language <code>|source|default`. Changing it later seeds the other set of tags alongside the first with `./stack restart docs`; the first set stays. `[ai] language` is the language the stack speaks to you, the voice that reads answers aloud. The installer sets it to the family language; set it to `en` for an English voice in a German household. `stack doctor` warns when `[core] language` is missing, which only happens on installs from before the installer wrote it.
- **AI model**: installer picks one for your RAM tier. Alternatives are listed as comments in `stack.toml`. Switch by uncommenting a different line and running `./stack setup ai`.

### users.toml

The accounts: who can sign in, to what. Generated by the installer. User accounts are seeded on the first `./stack up` of each stacklet. The people the family knows are a separate list, the memory stacklet's `family/people/` (see [Memory](#memory-memory-optional)): a baby has no account but is family.

```toml
[[users]]
name = "Homer"
email = "homer@home.local"
role = "admin"

[[users]]
name = "Sarah"
email = "sarah@home.local"
role = "member"
```

Admins get accounts on every stacklet. Members get accounts on the stacklets listed in their `stacklets` field.

Two optional keys describe the family: `relation` (`parent`, `child`, `grandparent` or `other`) and `words` (`she` or `he`; leave it out to use the name only). `./stack family` shows the family as famstack reads it. `./stack family set --from family.toml` writes the whole family from one file, with the family's name, its own web address if it has one, and the people; the file's format is in [stack-reference.md](stack-reference.md#the-family-as-one-file-stack-family). It keeps what each person already has, and refuses to add, rename or remove people once the chat is set up.

### .stack/secrets.toml

Auto-generated passwords for service accounts. Created once, reused on every `./stack up`. Treat like a password manager export.

```bash
./stack config              # show resolved configuration
./stack config --secrets    # include generated passwords
```

### Email (`[mail]`)

> Needs the `core` stacklet running; `docs` + `code` to file what arrives.

famstack can watch IMAP mailboxes and deliver new mail into chat rooms. The mail bot (part of `core`) fetches; the archivist files the message and its attachments. Configure mailboxes in `stack.toml`:

```toml
[mail]
poll_interval = 120                # seconds between checks
filter_noise  = true               # drop newsletters/automated mail (default true)

[[mail.accounts]]
name      = "family"               # used for the secret key and provenance tags
imap_host = "imap.example.org"
imap_port = 993                    # 993 = IMAP over SSL (the default)
imap_user = "family@example.org"
ssl       = true                   # default; set false only for a local/test server
folder    = "INBOX"
room      = "!roomid:yourserver"   # where this mailbox delivers
since     = "2026-01-01"           # optional backfill floor (omit = whole folder)
```

The password never goes in `stack.toml` or chat. Put it in `.stack/secrets.toml`, keyed by the account name uppercased:

```toml
mail__FAMILY_IMAP_PASSWORD = "<app-password>"
```

Then `./stack restart core` to apply.

**Check the connection and find folder names:**
```bash
./stack core mail                 # all accounts: log in, count INBOX, list folders
./stack core mail --account work  # just one
```
This logs in read-only and prints the server's **real folder names** (Gmail's `[Gmail]/All Mail`, a localized `Gesendet`, nested paths), which often differ from the webmail labels. Use it to confirm the `folder` value points where you expect, and to debug a wrong host/password before pointing the bot at the mailbox.

Key things to know:

- **Invite both bots into the room.** `@mail-bot` delivers, `@archivist-bot` files. Type the handle into Element's invite box; fresh bot accounts don't show up in directory search.
- **Where mail files = who is in the room.** A room with two or more people files under the shared bucket; a private room or DM (one person) files under that person; a `Topic: ...` room files under the topic. Route work mail and family mail to different rooms to keep them separate.
- **App passwords.** Gmail, iCloud and most providers need an app-specific password with IMAP enabled, not your login password.
- **Backfill with `since`.** It is a floor by the date the server received a message. Start narrow (last week) to check it works, then widen (the whole year); widening re-scans and already-filed mail is skipped. Omit `since` to pull the entire folder on first run.
- **Noise filtering.** `filter_noise` (default on) drops automated and marketing mail before it reaches the room: newsletters and mailing lists (a `List-Unsubscribe` or `List-Id` header), `Precedence: bulk`, auto-replies (`Auto-Submitted`), and machine senders (`noreply@`, `mailer-daemon@`, `bounce@`). Detection is header-only, never body text, so personal mail that merely mentions "unsubscribe" is not dropped. Set `filter_noise = false` to ingest everything.
- **Read-only.** The fetcher never marks mail read or deletes it. It remembers what it has handled by Message-ID and IMAP UID, so restarts don't re-file.
- **Links are defanged.** URLs in mail are filed as non-clickable plain text, so a phishing link can't be tapped by reflex.

---

## Day-to-day operations

### Lifecycle

```bash
./stack up <stacklet>         # start (idempotent, regenerates .env)
./stack down <stacklet>       # stop (data stays)
./stack restart <stacklet>    # down + up
./stack destroy <stacklet>    # stop + delete data (asks for confirmation)
./stack uninstall             # destroy everything (use with care)
```

#### What happens when a stacklet is already starting?

Only one run of a stacklet goes on at a time. A **run** is one `up`, `down`, `restart` or `destroy` of a stacklet. A run can start from the terminal, the dashboard, the Server Room or the menu bar app. For example, a second `./stack up photos` while the first is still installing does not start:

```
  ✗  photos is already starting (from the terminal, since 18:40, pid 4711)

      Wait until that run ends, then try again.
      Follow its containers meanwhile: stack logs photos
```

The refused command changes nothing and exits with code 75. A script can wait and try again on that code.

A run of another stacklet waits until the current run ended, and then runs. Its first step says which run it waits for, in the terminal and on the stacklet's tile on the dashboard:

```
  •  Waiting for another run to finish: up photos (from the dashboard, since 18:40, pid 4711)...
```

A first start asks its setup questions before it waits. A run started in a terminal can still wait for an answer there, and every other run waits for it. `./stack doctor` names such a run, and any run that has gone on for more than five minutes.

A start, stop or restart from the Server Room does not wait. While another run goes on, it changes nothing and the room says which run goes on:

```
⏳ another run goes on: up docs (from the dashboard, since 18:40, pid 4711). Try again when it is done.
```

`./stack update` does not move the checkout while a start waits at its setup questions. It names that start and changes nothing.

Thus "Start all three" on the setup page starts the three stacklets one after the other. One run at a time is necessary, because the first start of a stacklet ends with a restart of core. During that restart, core's card door and the bot runner do not answer, and a run of another stacklet that used them failed. `./stack update` also waits until the current run ended.

Each run writes its steps to the run log of the stacklet, `.stack/runs/<id>.jsonl`. The log holds the current run or the last run, because a new run replaces it. The dashboard reads the log and shows each run on the tile of the stacklet, wherever the run started.

#### Which setups need the terminal of the Mac?

Some stacklets need a person at the Mac for their first start, because the setup asks questions and macOS asks for permissions. These stacklets are `ai` and `backup`. Run their first `./stack up` in the terminal of the Mac.

When you send such a first start in the Server Room, the server starts nothing. It answers with the command to run in the terminal:

```
backup needs its first setup at the Mac: it asks questions, and macOS asks for permissions along the way.
Run `stack up backup` in the terminal.
```

After the first setup, the Server Room starts and stops these stacklets as usual.

### Observability

```bash
./stack status                # what's running, what's healthy
./stack list                  # all stacklets with enabled/disabled state
./stack logs <stacklet>       # tail logs
./stack errors                # recent error logs across all stacklets
./stack host                  # disk, memory, uptime
./stack updates               # check for newer Docker images
```

### Scheduled commands

`./stack core schedule` runs a stacklet command at a time, in your timezone. It survives restarts: a time that passed while the Mac was off or asleep runs once within its grace (15 minutes by default), later it is logged as missed.

```bash
./stack core schedule add dates "daily 07:00" -- memory list show dates
./stack core schedule add tea "in 10m" -- ai say "The tea is ready"
./stack core schedule add dentist "at 2026-11-04 18:00" --title "Lisa's dentist tomorrow" --by homer --for lisa -- memory list show dates
./stack core schedule list              # what is due next, and the last result
./stack core schedule list --for lisa   # what is for Lisa; --by for what someone created
./stack core schedule log               # what ran, missed or failed
./stack core schedule remove tea
```

It runs inside core's API service, so it runs while core runs. `./stack doctor` says when it has stopped checking, or when runs were missed in the last day.

### Reading a web page

`./stack core web fetch <url>` reads one web page and shows it as Markdown. It uses the same code that the archivist uses for each link that a family member sends. Use it when a link was filed badly, to see what the stack got from the page.

To read a page, run:

```bash
./stack core web fetch https://example.com/recipes/greek-salad
```

To get the result as JSON, add `--json`. The JSON has the verdict, the tier, the title and the text.

The command reads only `http` and `https` addresses on the internet, as the archivist does. It refuses a `file:` address, because the bot runner can see the data of the family. It also refuses a page in the home network, because the bot runner is on the network of the stack. An example is the page of your router.

When the stack cannot read a page, the command shows the reason. Examples are bot protection and a login wall. This result is not an error, and the command exits with 0.

The command needs core, because it runs in the bot runner of core.

### Checking the Mac's firewall

`./stack core firewall` tells you if the firewall of the Mac keeps phones away from the chat. The setup page shows the same warning in the step "Chat on your phone".

To check the firewall, run:

```bash
./stack core firewall
```

Example output when the firewall blocks OrbStack:

```
The firewall of this Mac keeps phones out.
Fix: System Settings › Network › Firewall › Options: allow OrbStack
```

The firewall keeps phones out in three cases:

| Setting in the firewall options | Fix |
|---|---|
| "Block all incoming connections" is on | Turn off "Block all incoming connections". |
| OrbStack is set to block incoming connections | Allow OrbStack. |
| Downloaded signed software is not allowed automatically | Allow OrbStack. |

The command only reads the settings. It does not need an admin password, and it does not change the firewall. To get the result as JSON, add `--json`.

### AI & Config

```bash
./stack ai models             # available AI models in the backend
./stack ai switch <url>       # use another AI server; `managed` for the engine the stack runs
./stack ai say "<text>"       # speak a text aloud on this Mac; `--voice <name>` picks the voice
./stack config                # show resolved configuration
./stack config --secrets      # include generated passwords
./stack version               # print version
```

All commands output JSON when piped. Use `--json` to force it, `--pretty` to force human output.

### The menu bar app (beta)

A macOS menu bar app shows the stack at a glance: what needs you (with the
command that fixes it), each stacklet with start, stop, restart, open and
logs, errors from the last 24 hours, and the Mac's disk and memory. A
**Setup** tab shows the family, address mode, language, AI and data dir, and
opens `stack.toml` and `users.toml` for editing. It runs the `stack` CLI and
nothing else, so it starts no services and keeps no state of its own.

**Setup…** at the bottom of the panel opens the dashboard's setup page
(`http://127.0.0.1:42002/setup`, the Mac's address) in the browser, with the
[admin key](#what-is-the-admin-key). The item is available only
for the stack on this Mac and only while core runs, because core serves the
page.

Install it from the checkout. It needs macOS 14 and the Command Line Tools,
which Homebrew already installed:

```bash
./stack app install
```

That builds the app (about a minute the first time), puts it in
`/Applications` (or `~/Applications` when that is not writable) and opens it.
The app is signed for the Mac it was built on, so install it on the Mac that
runs it. After `./stack update`, run `./stack app install` again to update the
app; `update` reminds you when the app is installed.

**Which machine.** **Setup → Connection** switches between this Mac and
another one:

- **This Mac**: the checkout you run `./stack` from (`~/famstack` unless you
  choose another with **Change…**).
- **Remote**: the server Mac over SSH, from a laptop for example. Give its
  SSH host (an alias from `~/.ssh/config` or `user@host`) and its checkout
  path. The server needs **Remote Login** on (System Settings → General →
  Sharing), and your SSH key must work without a password prompt. Logs and
  editing open Terminal with an SSH session to the server.

Against a checkout older than 0.4.0 the app leaves out the sections that need
newer commands (doctor, errors, memory, setup) and says so.

---

## Backups

This is the part everyone skips and regrets. famstack ships an opt-in backup stacklet that covers the irreplaceable data. That is the photo originals, the scanned documents, everything in your family chat including the voice messages, and the family memory with its diary and lists. Immich's and Paperless's databases are not covered yet, and neither is Forgejo as a whole (its database, its users and any other repositories). Layer it with Time Machine or a periodic tar if you want full coverage today.

### The backup stacklet

Run `stack up backup`. You need an APFS-formatted external drive plugged in. The setup wizard asks for the disk name, encryption (default: plain APFS), and a nightly time (default 02:00). It then installs a cron entry that runs `stack backup sync` at that hour, and walks you through granting Full Disk Access to `/usr/sbin/cron` in System Settings. The FDA grant is what lets the scheduled sync reach the archive disk; without it, cron jobs are sandbox-blocked from `/Volumes/*` on macOS Catalina and later. One drag-and-drop, then every cron job on this Mac inherits the access.

**What gets backed up**

| Source | Path on the archive disk |
|---|---|
| Immich photo originals | `/Volumes/<disk>/data/photos/library/` |
| Paperless archived PDFs | `/Volumes/<disk>/data/docs/media/` |
| Matrix uploads: voice messages, photos, files | `/Volumes/<disk>/data/messages/media/` |
| Matrix timeline, as dated snapshots | `/Volumes/<disk>/data/messages/synapse/` |
| Family memory: cards, diary, lists, as dated git bundles | `/Volumes/<disk>/data/memory/vault/` |
| Files behind memory pages | `/Volumes/<disk>/data/memory/media/` |

Immich's and Paperless's Postgres databases are still not covered. You get those files back but lose albums, tags, custom fields and saved views. They use the same snapshot mechanism the chat server already uses, so wiring them up is a small change rather than a new design.

**Disks from an earlier release**

Backups written by 0.3.0-beta.3 and earlier used one flat directory per source, `data/photos-library/` where this release writes `data/photos/library/`. Both layouts are read, and a sync keeps using whichever directory it finds, so leaving it alone is a valid choice. To move to the current layout, connect the disk and run `stack backup migrate`. It renames the directories in place, so nothing is copied however much is on the disk, and the files keep the immutable flag that makes the archive append-only. Add `--dry-run` to see what it would do first.

**How the protection works**

Every file written to the archive gets the kernel `uchg` flag. macOS refuses to modify or delete uchg files, even with `sudo`. `rsync --ignore-existing` means files already in the archive are skipped on every run, so the backup is append-only by design and accidental `rm -rf` on your main system cannot propagate.

A second check watches the sources themselves. Before each run the engine compares every source against **the number of files it held on the previous run**, recorded in its own history. A source that is suddenly empty, or that has lost more than half its files, aborts the whole run so you look before anything else happens. There is nothing to configure: the baseline is the source's own past, so it means the same thing whether you have fifty files or five hundred thousand. A source that has never had any data, like the chat media store before anyone sends a photo, is simply skipped and reported.

A **canary file** is a tripwire (named after the canary miners used to take underground to detect bad air). famstack plants a small file with known contents inside `~/famstack-data`; before every sync the engine reads it and refuses to proceed if the contents have changed. If something has been encrypting or modifying files under the data directory, the canary will not match what was planted and the sync aborts before opening the archive, so the corrupted state cannot propagate.

**Database snapshots**

Files that never change are easy: rsync copies them and the kernel locks them. A database is the opposite. Its files change under you continuously, and a copy taken mid-write will not restore. So databases are dumped instead.

Before each sync the backup stacklet runs `pg_dump` against every database that declares itself, and packs the dump into one dated `.tar.gz`. `pg_dump` takes its own consistent view, so nothing stops and nobody gets logged out while it runs. The tarballs then ride to the archive disk as an ordinary append-only source: each run adds one, no run ever touches an older one.

The chat server is the first thing wired up, and it is the one that matters most. The media store holds your family's actual voice recordings; the database is what makes them messages. Without it you would restore a folder of anonymous audio blobs with no sender, no room and no date.

A Synapse snapshot is around 200 KB and contains:

| File | Why |
|---|---|
| `synapse.sql` | The whole timeline: rooms, messages, who said what when |
| `homeserver.yaml` | Config, and the secret that keeps existing logins valid |
| `*.signing.key` | The server's identity |
| `MANIFEST.json` | What this file is, what produced it, and how to put it back |

The manifest also records the exact image versions and digests that were running when the snapshot was taken. A dump only restores into something compatible with what wrote it, and the failure is not subtle: a Paperless 3.x database will not boot under 2.x, and there is no downgrade. The digest matters more than the tag, because `latest` names a different image every month and nothing identifiable in five years. Nothing reads this yet. It is recorded now because it is the one part of a snapshot that cannot be added afterwards.

Those config files carry live secrets, including the database password. That is deliberate, because a dump without them restores a server nobody can log into, and it is one more reason the archive disk is a physical object you keep somewhere safe.

The last seven tarballs are kept on the internal disk; the archive disk keeps every one ever made. Pruning locally is safe precisely because the archive never deletes.

**The family memory**

The family memory is a git repository. The memory curator keeps a clone of it on this Mac and pushes to Forgejo. The backup takes a snapshot of the clone each night, because both copies are on the internal disk. The snapshot holds a git bundle of the whole history, including changes that wait for Forgejo because Forgejo was down.

A night with no new change writes no new snapshot, and the sync output says the memory is unchanged. Restore is manual. Unpack the newest tarball and run `git clone vault.bundle vault`. `MANIFEST.json` holds the same command, and its `refs` show what each remote had when the snapshot was taken. Point the clone's `origin` at Forgejo before you push, because after the clone it is the bundle file.

**Restoring the chat server**

Recover the snapshot and the media together, and do the database first. Media the database does not know about is harmless; a message whose recording is missing is broken.

```bash
# 1. Take the snapshot off the archive disk and unpack it
sudo chflags nouchg /Volumes/<disk>/data/messages/synapse/synapse-<date>.tar.gz
cp /Volumes/<disk>/data/messages/synapse/synapse-<date>.tar.gz ~/
tar xzf ~/synapse-<date>.tar.gz -C ~/restore/

# 2. Stop the homeserver so nothing writes while you work
./stack down messages

# 3. Put the database back
docker start stack-messages-db
docker exec stack-messages-db dropdb -U synapse --if-exists synapse
docker exec stack-messages-db createdb -U synapse synapse
docker exec -i stack-messages-db psql -U synapse -d synapse < ~/restore/synapse.sql

# 4. Put the config and identity back
cp ~/restore/homeserver.yaml ~/restore/*.signing.key \
   ~/famstack-data/messages/synapse/

# 5. Put the recordings back
sudo chflags -R nouchg /Volumes/<disk>/data/messages/media/
cp -R /Volumes/<disk>/data/messages/media/* \
   ~/famstack-data/messages/synapse/media_store/local_content/

./stack up messages
```

`MANIFEST.json` inside every tarball repeats the essentials, so this works even years from now with no famstack and no documentation to hand.

**Daily operation**

```bash
stack backup sync         # run a sync now (from any context)
stack backup status       # last run, source counts, current mount state
stack down backup         # remove the cron entry; canary, logs and archive contents stay
stack destroy backup      # also remove BACKUP_DATA_DIR; archive disk and Keychain are preserved
```

The scheduled nightly run leaves the disk mounted between runs (cron cannot trigger eject under the macOS sandbox; files are kernel-locked regardless). Manual `stack backup sync` from Terminal does eject when it finishes. Results post to the `#famstack` Matrix room via stacker-bot.

**Recovery (no special tooling needed)**

Plug the archive disk into any Mac and browse the files in Finder. To copy locked files out:

```bash
sudo chflags -R nouchg /Volumes/<disk>/data/photos/library/<folder>/
cp -R /Volumes/<disk>/data/photos/library/<folder>/ ~/recovered/
```

A `stack backup restore` command and `on_restore` hooks for database recovery are planned but not yet shipped.

**What it protects you from**

| Threat | Covered |
|---|---|
| Ransomware encrypts your Mac | Yes (uchg + canary) |
| Accidental `rm -rf` on `~/famstack-data` | Yes (rsync never deletes from the archive) |
| You delete a photo on your phone | Yes (Immich propagates the delete to disk; the archive keeps the original) |
| Vault drive stolen from your house | Only if you opted in to APFS encryption |
| Vault drive hardware failure | No (single physical copy; offsite engine planned) |
| Fire or flood | No (archive is in the same building; offsite engine planned) |

**Limitations to know about**

Only APFS or HFS+ disks attached via USB or Thunderbolt. Network shares (SMB/NFS/Synology) are refused at probe time because the kernel cannot enforce `uchg` over a network filesystem. One target only; encrypted offsite via restic is planned, not shipped.

### Everything else

`~/famstack-data` holds every byte of user data. Even with the backup stacklet running you may want Time Machine or `restic` against this directory for full coverage (databases, AI model caches).

What is **not** in `~/famstack-data` and needs separate handling:

- `stack.toml`, `users.toml`, `.stack/secrets.toml` in the repo. Small but irreplaceable. Gitignored on purpose, so they will not survive a fresh `git clone`.
- oMLX models (in `~/.omlx/models`). Re-downloadable.

A 30-second one-liner for the config:

```bash
tar czf famstack-config-$(date +%F).tgz stack.toml users.toml .stack/
```

Stash the tarball on a USB stick or in 1Password.

---

## Updating

Container images update themselves nightly at 3am via Watchtower. You do nothing for that. Each famstack release pins every service to a version line, so Watchtower brings in bug-fix releases, and a new minor or major version of Immich, Paperless or Synapse arrives with a famstack update, not overnight.

The famstack code is a git checkout, and a release is a tag. Updating means moving your checkout to a newer tag.

```bash
./stack update
```

That is the whole thing from v0.4.0-rc.1 onward. It fetches the releases, shows what the jump changes, moves the checkout, and sets your local edits aside and puts them back.

It does not restart anything. New code on disk is not new code running: a stacklet picks up a release when its containers are recreated, and when your family loses a service for thirty seconds is your call, not a side effect of updating. So the command works out which restarts the release actually earns and prints them:

```
  ✓  Updated to v0.4.0-rc.2

  Restart to pick it up
    ./stack restart docs
    ./stack doctor
```

```bash
./stack update --dry-run         # show the plan, change nothing
./stack update v0.4.0-rc.2       # a specific release instead of the one offered
./stack update --yes             # skip the confirmation
```

Pre-releases (`v0.5.0-beta.1`) are for testing the next release. On a final release you are never offered one: `./stack update` and `./stack doctor` look at final releases only, so with `v0.5.0-beta.1` tagged an instance on `v0.4.1` is up to date, and a `v0.4.2` would be offered. To test a pre-release, name it: `./stack update v0.5.0-beta.1`. From then on you are offered the newest release of all, each beta and then the final, which puts you back on final releases only.

Every form fetches tags first, including `--dry-run`: without it the answer
would only be as fresh as your last fetch. It opens with the version and,
once it knows, the move it is proposing:

```
  famstack 0.4.0-rc.1
  Fetching releases...

  Update  v0.4.0-rc.1 → v0.4.0-rc.2
```

A stacklet is named only if the release changed files inside it and it is running, so a documentation release asks for nothing. If the release changed anything under `lib/`, every running stacklet is named, because that is the code they all share.

Run `./stack restart` before you start other stacklets. Until then, `./stack up <id>` does not recreate core. The reason is that core's new containers can need a port that an old container still holds. It prints this line instead:

```
  ⚠  The update is not applied yet. Run: ./stack restart
```

### Updating by hand

`stack update` ships in v0.4.0-rc.1. Moving *to* that release, or moving between older ones, is the same steps typed out:

```bash
cd ~/famstack
git fetch --tags
git tag | tail -5              # what is available
git checkout v0.4.0-rc.1       # the one you want
./stack doctor
```

Then restart what the release touched. This prints the list:

```bash
git diff --name-only v0.3.0-beta.3 v0.4.0-rc.1 -- stacklets/ | cut -d/ -f2 | sort -u
```

Restart any of those you run. If the same command against `lib/` prints anything, the framework itself changed and everything wants a restart:

```bash
./stack down all && ./stack up all
```

Nothing in the update touches your instance. `stack.toml`, `users.toml`, `.stack/`, `~/famstack-data/` and `~/famstack-extensions/` are all outside git's reach, so your config, secrets, data and your own stacklets come through a tag switch untouched.

### If you have edited files in the checkout

`stack update` handles this for you. It lists the files it is about to set aside, warns in advance when one of them is a file the release also changes, and puts them back afterwards.

If they collide, it winds the whole update back: you end up on the release you started from, with your edits exactly where they were and nothing left in the stash. Then it tells you how to go ahead deliberately:

```
  ⚠  Your edits collide with v0.4.0-rc.2. Nothing changed.
      Back on v0.4.0-rc.1 with your edits where they were.
      The collision is in:
      • stacklets/docs/docker-compose.yml
      To take the release anyway, deal with that file first:
      git checkout -- <file>   drop your version for the release's
      git stash                keep it, then pop and merge by hand
      then run ./stack update again.
```

A half-applied update is worse than no update: a compose file with conflict markers in it does not parse, so the services you restart next fail for a reason that has nothing to do with the release. What follows is the same work done by hand, which is what an older release needs.

git refuses to move while one of your edits is in the way:

```
error: Your local changes to the following files would be overwritten by checkout:
	stacklets/docs/docker-compose.yml
Please commit your changes or stash them before you switch branches.
Aborting
```

Set them aside, switch, put them back:

```bash
git stash
git checkout v0.3.0-beta.3
git stash pop
```

If the release did not touch the same file, `git stash pop` re-applies your edit and you are done.

If the release changed the same lines, it reports a conflict and **keeps your stash**, so nothing is lost:

```
CONFLICT (content): Merge conflict in stacklets/docs/docker-compose.yml
The stash entry is kept in case you need it again.
```

From there, look at what your edit was, then keep it or drop it:

```bash
git stash show -p                    # your change

# keep it: edit the file to merge both sides, then
git add stacklets/docs/docker-compose.yml && git stash drop

# or take the release's version and give up your edit
git checkout HEAD -- stacklets/docs/docker-compose.yml && git stash drop
```

Editing a shipped stacklet means doing this on every update. There is no override mechanism: a stacklet in `~/famstack-extensions/` can add to what famstack ships, not replace it. If your change is one other families would want, an issue or a pull request costs you less in the long run.

### Knowing where you are

```bash
git describe --tags        # v0.3.0-beta.3, or a tag plus commits past it
```

Checking out a tag leaves you in "detached HEAD". That is normal and nothing to repair, but it does mean `git pull` no longer works:

```
$ git pull
You are not currently on a branch.
Please specify which branch you want to merge with.
```

Do not take git's suggestion to run `git pull origin main`. It succeeds, and it quietly moves you off releases onto unreleased development code while `./stack version` carries on reporting the last release number. Use `git fetch --tags` and `git checkout <tag>`.

If you cloned and never checked out a tag, you are on `main`, which is development. `git pull` there gives you unreleased work rather than the next release. `./stack update` moves you onto the newest release and says so before it does: your branch is left where it is, and `git switch main` goes back to it. If `main` already contains the newest release, which it will if you pulled recently, it tells you that instead of moving you backwards.

**On a fork**, `origin` is your copy and has none of the project's releases in it. `./stack update` fetches tags from every remote, so add the project as one:

```bash
git remote add upstream https://github.com/famstack-dev/famstack.git
./stack update
```

Without it there is nothing to update to, and the command says which remotes it looked at.

Pre-1.0 caveat: occasionally a release changes a config schema or env var. Release notes say so. Read them before updating.

### Paperless-ngx 3.x (docs stacklet)

This release moves the `docs` stacklet from Paperless-ngx 2.20.15 to 3.0.4.

**Back up `~/famstack-data/docs/` before you restart the stacklet.** Paperless migrates its database the first time 3.x starts, and 2.x refuses to boot on a migrated database. There is no downgrade. If the upgrade goes wrong, restoring that backup is the only way back.

```bash
./stack down docs
tar -czf ~/docs-pre-3x-backup.tar.gz -C ~/famstack-data docs
git fetch --tags && git checkout v0.3.0-beta.3
./stack up docs
```

The backup stacklet does not cover this yet. It archives Paperless's files but not its Postgres database, so a `stack backup sync` alone will not get you back to 2.x. (Database snapshots exist and are wired up for the chat server; Paperless is next.)

**If Watchtower already moved you to 3.x.** Older famstack releases tracked the `:latest` Paperless tag, and Watchtower's nightly pull rolled some instances from 2.x straight to 3.0 without asking. If that happened to you, your database is already migrated and pinning the image back to 2.20.15 will not start. You need a backup taken before the 3.x boot. Without one, staying on 3.x is the only option, which this release makes the supported path. Paperless is now pinned to an exact tag, so Watchtower can still deliver 3.0.x patches but can no longer jump a major version on its own.

**One behaviour change worth knowing.** 3.0 stopped rejecting a re-uploaded identical file by default and would have filed a second copy instead. famstack sets `PAPERLESS_CONSUMER_DELETE_DUPLICATES` to keep the old behaviour: send the same receipt twice and the archivist replies "already filed", with a link to the document you sent the first time.

---

## Uninstall

```bash
./stack uninstall
```

Stops every stacklet and deletes its data. Asks for confirmation. There is no undo. It also removes the `famstack` command the installer put on your PATH, and nothing else of that name.

For a single stacklet:

```bash
./stack destroy photos
```

`./stack destroy ai` removes famstack's oMLX service and its folder, `~/famstack-data/ai/omlx`. The Homebrew program stays, because something other than famstack can use it. To remove it, run:

```bash
brew uninstall omlx
```

Whisper.cpp is built inside `~/famstack-data/ai/`, so `./stack destroy ai` removes it. The launchd plist is unloaded too.

---

## Troubleshooting

### "Docker is not running"

Open OrbStack from Applications or the menu bar. Wait for the icon to turn green. Re-run `./stack`.

If both Docker Desktop and OrbStack are installed, the installer prefers OrbStack and warns if it had to fall back. To switch fully, quit Docker Desktop and uncheck "start at login" in its preferences.

### "Port 42030 already in use"

Find the offender:

```bash
lsof -nP -iTCP:42030 -sTCP:LISTEN
```

Stop it, or change the port mapping in `stacklets/<name>/docker-compose.yml`.

### Element shows "this browser is not supported"

The browser is fine; the address is the problem. Element needs HTTPS or `localhost`, and in port mode the Mac's LAN address is plain HTTP. "Continue anyway" then leads to a page that never finishes loading.

- On the Mac that runs famstack: open `http://localhost:42030`.
- On any other computer: use the Element desktop app, see [Element (other computers)](#element-other-computers).
- For Element in the browser everywhere: domain mode with HTTPS (`dns_provider`).

### Phones cannot reach the server

In port mode, make sure the Mac and phone are on the same network. Find the LAN IP:

```bash
ipconfig getifaddr en0     # Wi-Fi
ipconfig getifaddr en1     # Ethernet
```

Use `http://<that-ip>:42031` for Element X, `http://<that-ip>:42010` for Immich.

If the IP keeps moving, set a DHCP reservation on the router (see [Before you start](#before-you-start-stable-lan-address)).

### AI install fails on whisper.cpp build

Almost always missing Xcode Command Line Tools. Run:

```bash
xcode-select --install
```

Then `./stack up ai` again. The installer is idempotent.

### "Homebrew not found" but I have it installed

Your shell `$PATH` does not include Homebrew. On Apple Silicon, Homebrew lives at `/opt/homebrew/bin/brew`. Add to `~/.zshrc`:

```bash
eval "$(/opt/homebrew/bin/brew shellenv)"
```

Open a new terminal.

### LLM is slow or runs out of memory

Edit `[ai] default` in `stack.toml` to a smaller model (alternatives are listed as commented lines), then:

```bash
./stack setup ai
./stack ai download <new-model-id>
```

If 32 GB still swaps, close Slack, Chrome and anything else with 50 tabs. Local AI is a rude housemate.

### Out of disk space

```bash
./stack host
```

Shows free space. If the photo library filled the disk, move `data_dir` to an external SSD: edit `stack.toml`, stop photos, move the directory, restart.

### Want to start fresh

```bash
./stack uninstall
```

Removes everything. Only use this if you really mean it.

### Email: can't connect, or mail isn't filed

Run the diagnostic first. It logs in and lists the real folders:

```bash
./stack core mail
```

- **"connection failed" / "authentication failed".** Use an **app password** (not your login password) with IMAP enabled at the provider, and check `imap_host`/`imap_port` (993 for SSL). The `credential:` line reads `(EMPTY …)` when the secret-store key doesn't match the account name; it must be `mail__<NAME>_IMAP_PASSWORD` (account name uppercased) in `.stack/secrets.toml`.
- **Mail shows in the room but never gets filed.** The **archivist must be a member of that room**. Invite `@archivist-bot` by typing the handle into Element's invite box (bot accounts don't show in directory search).
- **Edited `stack.toml` but nothing changed.** `stack core mail` and the bot read the *rendered* config, which only refreshes on `./stack restart core`. Restart after any `[mail]` change.
- **Wrong or empty folder.** IMAP folder names differ from the webmail labels (Gmail's `[Gmail]/All Mail`, a localized `Gesendet`). `stack core mail` lists the exact names to copy into `folder`.
- **A whole inbox of old mail flooded in.** Set a `since` date floor and start narrow (last week), then widen it. With `since` unset, the first poll takes the whole folder.
- **Wanted mail is missing.** `filter_noise` (default on) drops newsletters and automated mail; set `filter_noise = false` to keep everything.
- **Threads render oddly on mobile.** Element X needs **beta features** enabled (Settings, then Labs) for threads.

### TCP connections stuck in TIME_WAIT (macOS)

After ~50 days of uptime, new TCP connections start failing or the server feels sluggish despite low CPU. `netstat` shows thousands of TIME_WAIT connections that never expire:

```bash
netstat -an | grep TIME_WAIT | wc -l
```

If this returns thousands (10k+), you've hit a [known XNU kernel bug](https://photon.codes/blog/we-found-a-ticking-time-bomb-in-macos-tcp-networking): a 32-bit unsigned integer overflow in `tcp_now` freezes the internal TCP timestamp clock after 49 days 17 hours 2 minutes 47 seconds of continuous uptime. Once frozen, TIME_WAIT connections never expire.

**Diagnose:**

```bash
# Check uptime — the bug triggers after ~49.7 days
uptime

# Confirm TIME_WAIT pile-up
netstat -an | grep TIME_WAIT | wc -l

# Check boot time precisely
sysctl kern.boottime
```

**Fix:** Reboot the Mac. No sysctl, no network restart, no OrbStack restart will clear it — only a full system reboot resets the kernel counter.

**Prevent:** Reboot the server once a month. A simple cron job as a reminder:

```bash
# Weekly uptime check — warns at 40 days so you can plan a reboot
(crontab -l 2>/dev/null; echo '0 9 * * 1 uptime_days=$(($(( $(date +\%s) - $(sysctl -n kern.boottime | grep -o "sec = [0-9]*" | grep -o "[0-9]*") )) / 86400)); [ $uptime_days -ge 40 ] && osascript -e "display notification \"Uptime: ${uptime_days} days — reboot soon (XNU TCP bug at 49d)\" with title \"famstack\""') | crontab -
```

After reboot, all containers come back automatically (OrbStack starts at login and restarts containers with `restart: unless-stopped`).

### Something else

```bash
./stack logs <stacklet>
./stack errors
```

Open an issue with that output, or paste it into [Discord](https://discord.gg/hfutdmmfBe). Better with `./stack status` attached.

---

## Community

famstack is a small project run by a small group of people who care about it. The faster the community grows, the faster the project gets better. Stop by, say hi, bring your weird homelab questions:

- **[Discord](https://discord.gg/hfutdmmfBe)**: where the day-to-day conversation happens. Install help, "is this normal?" questions, what other families are doing with their stacks.
- **[GitHub](https://github.com/famstack-dev/famstack)**: bug reports, feature requests, pull requests. A star is the single biggest signal we have to know whether to keep going.
- **[Bluesky](https://bsky.app/profile/famstack.dev)** and **[X](https://x.com/arthwaredev)**: release notes, benchmarks, occasional opinion.

If famstack saved you from another year of manually copying photos off your phone with a cable, the kindest thing you can do is tell one other family about it. That is how this kind of project survives.

---

## More docs

- [User Guide](user-guide.md): what your family does in chat. Send them this one.
- [Stack Reference](stack-reference.md): how stacklets are wired together, lifecycle, hooks, secrets.
- [Creating Stacklets](creating-stacklets.md): build your own stacklet.
- [Documentation index](README.md)
