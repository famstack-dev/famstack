"""on_prepare.py: install and download what the AI's route needs on this Mac.

    ./stack ai prepare        # on its own, while other stacklets start
    ./stack up ai             # runs it first when it was not done

The framework runs this hook holding only the ai stacklet's run lock,
outside the queue in which one run of the stack goes at a time, because
none of it touches Docker or core. What it does, and how it resumes, is
`prepare.py`.
"""

from ..prepare import prepare

HELP = "Install oMLX and download the AI's models, before `stack up ai`"


def run(ctx):
    """Prepare the AI's route: famstack's oMLX, or Whisper for your own
    server; nothing with no route or no AI."""
    prepare(ctx)
