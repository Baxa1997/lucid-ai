FROM e2bdev/code-interpreter:latest

# The base image runs as a non-root user — switch to root for apt/npm global installs
USER root

# Install Node.js 20 LTS + package managers
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && npm install -g pnpm yarn \
    && npm cache clean --forcewhat if I wnat to ru

# Pre-populate the pnpm content-addressable store with packages used by
# generated Next.js/Tailwind/shadcn projects.
# pnpm install downloads them once here; at runtime --prefer-offline serves
# from the store without hitting the network.
# This cuts pnpm install time from 3-4 minutes to ~15-20 seconds.
RUN mkdir -p /tmp/cache-warmer && cd /tmp/cache-warmer && cat > package.json << 'EOF'
{
  "name": "cache-warmer",
  "version": "1.0.0",
  "private": true,
  "dependencies": {
    "next": "^14",
    "react": "^18",
    "react-dom": "^18",
    "tailwindcss": "^3",
    "postcss": "^8",
    "autoprefixer": "^10",
    "class-variance-authority": "^0.7",
    "clsx": "^2",
    "tailwind-merge": "^2",
    "lucide-react": "^0.400.0",
    "@radix-ui/react-slot": "^1",
    "@radix-ui/react-dialog": "^1",
    "@radix-ui/react-dropdown-menu": "^2",
    "@radix-ui/react-label": "^2",
    "@radix-ui/react-select": "^2",
    "@radix-ui/react-separator": "^1",
    "@radix-ui/react-tabs": "^1",
    "@radix-ui/react-toast": "^1",
    "@radix-ui/react-tooltip": "^1",
    "framer-motion": "^11",
    "zustand": "^4",
    "axios": "^1",
    "date-fns": "^3",
    "zod": "^3"
  },
  "devDependencies": {
    "typescript": "^5",
    "@types/node": "^20",
    "@types/react": "^18",
    "@types/react-dom": "^18"
  }
}
EOF

# Warm pnpm store (primary) — also warm npm cache as fallback
RUN cd /tmp/cache-warmer && pnpm install --prefer-offline 2>&1 | tail -5
RUN cd /tmp/cache-warmer && npm install --prefer-offline 2>&1 | tail -5

# Keep the pnpm store (/root/.local/share/pnpm/store) and npm cache (/root/.npm)
# Discard the actual node_modules (they are rebuilt at runtime)
RUN rm -rf /tmp/cache-warmer

# Verify
RUN node --version && npm --version && pnpm --version
