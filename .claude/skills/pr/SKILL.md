---
name: pr
description: Write the pull request for the current famstack branch, a title that is the changelog line and a description by the repo's Pull requests rules, both checked with tools/commit-lint. Use when asked to write, draft or open a PR, or for its title or squash message.
---

# Write the pull request

A PR is squash merged, so its title becomes the commit subject on main, and
that subject is the line in the release notes. The rules live in
`docs/agent/dev.md`, sections "Commits: the subject is the changelog" and
"Pull requests". Read both before writing: this file is the procedure, dev.md
is the rule, and where they differ dev.md wins.

## 1. Read the branch

```bash
git fetch origin
git log --reverse --format='%h %s%n%b' origin/main..HEAD
git diff --stat origin/main...HEAD
```

Read the diff wherever the subjects don't say what an admin would notice. If
`HEAD..origin/main` is not empty, say the branch is behind; don't rebase unasked.

## 2. One changelog entry, or two

List what a reader of the release notes would see: commits of a shown type
(`feat`, `fix`, `security`, `perf`, `docs`) and any commit with `!`, `Upgrade:`
or `BREAKING CHANGE:`. Tests, refactors and chores that serve one of those
belong to it and add no entry of their own.

If that leaves two changes a reader would see separately, stop there. Don't
write a title with "and": say the PR should be split and which commits go in
which PR. One PR is one changelog entry.

## 3. The title

- `type(scope)!: subject`, with the type and scope from the tables in dev.md.
  The scope is what a reader recognises (a stacklet or a bot), never a file.
- The change as an admin sees it: imperative, lowercase after the colon, no
  full stop, no class or function names. At most 72 characters as commit-lint
  counts them; dev.md asks under 70 for a PR. The ` (#N)` GitHub appends is
  not counted.
- `!` only when the admin has to act, and then the description carries the
  `Upgrade:` or `BREAKING CHANGE:` footer.
- For a one-commit branch whose subject already passes, use that subject:
  GitHub takes the commit's subject as the squash title of a one-commit PR.

## 4. The description

```
## Summary
- <what changes for the admin, and why>

Upgrade: <what the admin must do, as markdown>
Refs: FAM-12
```

- One to three bullets. The footers only when they apply.
- No "Test plan" section. Test output and measurements go in a PR comment.
- No em dashes: a comma, colon, period or parentheses.
- No Co-Authored-By trailer, no "Generated with" line, no tool link. The repo
  is public and commit-lint rejects all three.

## 5. Check before showing it

```bash
python3 tools/commit-lint --title "<title>"
python3 tools/commit-lint --body "<description>"
python3 tools/commit-lint --range origin/main..HEAD
```

The last one is what CI runs over every commit in the PR. Fix and re-run until
all three pass. Rewording a commit that is already pushed needs a force push,
which needs Arthur's go.

Then show the title, the description in a code block, and the three results.

## Opening it

Pushing and opening the PR are Arthur's call. Ask for his go every time, even
if he gave it for the last one. With it:

```bash
git push -u origin HEAD
gh pr create --base main --title "<title>" --body-file <file>
```

`--draft` if he asks for a draft. Never merge.

## At merge time

The repo's squash setting fills the commit body from the branch's commit
messages, not from the PR description. A footer in the description reaches
main only if the merge uses the description as the body, in the merge dialog
or with `gh pr merge --squash --body-file`. Merging is Arthur's: say this when
handing the PR over, and don't run it.

A PR Arthur marks for a rebase merge is the exception: every commit lands as
its own changelog line, so each one has to pass commit-lint on its own, and
the title is not a changelog line.
