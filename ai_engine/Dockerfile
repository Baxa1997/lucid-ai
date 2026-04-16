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
#   node 20    — Required for running/building frontend projects
#   xvfb/x11vnc/novnc/websockify — noVNC live preview
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    docker.io \
    # ── noVNC live preview dependencies ──
    xvfb \
    x11vnc \
    novnc \
    websockify \
    && rm -rf /var/lib/apt/lists/*

# Virtual display for noVNC live preview
ENV DISPLAY=:99


# Install Node.js 20 for running frontend projects
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install pnpm + yarn (templates may use any of these)
RUN npm install -g pnpm@latest yarn@latest

# Warm the pnpm store with common dependencies shared across all 3 templates.
# This means workspace installs pull from the local store (~10-20s) instead
# of downloading from the network (~60-180s) every time.
RUN mkdir -p /tmp/pnpm-warmup && cd /tmp/pnpm-warmup \
    && pnpm init \
    && pnpm add \
        react react-dom \
        next \
        vue \
        vite @vitejs/plugin-react @vitejs/plugin-vue \
        tailwindcss postcss autoprefixer \
        @radix-ui/react-slot \
        class-variance-authority clsx tailwind-merge \
        lucide-react \
    && cd / && rm -rf /tmp/pnpm-warmup

# NOTE: cloudflared removed — E2B cloud sandboxes handle preview URLs natively

# Install Claude Code CLI (required by claude-code-sdk Python package)
RUN npm install -g @anthropic-ai/claude-code

# ── Pre-approve ALL Claude Code tool permissions ──────────
# This prevents the agent from ever pausing for permission prompts.
# DO NOT REMOVE — this has been repeatedly reset and must persist.
RUN mkdir -p /root/.claude && echo '{\
  "permissions": {\
    "allow": [\
      "Bash(*)",\
      "Read(*)",\
      "Write(*)",\
      "Edit(*)",\
      "MultiEdit(*)",\
      "Glob(*)",\
      "Grep(*)",\
      "LS(*)",\
      "TodoRead(*)",\
      "TodoWrite(*)"\
    ],\
    "deny": []\
  }\
}' > /root/.claude/settings.json

COPY requirements.txt .

# Install all Python dependencies including OpenHands SDK (all in requirements.txt)
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Copy startup script
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

ENV PYTHONUNBUFFERED=1
ENV PORT=8000

EXPOSE 8000
# noVNC preview port
EXPOSE 6080

CMD ["/app/start.sh"]
