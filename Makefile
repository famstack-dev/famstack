# famstack development tasks

PYTEST = uv run --extra test pytest

# Type check the shipped code. Not a gate: read what it says about the files
# you touched. `uvx basedpyright <paths>` narrows it further.
typecheck types:
	-uvx basedpyright

# Fast unit tests: no Docker. Run before every commit.
test-unit unit fast test:
	$(PYTEST) tests/framework tests/stacklets -v --ignore=tests/framework/test_config_to_container.py

# Live demo-rig tests: uses the already-running Simpsons demo instance.
test-demo demo-rig demo:
	tests/integration/stacktests demo-rig

# Docker lifecycle tests: validates stack up/down/destroy and container env.
test-lifecycle container-lifecycle lifecycle:
	$(PYTEST) tests/framework/test_config_to_container.py -v

# Managed container e2e tests: starts the test rig and runs every e2e test.
test-e2e container-e2e e2e:
	tests/integration/stacktests e2e

# Quick managed-rig e2e subset.
test-smoke smoke:
	tests/integration/stacktests smoke

# Backwards-compatible names.
test-all: test-lifecycle
test-integration: test-e2e

.PHONY: typecheck types test-unit unit fast test test-demo demo-rig demo test-lifecycle container-lifecycle lifecycle test-e2e container-e2e e2e test-smoke smoke test-all test-integration
