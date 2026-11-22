"""The instance's shared bucket, as the memory's commands read it.

    shared_bucket(config)        # "home" for `[core] shared_bucket = "home"`, else "family"

The shared bucket is the vault's folder everyone reads (`family/` by
default). It is a core setting, `[core] shared_bucket` in `stack.toml`,
because the archivist, the curator and the pages read it too. A command
that read it from another section always got the default, so an instance
with its own name for the bucket saw a person's folder where the shared
one belongs. The loader skips `_`-prefixed files, so this registers no
command.
"""

from __future__ import annotations

__all__ = ["shared_bucket"]

# The framework's default (`stack.vault.DEFAULT_SHARED_BUCKET`, and the
# default `Stack` renders); a host command imports no domain module.
DEFAULT_SHARED_BUCKET = "family"


def shared_bucket(config) -> str:
    """The shared bucket `config` (the stack's config as a command gets it)
    names in `[core]`, else the default.

        shared_bucket({"stack": {"core": {"shared_bucket": "home"}}})   # 'home'
        shared_bucket(None)                                             # 'family'
    """
    core = ((config or {}).get("stack") or {}).get("core") or {}
    return core.get("shared_bucket") or DEFAULT_SHARED_BUCKET
