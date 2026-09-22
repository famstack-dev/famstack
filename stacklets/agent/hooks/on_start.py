"""Check the agent has an AI server before it starts.

The agent talks to whatever `[ai] openai_url` points at: the local AI
stacklet, another machine on the network, or a hosted provider. So the
local stacklet is not a requirement, but an address is. Without one the
container would start and fail every message.

An address that does not answer is only a warning. The server may still
be booting, or be a machine that sleeps; neither should keep the agent
down once it comes back.
"""

from stack.ai.probe import probe
from stack.prompt import warn


def _host_url(docker_url: str) -> str:
    """The env holds the address as containers see it. This hook runs on
    the host, where the framework's localhost rewrite has to be undone."""
    return docker_url.replace("://host.docker.internal", "://localhost")


def _has_model(model: str, available: list[str]) -> bool:
    """Servers list ids with or without the org prefix, so compare the
    short name either way, as `stack ai models` does."""
    short = model.rsplit("/", 1)[-1]
    return any(short in m or m in short for m in available)


def run(ctx):
    url = ctx.env.get("AGENT_OPENAI_URL", "")
    if not url:
        raise RuntimeError(
            "No AI server configured in stack.toml [ai] openai_url. "
            "Install the local engine with './stack up ai', or use one "
            "elsewhere with './stack ai connect <url>'."
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
             f"Pick one it has with './stack ai connect {url} --model <id>'.")
