# famstack development tasks.
#
# One name per lane. The aliases that used to exist (unit, fast, test,
# demo, lifecycle, e2e, smoke, test-all, test-integration) are gone: they
# were eighteen names for six jobs, and `test-all` ran only the lifecycle
# lane, which is worse than not existing.
#
# `make` on its own prints the table.

PYTEST = uv run --extra test pytest

.DEFAULT_GOAL := help

help:
	@printf '%s\n' \
	  "" \
	  "  Linting is automatic: the hooks run ruff on staged files and check" \
	  "  the commit subject. Nothing to remember per commit." \
	  "" \
	  "  Run the cheapest lane that proves what you changed." \
	  "" \
	  "  LANE                TIME    NEEDS          EXCLUSIVE  RUN IT WHEN" \
	  "  make lint           <1s     nothing        no         the whole tree, when you want it" \
	  "  make test-unit      ~75s    nothing        no         before a push, or when a piece of work is done" \
	  "  make test-lifecycle ~6m     Docker         yes        lifecycle, env, compose, ports, health" \
	  "  make test-demo      ~4m     demo instance  yes        it must work against the running bots" \
	  "  make test-smoke     ?       test-owned rig yes        quick cross-service check" \
	  "  make test-e2e       20-30m  uninstalled    yes        end of a branch, before review" \
	  "" \
	  "  make typecheck              types across the shipped code, not a gate" \
	  "  make hooks                  point git at the repo's hooks" \
	  "" \
	  "  Exclusive means it owns fixed container names and ports, so exactly" \
	  "  one run at a time on this Mac. ? means nobody has timed it yet;" \
	  "  if you run one, write the number down. Details: tests/README.md" \
	  ""

# The whole tree, the way CI runs it. Per commit the hook already covers
# the staged files, which is faster and is why nothing here is per commit.
lint: hooks
	uvx ruff check .

# Type check the shipped code. Not a gate: read what it says about the files
# you touched. `uvx basedpyright <paths>` narrows it further.
typecheck: hooks
	-uvx basedpyright

# Point git at the repo's own hooks. Git will not do this on clone, by
# design: a fresh clone must not be able to run code. The nearest honest
# thing is to do it the first time someone uses the repo's tooling, which
# is what the lanes below depend on.
#
# Only when unset, so an explicit choice (an absolute path, a different
# directory, deliberately no hooks) is never overwritten.
hooks:
	@tools/init-repo --hooks-only

# Offline. No Docker, no live services, nothing shared. Fast enough to run
# when a piece of work is finished, too slow to run on every commit.
test-unit: hooks
	$(PYTEST) tests/framework tests/stacklets -v --ignore=tests/framework/test_config_to_container.py

# Docker lifecycle: up, down, destroy, env rendering, container environment.
# Owns its own throwaway instance, but on fixed names and ports.
test-lifecycle:
	$(PYTEST) tests/framework/test_config_to_container.py -v

# Against the demo instance already running in this checkout. Tests create
# unique data and clean up after themselves; they never reset the instance.
test-demo:
	tests/integration/stacktests demo-rig

# A small subset of the managed-rig e2e lane, for a quick answer.
test-smoke:
	tests/integration/stacktests smoke

# The full managed rig: seeds a test-owned instance and runs every e2e test.
test-e2e:
	tests/integration/stacktests e2e

.PHONY: help hooks lint typecheck test-unit test-lifecycle test-demo test-smoke test-e2e
