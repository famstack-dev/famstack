# Writing to memory — one primitive, validated per page type

> Status: Design. Nothing here is built except the findings, which are verified.
> Applies to: how humans, the archivist and the agent share the vault's writable pages
> Sibling docs:
>   - [memory-mutations.md](memory-mutations.md) — the write seam as built today
>   - [interaction-patterns.md](interaction-patterns.md) — the bot interaction layers
>   - [vault-format.md](vault-format.md) — what a page is
> Evidence: a real family Road-Trip room transcript (June to August 2026) plus a
>   reproduction on the demo rig, 2026-08-03.

## Why this doc exists

A family used a topic room for two months to plan a trip. It mostly worked. The
part that failed, failed completely: a thirteen-item list became twenty-seven
entries, none of them ever ticked off, and the agent claimed twice to have ticked
them. This doc records what we found, what we decided, and what is still open, so
the plan survives the session that produced it.

The headline is not the duplicates. It is that **the archivist invites a
conversation it cannot have**. It answered a pasted list with a title, a summary,
five extracted facts and six tags, which reads like a participant. Then:

    07:31:39  Marge: Fenstertasche ist bestellt, Thema 1 kann abgehakt werden
    07:32:27  Marge: Welche Themen sind noch auf der Liste?
    07:34:50  Marge: * ?Welche Themen sind noch auf der Liste?
              (nothing, three times)
    07:39:40  Marge: * Liste Bus Erweiterungen: 1. ... 10.

She tried plain language, then the archivist's own documented `?` syntax, then
gave up and re-posted the whole list. Every duplicate downstream is blast radius
from that workaround. Re-posting is not a habit to design around; it is what
someone does when nothing answers.

## Findings

Each of these was verified in code or reproduced, not inferred from the report
that started the session.

### The list path

1. **The agent hallucinates completed actions.** Reproduced on the rig: loop
   iteration 0, zero tool calls, and a reply claiming the list was updated.
2. **The hallucination was masked by a concurrent true write.** The list really
   did change, via the archivist. Commit `chore(todos): homer added action items
   to camping` is the curator's message, not the agent's. From the family's side
   it looked like the agent worked.
3. **Extraction inverts done-markers.** The capture LLM understood `-> CHECK`
   perfectly ("eine Mischung aus bereits geprüften Gegenständen") and then emitted
   `- [ ] Fenstertasche prüfen`. It had the understanding and no way to express
   it: `action_items` has no done state, so it bent the meaning to fit the shape.
4. **Extraction rewrites the family's words.** "Kühlbox" became "Kühlbox
   mitbringen". `add_items` dedups on exact task text, so each re-extraction of
   the same list added a fresh variant: "Alternative Dachbox"
   recherchieren/prüfen/suchen/besorgen, four entries for one item. This is the
   mechanical cause of 13 items becoming 27. **Verbatim is not politeness, it is
   what makes idempotency possible.**
5. **Message edits re-capture.** There is no `m.replace` handling anywhere in the
   archivist or microbot. An edit arrives as a new event with a `* ` body and is
   filed as a fresh paste. Six re-posts became six notes and six extractions.
6. **One list per topic.** Marge asked for two, in words: "Es sollen zwei Listen
   sein. Eine Liste mit Verbesserungen und eine Liste mit Dingen die zusätzlich
   auf unsere Packliste sollen."
7. **The agent can describe the right answer and not perform it.** Asked to split
   and dedupe, it produced the correct final document in chat, grouped and
   categorised, then failed to execute it as twenty-odd string-matched CLI calls.
   That gap is the argument for a different primitive, not a better prompt.

### Ownership

8. **`?` search is dead.** The room welcome promises `?<frage>`; `archivist.py`
   only searches when `mentioned or is_documents`. A documented feature that
   silently does nothing.
9. **Address-beats-ambient already exists in the code.** `_handle_correction` is
   gated on `not mentioned`, with a comment saying deliberate address beats
   ambient context. The archivist applies the rule to itself and has no idea the
   agent exists.
10. **The signal is free.** `AGENT_NAME=Stacky` and `AGENT_BOT_ID=stacky-bot` are
    already in the bot-runner's environment.
11. **But the matcher is not shared.** `name_trigger.py` lives in the agent
    stacklet, and the agent container mounts no `lib/stack`. If the two ever
    disagree about "was the agent addressed", either both act or neither does.
12. **The two answerers are not redundant.** The archivist's search is dual
    (Paperless plus vault, with synthesis and deep-dive); the agent's
    `memory_search` is vault-only, and `stack docs search` does not exist.
13. **`_on_text` has 17 decision points**, 7+ in the `elif` chain.

### Structure

14. **Capture sits in the wrong stacklet.** `capture_pipeline.py` writes no
    Paperless document; `_publish` is classify plus mirror. Its one docs
    dependency is `paperless.get_tags()` for the person roster, which is itself a
    memory concern sourced from docs. And `git_mirror.py:57` does a hard-coded
    `sys.path` traversal into `stacklets/memory/bot/cli/`.
15. **The agent reads the projection and writes the source.** `MEMORY_VAULT_DIR`
    is `{data_dir}/memory/brain`, mounted `:ro`; writes land in
    `{data_dir}/memory/vault` via `update_memory`. **A naive `write_file` bolted
    onto the path the agent already reads would write into a generated
    projection and lose it on the next sync.**
