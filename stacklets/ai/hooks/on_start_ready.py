"""AI stacklet on_start_ready: download the house voice's speech model.

Runs on every `stack up ai`, after the engine answers, so a speech model
that is missing, or one a release or a new house language needs, is
fetched. What is fetched and why is in `voices.py`.
"""


from ..voices import download_house_voice


def run(ctx):
    download_house_voice(ctx)
