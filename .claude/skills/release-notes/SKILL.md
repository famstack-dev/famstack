---
name: release-notes
description: Turn the generated release notes into the ones a release publishes. Generates the entries with script/release-notes, then merges entries that describe one change, drops work that never shipped and internal changes, rewrites entries into one consistent voice, and writes the Highlights and Upgrading sections, with an audit that accounts for every generated entry. Use before tagging a release, or when asked to draft, clean up or review release notes.
---

# Release notes

`script/release-notes` turns commit subjects into a change list. That list
is the input, not the release notes: it repeats one feature across the
commits that built it, lists fixes to code no release ever had, and mixes
internal work with what an admin notices. This skill is the pass between
the two. `docs/agent/dev.md` ("Releases") is the gate it sits in.

## 1. Generate

```bash
prev=$(git describe --tags --abbrev=0 --match 'v*')
script/release-notes "$prev..HEAD" > /tmp/generated.md
git log --reverse --format='%h %s%n%b%n---' "$prev..HEAD" > /tmp/commits.txt
```

The commit bodies are the source for any entry the subject does not
explain. Read the body before rewriting an entry, never guess from the
subject.

## 2. Sort every generated entry

Each entry ends in exactly one of four places:

- **Keep**: an admin or a family member notices it, and it did not exist
  in `$prev`.
- **Merge into <entry>**: it describes the same change as another entry
  (a feature and its follow-ups, the commits of one PR, a feature and the
  fixes to it made before the release). The merged entry says what is
  true at the tag.
- **Drop, unreleased**: it changes or fixes something that did not exist
  in `$prev`. Check with `git show "$prev:<path>"` or by finding the commit
  that introduced the thing. A rename of an unreleased command is not an
  entry; the command appears once, under its final name.
- **Drop, internal**: nobody running famstack notices it (metrics, cache
  keys, test tooling, refactors that slipped into a shown type).

Action required entries are never dropped for being internal. Check each
against the rule in dev.md: breaking is measured against `$prev`.

## 3. Write

```
## Highlights

<3 to 5 short paragraphs: what this release makes possible, for the admin
and the family, in plain words. The most visible change first.>

## Upgrading from <prev>

<Every action, in the order the admin performs it, as runnable commands.
Merge the Upgrade: and BREAKING CHANGE: footers; one restart line for all
stacklets that need one. "No action needed" when there is none.>

### Security
### Added
### Fixed
### Documentation

- **<Stacklet or Bot>:** <entry> (#PR)
```

Entries:

- One line, at most about 100 characters, starting with a verb in the
  imperative, like the commit subjects: "Choose the AI server with
  `./stack ai switch`".
- The admin's words: commands, rooms and pages they see. No function,
  class, file or module names.
- Commands and paths in backticks. Keep the `(#N)` of every PR the entry
  covers.
- No em dashes. No "now", "new" or "improved": every entry is new.
- Group by the rendered scope, as generated. Sort entries within a group
  by how much an admin notices them.
- A section with no entries is left out.

Plain, factual prose, as AGENTS.md asks of every text here: this one is
published on GitHub and on famstack.dev.

## 4. Audit

Next to the notes, never inside them, list every generated entry with its
fate: kept as is, rewritten as, merged into, or dropped (unreleased or
internal), with the reason. Nothing may be missing from the audit: the
count of audit lines equals the count of generated entries.

## Output

The notes as markdown, ready for `gh release create --notes-file`, and the
audit. Publishing is the maintainer's: the release gate in dev.md, step 6.
