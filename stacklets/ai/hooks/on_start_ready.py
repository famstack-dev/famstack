"""AI stacklet on_start_ready: keep thinking off, ask the AI one short question, then download the house voice's speech model.

Runs on every `stack up ai`, after the health check, which asks the AI
server only for its model list. A server can list its models and fail
every completion, so the run asks the AI one tiny question with `[ai]
default` and keeps the answer in the instance (`answer.py`), where the
setup page reads it. The question is one step of the run, as the health
checks are: a screen that follows the run sees it start, saying that a
model that is not loaded yet loads first, and end, or fail with the
server's error worded by its kind. A failed question does not fail the
run; it goes on and ends, so whatever follows it never waits for ever.

Before the question, famstack's oMLX gets thinking off for a Qwen3
model (`thinking.py`), on every start: the setting lives in oMLX's
per-model settings, which a new settings folder does not have, and a
start that finds it set changes nothing. Then a speech model that is
missing, or one a release or a new house language needs, is fetched.
What is fetched and why is in `voices.py`.
"""


from stack.prompt import dim

from .. import answer
from ..omlx import OMLXClient, is_omlx
from ..thinking import disable_thinking, is_qwen3
from ..voices import download_house_voice

__all__ = ["run"]

# What the question depends on, read from `[ai]`.
SETTINGS = ("openai_url", "default", "openai_key")
# The run's step for the question; the first answer waits for a model load.
QUESTION_STEP = "Asking the AI one short question (a model that is not loaded yet loads first)"


def run(ctx):
    """Keep thinking off, ask the AI, then fetch the house voice; none of
    them fails the run."""
    _keep_thinking_off(ctx)
    _ask_the_ai(ctx)
    download_house_voice(ctx)


def _keep_thinking_off(ctx) -> None:
    """Turn thinking off for a Qwen3 `[ai] default` on famstack's oMLX;
    nothing for another provider or model, or when oMLX's admin API does
    not let the stack in."""
    model = ctx.cfg("default", default="")
    if ctx.cfg("provider", default="") != "managed" or not is_qwen3(model):
        return
    admin_url = ctx.cfg("openai_url", default="").rstrip("/").removesuffix("/v1")
    if not is_omlx(admin_url):
        return
    client = OMLXClient(admin_url, api_key=ctx.cfg("openai_key", default=""))
    if client.login():
        disable_thinking(client, model, log=lambda msg: dim(f"  {msg}"))


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
