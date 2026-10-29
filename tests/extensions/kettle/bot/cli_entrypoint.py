"""The kettle's commands in the bot runner: `boil` asks the kettle's service.

`stack kettle boil` (`cli/boil.py`) starts it as
`python /extensions/kettle/bot/cli_entrypoint.py boil`, with the kettle's
rendered env. It answers JSON on stdout, which the host command renders.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, "/app")  # stack.*, which core mounts there

from stack.stacklet_code import import_stacklets  # noqa: E402

import_stacklets([Path(__file__).resolve().parents[2]])

from stacklets.kettle.water import reading  # noqa: E402


def main(argv: list[str]) -> int:
    if argv != ["boil"]:
        print("usage: cli_entrypoint.py boil", file=sys.stderr)
        return 2
    with urllib.request.urlopen(f"{os.environ['KETTLE_URL']}/water", timeout=5) as answer:
        print(json.dumps(reading(json.load(answer))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
