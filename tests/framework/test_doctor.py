"""Diagnosis rules, including the drift that took the dev instance's bots down.

Each case here is a failure that actually happened or that the rule exists to
prevent. Pure functions, no instance required.
"""

from __future__ import annotations

from stack.doctor import (
    ERROR,
    Finding,
    check_endpoint,
    check_env_drift,
    check_exited,
    compose_supplied,
    diagnose,
    env_drift,
    summarise,
)


def _fixture_instance():
    """A core stacklet with one drifted container and one dead sidecar."""
    containers = {
        "core": [
            {"name": "stack-core-bot-runner", "state": "running",
             "exit_code": 0, "since": "Up 4 minutes"},
            {"name": "stack-core-watchtower", "state": "exited",
             "exit_code": 128, "since": "3 weeks ago"},
        ],
    }
    envs = {
        "stack-core-bot-runner": {"MATRIX_SERVER_NAME": "test.local"},
        "stack-core-watchtower": {},
    }
    return (
        ["core"],
        lambda s: {"MATRIX_SERVER_NAME": "simpson"},
        lambda s: containers.get(s, []),
        lambda n: envs.get(n, {}),
        lambda n: {},
    )


# ── env_drift ────────────────────────────────────────────────────────────

def test_detects_the_realm_drift_that_broke_the_bots():
    # The real incident: stack.toml re-seeded to a new realm, container still
    # carrying the old one, every bot login 403ing against a realm that no
    # longer had accounts.
    rendered = {"MATRIX_SERVER_NAME": "simpson", "MATRIX_ADMIN_USER": "stackadmin"}
    actual = {"MATRIX_SERVER_NAME": "test.local", "MATRIX_ADMIN_USER": "stackadmin"}
    assert env_drift(rendered, actual) == ["MATRIX_SERVER_NAME"]


def test_clean_container_reports_nothing():
    env = {"MATRIX_SERVER_NAME": "simpson", "PAPERLESS_URL": "http://x:8000"}
    assert env_drift(env, dict(env)) == []


def test_key_the_container_never_receives_is_not_drift():
    # Learned from the first live run: a stacklet's rendered env covers the
    # whole compose project, but each service gets only the subset its
    # compose entry maps. Flagging the rest made a sidecar that receives two
    # variables report 36 problems, drowning the one that mattered.
    rendered = {"MAPPED": "same", "NOT_MAPPED_TO_THIS_SERVICE": "x"}
    assert env_drift(rendered, {"MAPPED": "same"}) == []


def test_image_defined_keys_are_not_drift():
    # The image legitimately sets things stack.toml says nothing about.
    assert env_drift({"A": "1"}, {"A": "1", "IMAGE_OWN_VAR": "x"}) == []


def test_runtime_keys_are_ignored():
    # PATH and friends always differ; comparing them would bury real findings.
    rendered = {"PATH": "/expected", "REAL": "yes"}
    actual = {"PATH": "/actual/from/image", "REAL": "yes"}
    assert env_drift(rendered, actual) == []


def test_non_string_rendered_values_compare_by_string():
    # stack.toml yields ints and bools; container env is always strings.
    assert env_drift({"PORT": 8000, "DEBUG": True}, {"PORT": "8000", "DEBUG": "True"}) == []
    assert env_drift({"PORT": 8000}, {"PORT": "9000"}) == ["PORT"]


def test_drift_never_leaks_values():
    # This environment holds admin passwords and API tokens. Findings get
    # pasted into issues and logs, so only key names may appear.
    rendered = {"ADMIN_PASSWORD": "hunter2", "API_TOKEN": "sk-secret"}
    actual = {"ADMIN_PASSWORD": "old-one", "API_TOKEN": "sk-old"}
    drifted = env_drift(rendered, actual)
    finding = check_env_drift("core", "stack-core-bot-runner", drifted)
    rendered_text = f"{finding.title} {finding.detail} {finding.fix}"
    for secret in ("hunter2", "sk-secret", "old-one", "sk-old"):
        assert secret not in rendered_text
    assert "ADMIN_PASSWORD" in rendered_text  # the name is the useful part


# ── compose_supplied ─────────────────────────────────────────────────────

def test_image_baked_value_is_not_ours_to_fix():
    # The gotenberg case, with its real values. `gotenberg/gotenberg:8` bakes
    # TZ=UTC; stack.toml says Europe/Berlin; the compose file never passes TZ
    # to that service. Doctor reported drift on every run and told the reader
    # to run `stack up docs`, which recreated the container and changed
    # nothing, because there was nothing to change.
    actual = {"TZ": "UTC", "PAPERLESS_URL": "http://localhost:42020"}
    baked = {"TZ": "UTC"}
    assert compose_supplied(actual, baked) == {"PAPERLESS_URL": "http://localhost:42020"}


def test_compose_overriding_an_image_default_is_still_ours():
    # Same key, different value: compose won, so we own it and it can drift.
    assert compose_supplied({"TZ": "Europe/Berlin"}, {"TZ": "UTC"}) == {"TZ": "Europe/Berlin"}


