---
name: pr
description: Write the pull request for the current branch: a title that is the changelog line and a description per the Pull requests rules, both checked with tools/commit-lint. Use when asked to write, draft or open a PR, or to produce its title or squash message.
---

# Pull request

A PR is squash merged: the title becomes the commit subject on main and the
release-notes line. `docs/agent/dev.md` ("Commits: the subject is the changelog",
"Pull requests") holds the rules; this file is the procedure. dev.md wins.

## 1. Read the branch

```bash
git fetch origin
git log --reverse --format='%h %s%n%b' origin/main..HEAD
git diff --stat origin/main...HEAD
```

Read the diff wherever a subject does not state the user-visible change. Report a
non-empty `HEAD..origin/main` as "branch is behind". Do not rebase unasked.

## 2. One entry or two

Count the entries a release-notes reader sees: commits of a shown type (`feat`,
`fix`, `security`, `perf`, `docs`) plus any commit carrying `!`, `Upgrade:` or
`BREAKING CHANGE:`. Tests, refactors and chores serving one of those add no entry.

Two visible entries mean two PRs. Never write a title containing "and": report
the split and which commits belong to which PR.

## 3. Title

- Format: `type(scope)!: subject`. Types and scopes come from the dev.md tables.
- The scope names what a reader recognises, a stacklet or a bot, never a file.
- Imperative, lowercase after the colon, no full stop, no identifiers.
- Limit 72 characters as commit-lint counts them; dev.md asks for under 70. The
  ` (#N)` appended at merge does not count.
- Use `!` only when the user must act. The description then carries `Upgrade:`
  or `BREAKING CHANGE:`.
- Single-commit branch whose subject passes: reuse that subject.

## 4. Description

```
## Summary
- <user-visible change, and why>

Upgrade: <required action, as markdown>
Refs: FAM-12
```

- One to three bullets. Footers only when they apply.
- No "Test plan" section. Test output and measurements go in a PR comment.
- No em dashes. Use a comma, colon, period or parentheses.
- No `Co-Authored-By`, no "Generated with" line, no tool link: this repository is
  public and commit-lint rejects all three.

## 5. Check

```bash
python3 tools/commit-lint --title "<title>"
python3 tools/commit-lint --body "<description>"
python3 tools/commit-lint --range origin/main..HEAD
```

CI runs the third over every commit in the PR. Fix and re-run until all three
pass. Rewording a pushed commit needs a force push: request approval first.

Output: the title, the description in a code block, the three results.

## 6. Open

Pushing and opening a PR need explicit approval, for every push. With approval:

```bash
git push -u origin HEAD
gh pr create --base main --title "<title>" --body-file <file>
```

Add `--draft` when asked. Never merge.

## Merge

The repository builds the squash body from the branch's commit messages, not
from the PR description. A footer written in the description reaches main only
when the merge uses the description as the body: the merge dialog, or
`gh pr merge --squash --body-file`. State this when handing the PR over.

Rebase merge, when requested, is the exception: every commit becomes its own
changelog line, each must pass commit-lint alone, and the title is not a
changelog line.
