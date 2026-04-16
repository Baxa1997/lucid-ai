# E2B custom template: Node.js 20 + pnpm pre-installed
# Uses Ubuntu base (required for E2B v2 templates)

FROM ubuntu:22.04

# Avoid interactive prompts
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    git \
    ca-certificates \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 20 LTS
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

# Install pnpm + yarn globally
RUN npm install -g pnpm@latest yarn@latest

# Verify installations
RUN node --version && npm --version && pnpm --version

# Create app directory owned by user
RUN mkdir -p /home/user/app && \
    useradd -m -s /bin/bash user || true && \
    chown -R user:user /home/user

WORKDIR /home/user/app