def test_gotenberg_style_container_produces_no_finding():
    # End to end through the caller's entry point, which is where the bug was
    # visible. This is the regression guard: it fails if the walk ever goes
    # back to comparing a container's whole environment.
    containers = [{"name": "stack-docs-gotenberg", "state": "running",
                   "exit_code": 0, "since": "Up 4 minutes"}]
    findings = diagnose(
        ["docs"],
        lambda s: {"TZ": "Europe/Berlin"},      # what stack.toml renders
        lambda s: containers,
        lambda n: {"TZ": "UTC"},                # container, from the image
        lambda n: {"TZ": "UTC"},                # image's own default
    )
    assert findings == []


def test_real_drift_still_reported_when_the_image_is_silent():
    # The guard must not swallow the incident it was built for: the image
    # says nothing about the realm, so a stale value is genuinely ours.
    containers = [{"name": "stack-core-bot-runner", "state": "running",
                   "exit_code": 0, "since": "Up 4 minutes"}]
    findings = diagnose(
        ["core"],
        lambda s: {"MATRIX_SERVER_NAME": "simpson"},
        lambda s: containers,
        lambda n: {"MATRIX_SERVER_NAME": "test.local"},
        lambda n: {},
    )
    assert len(findings) == 1
    assert "superseded config" in findings[0].title


# ── findings ─────────────────────────────────────────────────────────────

def test_env_drift_finding_is_actionable():
    finding = check_env_drift("core", "stack-core-bot-runner", ["MATRIX_SERVER_NAME"])
    assert finding.level == ERROR
    assert finding.fix == "stack up core"


def test_no_drift_produces_no_finding():
    assert check_env_drift("core", "stack-core-bot-runner", []) is None


def test_clean_exit_is_not_a_finding():
    assert check_exited("stack-core-job", 0, "2 minutes ago") is None


def test_nonzero_exit_names_the_container_and_code():
    # The real case: watchtower Exited(128) three weeks ago, while status
    # only said the stacklet was failing.
    finding = check_exited("stack-core-watchtower", 128, "3 weeks ago")
    assert finding.is_error
    assert "stack-core-watchtower" in finding.title
    assert "128" in finding.title
    assert "3 weeks ago" in finding.detail


def test_reachable_endpoint_is_not_a_finding():
    assert check_endpoint("AI", "http://localhost:42199/v1", reachable=True) is None


def test_unset_endpoint_is_not_a_finding():
    # Nothing configured is a choice, not a fault.
    assert check_endpoint("AI", "", reachable=False) is None


def test_unreachable_endpoint_names_the_url():
    finding = check_endpoint("AI", "http://localhost:42199/v1", reachable=False)
    assert finding.level == "warn"
    assert "http://localhost:42199/v1" in finding.detail


# ── diagnose (the whole walk) ────────────────────────────────────────────

def test_diagnose_reproduces_the_real_incident():
    # Both faults the dev instance actually had, found in one pass.
    findings = diagnose(*_fixture_instance())
    titles = " | ".join(f.title for f in findings)
    assert "stack-core-bot-runner is running superseded config" in titles
    assert "stack-core-watchtower exited (128)" in titles
    assert len(findings) == 2


def test_diagnose_skips_env_check_for_stopped_containers():
    # A stopped container's environment is stale by definition; reporting
    # drift on it would bury the finding that it is stopped at all.
    containers = [{"name": "stack-x-dead", "state": "exited",
                   "exit_code": 1, "since": "1 hour ago"}]
    findings = diagnose(
        ["x"], lambda s: {"A": "new"}, lambda s: containers, lambda n: {"A": "old"},
        lambda n: {},
    )
    assert len(findings) == 1
    assert "exited" in findings[0].title


def test_diagnose_survives_a_stacklet_that_cannot_render():
    # One broken config must not stop the rest being diagnosed.
    def exploding_env(stacklet):
        raise ValueError("bad config")

    containers = [{"name": "stack-x-1", "state": "running",
                   "exit_code": 0, "since": "Up 1 minute"}]
    assert diagnose(["x"], exploding_env, lambda s: containers,
                    lambda n: {}, lambda n: {}) == []


def test_diagnose_ignores_stacklets_with_no_containers():
    assert diagnose(["absent"], lambda s: {"A": "1"}, lambda s: [],
                    lambda n: {}, lambda n: {}) == []


def test_healthy_instance_yields_nothing():
    containers = [{"name": "stack-x-1", "state": "running",
                   "exit_code": 0, "since": "Up 1 minute"}]
    findings = diagnose(
        ["x"], lambda s: {"A": "1"}, lambda s: containers, lambda n: {"A": "1"},
        lambda n: {},
    )
    assert findings == []
    assert summarise(findings) == "No problems found."


# ── summary ──────────────────────────────────────────────────────────────

def test_summary_when_healthy():
    assert summarise([]) == "No problems found."


def test_summary_counts_and_pluralises():
    findings = [
        Finding(ERROR, "a", "", ""),
        Finding(ERROR, "b", "", ""),
        Finding("warn", "c", "", ""),
    ]
    assert summarise(findings) == "2 errors, 1 warning."


def test_summary_singular_error():
    assert summarise([Finding(ERROR, "a", "", "")]) == "1 error."
