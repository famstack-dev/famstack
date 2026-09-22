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
    check_missing_secrets,
    check_release,
    check_stale_code,
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
        lambda container: {"MATRIX_SERVER_NAME": "simpson"},
        lambda s: containers.get(s, []),
        lambda n: envs.get(n, {}),
    )


# ── env_drift ────────────────────────────────────────────────────────────

class TestStaleCode:
    """A stacklet whose containers predate the code on disk.

    Doctor's findings are built by hand, so a Finding whose fields do not
    match the dataclass only fails when a real instance has that problem.
    This one did: it shipped with `is_error=` instead of `level=` and blew
    up on the rig the first time a stacklet went stale.
    """

    def test_reports_the_stacklet_and_how_to_apply_it(self):
        finding = check_stale_code("core")

        assert "core" in finding.title
        assert finding.fix == "stack restart core"

    def test_is_a_warning_not_an_error(self):
        """Nothing is broken. The code on disk just is not running yet,
        so doctor should not exit non-zero over it."""
        assert check_stale_code("core").is_error is False

    def test_diagnose_raises_one_per_stale_stacklet(self):
        findings = diagnose(
            ["docs", "photos"],
            lambda container: {},
            lambda s: [{"name": f"stack-{s}", "state": "running",
                        "exit_code": 0, "since": ""}],
            lambda n: {},
            stale=["docs"],
        )

        titles = [f.title for f in findings]
        assert any("docs is running code" in t for t in titles)
        assert not any("photos is running code" in t for t in titles)


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


# ── what a container is compared against ─────────────────────────────────
#
# Every case here is a false positive doctor actually reported, and each
# one survived `stack up` because nothing about the container was wrong.

def _running(name):
    return [{"name": name, "state": "running", "exit_code": 0, "since": "Up 4 minutes"}]


def test_a_variable_compose_never_passes_is_not_drift():
    """The gotenberg case. `gotenberg:8` bakes TZ=UTC, stack.toml says
    Europe/Berlin, and the compose file never passes TZ to that service.
    Doctor reported drift on every run and told the reader to run
    `stack up docs`, which changed nothing because nothing was wrong."""
    findings = diagnose(
        ["docs"],
        lambda container: {},           # compose sets nothing for this service
        lambda s: _running("stack-docs-gotenberg"),
        lambda n: {"TZ": "UTC"},        # the image's own default
    )
    assert findings == []


def test_a_deliberate_compose_override_is_not_drift():
    """The curator case. The compose file sets the *container* path while
    the stacklet renders the host path, and the service name while the
    host renders a LAN URL. Compared against the rendered env those read
    as drift forever, through any number of recreates."""
    findings = diagnose(
        ["memory"],
        lambda container: {"BRAIN_REPO_DIR": "/data/memory/brain",
                           "CODE_URL": "http://stack-code:3000"},
        lambda s: _running("stack-memory-curator"),
        lambda n: {"BRAIN_REPO_DIR": "/data/memory/brain",
                   "CODE_URL": "http://stack-code:3000"},
    )
    assert findings == []


def test_real_drift_is_still_reported():
    """The guard must not swallow the incident it exists for: the
    container was created before the value changed."""
    findings = diagnose(
        ["core"],
        lambda container: {"MATRIX_SERVER_NAME": "simpson"},
        lambda s: _running("stack-core-bot-runner"),
        lambda n: {"MATRIX_SERVER_NAME": "test.local"},
    )
    assert len(findings) == 1
    assert "superseded config" in findings[0].title


def test_an_unanswerable_question_reports_nothing():
    """An unreadable compose file means we cannot say what the container
    should have. Silence beats a guess."""
    findings = diagnose(
        ["docs"],
        lambda container: {},
        lambda s: _running("stack-docs-paperless"),
        lambda n: {"ANYTHING": "at all"},
    )
    assert findings == []


# ── the release this instance is on ──────────────────────────────────────

