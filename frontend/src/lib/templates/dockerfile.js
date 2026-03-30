// ─────────────────────────────────────────────────────────
//  Lucid AI — Dockerfile Generator
//  Generates a production Dockerfile based on stack type.
//  Uses npm install for flexibility across AI-generated repos.
// ─────────────────────────────────────────────────────────

/**
 * Generate a Dockerfile for the given stack.
 *
 * @param {object} opts
 * @param {string} opts.stack — 'nextjs'|'react'|'react-vite'|'html-css'
 * @param {number} opts.nodeVersion — Node.js major version (default 20)
 * @returns {{ content: string, expose: number }}
 */
export function generateDockerfile({ stack = 'react', packageManager = 'npm', nodeVersion = 20 }) {
  const type = stack.toLowerCase();

  const isYarn = packageManager === 'yarn';
  const isPnpm = packageManager === 'pnpm';
  const isBun = packageManager === 'bun';

  const preInstall = isYarn || isPnpm 
    ? 'RUN corepack enable\n' 
    : isBun 
    ? 'RUN npm install -g bun\n' 
    : '';

  const installCmd = isYarn ? 'yarn install' : isPnpm ? 'pnpm install' : isBun ? 'bun install' : 'npm install';
  const buildCmd = isYarn ? 'yarn build' : isPnpm ? 'pnpm build' : isBun ? 'bun run build' : 'npm run build';
  const startCmd = isYarn ? 'CMD ["yarn", "start"]' : isPnpm ? 'CMD ["pnpm", "start"]' : isBun ? 'CMD ["bun", "run", "start"]' : 'CMD ["npm", "start"]';

  // Add high memory limit globally for Next/Vite builds 
  const buildWithMemory = `RUN NODE_OPTIONS=--max_old_space_size=4096 ${buildCmd}`;

  if (type === 'nextjs') {
    return {
      expose: 3000,
      content: `FROM node:${nodeVersion}-alpine AS builder
WORKDIR /app
COPY package.json ${isYarn ? 'yarn.lock ' : isPnpm ? 'pnpm-lock.yaml ' : isBun ? 'bun.lockb ' : '*lock* '}./
${preInstall}RUN ${installCmd}
COPY . .
${buildWithMemory}

FROM node:${nodeVersion}-alpine AS runner
WORKDIR /app
ENV NODE_ENV production
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/package.json ./package.json
COPY --from=builder /app/public ./public
EXPOSE 3000
${startCmd}
`,
    };
  }

  if (type === 'html-css') {
    return {
      expose: 80,
      content: `FROM nginx:alpine
COPY . /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
`,
    };
  }

  // React / React+Vite — multi-stage build → nginx
  return {
    expose: 80,
    content: `FROM node:${nodeVersion}-alpine AS builder
WORKDIR /app
COPY package.json ${isYarn ? 'yarn.lock ' : isPnpm ? 'pnpm-lock.yaml ' : isBun ? 'bun.lockb ' : '*lock* '}./
${preInstall}RUN ${installCmd}
COPY . .
${buildWithMemory}

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
`,
  };
}
