FROM python:3.12-slim

WORKDIR /app

# git reads the memory repo's history: when a linked card was removed.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
COPY access.py .
COPY resolver.py .
COPY cards_page.py .
COPY cards/ cards/
# The `stack` library (lib/stack) is not copied: compose mounts it at
# /app/stack, as it does for the bot-runner.

# --gecos "" suppresses the interactive Full Name/Room/Phone prompts
RUN adduser --disabled-password --gecos "" --uid 1000 tools
USER tools

# Two listeners (access.py): 8000 for the stack's containers, never
# published, and 8001, published as 42000, which serves only the links.
CMD ["python", "server.py"]
