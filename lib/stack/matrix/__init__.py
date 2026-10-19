"""stack.matrix: an account's connection to the Matrix homeserver.

Container-only: the modules here import matrix-nio, aiohttp and loguru,
which the host `./stack` CLI does not have, so nothing on the host imports
this package. Import the module you need explicitly:

    from stack.matrix.device import Device   # login, key store, key upkeep
    from stack.matrix.cross_signing import CrossSigning

Nothing here knows about bots: a bot's sync loop and a one-shot sender
log in, keep their keys and send the same way.
"""
