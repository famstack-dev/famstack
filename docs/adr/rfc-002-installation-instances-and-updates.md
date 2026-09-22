# RFC-002: Installation, Instances and Updates

## Status
Draft. No decision taken. Written after the extension-stacklets work surfaced
how much of the instance lives inside the code tree.

## Context

Today one directory is four things at once:

| Role | What lives there |
|---|---|
| The product | `lib/stack/`, `stacklets/`, `docs/` |
| The installation | the git checkout you run `./stack` from |
| The instance | `stack.toml`, `users.toml`, `.stack/secrets.toml`, setup markers |
| The git repo | `main`, tags, your local edits |

Data is already out (`~/famstack-data/`), and extension stacklets are now out
(`~/famstack-extensions/`). Everything else is entangled.

Updating, as documented in the admin guide:

```bash
git pull
./stack restart <stacklet>    # for any stacklet whose code changed
```

Container images update on a separate track: Watchtower pulls nightly at 3am
for every container whose stacklet declares `[upstream] channel = "patch"`,
which is the default (`lib/stack/stack.py:542`).

## The problems, precisely

**P1. You cannot install a release.** The README says `git clone`, which puts
you on `main`. Reaching `v0.3.0-beta.3` means `git checkout v0.3.0-beta.3`,
which detaches HEAD, after which `git pull` no longer does what the admin
guide says it does. There is no `stack update`. Five tags exist and nothing in
the product knows about them.

**P2. The version string was a constant in the working tree.** *(fixed)*
`VERSION = "0.3.0-beta.3"` was what `stack version` and `stack status`
reported, so on a checkout between tags it named a hundred different trees.
Every surface now derives it from `git describe --tags --dirty`, which
answers by comparing the tree against the commit a tag points at.

**P3. A release does not describe what runs.** *(narrowed: every image now
names a minor line or an exact version, so installs of one tag differ by patch
releases only)* Across the stacklets there are
19 image references. Exactly one is pinned to an exact upstream version
(`paperless-ngx:3.0.4`). Five are `:latest` (element-web, synapse, tika,
adguard, watchtower), one is `:main` (open-webui), one carries no tag at all
(openedai-speech-min), five float on a major or minor (`forgejo:14`,
`postgres:16-alpine`, `valkey:8-alpine`, `gotenberg:8`, `caddy:2-alpine`), and
Immich takes `${IMMICH_VERSION:-release}`. Two people installing the same tag
a month apart run different software, and neither can say which.

**P4. Two update channels that do not know about each other.** Watchtower
moves images nightly; git moves code when the admin remembers. The Paperless
3.x incident is written up in the admin guide: Watchtower rolled instances
from 2.20 to 3.0 across a major version while the code that configures it
stayed behind, and the documented recovery is a backup most people do not
have. That is the failure mode of two uncoordinated channels, not a
Paperless-specific accident.

**P5. Nothing migrates.** There is no migration code anywhere in `lib/stack/`.
A release that changes a config key, a secret name or a data layout relies on
the admin reading release notes. A live example found while writing this: the
installer's `write_stack_toml()` never wrote `[core] name`, so every instance
it has ever created calls itself "stack" rather than "famstack". That is
cosmetic until something derives a path from it, which the extensions work
just did. New installs are fixed; every existing instance still has the old
file and nothing tells it.

**P6. Nothing knows whether the running system matches the checkout.** The
admin guide's instruction is "if in doubt, restart everything". `stack doctor`
already compares each container's environment against freshly rendered env and
reports drift, which is the right shape; it just has no equivalent for image
digests or code.

**P7. Documented commands that do not exist.** `docs/agent/ops.md` listed
`./stack updates`. There is no such command. The row is gone, and both the
operator docs and the admin guide now describe the real tag-to-tag procedure,
including the stash dance a local edit forces.

## What "proper" would mean

Four pillars, in dependency order:

1. **A versioned artifact.** Installing names a version, and that version says
   what runs, including image digests.
2. **A stable on-disk layout.** An instance lives somewhere an upgrade cannot
   touch, so the artifact can be replaced wholesale.
3. **An update command.** One verb: fetch, show what changes, migrate, apply,
   verify, and say what it restarted.
4. **Instance identity.** Every runtime name derives from an instance, so more
   than one can coexist.

Pillars 2 and 4 are the same refactor seen from two angles. That is the
central claim of this document: **moving instance state out of the code tree
unlocks packaging, safe updates and multiple instances at once.** Everything
else is downstream of it.

## Option space: what we ship

### A. Git checkout plus `stack update` (evolve what we have)

