# Memory Mutations — writing back to the vault

> Status: Implemented (todo strike/unstrike); the write seam generalises
> Applies to: the memory stacklet's write path, and any agent capability that
>   mutates the vault (the `agent` stacklet reaches it through the host CLI bridge)
> Sibling docs:
>   - [plan.md](plan.md) — the brain/knowledge layer
>   - [../agent/addressing.md](../agent/addressing.md) — how the agent is addressed
> First caller: `stack memory topic <slug> todo strike "<item>" --by <person>`

## The problem

Reads are host-native: `stack memory topic <slug> todo` parses `todos.md` off the
local vault clone — stdlib only, no LLM, no network, works when the model or the
bots are down. Mutations are different: to be durable and to show up everywhere
(other clones, the rendered wiki), a change has to land in **Forgejo**, which is
the vault's source of truth, and it has to say **who** made it. This doc is how a
deterministic mutation gets there without dragging in the LLM or a `docker exec`.

## The shape (two layers + a CLI verb)

**Core mechanism — `ForgejoClient.edit_file` (`lib/stack/forgejo.py`).** The
read-modify-write companion to `get_file`/`put_file`: fetch the current body,
run a `transform`, and commit the result on the same branch with the prior sha.
Returns `None` when the transform is a no-op, so re-runs never churn the repo
with empty commits. `author_name`/`author_email` set the commit author, so the
person who triggered the change owns it in history — not the token's identity.
Generic; any Forgejo-backed stacklet can use it.

**Domain intent — `update_memory` (`stacklets/memory/lib.py`).** The single write
seam for deterministic memory mutations. It runs **host-native** — the persisted
write token (below) already has write access, so no `docker exec` and no LLM —
reading the canonical file from Forgejo (not the possibly-stale local clone),
applying the transform via `edit_file`, committing as `actor`, then
fast-forwarding the local clone so a following read agrees. Returns
`{"ok", "committed": bool}` or `{"error": ...}`; an unchanged transform commits
nothing. The git commit is an implementation detail here — the API is "update
this piece of memory, attributed to someone."

**CLI verb — the agent-facing capability.** One generic tool (the host bridge),
capabilities discovered by listing commands, mutations behind the same `stack
memory` surface as reads:

    stack memory topic <slug> todo strike   "<item>" --by <person>
    stack memory topic <slug> todo unstrike "<item>" --by <person>

`strike`/`unstrike` is a matched inverse pair (not `strike`/`reopen`), and not
`close`/`open` because the read path already calls an un-ticked item "open". The
toggle itself lives in `set_todo_done` (`todo_list.py`), so read and write share
one notion of a task line. It matches on exact text, else a substring, preferring
the task whose state would actually change — so "one open, one already done"
resolves on its own. The commit message names the actor:
`chore(todos): homer ticked off "…" in <slug>`.

## The credentials decision (option C)

To write host-native, `update_memory` needs a Forgejo token. Investigating turned
up a gap: `pull.py`, `ontology.py`, and `on_start_ready.py` all **read**
`memory__MEMORY_BOT_TOKEN`, but nothing ever **set** it. The active installer
(`install_memory_to_forgejo_admin`) is admin-only — it creates no bot user and
mints a write-scoped token only to clone, then throws it away. So the secret was a
dead key and the host had no clean write token.

Three options were on the table:

| | How | Trade-off |
|---|---|---|
| A. Local git | commit + push through the clone reads already use | creds already embedded, but a second (local-git) write surface next to the Forgejo API |
| B. Mint per call | admin login → issue token → `edit_file` each time | keeps the API path; an admin round-trip on every write |
| **C. Persist the token** | return the installer's write token and store it as a secret | tiny, *correct*; repairs the two dead reads; one write surface |

**Chosen: C.** The admin installer now returns its write-scoped `admin_token` as
`write_token`, and `on_install_success` persists it via
`ctx.secret("MEMORY_BOT_TOKEN", …)`. `update_memory` (and, as a bonus, the
recovery clone in `on_start_ready` and `pull.py`/`ontology.py`) then get the token
straight from the secrets API — the sanctioned path (`config["secrets"]`, backed
by `TomlSecretStore`). Because author attribution is set per commit, a shared
write token is fine: it is transport auth, not identity.

## Notes, deviations, follow-ups

- **Admin-scoped transport token.** Since the active install is admin-only, the
  persisted token is admin-minted (write scope). Acceptable for a single-owner
  family server; the per-commit author still names the real person. If memory
  ever grows a dedicated bot account, mint that token here instead.
- **Existing instances need one `stack up memory`.** The token is persisted on
  install/up; instances that installed before this change have the dead key until
  the hook runs again (it is idempotent and runs on every `up`).
- **`__code_url`.** The host-reachable Forgejo URL is *not* persisted (install-time
  `CODE_URL` is the container name `stack-code:3000`, unreachable from the host).
  `update_memory` falls back to `http://localhost:{code port}` from config, the
  same resolution `pull.py` uses. Verify the port when wiring a new instance.
- **Allowlist widening.** The agent host runner's allowlist is prefix-based, so a
  write verb under the already-allowed `memory topic` prefix is reachable without
  a new entry. That is intentional, and it is why the mutation is **undoable**:
  a 👎 on Stacky's "struck …" fires `unstrike` (the inverse), closing the loop
  with the emoji-interaction model.
- **Read/write coherence.** `update_memory` fast-forwards the local clone after a
  commit, so `stack memory topic … todo` reflects the change immediately instead
  of waiting for the next scheduled pull.
