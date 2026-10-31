# ADR-022: Extensions Are Copied From a Repository

## Status
Proposed, 2026-10-28. Amends [ADR-013](adr-013-stacklet-locations-and-stages.md).

## Context

ADR-013 put the stacklets that famstack does not ship into extension
directories (`[core] extension_dirs`, default `~/<product>-extensions`).
Until now, an admin copied a stacklet into such a directory by hand. ADR-013
left the install by URL for later: "It would clone into this directory and
pin a commit."

Now there is a catalog, the repository `famstack-dev/famstack-extensions`. Each
root folder of the catalog contains one extension, and the folder name is the
`id` of the extension. Each change to the catalog gets a review in a pull
request. Other people will publish extensions in their own repositories. Some
of these repositories contain one extension at the root. Others contain more
than one.

This ADR answers two questions:

1. How does an extension get from a repository into the extension directory?
2. What does the admin see before code that nobody reviewed runs on their Mac?

A git clone does not work, for three reasons:

- Discovery reads `<extension dir>/*/stacklet.toml`. A clone of the catalog
  puts each extension one level too deep. A sparse checkout of one folder also
  keeps the folder path (`b/b/stacklet.toml`).
- A symbolic link into a clone hides the extension from the bot runner. The
  bind mount of the bot runner shows the link, not its target (ADR-013).
- A clone is a working copy, and a working copy invites edits. An update from
  the repository is a use case. An edit of an installed extension is not.

## Decision

### 1. `add` copies an extension

`stack extensions add` does these steps:

1. It fetches the repository into a temporary directory. The fetch has depth 1
   and gets the contents of a file only when it needs them.
2. It writes the tree of the extension into the first extension directory with
   `git archive <commit>:<path>`. The bot runner mounts this directory.
3. It names the folder with the `id` from the manifest, not with the name of
   the repository or of the folder.

`add` writes the copy into the folder `.incoming` in the extension directory,
and then renames it into place. Discovery and the bot runner do not look into
`.incoming`. Because of this, a failed copy leaves nothing behind. Also, no
command finds a copy that is not complete.

`add` creates the extension directory if it does not exist. `add` does not
start the extension. After the copy, `add` shows the path of the README of the
extension. It names `stack up <id>` only when the extension has containers or
hooks, or has no README. Discovery and the bot runner do not change.

### 2. A repository contains one extension or more

- If `stacklet.toml` is at the root, the repository is one extension. `add`
  does not search its folders.
- If not, each folder one level down with a `stacklet.toml` contains one
  extension. `add` does not search deeper.
- If the repository contains more than one extension and the admin gives no
  id, `add` asks which one. Without a terminal, `add` refuses and shows the
  ids.
- `add` never installs all extensions of a repository, because the admin must
  decide to trust each one.

### 3. The folder records its source

`add` writes `<id>/.source.toml`:

```toml
url    = "https://github.com/famstack-dev/famstack-extensions"
path   = "hue"        # "." for a repository that is one extension
branch = "main"
commit = "3f2a9c1e..."
tree   = "9b41d07a..."
```

`tree` is the git tree of `path` at `commit`. It changes only when a file under
`path` changes. Because of this, a commit to a different extension in the same
repository is not an update to this extension.

A folder without `.source.toml` was put there by hand. `stack extensions` lists
it, but never changes it.

### 4. The catalog is a configuration key

`[core] extension_catalog` holds the URL of the catalog. The default is
`https://github.com/famstack-dev/famstack-extensions`.

- `add <id>` installs an extension from the catalog.
- `list` shows the extensions of the catalog. It finds the installed catalog
  extensions through their `.source.toml`. When the tree of an installed
  extension changed in the catalog, `list` shows "update available".
- `list` keeps the last catalog that it read in `.stack/catalog.json`. Because
  of this, `list` never creates an extension directory. When `list` cannot
  reach the catalog, it shows the last catalog that it read.

### 5. Code from a different repository always shows a warning

For each URL other than the URL of the catalog, `add` shows a warning before it
writes files. `add` compares the URLs without `.git` and without a final slash.
The warning shows:

- that the extension is not from the catalog, and that nobody reviewed it;
- the repository and the commit;
- the parts of the extension that run code, read from its files: compose
  services, host hooks, migrations, CLI commands and bots;
- that hooks and CLI commands run on the Mac with the account of the admin,
  and can read the secrets of the stack.

Then `add` asks `Install <id>? [y/N]`. `--yes` skips the question, but not the
warning. Without a terminal and without `--yes`, `add` refuses. `stack up` of
such an extension shows the URL of its repository in one line.

### 6. A refusal writes nothing

Before `add` writes files, it refuses:

- an `id` that famstack ships. The extension cannot load, because the stacklet
  of famstack has priority;
- an `id` that is already installed in an extension directory;
- a manifest that cannot be read, or that has no `id`, `name` or `description`;
- an `id` that is not a plain lowercase name.

When the fetch fails, `add` shows the reason from git.

### 7. `update` replaces an unchanged folder in one step

`stack extensions update [<id>...] [--yes]` installs the newest version of
installed extensions. Without an id, it checks each extension that has a
`.source.toml`. It names a folder without one as "installed by hand" and
does not change it.

- `update` fetches the branch in `.source.toml`, not the default branch.
  Extensions from the same repository and branch share one fetch.
- `update` compares the git tree of the folder with `tree` in
  `.source.toml`. It ignores `.source.toml`, the `.env` that `stack up`
  writes, Python caches and `.DS_Store`. The `.gitignore` of the folder
  applies. A different tree means that a person changed the folder. Then
  `update` refuses and changes nothing, because a command must not delete
  local changes that it did not name. There is no flag that overrides this.
- When the tree of the path in the repository is the same as `tree`,
  the extension is up to date. A commit to a different path of the
  repository is not an update.
- `update` refuses a new version in these cases: the manifest cannot be read,
  the `id` changed, or the path is gone. The folder stays as it is.
- An extension from a repository other than the catalog shows the warning
  of section 5 for the new version. Then `update` asks `Update <id>? [y/N]`.
  `--yes` skips the question, but not the warning.
- `update` writes the new version into `.incoming/` of the extension
  directory that holds the folder. It copies the old `.env` into it and
  writes a new `.source.toml`. Then it renames the old folder into
  `.incoming/` and the new folder into place. There are two renames,
  because macOS cannot rename a directory over a directory that is not empty.
  When the second rename fails, the old folder goes back.
- `update` restarts nothing. For an extension that is set up, it shows
  `stack restart <id>`.
- A refusal of one extension does not stop the others. The exit code is 1
  when `update` refused one.

## Consequences

- This ADR replaces the sentence of ADR-013 about a clone and a pinned commit.
  An extension is a copy, and `.source.toml` records its commit.
- An installed extension is not a git checkout. To develop an extension, clone
  it by hand into the extension directory. ADR-013 allows this. `stack
  extensions` does not change such a clone.
- The review of the catalog is the trust boundary. A different
  `extension_catalog` moves this boundary to the reviewers of that catalog.
- The warning gives information. It does not limit the extension. After the
  install, the code of the extension runs with all the access of the stack.
- `list` fetches the catalog each time it runs. This takes 1 to 2 seconds over
  the network. `list` does not compare extensions from other repositories with
  their repositories. `update` does that.
- An admin who edits an installed extension cannot update it. To change an
  extension, clone it by hand and develop it there.
- Next steps: `stack extensions remove`. After that, `stack update` will show
  updates of catalog extensions.
