"""The Simpsons' instance, fabricated, and the Stack `./stack` builds over it.

A stack.toml, users.toml and secrets file for the Simpsons, with the
stacklets the repo ships. Nothing in it points at a real service: no
Docker, no model, no network beyond localhost. Any test that runs a
`./stack` command through `Stack.run_cli_command` builds its instance here,
whichever module it tests:

    stack = make_stack(write_instance(tmp_path))
    stack.run_cli_command("memory", "history", [])

A module's test instance (`tests/<module>/unit/instance.py`) builds on it
with what that module's `stack up` leaves in the instance.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

USERS = """
[[users]]
id       = "homer"
name     = "Homer Simpson"
email    = "homer@springfield.example"
password = "donuts"
role     = "admin"

[[users]]
id       = "marge"
name     = "Marge Simpson"
email    = "marge@springfield.example"
password = "pretzels"
role     = "member"
"""


def write_instance(root: Path, *, extra: str = "",
                   server_name: str | None = "springfield.example",
                   data_dir: Path | None = None, core: str = "",
                   secrets: dict[str, str] | None = None) -> Path:
    """Write a Simpsons instance under `root` and return its directory.

    `extension_dirs` names a directory that stays empty, so discovery sees
    only what the repo ships, whatever the machine running the suite has
    in its home. `host` is set so no command has to work out a LAN address.
    `server_name` is the Matrix server's name; None leaves `[messages]` out.
    `data_dir` is where the stacklets keep their data, `<root>/data` unless
    given; `core` is more lines of `[core]`, `secrets` more secrets, both as
    a stacklet's `stack up` leaves them; `extra` is appended to stack.toml
    for a test that needs one more section.
    """
    messages = f'[messages]\nserver_name = "{server_name}"\n' if server_name else ""
    data_dir = data_dir or root / "data"
    instance = root / "instance"
    (instance / ".stack").mkdir(parents=True, exist_ok=True)
    (root / "extensions").mkdir(exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    (instance / "stack.toml").write_text(
        f'[core]\nname = "stack"\ndomain = ""\nhost = "127.0.0.1"\n'
        f'data_dir = "{data_dir}"\nextension_dirs = ["{root / "extensions"}"]\n'
        f'timezone = "America/Chicago"\nlanguage = "en"\n{core}\n{messages}{extra}',
        encoding="utf-8")
    (instance / "users.toml").write_text(USERS, encoding="utf-8")
    held = {"global__ADMIN_PASSWORD": "donuts", **(secrets or {})}
    (instance / ".stack" / "secrets.toml").write_text(
        "".join(f'{key} = "{value}"\n' for key, value in held.items()), encoding="utf-8")
    return instance


def make_stack(instance: Path):
    """The Stack `./stack` builds for `instance`, over the repo's stacklets."""
    from stack import Stack
    from stack.output import CollectorOutput

    data = tomllib.loads((instance / "stack.toml").read_text())["core"]["data_dir"]
    return Stack(root=REPO, data=Path(data), instance_dir=instance, output=CollectorOutput())
