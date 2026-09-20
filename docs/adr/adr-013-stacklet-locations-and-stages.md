# ADR-013: Stacklet Locations and Stages

## Status
Accepted

## Context
A stacklet was "a directory with a `stacklet.toml` under `stacklets/`". One
location, one git repo, one release. Everything that did not fit that shape
needed a workaround: a `.git/info/exclude` entry for a stacklet that was not
meant to be public, a long-lived branch for one that was not finished. Both
grow stale, and neither survives a rebase of `main`.

Three kinds of stacklet do not belong in `stacklets/`:

- **Private.** Written for one household or one office, not for the project.
- **Third-party.** Someone else's stacklet, with its own repo and release cycle.
- **Unfinished.** Ours, but not ready to be presented as part of a release.

The first two are a question of location. The third is orthogonal: a stacklet
in either location can be half-built, and an operator who starts one deserves
to be told before the containers come up, not after their family is using it.

Alternatives considered for location:

- **Keep one directory, gate with git.** What we were doing. A
  `.git/info/exclude` entry is local and invisible to everyone else, and a
  branch that carries a stacklet has to be rebased forever.
- **A directory under `data_dir`.** It would be covered by the one backup the
  admin guide mandates, and `stack uninstall` would clear it out with
  everything else. Rejected: `~/famstack-data/<id>/` is what running services
  write, and a stacklet is source code. Putting code there also makes
  `extensions` a reserved stacklet id.
- **`extensions/` inside the repo, beside `stacklets/`.** The most legible
  option, and the one that reads best in the tree. Rejected: it only works
  because today's install method is "clone a git repo". The repo is what an
  upgrade replaces, so a location inside it is a promise we can only keep
  while that stays true, and defaults are sticky long after.

The three populations this serves do not want the same thing. An incubating
stacklet of ours is disposable and wants to be near the repo; a household's
own stacklet must outlive every reinstall; a third-party one wants an
installer and a trust boundary. Only the first exists today, but a default is
picked once, so it is picked for the one with the strictest requirement.
- **Install by URL** (`stack extensions add <git-url>`). Useful later, and it
  only makes sense once a second location exists at all.

## Decision
Discovery searches the repo's `stacklets/` first, then every directory in
`[core] extension_dirs`, in the order given. That key is a list and defaults
to `["~/<product>-extensions"]`, so `~/famstack-extensions` on a famstack
instance; a bare string is read as a one-entry list. A directory with a
`stacklet.toml` is a stacklet in any of them.

Nothing creates that directory. An admin who has no extensions has no such
directory, and discovery skips a configured path that is not there. It is
outside both the repo and the data dir on purpose: the repo is replaced on
upgrade, and the data dir is what running services write.

The first tree to claim an `id` keeps it. The repo therefore wins a clash with
an extension, and an earlier extension dir wins a clash with a later one, the
way a `PATH` resolves. An extension can add a stacklet, never replace one the
release ships.

Everything downstream resolves a stacklet by `id` rather than by path, so
hooks, CLI plugins, secrets, `{photos_url}`-style template variables and
`stack up <id>` work identically wherever it lives. Origin is reported rather
than inferred: every stacklet carries `source` (`repo` or `extension`) and
`path` through `discover`, `list`, `status` and `up`, `list`/`status` also
report `extension_dirs`, and both printers group extensions under their own
heading instead of mixing them into one column.

The bot runner gets a second read-only bind mount,
`${EXTENSIONS_DIR}:/extensions:ro`, and searches both trees in the runtime's
order, so an extension stacklet can ship a bot. A compose file cannot iterate
a list, so exactly one extension dir is mounted: the first. A stacklet that
ships a bot from any other one would fail silently, so `stack up` says so.

A bind mount needs its source to exist, which collides with creating nothing.
While the mounted dir is absent the mount source resolves to an empty
stand-in, `.stack/no-extensions`, and core's `on_start` creates whichever of
the two applies. Any `stack up` re-renders core's env, so the real directory
takes over on its own once there is one.

A manifest declares how finished it is with `stage`. The default is `"stable"`
and is silent. Any other value is repeated back: `stack list` marks it, and
every `stack up` warns that the stacklet can change or disappear and is not
for production. `"beta"` and `"incubating"` have their own wording; an
unrecognised value gets a generic warning rather than a validation error,
because the manifest is making a claim and the runtime's job is to pass it on.

## Consequences
- A private or incubating stacklet needs no ignore rule and no branch. It is
  not in the repo at all, so `git status` never sees it and it can be its own
  git repo.
- A second directory is a search path, not a full second home: its stacklets
  install, run and get CLI plugins, but a bot there is invisible to the runner
  until the directory is first in the list. Mounting all of them needs a
  generated compose override, which is a framework change and its own decision.
- An extension now survives everything famstack does: upgrade, `stack
  uninstall`, a fresh clone elsewhere. Nothing backs it up either. The backup
  section of the operator docs lists it, and an extension that matters should
  have a git remote.
- An admin who would rather keep it elsewhere, including inside the data dir,
  points `extension_dirs` there. The uninstall prompt names any extension dir
  it is about to delete.
- The default is derived from `[core] name`, so an instance that renames the
  stack reads `~/<that name>-extensions`. The framework stays domain-agnostic;
  nothing in `lib/stack/` spells "famstack".
- The install wizard still scans only the repo. Extensions are installed after
  setup, with `stack up <id>`.
- Symlinking a stacklet into the extensions dir works for discovery and hooks,
  but not for a bot: the bind mount exposes the link, not its target.
- `stack extensions add <git-url>` becomes possible later without another
  decision. It would clone into this directory and pin a commit.
