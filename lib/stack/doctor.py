"""Diagnose an instance: say what is wrong, and what to type to fix it.

`stack status` answers "is it up?". When the answer is "no", it stops there.
Finding out *why* meant reading container logs, running `docker inspect`,
querying a service's database, and diffing that against stack.toml by eye.

WHY THE CHECKS ARE GENERIC
    Nothing here knows what Matrix is. The drift above is caught by
    comparing a container's actual environment against what stack.toml
    renders *now* - which finds the same class of bug for Paperless,
    Forgejo, or any stacklet added later, including ones that do not exist
    yet. A check that hardcodes one service's schema only ever finds that
    service's bugs, and belongs to that stacklet, not here.

This module is pure: it takes gathered facts and returns findings. All I/O
(docker inspect, reading config) lives in the caller, so every rule below
is unit-testable without a running instance.
"""

from __future__ import annotations

from dataclasses import dataclass

ERROR = "error"
WARN = "warn"
INFO = "info"

# Environment keys every container gets from the image or the runtime, not
# from stack.toml. Comparing them produces noise, never a real finding.
_RUNTIME_KEYS = frozenset({
    "PATH", "HOSTNAME", "HOME", "TERM", "LANG", "LC_ALL",
    "PYTHON_VERSION", "PYTHONUNBUFFERED", "GPG_KEY",
    # Stamped per container so staleness can be detected. It differs by
    # design after any commit, and check_stale_code reports that.
    "STACK_COMMIT",
})


@dataclass(frozen=True)
class Finding:
    """One diagnosis. `fix` is a command the reader can run verbatim."""

    level: str
    title: str
    detail: str
    fix: str

    @property
    def is_error(self) -> bool:
        return self.level == ERROR


def env_drift(expected: dict, actual: dict, ignore: frozenset = _RUNTIME_KEYS) -> list[str]:
    """Config keys whose live value is not what compose would set today.

    `expected` is what `docker compose config` resolves for that service:
    its `env_file` and its `environment:` block, interpolated. That is the
    only honest expectation, because it is literally what a fresh
    container would receive.

    Comparing against the stacklet's rendered env instead produced false
    positives that no `stack up` could ever clear, in three flavours. The
    rendered env is the whole set for the project, so every variable a
    service was never given looked missing, and watchtower appeared to
    have 36 problems. An image's own defaults are not ours to change, so
    gotenberg reported a TZ error whose suggested fix provably did
    nothing. And a compose file that deliberately overrides a value, a
    container path where the host has a host path, a service name where
    the host has a LAN URL, reported that override as drift forever.

    Returns key names only, never values - this environment carries admin
    passwords, API tokens and database credentials, and a diagnostic that
    prints them turns a config warning into a credential leak in whatever
    log or issue tracker the output gets pasted into.
    """
    drifted = []
    for key, value in expected.items():
        if key in ignore or key not in actual:
            continue
        if actual[key] != str(value):
            drifted.append(key)
    return sorted(drifted)


def check_env_drift(stacklet: str, container: str, drifted: list[str]) -> Finding | None:
    """A running container carrying superseded config."""
    if not drifted:
        return None
    return Finding(
        level=ERROR,
        title=f"{container} is running superseded config",
        detail=(
            f"{len(drifted)} setting(s) differ from what stack.toml renders now: "
            + ", ".join(drifted)
            + ". The container keeps its environment from creation time, so "
            "editing stack.toml alone changes nothing until it is recreated."
        ),
        fix=f"stack up {stacklet}",
    )


def check_exited(container: str, exit_code: int, since: str) -> Finding | None:
    """A container that stopped and stayed stopped.

    `stack status` reports the stacklet as failing without naming which
    container died or when, which is the difference between a one-line fix
    and a log-reading session.
    """
    if exit_code == 0:
        return None
    return Finding(
        level=ERROR,
        title=f"{container} exited ({exit_code})",
        detail=f"Stopped {since} and has not come back.",
        fix=f"stack logs {container.split('-')[1] if '-' in container else container}",
    )


