"""The archivist loads in the bot runner, with the path the runner gives it.

The bot runner (`stacklets/core/bot-runner/main.py`) runs from `/app`,
which holds its own modules and the framework (`lib/stack` mounted as
`/app/stack`). It puts the bot's directory on `sys.path` and loads the
bot's module from its file. Nothing else is on the path: a bot module
that imports another stacklet (`memory.note_card`) puts the stacklets
directory there itself. The other tests set the path up for themselves
and cannot see a module that forgets to; this one loads the archivist in
a fresh interpreter with exactly the runner's path.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
# Locally, `/app` is two directories: the runner's modules and `lib/`.
APP = (_REPO_ROOT / "stacklets" / "core" / "bot-runner", _REPO_ROOT / "lib")
BOT_DIR = _REPO_ROOT / "stacklets" / "docs" / "bot"

# What `main.py` does for each bot it discovered, after `/app`.
LOAD = textwrap.dedent("""
    import importlib.util, sys
    from pathlib import Path
    *app, bot_dir = sys.argv[1:]
    sys.path[:0] = app
    sys.path.insert(0, bot_dir)
    spec = importlib.util.spec_from_file_location("archivist", Path(bot_dir) / "archivist.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    getattr(mod, "ArchivistBot")
""")


def test_the_archivist_loads_with_only_the_bot_runners_path(tmp_path):
    # `-I`: no current directory, no PYTHONPATH, no user site on the path.
    loaded = subprocess.run(
        [sys.executable, "-I", "-c", LOAD, *map(str, APP), str(BOT_DIR)],
        capture_output=True, text=True, cwd=tmp_path, timeout=120,
    )
    assert loaded.returncode == 0, loaded.stderr[-2000:]
