"""stack kettle boil: the water's temperature, as the kettle's service reports it.

The work runs in the bot runner (`bot/cli_entrypoint.py`), which reaches
the service by its container address, `KETTLE_URL` in the kettle's own
settings. The command prints the reading for people and returns it, so
`--json` prints it alone.
"""

HELP = "Read the water's temperature"

import json

from stack import bot_runner


def run(args, stacklet, config):
    reading = json.loads(bot_runner.call_stacklet(stacklet, "boil", *args))
    print(f"  {reading['celsius']} °C{', boiling' if reading['boiling'] else ''}")
    return reading