`stack update` fetches tags, shows the release notes for the delta, checks out
the tag, runs migrations, restarts what changed, and runs `doctor`.

- **For:** days of work, not weeks. Keeps clone-and-hack, which is the
  contributor funnel. No packaging infrastructure, no signing, no formula.
  Rollback is `git checkout <previous tag>` for the code half.
- **Against:** the admin still needs git and a toolchain. A dirty working tree
  blocks the update, which we have to handle in prose. It cannot be
  unattended, because a checkout can conflict.

### B. Homebrew tap

`brew install famstack-dev/tap/famstack`, then `brew upgrade famstack`.

- **For:** the native macOS answer for a macOS-only product. Homebrew is
  already a hard prerequisite, so it adds no new dependency. Versioning,
  rollback and uninstall come for free. `brew upgrade` is a verb people
  already run.
- **Against:** we maintain a formula per release. The prefix is replaced on
  upgrade, so pillar 2 is a hard prerequisite rather than a nice-to-have. A
  hacked stacklet inside the prefix is destroyed by the next upgrade, so the
  dev path has to be explicitly supported (`STACK_DIR` plus a `--root`).

### C. PyPI wheel plus pipx

- **For:** `pip install famstack` is familiar, and the CLI is stdlib-only so a
  wheel is trivial. Needs `[build-system]` and `[project.scripts]`, neither of
  which exists today.
- **Against:** buys nothing over Homebrew on a macOS-only product, and adds a
  Python-version matrix we do not want. The stacklets tree is data (compose
  files, hooks executed from disk, taxonomy), which fits a wheel badly.

### D. Signed tarball and a `curl | sh` installer

- **For:** total control over layout and upgrade, no third-party index.
- **Against:** we own the updater, the signing and eventually notarization.
  This is the most work for the least differentiation, and `curl | sh` is a
  hard sell to the privacy-minded audience this product is for.

**Recommendation:** A now, B as the mainstream path once pillar 2 lands. Not
C. D only if we ever ship something that must be notarized.

## Multi-instance: what is actually in the way

Config isolation already exists: `STACK_DIR` points the CLI at a different
`stack.toml`, `users.toml` and `.stack/` while sharing one stacklets tree
(`lib/stack/cli.py find_instance_dir`). The integration rig uses it.

Runtime isolation does not exist. Every name below is machine-global:

| Resource | Where it is fixed |
|---|---|
| Compose project and `container_name` (`stack-<id>`) | every `docker-compose.yml` |
| Docker network `stack` | `lib/stack/docker.py:117` |
| Service ports 42xxx | each `stacklet.toml` |
| Host API port 42001 | `stacklets/core/famstack-api.py:34` (env-overridable, but the launchd wrapper does not set it) |
| launchd label `dev.famstack.api` | `stacklets/core/hooks/on_start.py:11` |
| launchd label `dev.famstack.whisper` | `stacklets/ai/hooks/on_install.py:39` |
| The user's crontab entry | backup stacklet hooks |
| `omlx` via Homebrew and `~/.omlx/models` | machine-wide by nature |

One subtler than the rest: `project_states()` (`lib/stack/docker.py:254`) maps
any compose project named `stack-*` to a stacklet id. A second instance would
report the first instance's containers as its own, so `stack list` lies before
anything collides.

This is why `tests/e2e/stacktests` takes the dev instance down rather
than running beside it, and why the operator docs say two instances cannot run
on one Mac.

**Honest read on demand:** the only real consumers today are the test rig and
a developer wanting dev beside prod. A household needs one instance. An office
deployment might want more, and that is the question to answer before building
the UX.
The namespacing prep is worth doing anyway, because `project_states()`
cross-talk is a correctness bug waiting for the first person who tries.

## Recommended path

Each phase stands alone and ships on its own. Gates are what proves it.

### Phase 0: make a release installable (days)

- Install instructions clone and check out the latest tag. *(open)*
- ~~`stack update`: fetch tags, print the delta, stash a dirty tree, check
  out, restart stacklets whose files changed, run doctor.~~ Shipped. It
  refuses to restart anything when the stash conflicts, and prints the
  recovery commands instead.
- ~~`stack version` reports the checkout's tag, the SHA, and whether the tree
  is dirty.~~ Shipped. `git describe --tags --dirty` compares the tree against
  the commit a tag points at, and every surface prints the same derived
  string. The constant stays in `cli.py` for the release gate to check
  against the tag; it is no longer what anyone is shown.
- ~~Delete `./stack updates` from ops.md or implement it.~~ Done: the row is
  removed and the manual procedure is documented, which is what `stack update`
  has to automate.

