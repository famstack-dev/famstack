# curator.Dockerfile — slim runtime for the wiki curator sidecar.
#
# Deliberately NOT the bot-runner image: the curator needs python,
# git, loguru, and the OpenAI SDK (the framework LLM client behind
# the wiki generation it subprocesses) — 2 of the bot-runner's 10
# dependencies. No Matrix, no libolm, no PDF stack. Code arrives by
# bind mount (see docker-compose.yml), same as the bot-runner, so
# code changes don't need an image rebuild; tzdata makes the
# WIKI_NIGHTLY local time honest inside the container.

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git tzdata \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "loguru>=0.7,<1.0" "openai>=1.50,<3.0"

RUN adduser --disabled-password --uid 1000 curator
USER curator

CMD ["python", "-u", "/stacklets/memory/bot/curator.py"]
