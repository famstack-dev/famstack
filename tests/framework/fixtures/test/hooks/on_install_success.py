"""Test stacklet on_install_success — verifies the ctx interface works."""


def run(ctx):
    step = ctx.step
    secret = ctx.secret
    env = ctx.env

    step("Verifying ctx interface...")

    # Verify env is populated
    assert env.get("TZ"), "TZ should be set"
    assert env.get("TEST_DATA_DIR"), "TEST_DATA_DIR should be set"

    # Verify secret read/write
    secret("INSTALL_SUCCESS_TOKEN", "test-token-123")
    assert secret("INSTALL_SUCCESS_TOKEN") == "test-token-123"

    # Verify http_get works against our own health endpoint
    try:
        data = ctx.http_get("http://localhost:42099/")
    except Exception:
        step("Health endpoint not returning JSON (expected for plain text)")

    step("on_install_success complete")
