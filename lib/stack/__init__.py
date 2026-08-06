"""stack — the stacklet framework.

Public API:
    from stack import Stack
    from stack import TomlSecretStore, HookResolver, build_hook_ctx
    from stack import CollectorOutput, SilentOutput
    from stack import user_id, resolve_model
"""

from .stack import Stack
from .secrets import TomlSecretStore
from .hooks import HookResolver, StackContext, build_hook_ctx
from .output import SilentOutput, CollectorOutput
from .users import user_id, family_display_name, family_plural
from .ai import resolve_model
from . import docker
from .cli import CLI
from .commands import COMMANDS, EnvCommand, ListCommand, UpCommand, DownCommand, DestroyCommand

# Re-exported names form the framework's public API (see module docstring).
# Listing them in __all__ makes the intent explicit and keeps linters from
# flagging the imports above as unused.
__all__ = [
    "Stack",
    "TomlSecretStore",
    "HookResolver",
    "StackContext",
    "build_hook_ctx",
    "SilentOutput",
    "CollectorOutput",
    "user_id",
    "family_display_name",
    "family_plural",
    "resolve_model",
    "docker",
    "CLI",
    "COMMANDS",
    "EnvCommand",
    "ListCommand",
    "UpCommand",
    "DownCommand",
    "DestroyCommand",
]
