FROM python:3.12-slim

WORKDIR /app

# git reads the memory repo's history: when a linked card was removed.
# tzdata: the dashboard's "today" is the instance's (`TIMEZONE`).
RUN apt-get update && apt-get install -y --no-install-recommends git tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
COPY access.py .
COPY resolver.py .
COPY cards_page.py .
COPY cards/ cards/
COPY dashboard_page.py .
COPY family_view.py .
COPY activity_widget.py .
COPY stack_widget.py .
COPY setup_widget.py .
COPY live_routes.py .
COPY dashboard/ dashboard/
# The `stack` library (lib/stack) is not copied: compose mounts it at
# /app/stack, as it does for the bot-runner.

# --gecos "" suppresses the interactive Full Name/Room/Phone prompts
RUN adduser --disabled-password --gecos "" --uid 1000 tools
USER tools

# Three listeners (access.py): 8000 for the stack's containers, never
# published; 8001, published as 42000, which serves the links and the
# dashboard; 8002, the card browser, published on this Mac only.
CMD ["python", "server.py"]
