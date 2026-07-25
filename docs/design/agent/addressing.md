# Family Agent — Addressing & Activation Model

> Status: Design, deferred (capture now, build later)
> Applies to: the `agent` stacklet (Stacky, nanobot in a container)
> Sibling docs:
>   - [plan.md](plan.md) — the agent implementation plan
> Related shim precedent: `stacklets/agent/runtime/` (the `brief` sitecustomize
>   monkeypatch) — the same technique wires the changes below without forking nanobot.

## Problem

Stacky runs `group_policy: "mention"`, so every message must `@`-mention it. In a
back-and-forth that is annoying — you re-mention on every turn. We want lower
friction without turning Stacky into a bot that butts into normal family chatter.

## What nanobot offers natively

The respond-decision lives in `channels/matrix.py :: _should_process_message`:

| Mechanism | Behavior | Fit |
|---|---|---|
| DM room (`member_count <= 2`) | short-circuits to **always respond**, no mention | private Stacky chat |
| `group_policy: "mention"` | respond only when `@`-mentioned (current) | shared rooms, but the friction |
| `group_policy: "open"` | respond to **every** message | too noisy for a family room |
| `group_policy: "allowlist"` | respond to everything, but only in listed room IDs (`group_allow_from`) | a dedicated Stacky room |

Constraints:
- **The policy is global** — you cannot say "mention in topic rooms, open in a chat room" at once.
- **No built-in follow-up window** — the thread code only *formats* replies in-thread; it does not decide whether to answer.
- `_is_bot_mentioned` checks the `m.mentions.user_ids` payload. A **reply** to
  Stacky carries that mention automatically (Element adds the replied-to user),
  so "reply to continue" already works without typing `@stacky-bot`.
- Threads: **in practice Stacky's replies land in the main timeline, NOT in a
  thread** (observed). The plumbing half-exists — the inbound path captures the
  thread root (`_base_metadata` merges `_thread_metadata(event)`), and the reply
  path can build a threaded `m.relates_to` from `msg.metadata` — but the root does
  not survive through the agent loop to the outbound reply, so nothing threads.
  Two further gaps: nanobot **only** threads when the *incoming* message is itself
  a thread message (Element "Reply in thread", not a plain reply/mention), and it
  **never starts** a thread. And the **session key is per `room_id`** (`chat_id`),
  so even a working thread would share the room's conversation memory.
- Silence: `AgentHook.finalize_content(ctx, content) -> str | None` can return
  `None` to **drop a reply** — the seam a "stay silent" mode needs.

## The options (three composable layers)

### 1. DMs — free, mention-less (works today)
A 1:1 room with `@stacky-bot` needs no mentions. This is the natural home for a
private, fluid conversation with Stacky. No work required.

### 2. Threads-as-conversation (DECIDED — the priority fix)
> **Requirement (Arthur, with repro):** when a user **replies inside a thread
> Stacky is part of**, Stacky must auto-respond **without a mention**. Repro:
> Stacky posted the Itchy & Scratchy Land list; Homer replied in-thread "gibts
> noch mehr?"; Stacky stayed silent because the thread reply carried no
> `@`-mention. A thread you are in IS the conversation — no re-mentioning.

Make a **thread the conversation unit** in shared rooms. Three shims (step 0 is
the fix for the gap above — replies don't thread today):
0. **Make Stacky actually reply in a thread** — propagate the incoming thread root
   through to the outbound reply metadata (it is dropped today), and start a thread
   on the first reply so even a plain mention opens one. (Without this, even after
   the gate change below, Stacky's answer would land in the main timeline, not the
   thread.)
1. Fold the thread root into the session key so each thread is its own scoped
   memory (today all threads share the room session).
2. In `_should_process_message`, treat a message in a thread **whose root Stacky
   authored, or where Stacky has already posted**, as addressed — so a thread reply
   auto-responds with no mention. This is the core of the requirement above.

Result: *mention once → Stacky opens a thread → the whole thread is a conversation,
no re-mentioning → the thread is visibly scoped and just ends when you stop.* This
is more explicit and less magical than a timer, and it won't leak into family
chatter (only that thread is live).

Alternative to this: a **timed follow-up window** — mention/reply opens a ~90s
per-speaker engagement window in that room, refreshed each turn, auto-expiring on
idle. Simpler conceptually but expires mid-thought and is invisible to the user.
Prefer threads-as-conversation; keep the timed window as a fallback idea.

### 3. "When it has something to contribute" mode (ambitious; build last)
Stacky decides on its own whether to chime in on a message it was not addressed
on. Not a native policy — build it as **two stages** so it stays cheap and quiet:

1. **Cheap gate, every un-addressed group message:** a lightweight "would Stacky
   helpfully add something here?" check — a small/fast classifier (the
   pre-selection model idea) or heuristics. Silence-by-default, tuned for
   **precision**: a bot that butts in wrongly is worse than one that stays quiet.
2. **Full agent, only when the gate fires:** it produces a contribution, or stays
   silent even after passing the gate, via a `finalize_content` hook returning
   `None`. Two silence points, not one.

Wiring: a shim on `_should_process_message` (let a message through when the gate
says "contribute") plus a `finalize_content` hook (suppress no-op replies).

Design cautions (this mode lives or dies on restraint):
- **Scope it** — topic rooms only (where Stacky has vault context worth adding),
  and only certain triggers: an unanswered question, a task, a factual gap it can
  fill *from the vault*. Not the open family chat.
- **Cost** — the gate runs on every message, so it must be cheap. That is the
  classifier's job. Good scenario for local-llm-bench (relevance routing on
  Apple Silicon).
- **Observability** — log every "chimed in / stayed quiet" decision so the
  threshold can be calibrated.
- **Privacy** — this means Stacky reads every message to decide. The archivist
  already does (capture), so it is not new locally, but the family should know
  Stacky listens.

## Recommended sequencing

1. **DMs** — already free; document it as the "talk to Stacky privately" path.
2. **Threads-as-conversation** — smallest change that removes per-message mentions
   in shared rooms. Two shims: session key + should-respond.
3. **Contribute mode** — deliberate follow-up. Gate hard, scope to topic rooms,
   make it observable, ship the classifier separately.

## Implementation seams (nanobot, no fork)

- `channels/matrix.py :: _should_process_message(room, event)` — the respond
  decision. Wrap it (sitecustomize monkeypatch, as with `brief`) to add
  thread-membership and/or the contribute gate.
- Session key (`chat_id` = `room_id`) — extend to include the thread root for
  per-thread isolation.
- Thread helpers already present: `_event_thread_root_id`, `_thread_metadata`,
  `_build_thread_relates_to`.
- `AgentHook.finalize_content(ctx, content) -> str | None` — return `None` to
  suppress a reply (contribute-mode silence).
- `_is_bot_mentioned` reads `m.mentions.user_ids`; a reply already carries it.

If these accumulate alongside the existing `brief` shim and past friction (the
coding-flavoured tool contract, single-user `USER.md`), that is the trigger to
graduate from shims to a nanobot fork and upstream a proper policy/hook API.
