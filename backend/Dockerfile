FROM python:3.11-slim

# Build tools: ujson (a transitive dep of screenplay-pdf-to-json) is pinned to
# 2.0.3 and has no Python 3.11 wheels — pip falls back to compiling it from source.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
