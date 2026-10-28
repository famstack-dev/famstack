# ADR-022: Extensions Are Copied From a Repo

## Status
Proposed, 2026-10-28. Amends [ADR-013](adr-013-stacklet-locations-and-stages.md).

## Context
ADR-013 gave stacklets the release does not ship a home outside the repo,
`[core] extension_dirs` (by default `~/<product>-extensions`), and left
installing by URL for later: "It would clone into this directory and pin a
commit." Until now an admin put a stacklet there by hand.

There is now a catalog, `famstack-dev/famstack-extensions`: one extension
per root folder, named after its `id`, each change reviewed in a pull
request. Other people will publish stacklets in repos of their own, some
holding one stacklet at the root, some several. Two questions follow: how a
stacklet gets from such a repo into the extension dir, and what an admin is
told before code nobody reviewed runs on their Mac.

A clone does not fit. Discovery reads `<extension dir>/*/stacklet.toml`, so a
clone of the catalog puts every extension one level too deep, and a sparse
checkout of one folder keeps its path (`b/b/stacklet.toml`, next to the
repo's root files). A symlink into a clone hides the stacklet from the bot
runner, whose bind mount shows the link, not its target (ADR-013). And a
clone is a working copy, which invites edits: updating an installed
extension from its repo is a use case, editing it in place is not.

## Decision

**1. An extension is copied, not cloned.** `stack extensions add` fetches the
repo into a temporary directory (depth 1, without blobs until they are
needed) and writes the stacklet's tree flat into the first extension dir,
the one the bot runner mounts, with `git archive <commit>:<path>`. The folder
is named by the manifest's `id`, not by the repo or its folder. The copy is
written beside the target and renamed into place, so a failed copy leaves
nothing behind. `add` creates the extension dir if it is missing and starts
nothing; `stack up <id>` does, as for any stacklet. Discovery and the bot
runner do not change.

**2. A repo is one extension or several.** With `stacklet.toml` at its root,
the repo is the extension and its folders are not searched. Otherwise every
folder one level down with a `stacklet.toml` is one, never deeper. Several
and none named: at a terminal `add` asks which one, without one it refuses
and names them. It never installs all of them, because each is code to
trust on its own.

**3. The folder says where it came from.** `add` writes `<id>/.source.toml`:

```toml
url    = "https://github.com/famstack-dev/famstack-extensions"
path   = "hue"        # "." for a repo that is one extension
branch = "main"
commit = "3f2a9c1e..."
tree   = "9b41d07a..."
```

`tree` is the git tree of `path` at `commit`. It changes exactly when
something under that path does, so a commit to another extension in the same
repo is not an update to this one. A folder without `.source.toml` was put
there by hand: `stack extensions` lists it and never touches it.

**4. The catalog is a config key.** `[core] extension_catalog`, by default
`https://github.com/famstack-dev/famstack-extensions`. `add <id>` installs from
it. `list` shows what it offers, matches what is installed by `.source.toml`,
and marks an extension whose tree there has moved on as having an update. It
keeps what it read in `.stack/catalog.json`, so listing never creates an
extension dir, and a catalog it cannot reach is shown as last fetched.

**5. Code from elsewhere warns, every time.** Any URL other than the
catalog's (compared without `.git` and a final slash) prints, before anything
is written: that it is not from the catalog and nobody reviewed it; its
source and commit; what it brings, read from its files (compose services,
host hooks, migrations, CLI commands, a bot); and that hooks and CLI commands
run on this Mac as the admin, with access to the stack's secrets. Then
`Install <id>? [y/N]`. `--yes` skips the question, never the warning. Without
a terminal and without `--yes`, it refuses. `stack up` of such an extension
prints one line naming where it came from.

**6. Refusals leave nothing behind.** Before writing, `add` refuses an `id`
the release ships (the release wins that clash, so it would never load), an
`id` already installed in any extension dir, a manifest that does not parse
or lacks `id`, `name` or `description`, and an `id` that is not a plain
lowercase name. A failed fetch shows git's reason.

## Consequences
- ADR-013's sentence about cloning and pinning is replaced: an extension is
  copied, and the commit is recorded beside it.
- An installed extension is not a git checkout. Working on one is a hand
  clone into the extension dir, as ADR-013 allows, which `stack extensions`
  leaves alone.
- The trust boundary is the catalog's review. Pointing
  `extension_catalog` at another repo moves that boundary with it.
- The warning informs, it does not contain: once installed, an extension's
  code runs with everything the stack has.
- `list` fetches the catalog on every run, about a second over the network.
  Extensions from other repos are only compared with their repo by an
  update.
- Next: `stack extensions update` (compares the tree, replaces the folder in
  one swap, keeps what the stack wrote into it) and `remove`. `stack update`
  naming catalog updates follows after that.
