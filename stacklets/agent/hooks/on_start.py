"""Tell the agent what it may run, check who answers as it, and that it
has an AI server.

First the command list: the stacklet commands `[agent] allow` grants,
written into the agent's workspace as an always-loaded skill for
nanobot, and next to the account's login state for another runtime. It
goes first because the AI checks below may return early, and the list
must not depend on whether the AI server answers.

Then the runtime, `[agent] runtime`. nanobot answers from this
stacklet's container. Any other value names a stacklet whose
`bot/agent.py` the bot runner runs as the agent's account while the
container waits, so a stacklet the bot runner cannot run would leave the
agent silent: the start stops instead and says what to set.

The agent talks to whatever `[ai] openai_url` points at: the local AI
stacklet, another machine on the network, or a hosted provider. So the
local stacklet is not a requirement, but an address is. Without one the
container would start and fail every message.

An address that does not answer is only a warning. The server may still
be booting, or be a machine that sleeps; neither should keep the agent
down once it comes back.
"""

from pathlib import Path

from stack.agent_access import write_command_list, write_command_skill
from stack.ai.probe import probe
from stack.prompt import warn
from stack.stack import AGENT_RUNTIME_DEFAULT


def _host_url(docker_url: str) -> str:
    """The env holds the address as containers see it. This hook runs on
    the host, where the framework's localhost rewrite has to be undone."""
    return docker_url.replace("://host.docker.internal", "://localhost")


def _has_model(model: str, available: list[str]) -> bool:
    """Servers list ids with or without the org prefix, so compare the
    short name either way, as `stack ai models` does."""
    short = model.rsplit("/", 1)[-1]
    return any(short in m or m in short for m in available)


def _write_command_list(ctx):
    data = Path(ctx.env["AGENT_DATA_DIR"])
    # Another runtime reads it in the bot runner's login dir for the account.
    write_command_list(ctx.stack, data / "bot" / "allowed-commands.json")
    for entry in write_command_skill(ctx.stack, data / "workspace"):
        warn(f"[agent] allow: '{entry}' names no stacklet command, so the "
             "agent cannot run it. Entries are '<stacklet>' or "
             "'<stacklet> <command>', as listed by './stack <stacklet>'.")


def _runtime_refusal(stack, runtime: str) -> str | None:
    """Why the bot runner could not answer as the agent with `runtime`'s
    code, or None when it can (or nanobot answers)."""
    if runtime == AGENT_RUNTIME_DEFAULT:
        return None
    found = next((s for s in stack.discover() if s["id"] == runtime), None)
    if found is None or not (Path(found["path"]) / "bot" / "agent.py").exists():
        return (f"[agent] runtime = '{runtime}' names no stacklet with a bot/agent.py. "
                f"Set it to '{AGENT_RUNTIME_DEFAULT}', or to the id of a stacklet that "
                "answers as the agent.")
    where = Path(found["path"]).parent
    if found["source"] != "repo" and where != stack.mounted_extension_dir:
        return (f"[agent] runtime = '{runtime}' is in {where}, and the bot runner sees "
                f"only {stack.mounted_extension_dir}. Move the stacklet there, or list "
                "its folder first in [core] extension_dirs.")
    return None


def run(ctx):
    _write_command_list(ctx)

    refusal = _runtime_refusal(ctx.stack, ctx.env.get("AGENT_RUNTIME", AGENT_RUNTIME_DEFAULT))
    if refusal:
        raise RuntimeError(refusal)

    url = ctx.env.get("AGENT_OPENAI_URL", "")
    if not url:
        raise RuntimeError(
            "No AI server configured in stack.toml [ai] openai_url. "
            "Install the local engine with './stack up ai', or use one "
            "elsewhere with './stack ai switch <url>'."
        )

    url = _host_url(url)
    result = probe(url, ctx.env.get("AGENT_OPENAI_KEY", ""))
    if not result.reachable:
        warn(f"AI server at {url} is not answering. "
             "The agent starts anyway and replies once it is back.")
        return

    model = ctx.env.get("AGENT_MODEL", "")
    if model and result.models and not _has_model(model, result.models):
        warn(f"The AI server at {url} does not list the model {model}. "
             f"Pick one it has with './stack ai switch {url} --model <id>'.")
