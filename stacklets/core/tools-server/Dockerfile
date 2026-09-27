FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
COPY access.py .
COPY resolver.py .
COPY capture_index.py .

# --gecos "" suppresses the interactive Full Name/Room/Phone prompts
RUN adduser --disabled-password --gecos "" --uid 1000 tools
USER tools

# Two listeners (access.py): 8000 for the stack's containers, never
# published, and 8001, published as 42000, which serves only the links.
CMD ["python", "server.py"]
