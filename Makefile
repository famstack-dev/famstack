# Aliases for the scripts in script/, which hold the logic. `make` on
# its own prints the lane table. Details: script/test help.

.DEFAULT_GOAL := help

help:
	@script/test help

hooks:
	@script/setup --hooks-only

lint:
	@script/test lint

typecheck:
	@script/test typecheck

test-unit:
	@script/test unit

test-lifecycle:
	@script/test lifecycle

test-demo:
	@script/test demo

test-smoke:
	@script/test smoke

test-e2e:
	@script/test e2e

.PHONY: help hooks lint typecheck test-unit test-lifecycle test-demo test-smoke test-e2e
