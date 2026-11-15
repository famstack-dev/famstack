# The release gate's install test

**The install test** is a first-time install on a clean Mac before a release is tagged. It follows the path of a new
admin. A script walks the terminal and the setup page. A person does the parts that need a phone and a judgment. The
test ends with the e2e lanes on the same checkout.

It is step 4 of the pre-tag gate in [the engineer file](../../docs/agent/dev.md#releases): "Fresh-instance install
verified".

## What does the test need?

| What | Why |
|---|---|
| A test Mac with Apple Silicon and 16 GB, on AC power and with its lid open | 16 GB is the smallest Mac that famstack supports. A closed lid sleeps the Mac. |
| No famstack instance on the test Mac: no containers, no `~/famstack-data`, no `stack.toml` | A first install starts from nothing. |
| The release candidate checked out in `~/famstack` on the test Mac | The test runs the code that the release ships. |
| An AI app on the test Mac (the oMLX app) with its models and its API key | A model download takes hours and does not test famstack. |
| This Mac: Chrome, `uv` and ssh access to the test Mac | The driver runs here and acts on the test Mac. |
| An Android phone on this Mac's USB, with `adb` and Element X | The phone step needs a real phone and the real app. |

> [!WARNING]
> Make sure that nobody uses an instance on the test Mac before you remove it. The test removes all of its data.

## How do I run the test?

1. Set the test Mac for the driver:

   ```sh
   export DRIVER_HOST=<test-mac>
   ```

2. Run the installer as a person does:

   ```sh
   tests/driver/driver tty "./stack" --answer "Start the installation=y" --record run/01-install.cast
   ```

3. Walk the setup page. Give the AI app's key on stdin, because a key on the command line goes into the shell's
   history:

   ```sh
   tests/driver/driver setup --language de --out run --ai-key - --document letter.jpg --phone < key.txt
   ```

4. Look at the phone after the walk. Write down what Element X asks after the sign-in, for example to confirm the
   new device.

5. Read `run/protocol.md` as a new admin. Write down each text that is unclear, wrong or in the wrong language.

6. Run the e2e lanes on the same checkout:

   ```sh
   tests/e2e/stacktests preflight
   tests/e2e/stacktests ai local
   tests/e2e/stacktests e2e
   ```

`driver setup` writes `run/protocol.md` and one screenshot per step. It stops at the first step that does not move on,
and exits with 1. The protocol then shows that step's text and the output of `./stack doctor`.

## What does the script check?

| Step | The step passes when |
|---|---|
| family | The first run takes the family and opens the setup page. |
| chat | **Start** brings up the chat, and the page moves on. |
| phone | The phone opens the QR code's link and signs in to Element X. Then the page moves on. |
| ai | The AI app's key connects, and the page moves on. |
| services | Photos, documents and memory start, and the page moves on. With `--one-by-one`, they start one at a time. |
| try | A photo of a letter in the documents room is filed, and the page notices the first document. |
| doctor | `./stack doctor` names no error. |

Each step has its own time limit in `setup_walk.py` (`STEP_SECONDS`). A step that takes longer is stuck.

## What does a person check?

The script cannot judge these:

- **Element X on the phone.** The protocol gives the time to the user name screen. It must be less than a minute.
  Also check what Element X asks after the sign-in.
- **The words.** Each text for people is in the browser's language and says what to do next. Technical lines (job
  steps, log lines, account ids) can stay in English.
- **The first document.** The archivist's answer in the chat names a title and a summary that match the letter.
- **The voice**, if the setup turned speech on: `./stack ai say` speaks in the family's language.
- **The first hour.** Nothing is silent for long, and nothing asks for a step that the page does not explain.

## When does a finding block the release?

A finding blocks the release only if it does one of these:

- It loses or garbles family data.
- It stops an install or an update.
- It stops the bots from answering.

Every other finding goes to the next release. For each finding, write these four parts:

- what you did;
- what happened;
- the evidence: a log line, a command and its output, or a screenshot;
- a recommendation.

## What can go wrong in the test itself?

| Symptom | Cause | What to do |
|---|---|---|
| The browser shows a page that is not the test Mac's | A port tunnel (`ssh -L`) reached a stack on this Mac | Use the driver, because its ssh SOCKS proxy makes `localhost` the test Mac. |
| A file appears as a room's picture in Element | The room header has its own file input | Attach files with the composer's attachment button. |
| Element X waits for minutes on **Sign in manually** | The app tries HTTPS first on the chat's plain HTTP port | Write down the time. It is a finding for the phone step. |
| The e2e lane fails with a restart loop in Paperless | An old `~/famstack-test-data` from an earlier run | Run `tests/e2e/stacktests cleanup`, then run the lane again. |
