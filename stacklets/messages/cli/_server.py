"""The address at which the messages commands reach Synapse from the host.

Every `stack messages <cmd>` talks to the stack's Synapse from the host,
on the port the stacklet's manifest declares. This module is the one
place that turns a command's config into that address:

    client = MatrixClient(synapse_url(config), server_name, instance_dir)

The host is 127.0.0.1, not `localhost`. Synapse's port is published on
an IPv4 address only (`PORT_BIND_IP` in the compose file). `localhost`
resolves to ::1 first, and the same port number on ::1 can belong to
another process, which would then receive the admin's login.
"""

SYNAPSE_HOST = "127.0.0.1"

# The port in `stacklet.toml`, for a config that carries no manifest.
DEFAULT_SYNAPSE_PORT = 42031


def synapse_port(config) -> int:
    """The host port of Synapse, from the manifest's `[ports]` table."""
    return config.get("manifest", {}).get("ports", {}).get("synapse", DEFAULT_SYNAPSE_PORT)


def synapse_url(config) -> str:
    """The base URL of Synapse's client and admin API, as the host reaches it."""
    return f"http://{SYNAPSE_HOST}:{synapse_port(config)}"
