# driver

Operate a running famstack instance from its own surfaces: act as a family
member in the chat, run `./stack` as the admin, read what the bots answered.
It is for agents and people trying things on a real instance, not a test
suite: it acts and reports, and asserts nothing.

## The one rule

The driver only uses what the stack offers its users: the `stack` CLI, and
the chat through `stack messages`. It never talks to Matrix, a database or a
config file itself. When an action needs more than those surfaces give, the
gap is in the surface, and that is where it gets fixed. `stack messages react`,
`send --reply-to` and `read --after/--from/--wait` came from exactly that.

What the driver adds is only what the CLI rightly does not do:

| | |
|---|---|
| Where | this checkout, or another Mac with `--host <ssh-host>` |
| Speech | text to a voice message through a TTS server, as a phone records one |
| Files | a local photo or PDF copied to the instance's Mac before it is posted |
| Ids | every action prints the event id it produced, for the next step |
| A terminal | interactive commands such as the installer, answered prompt by prompt, with a transcript and an asciinema recording |
| A browser | a stacklet's web UI in Playwright, signed in as a family member, as a screenshot or a video |

## Setup

```sh
export DRIVER_HOST=famstack-e2e              # omit to drive this checkout
export DRIVER_ROOT=~/famstack                # the checkout on that Mac (default)
export DRIVER_TTS_URL=https://tts.example    # for `voice` without --file
```

Runs with `uv` (it declares `pexpect` and `playwright` itself) and Chrome for
the browser. Needs `ssh` for a remote instance, and `ffmpeg` for voice
messages in the phone's format (ogg/opus; mp3 without it).

## Commands

```sh
driver as marge say picnic "Archivist, where do we meet?"      # prints the event id
driver as marge say picnic "this is Marge's car" --reply-to <id>
driver as marge say picnic "and the grill?" --thread <root-id>
driver as marge voice picnic "Stacky, add sunscreen to the list"
driver as marge voice picnic --file memo.ogg
driver as lisa send documents report-card.pdf
driver as marge react picnic <id> 📌

driver read picnic [--limit 20]                   # recent messages, with ids
driver answer picnic --from archivist --after <id> [--first] [--timeout 180]   # its answer to <id>

driver cycle curator                              # the pages a filing touched, now
driver cycle nightly                              # tonight's sweep, now
driver stack up agent                             # ./stack on the instance
driver tty "./stack" --answer "Family name=Simpson" --answer "Your first name=Homer" \
    --answer "Name (leave empty=Marge" --answer "Name (leave empty=" \
    --answer "Ready?=y" --record install.cast            # the installer, answered
driver browse messages --as lisa --room "Lisa Notes" --shot lisa.png
driver browse memory --shot wiki.png --video videos/
driver logs archivist [--grep search]             # a bot's lines from its log
```

A room is its alias (`picnic`, `documents`, `memories`) or a room id. A family
member is their user name (`marge`). `--json` on `as`, `read`, `answer` and
`cycle` gives the stack's own answer.

## Timed work

Some of the chain from a message to the wiki runs on a timer: the curator
regenerates pages after a quiet window following a filing, and sweeps
everything once a night. `driver cycle` runs that work now and returns when it
is done, through `stack memory wiki update` and `stack memory wiki update --all`. In
the protocol it is a `CYCLE` line, so a reader tells what the family did from
what the stack did on its own:

```
[20:14:02.118] WHEN       lisa sends report-card.pdf to documents
[20:14:40.905] CYCLE      the curator regenerates the pages the last filing touched (...)
[20:14:52.330]   ✓        done after 11.4s
```

## An exchange

```sh
id=$(driver as marge say picnic "Archivist, where do we meet for the picnic?")
driver answer picnic --from archivist --after "$id" --first
```

```
archivist-bot  $iDKNM5... [reply $5O23YtaZH]
  💡 **Antwort:** Wir treffen uns am Nordeingang des Springfield Park [1].
```

The same with speech is `driver as marge voice picnic "..."`: the bots hear it
the way they hear a phone's voice message.

## The terminal

`driver tty "<command>"` runs an interactive command where the instance lives
and answers its prompts: `› <prompt> [default]` and `? <question> [Y/n]`. An
`--answer` matches a prompt by how it starts; one given several times is used
in order, for a prompt that repeats. A prompt no answer matches **stops the
command** and is reported by name, because pressing Enter on an unexpected
"Switch to the local engine? [Y/n]" changes the instance. `--take-defaults`
presses Enter instead. The transcript comes back with the prompts asked;
`--record` writes an asciinema recording.

## The browser

`driver browse <stacklet or URL>` opens the web UI in Chrome through
Playwright, on this Mac. For Element on another Mac it opens an ssh tunnel and
browses `localhost`, the only address Element runs on over plain HTTP. `--as`
signs in to Element (password: the user name unless `--password`), keeping one
browser profile per person so the next run is the same device. Element asks a
second device to confirm its identity; the driver then stops and says so,
with a screenshot.

## The protocol

Every command tells on stderr what it does and what came of it, in the
Given / When / Then form of the e2e tests (`tests/e2e/bdd.py`), with a
timestamp and, for an answer, how long the bot took. A failure is a `✗` line.
stdout stays the command's answer, so scripts are not affected.

```
[20:10:13.402] WHEN       marge says in picnic: 'Archivist, where do we meet?'
[20:10:14.017]   .        sent $5O23YtaZHKj8...
[20:10:14.018] THEN       archivist answers in picnic to $5O23YtaZHKj
[20:10:20.431]   ✓        after 6.4s: ✨ Gesucht nach: Picknick, Treffen, Ort | 💡 Antwort: ...
```

`DRIVER_LOG=run.log` also appends the lines to a file, so the driver calls of
one scenario leave one protocol behind.
