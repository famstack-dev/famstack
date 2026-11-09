"""Agent stacklet first-run configuration: ask first on a small Mac.

The agent sends the AI long prompts: its context window is 32,768 tokens
(`config.json`). On a 16 GB Mac (the small memory profile,
`stack.profile`) a 6,000-token prompt took 4 minutes, with 2.5 GB of
swap, and the other services slowed down with it. So the agent's first
start on a small Mac asks whether to start it anyway, default no. A no
starts nothing and writes nothing (`Cancelled`); a yes goes on as on any
other Mac.

The question is a setup form (ADR-021): a terminal asks it, the
dashboard's Add shows it, and `--answer start_anyway=yes` answers it
without either. A medium or large Mac, or an agent set up already, is
asked nothing. The answer is not stored: once the agent is set up there
is nothing left to ask.
"""

from stack.forms import Form
from stack.hooks import Cancelled
from stack.profile import profile_of

__all__ = ["questions", "apply"]

SMALL_MAC = Form(
    id="agent.small-mac",
    title="Agent on a 16 GB Mac",
    text=("Not recommended on 16 GB Macs. Stacky's prompts are long: on the 16 GB test Mac "
          "a 6,000-token prompt took 4 minutes, and other services moved to swap."),
    schema={
        "type": "object",
        "properties": {
            "start_anyway": {"type": "boolean", "title": "Start the agent anyway?",
                             "default": False},
        },
        "required": ["start_anyway"],
    },
)


def questions(ctx, answers):
    """The small-Mac form on the agent's first start on a small Mac, else
    None. Reads only."""
    if not ctx.stack._is_first_run(ctx.stacklet_id):
        return None
    if profile_of(ctx.stack.config).name != "small":
        return None
    return SMALL_MAC.pending(answers)


def apply(ctx, answers):
    """Go on with a yes; on a no, stop before anything is installed.

    Called only after the small-Mac form was answered."""
    if answers["start_anyway"]:
        return
    raise Cancelled("Cancelled. The agent was not started.")
