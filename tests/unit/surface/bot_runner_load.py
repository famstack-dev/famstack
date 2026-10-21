"""Load one bot the way the bot runner does, and say what happened.

Run as `python -I bot_runner_load.py <app> <stacklets> <state> <bot.toml>`
by `test_every_bot_loads.py`, one fresh interpreter per bot. `-I` keeps
the test session out of it: no inherited `sys.path`, no `PYTHONPATH`, no
user site, and the working directory is not on the path.

`<app>` and `<stacklets>` are a copy of what the container has at `/app`
and `/stacklets` (stacklets/core/bot-runner/Dockerfile and the bot-runner
service in stacklets/core/docker-compose.yml). The runner starts as
`python -u main.py` in `/app`, so `/app` is the first entry on its path,
and so it is here.

Discovery is the runner's own `discover_bots`, pointed at the copy. The
load is `main()`'s, replayed line for line for the one bot asked for:
`main()` loads every bot in one loop with nothing to call in between.
`test_the_runner_still_loads_bots_this_way` fails when those lines change.

Prints one JSON line on stdout:
    {"outcome": "loaded", "class": "ArchivistBot"}
    {"outcome": "no-module", "module": "<path>"}
    {"outcome": "missing", "module": "<name>", "error": "..."}
    {"outcome": "error", "error": "<traceback>"}
"""

import importlib.util
import json
import os
import sys
import traceback
from functools import partial
from pathlib import Path

app, stacklets, state, bot_toml = (Path(a) for a in sys.argv[1:5])

os.chdir(app)
sys.path.insert(0, str(app))

import main  # noqa: E402  (the runner itself, as /app/main.py)


class _AnyPassword(dict):
    """Every bot has a password: which one is not what this checks."""

    def get(self, key, default=None):
        return "springfield"


main.SETUP_STATE_DIR = state
main.DATA_DIR = state / "data"
main._read_secrets = _AnyPassword
main.bot_dir_for = partial(main.bot_dir_for, roots=(stacklets, state / "extensions"))


def _report(**answer):
    print(json.dumps(answer))
    sys.exit(0)


import tomllib  # noqa: E402

with open(bot_toml, "rb") as f:
    bot_id = main._subst_env(tomllib.load(f).get("id", ""))
cfg = next((c for c in main.discover_bots() if c["bot_id"] == bot_id), None)
if cfg is None:
    _report(outcome="error", error=f"the runner did not discover {bot_id!r} from {bot_toml}")

# ── main.main(), the load of one bot ─────────────────────────────────────
try:
    bot_dir = cfg["bot_dir"]
    if bot_dir not in sys.path:
        sys.path.insert(0, bot_dir)

    module_path = Path(bot_dir) / f"{cfg['module_stem']}.py"
    if not module_path.exists():
        _report(outcome="no-module", module=str(module_path))

    spec = importlib.util.spec_from_file_location(cfg["module_stem"], module_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    bot_class = getattr(mod, cfg["class_name"])
except ModuleNotFoundError as e:
    _report(outcome="missing", module=(e.name or "").split(".")[0],
            error=traceback.format_exc())
except Exception:  # noqa: BLE001 - the runner logs and skips any failure
    _report(outcome="error", error=traceback.format_exc())

_report(outcome="loaded", **{"class": bot_class.__name__})
