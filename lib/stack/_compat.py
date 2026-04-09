"""Python version compatibility shims."""

import sys

if sys.version_info >= (3, 11):
    import tomllib
else:
    from ._vendor import tomli as tomllib  # noqa: F401

__all__ = ["tomllib"]
