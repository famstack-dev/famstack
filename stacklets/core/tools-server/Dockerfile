FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

# --gecos "" suppresses the interactive Full Name/Room/Phone prompts
RUN adduser --disabled-password --gecos "" --uid 1000 tools
USER tools

# Uvicorn on port 8000 inside the container, mapped to 42000 on host
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
