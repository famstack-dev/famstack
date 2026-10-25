"""AI stacklet on_start_ready: download the voices of the house's languages.

Runs on every `stack up ai`, after the speech container answers, so a
voice model that is missing, or a language added to stack.toml since,
is fetched. What is fetched and why is in `voices.py`.
"""


from ..voices import download_house_voices


def run(ctx):
    download_house_voices(ctx)
