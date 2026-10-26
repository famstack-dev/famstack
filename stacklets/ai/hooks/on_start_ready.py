"""AI stacklet on_start_ready: download the stack's speech models.

Runs on every `stack up ai`, after the engine answers, so a speech model
that is missing, or one a release added since, is fetched. What is
fetched and why is in `voices.py`.
"""


from ..voices import download_speech_models


def run(ctx):
    download_speech_models(ctx)
