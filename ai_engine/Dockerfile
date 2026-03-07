# ─────────────────────────────────────────────────────────
#  Lucid AI Engine — Dockerfile
#  OpenHands Software Agent SDK V1
# ─────────────────────────────────────────────────────────

FROM python:3.12-slim

WORKDIR /app

# System dependencies:
#   docker.io — Docker CLI + daemon socket access for sandbox management
#   git       — Required by the agent for repo operations inside workspaces
#   curl      — Container health checks
RUN apt-get update && apt-get install -y \
    curl \
    git \
    docker.io \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Install all Python dependencies including OpenHands SDK (all in requirements.txt)
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

ENV PYTHONUNBUFFERED=1
ENV PORT=8000

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
