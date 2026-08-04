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


def env_drift(rendered: dict, actual: dict, ignore: frozenset = _RUNTIME_KEYS) -> list[str]:
    """Config keys whose live value no longer matches what stack.toml renders.

    Returns key names only, never values - this environment carries admin
    passwords, API tokens, and database credentials, and a diagnostic that
    prints them turns a config warning into a credential leak in whatever
    log or issue tracker the output gets pasted into.

    Only keys the container actually carries are compared. A stacklet's
    rendered env is the whole set for the compose project, while each
    service receives its own subset - so "missing" overwhelmingly means
    "this service was never given that variable", not "config drifted".
    Reporting those made watchtower look like it had 36 problems and
    buried the one setting that had genuinely changed.
    """
    drifted = []
    for key, expected in rendered.items():
        if key in ignore or key not in actual:
            continue
        if actual[key] != str(expected):
            drifted.append(key)
    return sorted(drifted)


def compose_supplied(actual: dict, baked: dict) -> dict:
    """The part of a container's environment that compose actually set.

    A container's environment is the image's own defaults plus whatever
    compose passed in. Only the second half can drift from stack.toml; the
    first half belongs to the image author and no famstack command can
    change it. Comparing it produces a finding that is permanently true and
    whose suggested fix provably does nothing, which is how gotenberg came
    to report a TZ error that survived every `stack up docs`.

    A value equal to the image's default is treated as not-ours. That is
    deliberately conservative: if compose sets a key to exactly what the
    image already baked, real drift on that key goes unreported. Missing a
    finding costs one debugging session; a permanent false positive teaches
    the reader to skim past every finding, including the true ones.
    """
    return {k: v for k, v in actual.items() if baked.get(k) != v}


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
        fix="tests/integration/stacktests ai   # check or switch the backend",
    )


def diagnose(stacklets, rendered_env, containers_for, container_env,
             image_env, *, missing_secrets=None) -> list[Finding]:
    """Run every check across the given stacklets.

    The collaborators are injected rather than imported so the whole walk
    is testable with plain dicts - no Docker, no instance. Each is a
    callable taking a stacklet id (or container name) and returning facts.
    `missing_secrets` is optional so a caller that has no secret store to
    consult still gets the container checks.

    A stacklet whose env cannot be rendered is skipped rather than fatal:
    one misconfigured stacklet should not stop the others being diagnosed,
    which is the moment a doctor is most needed.
    """
    findings: list[Finding] = []
    for stacklet in stacklets:
        containers = containers_for(stacklet)
        if not containers:
            # Nothing running means the stacklet is not part of this
            # instance, so its missing credentials are not yet a problem.
            continue

        if missing_secrets:
            found = check_missing_secrets(stacklet, missing_secrets(stacklet))
            if found:
                findings.append(found)

        try:
            rendered = rendered_env(stacklet)
        except Exception:
            rendered = None

        for container in containers:
            name = container["name"]
            if container["state"] != "running":
                found = check_exited(name, container["exit_code"], container["since"])
                if found:
                    findings.append(found)
                # A stopped container's environment says nothing useful.
                continue
            if rendered:
                ours = compose_supplied(container_env(name), image_env(name))
                drifted = env_drift(rendered, ours)
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
