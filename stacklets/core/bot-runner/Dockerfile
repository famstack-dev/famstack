FROM python:3.12-slim

# libolm is needed by matrix-nio for E2E encryption
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libolm-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py microbot.py accounts.py ./

RUN adduser --disabled-password --uid 1000 botrunner
RUN mkdir -p /botdata && chown botrunner:botrunner /botdata
USER botrunner

CMD ["python", "-u", "main.py"]
