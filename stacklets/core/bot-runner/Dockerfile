FROM python:3.12-slim

# libolm is needed by matrix-nio for E2E encryption. ffmpeg makes the
# derivatives the media archive publishes: an AAC copy of a voice note
# (Safari does not decode Opus in Ogg) and a width-bounded image, both
# written by `stack memory diary` and the capture path, which run here.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libolm-dev ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py microbot.py room_context.py accounts.py voice.py ./

RUN adduser --disabled-password --uid 1000 botrunner
RUN mkdir -p /botdata && chown botrunner:botrunner /botdata
USER botrunner

CMD ["python", "-u", "main.py"]
