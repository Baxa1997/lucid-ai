# ─────────────────────────────────────────────────────────
#  Lucid AI Engine — Dockerfile
#  OpenHands Software Agent SDK V1
# ─────────────────────────────────────────────────────────

FROM python:3.12-slim

WORKDIR /app

# System dependencies:
#   docker.io  — Docker CLI + daemon socket access for sandbox management
#   git        — Required by the agent for repo operations inside workspaces
#   curl       — Container health checks
#   chromium   — Required by Playwright for browser-based app inspection
#   node 20    — Required for running/building frontend projects
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    docker.io \
    chromium \
    fonts-liberation \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

# Tell Playwright to use the system-installed Chromium
ENV PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1

# Install Node.js 20 for running frontend projects
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Install all Python dependencies including OpenHands SDK (all in requirements.txt)
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers (uses system Chromium as fallback)
RUN pip install playwright && python -m playwright install chromium 2>/dev/null || true

# Copy application source
COPY . .

ENV PYTHONUNBUFFERED=1
ENV PORT=8000

EXPOSE 8000

CMD ["uvicorn", "main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--reload", \
     "--ws-ping-interval", "20", \
     "--ws-ping-timeout", "60", \
     "--timeout-keep-alive", "65"]

