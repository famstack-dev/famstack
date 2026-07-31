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


def diagnose(stacklets, rendered_env, containers_for, container_env) -> list[Finding]:
    """Run every check across the given stacklets.

    The four collaborators are injected rather than imported so the whole
    walk is testable with plain dicts - no Docker, no instance. Each is a
    callable taking a stacklet id (or container name) and returning facts.

    A stacklet whose env cannot be rendered is skipped rather than fatal:
    one misconfigured stacklet should not stop the others being diagnosed,
    which is the moment a doctor is most needed.
    """
    findings: list[Finding] = []
    for stacklet in stacklets:
        containers = containers_for(stacklet)
        if not containers:
            continue

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
                drifted = env_drift(rendered, container_env(name))
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
