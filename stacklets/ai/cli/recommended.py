"""stack ai recommended: the settings recommended for this Mac.

    stack ai recommended          # lists the verbs
    stack ai recommended show     # the memory profile, speech, the models, the server [ai] names

The setup page's AI step reads `show` through the host API; a terminal
can run it too. It reads `stack.toml` and this Mac's memory, asks no
server and changes nothing. The logic is in `recommended.py`.
"""

__all__ = ["HELP", "run"]

HELP = "The settings recommended for this Mac: show"

from ..recommended import recommended

VERBS = {"show": "The memory profile, speech and the models recommended for this Mac"}


def run(args, stacklet, config):
    """The verb's answer as a dict; the verbs without one.

        run(["show"], stacklet, config)
        # {"memory_gb": 16, "profile": "small", "speech": False, "models": [...], "other": None}
    """
    if not args or args[0] not in VERBS:
        return {"verbs": VERBS} if not args else {
            "error": f"Unknown verb '{args[0]}'. Use: {', '.join(VERBS)}"}
    return recommended(config.get("stack", {}))
