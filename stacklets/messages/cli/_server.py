"""The address at which the messages commands reach Synapse from the host.

Every `stack messages <cmd>` talks to the stack's Synapse from the host,
on the port the stacklet's manifest declares. This module is the one
place that turns a command's config into that address:

    client = MatrixClient(synapse_url(config), server_name, instance_dir)

The host is 127.0.0.1, not `localhost`, as for every service of the
stack the host calls (`stack.own_service` says why).
"""

from stack.own_service import own_service_url

# The port in `stacklet.toml`, for a config that carries no manifest.
DEFAULT_SYNAPSE_PORT = 42031


def synapse_port(config) -> int:
    """The host port of Synapse, from the manifest's `[ports]` table."""
    return config.get("manifest", {}).get("ports", {}).get("synapse", DEFAULT_SYNAPSE_PORT)


def synapse_url(config) -> str:
    """The base URL of Synapse's client and admin API, as the host reaches it."""
    return own_service_url(synapse_port(config))
