# User Guide

famstack is an opinionated, batteries-included stack built on established open source projects (Immich, Paperless, Matrix, MLX). The glue is the part that matters: everything is wired together and accessible through instant messaging, so your family actually uses it instead of just you.

Getting started, day-to-day operations, and the things you actually need to know.

## Install

```bash
git clone https://github.com/famstack-dev/famstack.git
cd famstack
./stack
```

The installer walks you through everything: family name, user accounts, and your first stacklet (Messages). Takes about 20 minutes including downloads.

After the installer finishes, open Element X on your phone or browser and sign in. You now have a private family chat with a Server Room for managing your stack.

## Adding stacklets

Messages is set up by the installer. Everything else is one command:

```bash
./stack up photos      # Immich: photo library + phone backup
./stack up docs        # Paperless-ngx: document archive with OCR
./stack up ai          # oMLX + Whisper + TTS: local AI engine
./stack up chatai      # Open WebUI: ChatGPT-like interface
./stack up code        # Forgejo: private Git server
```

Each stacklet runs its own first-time setup on the first `stack up`. Subsequent runs just start the containers.

Check what's running:

```bash
./stack status
```

## Stacklets

### Messages (Matrix + Element)

Your family's private chat. WhatsApp replacement on your hardware.

```bash
./stack up messages    # set up by the installer, usually already running
```

The installer creates three rooms: Family Room (everyday chat), Memories (voice diary), and Server Room (admin alerts). Every family member gets an account. Default login is your first name (lowercase) as both username and password. Change it after first login.

Install Element X on your phone to chat from anywhere on your network.

**Port:** 42030 (Element), 42031 (Synapse)
**Data:** `~/famstack-data/messages/synapse/` (history, media), `~/famstack-data/messages/postgres/` (database)

### Photos (Immich)

Google Photos replacement. Face recognition, maps, memories, albums.

```bash
./stack up photos
```

Install the Immich app on your phone, enter your server's URL, and photos sync automatically. Every family member gets their own account and library.

**Port:** 42010
**Data:** `~/famstack-data/photos/library/` (originals + thumbnails), `~/famstack-data/photos/postgres/` (database)

### Docs (Paperless-ngx)

Document archive with OCR. Receipts, letters, contracts, tax documents.

```bash
./stack up docs
```

The archivist bot creates a Documents room in your chat. Send it a photo of a receipt and it files it automatically. AI classifies and tags documents when the AI stacklet is running. Type `show 42` to read a document's content, or search by typing any term.

**Port:** 42020
**Data:** `~/famstack-data/docs/paperless/` (documents, media), `~/famstack-data/docs/postgres/` (database), `~/famstack-data/docs/consume/` (inbox folder)

### AI (oMLX + Whisper + TTS)

Local AI engine. Powers document classification, voice transcription, and text-to-speech.

```bash
./stack up ai
```

Three components installed natively on your Mac (not Docker):
- **oMLX**: LLM inference on Metal GPU. The model is selected based on your RAM. Alternatives are listed as comments in `stack.toml` under `[ai]`. Switch by uncommenting a different line and running `./stack setup ai`.
- **Whisper**: speech-to-text. Transcribes voice messages in chat automatically.
- **TTS**: text-to-speech. The AI can talk back.

**Port:** 42060 (oMLX), 42062 (Whisper)
**Data:** `~/famstack-data/ai/speech/` (Whisper model, TTS voices). LLM models are managed by oMLX in its own cache directory.

### ChatAI (Open WebUI)

ChatGPT-like web interface for your local AI. Conversations stay on your machine.

```bash
./stack up chatai
```

**Port:** 42050
**Data:** `~/famstack-data/chatai/`

### Code (Forgejo)

Private Git server. Lightweight GitHub alternative.

```bash
./stack up code
```

**Port:** 42040
**Data:** `~/famstack-data/code/`

## The Memories room

One of the most valuable things you can do with famstack has nothing to do with code.

The Memories room is a place to record your family's life. Voice messages, photos, text. We record a voice diary once or twice a week at the dinner table: what was funny, what was special, what the kids want to tell their future selves. Holiday diaries, first days at school, bedtime stories in their own words.

Voice messages are transcribed by Whisper and become searchable. Everything stays on your Mac.

Start now. You'll wish you had started earlier.

## Configuration

### stack.toml

The single config file. Everything flows from here.

```toml
[core]
domain   = ""                    # empty = port mode (recommended to start)
data_dir = "~/famstack-data"     # where all data lives
timezone = "Europe/Berlin"

[ai]
default = "mlx-community/Qwen3.5-9B-MLX-4bit"   # change to match your RAM
language = "en"                                    # "de" for German voice/transcription
```

Key things to know:
- **domain**: leave empty to start. Services are reachable via `hostname:port`. Set a domain later for pretty URLs like `photos.home.internal` (requires wildcard DNS on your router).
- **data_dir**: where databases, uploads, and media live. Back this up. It's outside the git repo.
- **AI model**: the installer picks one for your RAM tier. The alternatives are listed as comments in `stack.toml`. Switch by uncommenting a different line and running `./stack setup ai`.

### users.toml

Your family members. Generated by the installer. User accounts are seeded on the first `stack up` of each stacklet.

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

## Daily operations

```bash
./stack status              # what's running, what's healthy
./stack logs <stacklet>     # tail logs
./stack restart <stacklet>  # restart (does down + up)
./stack errors              # recent error logs
```

Stopping and starting:

```bash
./stack down photos         # stop photos (data stays)
./stack up photos           # start again
./stack destroy photos      # remove everything (asks for confirmation)
```

## Updating

famstack uses Watchtower to auto-update Docker images nightly at 3am. You don't need to do anything for container updates.

To update the famstack code itself:

```bash
git pull
./stack restart <stacklet>  # for any stacklet that changed
```

## Data and backups

All data lives in `~/famstack-data/` (or whatever you set in `stack.toml`). Each stacklet has its own subdirectory:

```
~/famstack-data/
├── photos/     # Immich library, database
├── docs/       # Paperless documents, database
├── messages/   # Matrix history, media
├── ai/         # Downloaded models
└── core/       # Bot data, sessions
```

Back up this directory. That's it. The git repo has no user data.

`stack destroy` deletes a stacklet's data directory. There is no undo.

## Getting help

- [Discord](https://discord.gg/hfutdmmfBe): fastest way to get help
- [GitHub Issues](https://github.com/famstack-dev/famstack/issues): bug reports and feature requests
- [famstack.dev](https://famstack.dev): guides, benchmarks, and blog
- Server Room in your chat: stacker-bot responds to `status` and `help`

## Troubleshooting

**Stacklet won't start**: check `./stack logs <stacklet>` for errors. Most issues are port conflicts or Docker not running.

**Can't reach services from phone**: make sure your Mac and phone are on the same network. Services bind to all interfaces in port mode.

**AI model too slow or too big**: edit `[ai] default` in `stack.toml` to a smaller model (see the commented alternatives), then run `./stack setup ai`.

**Out of disk space**: check `./stack host`. Immich photo libraries grow fast. Move `data_dir` to an external drive if needed.

**Want to start fresh**: `./stack uninstall` removes everything. Only use this if you really mean it.
