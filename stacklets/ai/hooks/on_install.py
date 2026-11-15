"""on_install.py: the AI's first `stack up ai`, once the preparation is done.

    ./stack up ai        # prepares first (`on_prepare`), then this, once

The long part of the setup is the preparation (`prepare.py`): oMLX from
Homebrew, its models, and Whisper wherever voice messages need it on
this Mac, for either route. The framework runs it before the start takes
its turn in the queue of runs, so here it has nothing left to do and
finishes in seconds. It runs again here all the same: a step whose state
went missing since (a `brew cleanup`, a deleted model) is put right.

Installs nothing while `[ai] provider = "none"` (no AI for now, `off.py`).

After this, `on_start` starts famstack's oMLX and Whisper on every
`stack up ai`, through the lifecycle they share (`host_services.py`), and
`on_stop` stops both.
"""

from pathlib import Path

from stack.prompt import section

from .. import off, whisper_service
from ..prepare import prepare
from ..speech_setting import speech_is_off


def run(ctx):
    """Prepare the AI's route (nothing left on a done preparation), then
    start Whisper when it is on this Mac."""
    # No AI for now: build and download nothing; a route turns it on.
    off.refuse(ctx.stack.config)
    prepare(ctx)

    data_dir = Path(ctx.stack.data)
    server, model = whisper_service.server(data_dir), whisper_service.model_file(data_dir)
    if not speech_is_off(ctx.stack.config) and server.exists() and model.exists():
        whisper_service.start(ctx, data_dir, server, model)
    section("Setup complete")