16. **No lock on the working copy.** The host CLI and the curator share one, with
    no `status --porcelain` guard on the rebase/reset paths.

### Smaller

17. **Fetched interstitials get filed as knowledge.** A Google Maps link became
    "Google Cookie- und Datenschutzhinweise", tagged `privacy-first`, `consent`.
18. **The agent reached for `stack up memory`** twice. `DOMAIN_ALLOW` refused it,
    so the refusal is doing real work.
19. **Capture latency is 10 to 16 seconds**, not the 30 estimated from log
    timestamps. Synchronous is fine; the async design was unnecessary.

## Decisions taken

**Capture is a memory capability.** `stack memory capture` ships in memory's
namespace (PR #60), handling pasted text, links and images through the
archivist's own pipeline. The handler still sits under `docs/bot` because the
pipeline does; it travels with the pipeline when that moves.

**Reject unvalidated free-form rewrite; accept validated primitive writes.** The
first proposal was to let the agent rewrite `todos.md` freely. Rejected: it hands
whole-file overwrite to the model that just hallucinated, turning a visible
harmless failure ("nothing happened") into an invisible destructive one ("six of
twenty-five items quietly vanished"). What changes the calculus is **feedback**:
a validator that reports what the edit did, so loss is never silent.

**Address decides who acts.** Exactly one component responds to a message.
Addressed to the agent, the agent owns it. Nobody addressed, the archivist's
ambient rules apply and its shape heuristics are fine precisely because nobody
asked for anything.

**The archivist becomes ambient-only, eventually.** It watches, files, corrects,
and never answers. Not yet: its search reaches Paperless and the agent's does
not. Sequenced, not big-bang.

## The direction: a markdown-native write layer

Reads are already fs-native. The agent does `read_file("vault/homer/about.md")`
and it works, because models are trained on it and nobody had to design a
retrieval verb. Writes have no counterpart, and that asymmetry is what forced
domain verbs like `todo strike "<item>" --by <person>`. We did not choose verbs
because writes are special. We chose them because there was no write primitive.

The proposal is the write analogue of what `/go` did for links: a stable logical
surface whose backing store is implementation detail. `update_memory` already
says this out loud, and the layer is the generalisation of it.

**Shape.**

- fs-like primitives (`write_file`, `apply_patch`) over one logical namespace.
- Routing under the hood: source versus projection, which bucket, which store.
- Registration per well-known page type: which schema applies, and what is
  writable at all. It is a capability boundary as much as a validator.
- Validation is **semantic**, not syntactic. "Valid markdown with `- [ ]` lines"
  is easy and worthless. The check that matters compares before and after:
  items removed outright, items reworded, items added, structure violated.
- The tool result **is** the review: "Struck 7. Removed 6 you were not asked to
  remove: Fenstertasche, Kochlöffel, ... Reworded 3. Confirm or revise."
- The semantic diff also writes the commit message, so intent falls out of
  validation instead of being a string the caller invents.
- Both writers bind to the same schema. One place states "task text is the
  family's words, verbatim", instead of it being a habit we hope survives.

**What it subsumes.** Variadic `strike`, `--list`, `--done`, and sections-as-CLI
-grammar were all attempts to make a verb expressive enough to describe a
document edit. If the primitive is a document edit, none get built. That is the
reason to decide this before shipping them, not after.

**What it does not fix.** An agent that claimed a strike without calling anything
can still claim an edit without calling anything. Validation only runs on writes
that happen. What changes is the odds: the transcript shows it *did* perform the
batched add and *did not* perform eight strikes, so collapsing the operation to
one call is the lever. It also does not remove the need for verbatim extraction
on the ambient path, where no agent is involved at all.

## Open decisions

**The write namespace.** Mirror the disk (`vault/family/camping/todos.md`) or
mirror `/go` (`topic/camping/todos`)? Mirroring `/go` is the more honest version
of the analogy, and bucket derivation is exactly the routing the layer should
own. But the agent's reads use disk-shaped paths today, so either the read side
moves too or two namespaces coexist for a while.

**Concurrency semantics.** `update_memory` takes a transform (`doc -> doc`)
applied to a fresh read, which is read-modify-write. `write_file` implies
last-writer-wins: the agent reads, thinks for thirty seconds, the curator writes,
the agent writes, and the curator's change is gone silently. The fs world's
answer fits the model: hand out a revision on read, carry it on write, fail a
stale write with "this changed under you, here it is again". Same diagnostic
channel as schema validation. Worth doing regardless, given finding 16.

**Whether the capture pipeline migration precedes or follows the write layer.**

## Order of work

1. **The list schema and validator, as a pure module.** No I/O. It is what the
   write layer calls, what a CLI verb would call, and what the curator needs. It
   is testable today against the real list from the transcript. First step
   regardless of which way the open decisions go.
2. **Verbatim extraction.** The ambient path stops rewriting the family's words.
   Independent of everything else and the single highest-value mechanical fix.
3. **The agent can actually change a list.** Behind the validator, whichever
   primitive wins.
4. **The ownership rule.** Share `addressed_by_name`, mount `lib/stack` into the
   agent, archivist skips its capture branches when the agent was addressed.
5. **The archivist stops promising what it cannot do.** Either `?` works or the
   welcome stops offering it.
6. Then: `m.replace`, the interstitial capture, `stack docs search`, and the
   capture-pipeline migration.
