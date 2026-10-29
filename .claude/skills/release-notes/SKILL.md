---
name: release-notes
description: Turn the generated release notes into the ones a release publishes. Generates the entries with script/release-notes, then merges entries that describe one change, drops work that never shipped and internal changes, rewrites entries into one consistent voice, names the issue each fix closes, and writes the Highlights and Upgrading sections, with an audit that accounts for every generated entry. After publishing, puts every fixed issue in the release's milestone. Use before tagging a release, or when asked to draft, clean up or review release notes.
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

Then find the PRs of the release and the issues they close. A rebase
merge leaves no `(#N)` in the subject, so the generated entry has none.
`base` is the branch the tag is cut from; add every branch that was
merged into it as a whole (for 0.5: `feature/0.5.0-beta.1
feature/memory-compile`):

```bash
base="main"
since=$(git log -1 --format=%cI "$prev")
closing='(close[sd]?|fix(e[sd])?|resolve[sd]?):? *#[0-9]+'
for b in $base; do
  gh pr list --base "$b" --state merged --search "merged:>$since" --limit 300 \
    --json number,title,body --jq '.[] | [.number, .title, (.body | gsub("\n"; " "))] | @tsv'
done | while IFS=$'\t' read -r n title body; do
  echo "#$n $title | closes: $(echo "$body" | grep -oiE "$closing" | grep -oE '[0-9]+' | sort -un | xargs)"
done > /tmp/prs.txt
git log --format='%h %s%n%b' "$prev..HEAD" | grep -iE "$closing" >> /tmp/prs.txt
```

The closing lines are read from the PRs and the commits, because GitHub
links `Closes: #N` (the `pr` skill) only for a PR into `main`. A PR into
a `feature/*` base closes nothing and links nothing, and a rebase of the
base cuts the link between a commit and its PR. Match each PR to its
entry by title. The first release of a new line (0.5.0-beta.1) was built
next to the previous one: its `since` is the day its base was cut, not
the previous tag. An issue that a PR fixed without a closing line is not
in the list: add it by hand.

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

- **<Stacklet or Bot>:** <entry> (#PR, fixes #issue)
```

Entries:

- One line, at most about 100 characters, starting with a verb in the
  imperative, like the commit subjects: "Choose the AI server with
  `./stack ai switch`".
- The admin's words: commands, rooms and pages they see. No function,
  class, file or module names.
- Commands and paths in backticks. Keep the `(#N)` of every PR the entry
  covers, and name every issue those PRs close: `(#364, fixes #334)`. The
  person who reported the issue finds the release that fixed it.
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

Below the entries, list the fixed issues, each with the PR that closed it.
An issue closed by a PR whose entry was dropped is not fixed by this
release: say why.

## 5. Milestone

After the release is published, every issue it fixes carries a
milestone: the first release that contains the fix. That is this tag,
or the prerelease that shipped the fix before it. An issue that has a
prerelease's milestone keeps it.

- **A prerelease** (`vX.Y.Z-beta.N`) leaves its issues open. The
  milestone says that the fix is in a prerelease but not yet in a
  stable release.
- **A stable release** (`vX.Y.Z`, from `main`) closes every issue it
  fixes. `Closes: #N` on `main` closes most of them; close the others by
  hand, among them the issues its prereleases left open.

The milestone is named after the tag, and its due date is the day the
release was published. Create it, or use it when it was made to plan
the release:

```bash
tag=v0.4.2
day=$(gh release view "$tag" --json publishedAt --jq '.publishedAt[:10]')
m=$(gh api repos/famstack-dev/famstack/milestones -f title="$tag" \
  -f description="Fixes released in famstack ${tag#v}" --jq .number)
gh api -X PATCH "repos/famstack-dev/famstack/issues/<N>" -F milestone="$m"   # each fixed issue without one
gh issue close <N> --reason completed                                        # stable release only
gh api -X PATCH "repos/famstack-dev/famstack/milestones/$m" \
  -f due_on="${day}T00:00:00Z" -f state=closed
```

GitHub keeps only the date of `due_on`, so the time is always midnight
UTC.

A milestone made for planning can hold issues the release did not fix.
Move them to the next milestone before closing it, because every issue
left in a closed milestone reads as fixed in that release.

## Output

The notes as markdown, ready for `gh release create --notes-file`, the
audit, and the issues for the milestone. Publishing is the maintainer's:
the release gate in dev.md, steps 7 and 8.
