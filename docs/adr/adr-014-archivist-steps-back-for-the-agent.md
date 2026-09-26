# ADR-014: The Archivist Steps Back When the Agent Is in the Room

## Status
Accepted (direction). Implementation is staged; see Order.

## Context
Two bots can read the same conversation room, and they are built for different
machines and different jobs.

- **The archivist** is fast and deterministic, and runs on every Mac famstack
  supports, 16 GB included. It files what the family drops (documents, notes,
  bookmarks, photos, voice memos), answers when asked, and keeps the diary.
- **The agent** (Stacky) talks: it answers in conversation, keeps lists and
  searches the vault. It needs a larger model, so a 32 GB Mac, and is optional.

Each decides on its own whether a message is for it, and neither knows the other
exists. A walkthrough on a test Mac with both in a topic room showed what that
costs a family:

- **Both bots act on one message.** Marge replied to a photo with "Stacky, what
  do you see in this picture?". For the archivist a reply to its filing is a
  correction, so it re-classified the photo using that sentence as a hint. The
  agent answered the same message.
- **A spoken request to the agent is filed as a note.** "Stacky, add sunscreen
  to the picnic packing list", sent as a voice message, was transcribed by the
  archivist and filed as a note with a to-do. The agent never heard it: it
  receives no voice messages.
- **The agent cannot see images.** Asked about a photo it had been replied to,
  it answered that no picture was attached. The archivist described the same
  photo correctly.

The archivist's routing is in `docs/design/brain/who-answers.md`.

## Decision
**The archivist keeps its job everywhere, and steps back where the agent is in
the room.** Alone, it behaves as it does today. With the agent present, it acts
only on what is explicitly for it, and leaves conversation to the agent.

| In a room | Archivist alone | Archivist with the agent in the room |
|---|---|---|
| Addressed by name, or picked from the `@` list | answers | answers |
| A reaction (📌 🔖 📎 🔁) | acts | acts |
| A correction in the thread of its own filing | re-files | re-files |
| Addressed to the agent by name, typed or spoken | files a transcript as a note | stays silent |
| Anything not addressed: a paste, a link, a voice memo, a file | captures it | leaves it to the agent, once the agent can handle that kind (see Order) |
| Documents room, Memories | unchanged | unchanged; the agent does not belong there |

"The agent is in the room" means the agent's account (`AGENT_BOT_ID`) is a
member. The agent's name (`AGENT_NAME`) and account are already in the
bot-runner's environment, and both bots use the same `stack.name_trigger` for
"was this addressed to me".

## Order
Stepping back grows with what the agent can do, so nothing a family sends is
left unhandled:

1. **Now:** the archivist stays silent on a message that addresses the agent by
   name, and does not treat a reply to its filing as a correction when the reply
   addresses the agent.
2. **When the agent hears voice messages:** unaddressed voice memos are left to
   the agent.
3. **When the agent sees images:** unaddressed photos and files are left to the
   agent.
4. **When the agent can file through the CLI** (tools over `stack memory capture`
   and the list commands, deciding between to-do, note and bookmark, with its
   decisions recorded in the room for replay, ADR-010): unaddressed pastes and
   links are left to the agent.

## Consequences
- A 16 GB household keeps every capability it has today; nothing depends on the
  agent.
- With the agent present, one message gets one answer. The archivist's ambient
  guesses (paste length, room size) stop applying in those rooms step by step.
- The capture pipeline stays the one implementation behind the CLI, used by the
  archivist directly and by the agent through its tools: one core, two surfaces.
- The gates added so the two could coexist (leaving threads the archivist does
  not own, addressing by name) stay; they are right for any room with more than
  one bot.

## Open
- What the agent searches when asked about documents: the archivist searches
  Paperless and the vault, the agent the vault only
  (`docs/design/brain/write-layer.md`, item 12).