class TestRelease:
    """Doctor answers "what am I running, and is there something newer".

    Both halves matter to an operator deciding whether tonight is an
    update night, and neither was reported anywhere before: `stack
    version` prints a constant from the working tree, which names a
    hundred different trees between tags.
    """

    def test_a_newer_release_is_worth_saying(self):
        finding = check_release("v0.3.0-beta.2", "v0.3.0-beta.3", up_to_date=False)

        assert "v0.3.0-beta.3" in finding.title
        assert finding.fix == "stack update"

    def test_being_behind_is_not_an_error(self):
        """Nothing is broken, and doctor's exit code gates scripts."""
        finding = check_release("v0.3.0-beta.2", "v0.3.0-beta.3", up_to_date=False)
        assert finding.is_error is False

    def test_nothing_to_say_when_current(self):
        assert check_release("v0.3.0-beta.3", "v0.3.0-beta.3", up_to_date=True) is None

    def test_nothing_to_say_without_tags(self):
        """A shallow clone, or a checkout with no releases fetched, cannot
        answer the question. Silence beats a wrong answer."""
        assert check_release("abc1234", "", up_to_date=False) is None

    def test_says_the_answer_is_only_as_fresh_as_the_clone(self):
        """Doctor does not reach the network, so the newest release it
        knows is the newest this clone has fetched."""
        finding = check_release("v0.3.0-beta.2", "v0.3.0-beta.3", up_to_date=False)
        assert "clone" in finding.detail


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


# ── missing credentials ──────────────────────────────────────────────────
#
# The real incident: `memory` mints its Forgejo write token in
# on_install_success, which only ever runs on first install. Instances set
# up before that hook learned to persist the token held none, every vault
# write answered "Forgejo credentials missing", and doctor -- which knew
# only about containers -- reported a perfectly healthy stack.

def test_a_declared_credential_that_is_absent_is_an_error():
    finding = check_missing_secrets("memory", ["MEMORY_BOT_TOKEN"])
    assert finding.is_error
    assert "MEMORY_BOT_TOKEN" in finding.detail
    assert finding.fix == "stack setup memory"


def test_all_credentials_present_is_not_a_finding():
    assert check_missing_secrets("memory", []) is None


def test_missing_credentials_are_reported_in_one_finding():
    # One line per stacklet, not per key: a stacklet whose provisioning
    # never ran is missing all of them, and the cure is a single command.
    finding = check_missing_secrets("docs", ["API_TOKEN", "BOT_PASSWORD"])
    assert "API_TOKEN" in finding.detail and "BOT_PASSWORD" in finding.detail
    assert finding.fix == "stack setup docs"


def test_a_stacklet_that_declares_no_secrets_is_never_flagged():
    # The collaborator is optional; stacklets that declare nothing must
    # not start reporting findings the moment the check ships.
    containers = [{"name": "stack-x-1", "state": "running",
                   "exit_code": 0, "since": "Up 1 minute"}]
    findings = diagnose(
        ["x"], lambda container: {"A": "1"}, lambda s: containers,
        lambda n: {"A": "1"}, missing_secrets=lambda s: [],
    )
    assert findings == []


def test_diagnose_finds_the_missing_vault_token():
    # End to end through the walk: this is the production symptom doctor
    # was silent about.
    containers = [{"name": "stack-memory-wiki", "state": "running",
                   "exit_code": 0, "since": "Up 2 days"}]
    findings = diagnose(
        ["memory"], lambda container: {}, lambda s: containers, lambda n: {},
        missing_secrets=lambda s: ["MEMORY_BOT_TOKEN"],
    )
    assert len(findings) == 1
    assert findings[0].fix == "stack setup memory"


def test_a_stacklet_that_was_never_installed_is_not_flagged():
    # No containers means the stacklet is not part of this instance. Its
    # credentials are supposed to be absent, and telling the reader to set
    # up something they never asked for is noise.
    findings = diagnose(
        ["memory"], lambda container: {}, lambda s: [], lambda n: {},
        missing_secrets=lambda s: ["MEMORY_BOT_TOKEN"],
    )
    assert findings == []


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
        ["x"], lambda container: {"A": "new"}, lambda s: containers,
        lambda n: {"A": "old"},
    )
    assert len(findings) == 1
    assert "exited" in findings[0].title


def test_diagnose_ignores_stacklets_with_no_containers():
    assert diagnose(["absent"], lambda container: {"A": "1"}, lambda s: [],
                    lambda n: {}) == []


def test_healthy_instance_yields_nothing():
    containers = [{"name": "stack-x-1", "state": "running",
                   "exit_code": 0, "since": "Up 1 minute"}]
    findings = diagnose(
        ["x"], lambda container: {"A": "1"}, lambda s: containers,
        lambda n: {"A": "1"},
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
