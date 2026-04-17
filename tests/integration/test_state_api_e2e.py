"""End-to-end coverage for the is_installed / is_running / is_healthy /
wait_for_healthy framework API.

Exercises each method against the live test stack — real containers,
real [health] probes. Complements the unit tests in
tests/framework/test_stack_health.py which mock the HTTP side.
"""

from __future__ import annotations

import time

import pytest

from stack.stack import StackletNotHealthyError


async def test_is_installed_true_after_setup(bdd, stack, paperless):
    """The `paperless` fixture brings docs through first-run setup."""
    bdd.scenario("is_installed reflects the setup-done marker")
    bdd.given("the docs stacklet has completed first-run setup")
    bdd.then("stack.is_installed('docs') is True")
    assert stack.is_installed("docs")
    bdd.ok("marker present")

    bdd.and_("stack.is_installed returns False for an unknown stacklet")
    assert not stack.is_installed("nonexistent")


async def test_is_running_reflects_docker_state(bdd, stack, paperless):
    bdd.scenario("is_running matches actual container state")
    bdd.given("the docs stacklet's containers are up")
    bdd.then("stack.is_running('docs') is True")
    assert stack.is_running("docs")
    bdd.ok("docs containers running")


async def test_is_healthy_probes_real_service(bdd, stack, paperless):
    bdd.scenario("is_healthy hits the declared [health] URL")
    bdd.given("Paperless is up and responding")
    bdd.then("stack.is_healthy('docs') returns True from a live HTTP probe")
    assert stack.is_healthy("docs")
    bdd.ok("/api/ responded 200")


async def test_wait_for_healthy_returns_fast_when_already_healthy(
    bdd, stack, paperless,
):
    bdd.scenario("wait_for_healthy short-circuits when already up")
    bdd.when("wait_for_healthy('docs', timeout=5) is called")
    t0 = time.monotonic()
    stack.wait_for_healthy("docs", timeout=5.0)
    elapsed = time.monotonic() - t0
    bdd.then(f"it returns in {elapsed:.2f}s (well under timeout)")
    assert elapsed < 1.5, f"should be near-instant, took {elapsed:.2f}s"
    bdd.ok("returned without polling")


async def test_wait_for_healthy_raises_on_timeout(bdd, stack):
    bdd.scenario("wait_for_healthy times out and raises for an unreachable stacklet")
    bdd.when("wait_for_healthy('nonexistent', timeout=0.5) is called")
    with pytest.raises(StackletNotHealthyError) as exc_info:
        stack.wait_for_healthy("nonexistent", timeout=0.5)
    bdd.then("StackletNotHealthyError is raised naming the stacklet")
    assert "nonexistent" in str(exc_info.value)
    assert exc_info.value.timeout == 0.5
    bdd.ok(f"raised: {exc_info.value}")