def check_missing_secrets(stacklet: str, missing: list[str]) -> Finding | None:
    """A stacklet whose declared credentials were never provisioned.

    Secrets are minted by `on_install_success`, which runs once, on the
    first install. A stacklet that grows a new credential later leaves
    every existing instance without it: the hook has already run and
    will not run again, so the gap is permanent and silent. `memory`
    did exactly that, and the symptom reached the operator as a vault
    write failing with "Forgejo credentials missing" on a stack whose
    containers were all green.

    Generic on purpose, in the spirit of the rest of this module: the
    stacklet says which keys it cannot work without (`required_secrets`
    in its manifest) and the caller says which are absent. Nothing here
    knows what a Forgejo token is, so the same rule covers whatever
    credential the next stacklet adds.

    Names only, never values - see `env_drift`.
    """
    if not missing:
        return None
    return Finding(
        level=ERROR,
        title=f"{stacklet} is missing credentials it needs",
        detail=(
            ", ".join(missing)
            + " declared as required but absent from the secret store. "
            "These are provisioned once, during install, so a stacklet "
            "installed before it started needing one never gets it."
        ),
        fix=f"stack setup {stacklet}",
    )


def check_stale_code(stacklet: str) -> Finding:
    """A stacklet whose containers predate the code on disk.

    Not a fault, a gap: someone updated the sources and the containers
    still run what they were started with. Nothing else reports it,
    because from Docker's side nothing is wrong.
    """
    return Finding(
        title=f"{stacklet} is running code from before the last update",
        detail=(
            "Its containers were started from an earlier commit than the one "
            "checked out. New code on disk is not new code running until the "
            "containers are recreated."
        ),
        fix=f"stack restart {stacklet}",
        level=WARN,
    )


def check_release(position: str, latest: str, up_to_date: bool) -> Finding | None:
    """Whether a newer release exists than the one this checkout is on.

    Read from the tags this clone already has, because doctor does not
    reach the network: it is run often, and often on an instance whose
    problem is that something is unreachable. So the answer is only as
    fresh as the last fetch, and the detail says so rather than implying
    authority it does not have.

    A warning, not an error. Being a release behind breaks nothing, and
    doctor's exit code gates scripts.
    """
    if not latest or up_to_date:
        return None
    return Finding(
        level=WARN,
        title=f"a newer release is available: {latest}",
        detail=(
            f"This checkout is at {position}. Tags are read from this clone, "
            f"so the newest release may be newer still."
        ),
        fix="stack update",
    )


def check_language(core_language: str, ai_language: str) -> Finding | None:
    """Whether the family's language, `[core] language`, is set.

    Installs from before the installer wrote it have none. Everything that
    reads the family language then falls back to `[ai] language`, the
    language the stack speaks to you, which may be a different one: the
    bots, the document categories and transcription follow the voice.
    A `[core] language` different from `[ai] language` is a valid setup
    and not reported.
    """
    if core_language:
        return None
    fallback = ai_language or "en"
    return Finding(
        level=WARN,
        title="no family language set",
        detail=(f"stack.toml has no `language` under [core]. The bots, document "
                f"tags and transcription fall back to \"{fallback}\""
                + (", the language of the voice under [ai]." if ai_language else ".")),
        fix=(f'add language = "{fallback}" under [core] in stack.toml (or the '
             f"language your family reads), then restart what is running: "
             f"stack restart <id>..."),
    )


def check_endpoint(name: str, url: str, reachable: bool) -> Finding | None:
    """A configured endpoint that does not answer.

    Covers the AI backend in particular: pointing at a self-hosted model
    that is switched off fails deep inside a bot, as a timeout with no
    mention of the endpoint.
    """
    if reachable or not url:
        return None
    return Finding(
        level=WARN,
        title=f"{name} endpoint is not answering",
        detail=f"Configured as {url}, but it did not respond.",
        fix="tests/e2e/stacktests ai   # check or switch the backend",
    )


