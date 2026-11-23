"""core_refresh.py: which of core's containers a refresh recreates.

    from stack import core_refresh

    core_refresh.stale_services(core_compose, first_run=True)
    # ["stack-core-bot-runner"]: what `stack.cli._refresh_core` recreates

    core_refresh.services_to_recreate(
        expected={"stack-core-bot-runner": {"OPENAI_URL": "new"},
                  "stack-core-tools": {"TIMEZONE": "Europe/Berlin"}},
        running={"stack-core-bot-runner": {"OPENAI_URL": "old"},
                 "stack-core-tools": {"TIMEZONE": "Europe/Berlin"}},
        first_run=False)
    # ["stack-core-bot-runner"]

At the end of `stack up <id>` the stack renders core's env again
(`stack.cli._refresh_core`). Compose does not see a changed env file on
its own, so a container keeps an old value until it is recreated. A
recreate of core's tools server takes the setup page and the dashboard
away for seconds, and on a fresh install the admin has the setup page
open while the stacklets they picked finish their first runs. So only the
containers that run with values that are no longer current are recreated,
and the bot runner after a first run, because it finds bots only when it
starts.

`expected` is what compose gives each service now (`docker compose
config`), `running` what each container was created with (`docker
inspect`), compared as `stack doctor` compares them (`doctor.env_drift`).
Core names each container as its service, so one name serves both.

The tools server serves the setup page and the dashboard, and is given
only the values of core's env its code reads (`environment:` in core's
compose file), so the AI's new address and key, which only the bot runner
reads, leave it running. `stale_services` reads Docker; the decision,
`services_to_recreate`, is pure. Neither recreates anything: the caller
does.
"""

from __future__ import annotations

from pathlib import Path

from . import docker
from .bot_runner import BOT_RUNNER_CONTAINER
from .doctor import env_drift


def stale_services(core_compose: str | Path, first_run: bool) -> list[str] | None:
    """The services of core to recreate, from what compose gives each
    service now and what each container runs with; None for every service.

    Compose that cannot read the file answers no service. Then every
    service is recreated, as before, and the recreate reports compose's
    error instead of a refresh that silently does nothing.
    """
    expected = docker.compose_service_env(core_compose)
    if not expected:
        return None
    running = {service: docker.container_env(service) for service in expected}
    return services_to_recreate(expected, running, first_run)


def services_to_recreate(expected: dict[str, dict[str, str]],
                         running: dict[str, dict[str, str]],
                         first_run: bool) -> list[str]:
    """The services of core to recreate, sorted; empty when every container
    runs with current values and the start was not a first run.

    A service with no container (`running` has nothing for it) is
    recreated, because compose would create it. The bot runner is added on
    a first run (`first_run`), whose setup-done marker names a stacklet
    whose bot it has not loaded yet.
    """
    stale = {service for service, env in expected.items()
             if not running.get(service) or env_drift(env, running[service])}
    if first_run and BOT_RUNNER_CONTAINER in expected:
        stale.add(BOT_RUNNER_CONTAINER)
    return sorted(stale)
