"""AI stacklet on_start_ready: ask the AI one short question, then download the house voice's speech model.

Runs on every `stack up ai`, after the health check, which asks the AI
server only for its model list. A server can list its models and fail
every completion, so the run asks the AI one tiny question with `[ai]
default` and keeps the answer in the instance (`answer.py`), where the
setup page reads it. The question is one step of the run, as the health
checks are: a screen that follows the run sees it start, saying that a
model that is not loaded yet loads first, and end, or fail with the
server's error worded by its kind. A failed question does not fail the
run; it goes on and ends, so whatever follows it never waits for ever.

Then a speech model that is missing, or one a release or a new house
language needs, is fetched. What is fetched and why is in `voices.py`.
"""


from .. import answer
from ..voices import download_house_voice

__all__ = ["run"]

# What the question depends on, read from `[ai]`.
SETTINGS = ("openai_url", "default", "openai_key")
# The run's step for the question; the first answer waits for a model load.
QUESTION_STEP = "Asking the AI one short question (a model that is not loaded yet loads first)"


def run(ctx):
    """Ask the AI, then fetch the house voice; neither fails the run."""
    _ask_the_ai(ctx)
    download_house_voice(ctx)


def _ask_the_ai(ctx) -> None:
    """Ask the AI one tiny question and keep the answer, as one step of
    the run: done when it answered, failed with the server's error when
    it did not."""
    ai = {key: ctx.cfg(key) for key in SETTINGS}
    if not ai["openai_url"]:
        return
    with ctx.stack.output.spinner(QUESTION_STEP) as step:
        found = answer.check(ctx.stack.instance_dir, ai)
        if not found["ok"]:
            step.fail(answer.said(found))
