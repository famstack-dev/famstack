"""stack.matrix: an account's connection to the Matrix homeserver.

Container-only: the modules here import matrix-nio, aiohttp and loguru,
which the host `./stack` CLI does not have, so nothing on the host imports
this package. Import the module you need explicitly:

    from stack.matrix.cross_signing import CrossSigning
"""
