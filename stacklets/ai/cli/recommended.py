"""stack ai recommended: the models recommended for this Mac, on the oMLX app.

    stack ai recommended                          # lists the verbs
    stack ai recommended show                     # the oMLX app, and each model's state
    stack ai recommended download --speech off    # downloads what is missing, and waits
    stack ai recommended download --speech on --no-wait

The setup page's AI step reads `show` and starts `download --no-wait`
through the host API; a terminal can run both. `download` records
`[ai] voice` and `[ai] default`, so the AI's first start that follows
installs speech or not and loads that model. Without `--no-wait` it
waits until oMLX has finished, and prints each model's progress, as a
command that hands work to a background worker does (command rule 5).
With it, it returns once oMLX has the downloads, which go on by
themselves; `show` says where they are. The logic is in `recommended.py`.
"""

__all__ = ["HELP", "run"]

HELP = "Models recommended for this Mac on the oMLX app: show, download"

import argparse
import sys

from ..recommended import download, recommended, wait

VERBS = {"show": "The oMLX app on this Mac, and where each recommended model stands",
         "download": "Download what is missing: --speech on|off [--no-wait]"}


def run(args, stacklet, config):
    """The verb's answer as a dict; the verbs without one.

        run(["download", "--speech", "off", "--no-wait"], stacklet, config)
        # the plan, as `recommended.recommended` reads it, with the chat
        # model "downloading"
    """
    if not args or args[0] not in VERBS:
        return {"verbs": VERBS} if not args else {
            "error": f"Unknown verb '{args[0]}'. Use: {', '.join(VERBS)}"}
    toml = config.get("stack", {})
    if args[0] == "show":
        return recommended(toml)
    opts = _download_options(args[1:])
    if isinstance(opts, dict):
        return opts
    plan = download(toml, config["set_cfg"], speech=opts.speech == "on")
    if "error" in plan or opts.no_wait:
        return plan
    return wait(toml, lambda line: print(line, file=sys.stderr))


def _download_options(args: list[str]) -> argparse.Namespace | dict:
    p = argparse.ArgumentParser(prog="stack ai recommended download", add_help=False)
    p.add_argument("--speech", choices=("on", "off"), required=True)
    p.add_argument("--no-wait", action="store_true")
    try:
        return p.parse_args(args)
    except SystemExit:
        return {"error": "Usage: stack ai recommended download --speech on|off [--no-wait]"}

