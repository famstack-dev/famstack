# curator.Dockerfile — slim runtime for the wiki curator sidecar.
#
# Deliberately NOT the bot-runner image: the curator needs python,
# git, loguru, the OpenAI SDK (the framework LLM client behind the
# wiki generation it subprocesses), and PyYAML (the wiki CLI it both
# imports and subprocess-runs validates generated frontmatter with
# yaml.safe_load) — 3 of the bot-runner's 10 dependencies. No Matrix,
# no libolm, no PDF stack. Code arrives by
# bind mount (see docker-compose.yml), same as the bot-runner, so
# code changes don't need an image rebuild; tzdata makes the
# WIKI_NIGHTLY local time honest inside the container.
#
# ffmpeg is the one heavy addition. The nightly diary runs in THIS
# container (curator.py subprocesses the compiler rather than exec-ing
# into the bot-runner), and the diary archives the media it publishes:
# voice notes need an AAC copy Safari can play, photographs need a
# bounded one a page can carry. Without it here the nightly run files
# originals and no page can show them.

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git tzdata ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "loguru>=0.7,<1.0" "openai>=1.50,<3.0" "pyyaml>=6,<7"

RUN adduser --disabled-password --uid 1000 curator
USER curator

CMD ["python", "-u", "/stacklets/memory/bot/curator.py"]
