# The neuro lens

A design lens, not a spec. Reads famstack's memory architecture against
human-memory neuroscience to sharpen a few decisions. Companion to
`domain-model.md` and ADR-011.

Grounded mainly in three well-replicated frameworks — Complementary
Learning Systems (McClelland, McNaughton & O'Reilly 1995), hippocampal
indexing theory (Teyler & DiScenna), schema-consistent consolidation
(Tse et al. 2007) — plus one specific 2026 finding on content/context
separation (Bausch et al., Nature 650:690, "Distinct neuronal
populations in the human brain combine content and context").

## Why "brain" is the right name for the projection

The finding underneath the projection repo's name: the human brain
keeps *content* (what — context-invariant concept cells) separate from
*context* (where/when/why — context cells), in two largely distinct
neuronal populations that couple on demand. famstack stores memory the
same way: a record's content is context-invariant, its scope/topic
bindings are separate, and links couple them. Two names were on the
table for the projection repo:

- **brain** — the whole organ: the integrated, rendered, browsable
  view of everything the household knows. This is what we shipped, and
  it is the honest name: `family/brain` is the projected, coupled
  whole, while `family/memory` is the raw substrate.
- **cortex** — also defensible, and arguably more precise: the
  neocortex is the *slow, generalized, decontextualized* store that
  memory consolidates into, which is closer to what a projection
  literally is (a derived, generalized view). If we ever split the
  projection into "raw mirror" and "generalized semantic layer", the
  semantic layer wants the name `cortex` and `brain` stays the whole.

Decision: keep `brain` for the shipped projection. Reserve `cortex` as
the name for a future semantic/fact layer if one is built (see below).

## The mappings that change decisions

**famstack has a hippocampus, not yet a neocortex.** CLS theory: a
fast one-shot episodic store (hippocampus) feeds a slow store that
distills many episodes into generalized knowledge (neocortex). The
vault is a clean hippocampus — fast capture, pattern-separated
(content-addressed, distinct records), episodic. The brain projection
looks like the neocortex but is not: it is a rendered view, not a
generalized semantic store. The real neocortex — the FactStore and
EntityRegistry from `domain-model.md` — is unbuilt. This reframes them
from features to the missing half of a two-system memory: the part
that turns "a folder of documents" into "knows things about the
family". If built as a distinct layer, its name is `cortex`.

**Pruning belongs in projections, never in source.** Forgetting is
adaptive: a store that never forgets overfits and retrieves poorly. The
vault must never forget (record of record, the chronicle, the
durability promise) — so pruning is not a property of memory. It is a
property of brain: the derived layer keeps gist over specifics, shows
current state not full history, summarizes rather than accumulates.
Forget in the projection, never in the source.

**Retrieval quality is coupling quality.** The 2026 finding: content
and context couple through co-firing, strongest on correct trials.
famstack's coupling between an entity and its contexts is currently
heuristic (longest-synonym-wins identity), which is weak, error-prone
co-firing — "Maggie" and "Margaret" fail to fire together, so recall
does not complete. The EntityRegistry is not deduplication hygiene; it
is strengthening the co-firing pathway, which is the mechanism that
turns a partial cue into a correct recall. Registry = retrieval
quality, not cleanup.

## Mappings that are only metaphor (do not over-fit)

- **Indexing theory** (hippocampus stores pointers, not content)
  validates the pointer-memory and `/go` bets we already made; it does
  not ask for new work.
- **Reconsolidation** (recall makes memory editable) is a near-miss
  worth *not* chasing: famstack made recall read-only on purpose, so it
  avoids the corruption reconsolidation causes. The correction chain is
  the one deliberate write-on-recall path; keeping it the *only* one is
  better than the biology.
- The source paper is a single-neuron encoding study. Its one durable
  gift is the principle **separate content from context, invest in the
  links** — the rest of the lens rests on the older frameworks, not on
  it. Resist turning one encoding result into a brain blueprint.
