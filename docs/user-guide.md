# User Guide

famstack is an opinionated, batteries-included stack built on established open source projects (Immich, Paperless, Matrix, MLX). The glue is the part that matters: everything is wired together and reachable through chat, so your family actually uses it instead of just you.

This guide covers everything: hardware, install, the stacklets, day-to-day operations, troubleshooting. One page on purpose. Search it with `Cmd+F`.

If you get stuck, jump into [Discord](https://discord.gg/hfutdmmfBe) or open an issue on [GitHub](https://github.com/famstack-dev/famstack/issues).

[![famstack install walkthrough on YouTube](https://img.youtube.com/vi/2trwwigKyEY/maxresdefault.jpg)](https://www.youtube.com/watch?v=2trwwigKyEY)

Prefer to watch? [Full install walkthrough on YouTube](https://www.youtube.com/watch?v=2trwwigKyEY): 19 minutes, clone to a working document archive with mobile access. The whole [famstack channel](https://www.youtube.com/@famstacker) has the short clips too.

---

## Quick Start

If you already have [Homebrew](https://brew.sh):

```bash
git clone https://github.com/famstack-dev/famstack.git
cd famstack
./stack
```

The installer asks four or five questions, brings up Matrix and Element, and prints a sign-in URL. Open it and you have a working family chat.

Everything else is one command at a time:

```bash
./stack up photos      # Immich (photo library, mobile backup)
./stack up docs        # Paperless-ngx (document archive with OCR)
./stack up ai          # Local AI (oMLX + Whisper + Piper TTS)
./stack up chatai      # Open WebUI (skip on 16 GB Macs)
./stack up code        # Forgejo (private git server)
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

famstack runs on Apple Silicon. Whisper, Piper and oMLX all use Metal GPU acceleration. Intel Macs technically run Docker, but the AI parts will be unusably slow.

| Resource | Minimum | Recommended | Why |
|----------|---------|-------------|-----|
| CPU | Apple Silicon (M1) | M2 or newer | Metal GPU is mandatory for the AI stacklet. |
| RAM | 16 GB | 32 GB or more | See the RAM note below. |
| Disk | 60 GB free | 256 GB+ free | Software is small. Photo library and AI models are not. |
| Network | Local LAN | Wired ethernet for the host | Stable host link helps phone uploads. |

### A note on RAM

The AI stacklet picks an LLM at install time based on `sysctl hw.memsize`:

| RAM | Default model | Notes |
|-----|---------------|-------|
| 48 GB or more | Qwen3.5-35B-A3B 8-bit | Best quality, including for German. |
| 36 GB or more | Qwen3.5-35B-A3B 4-bit | Strong all-rounder. |
| 16 GB | Qwen3.5-9B 4-bit | Lightweight. English is fine. German output is noticeably weaker, especially on document classification with long, formal text. |

If your household uses German (or any non-English language) and you care about how documents get tagged, transcribed and summarized, 32 GB is the sweet spot. 16 GB works but you will feel the difference. You can always switch models later by editing `[ai] default` in `stack.toml` and running `./stack setup ai`.

The `chatai` stacklet (Open WebUI) is also memory-hungry. On 16 GB Macs, skip it. Local AI still works for document classification and voice transcription without a chat UI.

### A note on disk

What actually fills the disk:

- Docker images: roughly 5 to 8 GB across all stacklets.
- Whisper model: 1.5 GB (large-v3-turbo).
- LLM model: 2.5 GB (9B 4-bit) up to 25 GB (35B 8-bit).
- Photo library: depends on you. Plan for what you would back up to iCloud, plus about 20 percent overhead for Immich thumbnails.
- Documents: small. Even 10 years of household paperwork rarely exceeds a few GB after OCR.

`data_dir` defaults to `~/famstack-data` but accepts any path. If your internal SSD is tight, point it at an external drive in `stack.toml` before running `./stack up photos` for the first time.

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
| 42010 | photos (Immich) |
| 42020 | docs (Paperless-ngx) |
| 42030 | messages (Element web) |
| 42031 | messages (Synapse, Matrix homeserver) |
| 42040 | code (Forgejo) |
| 42050 | chatai (Open WebUI) |
| 42060 | ai (oMLX) |
| 42062 | ai (Whisper) |

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
git checkout v0.2.1
```

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

1. **Checks Homebrew.** If missing, prints the install one-liner and waits.
2. **Checks Docker.** If missing, offers to install OrbStack via Homebrew. If installed but not running, asks you to start it and waits.
3. **Asks for a family name.** Becomes your Matrix server identity. Permanent. Pick something short and lowercase-friendly. `mueller` is fine; "The Müller-Schmidt Family" gets sanitized to `mueller-schmidt`.
4. **Asks for your first name.** Becomes the admin account on every stacklet. Default password is your first name in lowercase. Change it at first login.
5. **Asks for additional family members.** Empty input ends the loop. Each member gets their own account on each stacklet.
6. **Writes config.** Three files appear:
   - `stack.toml`: central config, gitignored, yours to edit.
   - `users.toml`: family roster, gitignored.
   - `.stack/secrets.toml`: auto-generated passwords for service accounts. Treat like a password manager export.
7. **Brings up `messages` and `core`.** Pulls Synapse, Element and Postgres images (about 600 MB), starts them, creates Matrix accounts, seeds two default rooms (`#famchat`, `#famstack`).
8. **Prints a sign-in URL.** Something like `http://192.168.1.42:42030`.

Total time on a fresh Mac with a decent connection: 5 to 10 minutes.

If something fails partway through, fix the underlying issue (usually Docker not running) and run `./stack` again. The installer is idempotent.

### 4. Sign in (browser)

Open the URL the installer printed. You should see Element. If your browser warns "this browser is not supported", click Continue anyway. Element works in Safari, Chrome, Firefox and Edge despite the warning.

- **Username**: your first name, lowercase.
- **Password**: same as the username.

Change the password right after first login (Element > Settings > Account).

You will see two rooms:

- `#Family Chat`: where the family talks. Photos, voice memos, life.
- `#Server Room`: where the server talks back. Status, alerts, install confirmations.
- `#Memories`: a place to store your memories, voice diaries, stories, funny moments.

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

   That is the Synapse port (`42031`), not the Element web port (`42030`). Phones talk to Synapse directly.

3. Element X warns the connection is not HTTPS. Correct. famstack runs unencrypted on your LAN by default, because trusting your own router is fine and Let's Encrypt does not issue certs for `192.168.x.x`. Confirm and continue. (For HTTPS, set up `domain` mode and Caddy will issue certs. See [stack-reference.md](stack-reference.md).)
4. Username: first name, lowercase. Password: same, unless changed in the browser.
5. Set up encryption. First device on the account: **Reset identity**. Subsequent devices: **Verify** with the QR-code flow from an already-signed-in session.
6. Allow notifications when prompted. Without this, you do not get pings for new messages.

Verify: send a message from the phone. It should appear in the browser session within a second. Reverse it. If both directions work, you are done. If only one direction works, it is almost always a notification permission issue, not a Matrix issue.

Repeat for every family member. Each one logs in with their own first-name account.

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

## Stacklets

Each stacklet except `core` and `messages` is opt-in. Mix and match as you like. `messages` is technically optional, but without it the whole point of famstack (operating from your phone) goes away.

famstack also ships a lightweight bot runtime. Bots are tiny helpers that automate things in your family chat: filing documents, transcribing voice memos, managing the stack itself. Some are also experimenting with [nanobot](https://github.com/HKUDS/nanobot)-based agent flows.

### Messages (`messages`)

Matrix homeserver (Synapse) plus the Element web client. Your family's private chat, and the messaging backbone of famstack.

**Why Matrix?** Element X has native apps for every platform: iOS, Android, macOS, Windows, browser. Install once per device and forget about it. Matrix was built for federated public servers with full E2E encryption; we do not need any of that for a local setup, and the config options are complex. famstack hides that complexity. We did not find a better free messenger covering all platforms. (See [ADR-004](adr/adr-004-messaging-backend-and-abstraction.md).)

```bash
./stack up messages    # set up by the installer, usually already running
```

The installer creates `#famchat` and `#famstack` and seeds an account for every family member. Default password is the first name in lowercase. Change it after first login.

| | |
|---|---|
| Element web port | `42030` |
| Synapse port (mobile clients) | `42031` |
| Data | `~/famstack-data/messages/synapse/`, `~/famstack-data/messages/postgres/` |

Useful commands:

```bash
./stack messages users
./stack messages send <room> "<message>"
./stack messages room list
./stack messages setup
```

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

### Documents (`docs`)

Paperless-ngx archive with OCR and structured classification. Receipts, letters, contracts, tax documents. Search across everything by content.

**Why Paperless?** Gold standard for self-hosted document management. Mature project, large community. famstack makes the document inbox available on every phone through the chat: photograph a receipt, send it to the `#documents` room, and it gets filed.

```bash
./stack up docs
```

The archivist bot creates a `#documents` room in your chat. AI classifies and tags incoming documents when the `ai` stacklet is running. Without `ai`, documents are still stored and searchable by OCR text, just not auto-tagged. Type `show 42` in `#documents` to read a document; type any term to search.

On first setup, famstack seeds Paperless with common document categories and types in your configured language. The LLM picks from these when classifying, so tags stay consistent. See [`stacklets/docs/taxonomy.toml`](../stacklets/docs/taxonomy.toml).

**Mirror to git (beta).** Flip `mirror_to_git = true` in `stacklets/docs/bot/bot.toml` to get a markdown mirror of every filed doc in your Forgejo repo (`family/documents`). Paperless stays the canonical store; the mirror is the browsable, git-versioned human view. Requires the `code` stacklet.

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

First-run setup takes 10 to 20 minutes:

1. Asks managed oMLX (default, recommended) or external OpenAI-compatible endpoint.
2. Installs oMLX via Homebrew.
3. Installs `cmake` and `ffmpeg` if missing.
4. Clones and builds whisper.cpp with Metal support (1 to 2 minutes of compilation).
5. Downloads the Whisper large-v3-turbo model (1.5 GB).
6. Downloads the LLM picked for your RAM tier (2.5 to 25 GB).
7. Starts oMLX as a Homebrew service, Whisper as a launchd service.
8. Brings up the Piper TTS container.

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
./stack setup ai
```

To switch LLM models: edit `[ai] default` in `stack.toml` (alternatives are listed as commented lines), then `./stack setup ai`.

### ChatAI (`chatai`) optional

Open WebUI: a ChatGPT-style interface for your local LLM.

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

---

## The Memories Room

One of the most valuable things you can do with famstack has nothing to do with code.

The Memories Room is a place to record your family's life. Voice messages, photos, text. We record a voice diary once or twice a week at the dinner table: what was funny, what was special, what the kids want to tell their future selves. Holiday diaries, first days at school, bedtime stories in their own words.

Start collecting these. They become valuable just as they are. One of the upcoming famstack updates will use local AI to make memories searchable: "Remember? One year ago...", "Sarah's third birthday...". Everything stays on your Mac. Your memories are just yours. That is the beauty of the stack.

Start now. You will wish you had started earlier.

---

## Configuration

### stack.toml

The single config file. Generated by the installer. Yours to edit.

```toml
[core]
domain   = ""                    # empty = port mode (recommended to start)
data_dir = "~/famstack-data"     # where databases, uploads, media live
timezone = "Europe/Berlin"
language = "de"                  # "de" or "en", used for document tags and UI

[updates]
schedule = "0 0 3 * * *"         # Watchtower nightly image updates

[ai]
default = "mlx-community/Qwen3.5-9B-MLX-4bit"   # change to match your RAM
language = "en"                                   # "de" for German voice/transcription
```

Key things to know:

- **domain**: leave empty to start. Services are reachable via `<host>:<port>`. Set a domain later for pretty URLs like `photos.home.internal` (requires wildcard DNS on your router).
- **data_dir**: where all persistent data lives. Back this up. Outside the git repo.
- **language**: detected from your timezone. Controls which document categories get seeded (German or English). Change it and run `./stack restart docs` to seed missing tags.
- **AI model**: installer picks one for your RAM tier. Alternatives are listed as comments in `stack.toml`. Switch by uncommenting a different line and running `./stack setup ai`.

### users.toml

Your family members. Generated by the installer. User accounts are seeded on the first `./stack up` of each stacklet.

```toml
[[users]]
name = "Arthur"
email = "arthur@home.local"
role = "admin"

[[users]]
name = "Sarah"
email = "sarah@home.local"
role = "member"
```

Admins get accounts on every stacklet. Members get accounts on the stacklets listed in their `stacklets` field.

### .stack/secrets.toml

Auto-generated passwords for service accounts. Created once, reused on every `./stack up`. Treat like a password manager export.

```bash
./stack config              # show resolved configuration
./stack config --secrets    # include generated passwords
```

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

### Observability

```bash
./stack status                # what's running, what's healthy
./stack list                  # all stacklets with enabled/disabled state
./stack logs <stacklet>       # tail logs
./stack errors                # recent error logs across all stacklets
./stack host                  # disk, memory, uptime
./stack updates               # check for newer Docker images
```

All commands output JSON when piped. Use `--json` to force it, `--pretty` to force human output.

---

## Backups

This is the part everyone skips and regrets. famstack ships an opt-in backup stacklet for the irreplaceable file-level data (photo originals, scanned documents). It does not yet cover stacklet databases or your config files; for those, layer it with Time Machine or a periodic tar.

### The backup stacklet

Run `stack up backup`. You need an APFS-formatted external drive plugged in. The setup wizard asks for the disk name, encryption (default: plain APFS), and a nightly time (default 02:00). It then installs a small `.app` wrapper, asks you to grant it Full Disk Access, and adds a cron entry.

**What gets backed up**

| Source | Path on the vault disk |
|---|---|
| Immich photo originals | `/Volumes/<disk>/data/photos-library/` |
| Paperless archived PDFs | `/Volumes/<disk>/data/docs-media/` |

Postgres databases for both are not backed up yet. You get your files back but lose albums, tags, custom fields, and saved views. Pg-dump snapshots will ship as `[[backup.snapshot]]` in a later release.

**How the protection works**

Every file written to the vault gets the kernel `uchg` flag. macOS refuses to modify or delete uchg files, even with `sudo`. `rsync --ignore-existing` means existing vault files are skipped on every run, so the backup is append-only by design and accidental `rm -rf` on your main system cannot propagate. A canary file is checked before every sync; if ransomware has touched `~/famstack-data`, the canary won't match and the sync aborts before opening the vault.

**Daily operation**

```bash
stack backup sync         # run a sync now (from any context)
stack backup status       # last run, source counts, current mount state
stack down backup         # remove the cron entry, keep .app and vault data
stack destroy backup      # also remove the .app; vault and Keychain are preserved
```

The scheduled nightly run leaves the disk mounted between runs (cron cannot trigger eject under the macOS sandbox; files are kernel-locked regardless). Manual `stack backup sync` from Terminal does eject when it finishes. Results post to the `#famstack` Matrix room via stacker-bot.

**Recovery (no special tooling needed)**

Plug the vault disk into any Mac and browse the files in Finder. To copy locked files out:

```bash
sudo chflags -R nouchg /Volumes/<disk>/data/photos-library/<folder>/
cp -R /Volumes/<disk>/data/photos-library/<folder>/ ~/recovered/
```

A `stack backup restore` command and `on_restore` hooks for database recovery are planned but not yet shipped.

**What it protects you from**

| Threat | Covered |
|---|---|
| Ransomware encrypts your Mac | Yes (uchg + canary) |
| Accidental `rm -rf` on `~/famstack-data` | Yes (rsync never deletes from the vault) |
| You delete a photo on your phone | Yes (Immich propagates the delete to disk; vault keeps the original) |
| Vault drive stolen from your house | Only if you opted in to APFS encryption |
| Vault drive hardware failure | No (single physical copy; offsite engine planned) |
| Fire or flood | No (vault is in the same building; offsite engine planned) |

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

Container images update themselves nightly at 3am via Watchtower. You do nothing for that.

For the famstack code itself:

```bash
git pull
./stack restart <stacklet>    # for any stacklet whose code changed
```

If `git pull` shows changes to `lib/stack/` or to a stacklet you are running, restart that stacklet. If in doubt, restart everything.

Pre-1.0 caveat: occasionally a release changes a config schema or env var. Release notes will say so. Read them before pulling.

---

## Uninstall

```bash
./stack uninstall
```

Stops every stacklet and deletes its data. Asks for confirmation. There is no undo.

For a single stacklet:

```bash
./stack destroy photos
```

oMLX is left alone, since it might be used by something other than famstack:

```bash
brew uninstall omlx
brew services stop omlx
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

Click Continue anyway. Element works in every modern browser. The warning is from an outdated check.

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

- [Stack Reference](stack-reference.md): how stacklets are wired together, lifecycle, hooks, secrets.
- [Creating Stacklets](creating-stacklets.md): build your own stacklet.
- [Documentation index](README.md)
