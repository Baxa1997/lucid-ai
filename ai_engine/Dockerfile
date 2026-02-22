# ─────────────────────────────────────────────────────────
#  Lucid AI Engine — Dockerfile
#  OpenHands Software Agent SDK V1 · Docker-in-Docker
# ─────────────────────────────────────────────────────────

FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
# docker.io — Docker CLI for the Python Docker SDK
# git       — Required for cloning repos in sandbox
# curl      — Health checks
# libpq-dev — Required for psycopg2
RUN apt-get update && apt-get install -y \
    curl \
    git \
    docker.io \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Install core dependencies (all exist on PyPI)
RUN pip install --no-cache-dir \
    fastapi>=0.115.0 \
    "uvicorn[standard]>=0.30.0" \
    pydantic>=2.7.0 \
    websockets>=12.0 \
    python-multipart>=0.0.6 \
    "python-jose[cryptography]>=3.3.0" \
    "sqlalchemy[asyncio]>=2.0.0" \
    asyncpg>=0.29.0 \
    psycopg2-binary>=2.9.0 \
    alembic>=1.13.0 \
    python-dotenv>=1.0.0 \
    httpx>=0.27.0 \
    docker>=7.1.0 \
    litellm>=1.50.0 \
    google-generativeai>=0.8.0

# OpenHands SDK packages (not yet on PyPI — skip gracefully)
RUN pip install --no-cache-dir \
    openhands-sdk>=1.0.0 \
    openhands-tools>=1.0.0 \
    openhands-workspace>=1.0.0 \
    openhands-agent-server>=1.0.0 \
    2>/dev/null || echo "OpenHands SDK not available — running in mock mode"

# Copy application source
COPY . .

# Environment Defaults
ENV PYTHONUNBUFFERED=1
ENV PORT=8000

# Expose main API port
EXPOSE 8000

# Run Alembic migrations then start the application
CMD ["sh", "-c", "alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port 8000"]
