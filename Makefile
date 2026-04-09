# famstack development tasks

# Fast tests: no Docker, ~3 seconds. Run before every commit.
test:
	uvx pytest tests/framework/ -v --ignore=tests/framework/test_config_to_container.py

# All tests including Docker-based pipeline tests (~2 min).
test-all:
	uvx pytest tests/framework/ -v

# Integration tests: real stacklets with Docker, opt-in.
test-integration:
	uvx pytest tests/integration/ -v

.PHONY: test test-all test-integration