*Gate:* on the rig, from `v0.3.0-beta.2`, `stack update` lands on beta.3,
restarts exactly the stacklets that changed, and `stack doctor` is clean.

### Phase 1: instance state leaves the code tree (the enabling refactor)

- `stack.toml`, `users.toml` and `.stack/` move to `~/.famstack/<instance>/`,
  or `~/Library/Application Support/famstack/<instance>/`.
- `STACK_DIR` stays the override; the default stops being the checkout.
- `stack migrate` moves an existing instance and prints what it did.
- A checkout that still holds a `stack.toml` fails loudly and names the
  command, rather than silently running a half-migrated instance. Pre-1.0
  says no compatibility shims; a one-shot migration with a hard cut honours
  that better than a fallback path.

*Gate:* a fresh clone at a new path, pointed at a migrated instance, brings
the whole stack up with no state in the checkout. `git clean -xdf` in the
checkout destroys nothing but build artifacts.

### Phase 2: a release describes what runs (days, independent of 1)

- Pin every image to an exact upstream version, as `docs` already is.
- Watchtower keeps running, but against exact tags it can only deliver digest
  rebuilds, never a major jump.
- A release manifest (`versions.toml` or generated from the compose files)
  lists the image set for that tag, and `stack doctor` compares what is
  running against it.

*Gate:* two fresh installs of the same tag, a month apart, produce the same
image digests. The Paperless failure mode cannot recur without a code change.

### Phase 3: migrations (weeks)

- `schema_version` in `stack.toml`, a `migrations/` directory, and `stack
  update` applying them in order before restarting anything.
- Forward-only. Say so plainly: Paperless proved that a data migration is not
  reversible, so the safety net is backups, not rollback.
- Which makes backup coverage (Postgres dumps, not just files) a prerequisite
  for any unattended updater.

*Gate:* an instance created by today's installer, with no `[core] name`, is
migrated by `stack update` and ends up identical to a fresh install.

### Phase 4: packaged artifact (weeks, needs 1 and 3)

Homebrew tap. `brew upgrade famstack` replaces the prefix; the instance is
untouched because of Phase 1; migrations run on the next `stack` invocation
because of Phase 3.

*Gate:* `brew install`, `brew upgrade`, `brew uninstall` leave a working
instance, an upgraded instance, and an instance whose data survived.

### Phase 5: instance namespacing (weeks, only on demand)

Derive every runtime name from an instance id: compose project prefix,
network, port offsets, launchd labels, API port. Scope `project_states()` to
the instance's own prefix. Add `stack --instance <name>` and a registry.

*Gate:* two instances up at once on one Mac, each `stack list` reporting only
its own containers, both reachable.

## Pros and cons versus today's model

| | Today (git checkout) | Proposed (Phases 0 to 4) |
|---|---|---|
| Install | `git clone`, one command, no packaging work | `brew install`, or clone for contributors |
| What you get | whatever `main` is at that minute | a named version with a known image set |
| Update | `git pull` plus manual restarts by feel | one command that migrates, restarts and verifies |
| Rollback | `git checkout` for code; data is one-way regardless | same, but stated honestly and backed by backups |
| Hackability | edit anything in the tree, it takes effect | preserved via a dev checkout; the packaged prefix is read-only |
| Instance state | inside the checkout, gitignored | outside it, survives any reinstall |
| Multi-instance | config only, runtime collides | possible, once namespaced |
| Cost to us | zero | a formula per release, a migration per schema change, image pins to maintain |
| Risk | drift nobody can see | a wrong migration hits every instance at once |

The honest summary: today's model is cheap for us and fine for someone who
reads release notes. It fails the person who installed once in March, let
Watchtower run, and now cannot say what they are running or how to move to
the current tag. That person is the target audience.

## Non-goals and risks

- **Do not break clone-and-hack.** It is how contributors arrive and how
  stacklet authors work. Any packaged path keeps a documented dev checkout.
- **Do not promise rollback.** Forward-only with backups is the truthful
  contract while data migrations are one-way.
- **Do not build the multi-instance UX before there is demand.** Do the
  namespacing prep, which is a bug fix either way.
- **Watchtower's default deserves a product decision.** Nightly auto-update is
  a real feature for families and it is also what broke Paperless. Exact pins
  make it safe; keeping floating tags and auto-update is the combination that
  bites.

## Open questions

1. Is clone-and-hack a supported install path at 1.0, or the contributor path
   only?
2. Does an office deployment need several instances per machine, or one per
   box?
3. Are we willing to own a Homebrew tap, formula and bottle, per release?
4. Should Watchtower stay on by default once releases pin exact versions?