def check_oidc(providers: list[str], clients: dict[str, bool],
               running: set[str]) -> list[Finding]:
    """Single sign-on that is set up on one side only.

    A client's `{oidc_*}` variables render empty both when there is no
    provider and when the provider has not registered it yet, and either
    way the service shows only its own login. The first is a choice, the
    second a gap. `providers` are the stacklets declaring
    `[oidc_provider]` in discovery order, `clients` maps each stacklet
    declaring `[oidc]` to whether its credentials are stored, `running`
    names the stacklets with containers.

    A provider that is not running raises nothing: an extension in the
    directory is not yet a decision to use it.
    """
    findings: list[Finding] = []
    if len(providers) > 1:
        used, ignored = providers[0], ", ".join(providers[1:])
        findings.append(Finding(
            level=WARN,
            title="more than one stacklet provides single sign-on",
            detail=f"{used} is used, {ignored} is ignored. Only the first "
                   "stacklet that declares [oidc_provider] is used.",
            fix=f"remove [oidc_provider] from {ignored}",
        ))
    if not providers or providers[0] not in running:
        return findings
    provider = providers[0]
    for stacklet, registered in sorted(clients.items()):
        if registered or stacklet not in running:
            continue
        findings.append(Finding(
            level=WARN,
            title=f"{stacklet} has no single sign-on yet",
            detail=f"It declares [oidc] and {provider} provides OpenID "
                   f"Connect, but {provider} has not registered it, so "
                   f"{stacklet} shows only its own login. Registration "
                   f"runs on `stack up {provider}`; then run "
                   f"`stack up {stacklet}` to apply the credentials.",
            fix=f"stack up {provider}",
        ))
    return findings


def diagnose(stacklets, expected_env, containers_for, container_env,
             *, missing_secrets=None, stale=()) -> list[Finding]:
    """Run every check across the given stacklets.

    The collaborators are injected rather than imported so the whole walk
    is testable with plain dicts - no Docker, no instance. Each is a
    callable taking a stacklet id (or container name) and returning facts.
    `expected_env` takes a *container* name and returns what compose would
    give that service now; an empty answer means the question could not be
    asked, and no drift is reported rather than a guess.
    `missing_secrets` is optional so a caller that has no secret store to
    consult still gets the container checks. `stale` names the stacklets
    whose containers predate the code on disk, which only a caller with a
    git checkout can work out.

"""
    findings: list[Finding] = []
    for stacklet in stacklets:
        containers = containers_for(stacklet)
        if not containers:
            # Nothing running means the stacklet is not part of this
            # instance, so its missing credentials are not yet a problem.
            continue

        if stacklet in stale:
            findings.append(check_stale_code(stacklet))

        if missing_secrets:
            found = check_missing_secrets(stacklet, missing_secrets(stacklet))
            if found:
                findings.append(found)

        for container in containers:
            name = container["name"]
            if container["state"] != "running":
                found = check_exited(name, container["exit_code"], container["since"])
                if found:
                    findings.append(found)
                # A stopped container's environment says nothing useful.
                continue

            expected = expected_env(name)
            if expected:
                drifted = env_drift(expected, container_env(name))
                found = check_env_drift(stacklet, name, drifted)
                if found:
                    findings.append(found)
    return findings


def summarise(findings: list[Finding]) -> str:
    """One line for the reader who only wants the verdict."""
    if not findings:
        return "No problems found."
    errors = sum(1 for f in findings if f.is_error)
    warns = len(findings) - errors
    parts = []
    if errors:
        parts.append(f"{errors} error{'s' if errors != 1 else ''}")
    if warns:
        parts.append(f"{warns} warning{'s' if warns != 1 else ''}")
    return ", ".join(parts) + "."
