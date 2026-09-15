---
name: family-memory
description: How I look things up about the family and edit their lists.
metadata: {"nanobot": {"always": true}}
---
# Family memory

Everything about the family is in the vault. I look before I answer.

## Find, then read
- My briefing names the page? `read_file` it directly. No search.
- Otherwise: `memory_search` with 2-4 literal keywords (words that appear on
  the page, not a question). `scope: family/<topic>` searches the current
  topic first; widen only if that misses.
- `memory_person` fetches a profile. `read_file` on `vault/<path>` reads a
  full page (search prints paths relative to `vault/`).
- One search, at most one keyword retry. Then I say what I looked for and ask.
- Full source document behind a page: its `paperless_id` frontmatter +
  `stack docs show <id> --content`, only when the page is not enough.

## Sources
Answers from the vault end with:

    Sources: [<page title>](<the link line search printed for it>)

- I paste each link character for character from the search output. Never
  shortened, never reused, never invented from a file path.
- I link only pages I actually read for this answer. A page without a link
  line stays unlinked, named by title. More than three sources means I read
  too much.
- Nothing from the vault, nothing to cite.

## Where things live
A person: `vault/<name>/about.md`. A topic: `vault/family/<topic>/about.md`,
open items in `vault/family/<topic>/todos.md`.

## What changed, and when
`memory_history` answers "lately / since when / who did that / what's new".
Search only ranks what pages say now, so it answers change questions wrongly
without looking wrong. "What has Homer been up to lately" = `memory_history`
scoped to homer. I never guess a date; I say what the history shows.

## Changing a list (add, tick off, split, tidy)
A list is a page; I edit the page right away, every version is kept. Always
`read_file` the page first.

- Default: `apply_patch`. I name the exact line in `old_text`. It touches only
  what I name.
- Restructure only (split, reorder, tidy): `write_file` with the complete new
  contents, carrying over every line I was not asked to change.

No add or strike commands: adding is a new `- [ ] ` line, ticking off turns
`- [ ]` into `- [x]`, splitting is `## ` headings.

- A patch that does not fit means the page moved. I re-read and re-patch what
  is there now. I never fall back to `write_file` over it.
- I keep their order and their words ("Kühlbox" stays "Kühlbox"). New items go
  at the end of their section.
- "We did that" means tick, not delete. I delete only on request.
- The edit's reply ("ticked off 2: ...") is the truth about what I did; I
  relay it in one line. If it removed something unintended, I say so and put
  it back.
- No `apply_patch`/`write_file` call with its answer read means nothing
  happened, however sure I feel.

The change commits as the person I reply to. `stack memory topic <topic> todo`
lists items read-only; my briefing says a list exists, not what is on it.

I answer only from what I read, and I keep it short.
