// ─────────────────────────────────────────────────────────
//  Lucid AI — Dockerfile Generator
//  Generates a production Dockerfile based on stack type.
//  Company standard: yarn + nginx for static builds.
// ─────────────────────────────────────────────────────────

/**
 * Generate a Dockerfile for the given stack.
 *
 * @param {object} opts
 * @param {string} opts.stack — 'nextjs'|'react'|'vue'|'angular'|'html-css'
 * @param {number} opts.nodeVersion — Node.js major version (default 20)
 * @returns {{ content: string, expose: number }}
 */
export function generateDockerfile({ stack = 'react', nodeVersion = 20 }) {
  const type = stack.toLowerCase();

  if (type === 'nextjs') {
    return {
      expose: 3000,
      content: `FROM node:${nodeVersion}-alpine AS builder
RUN apk update && apk add yarn
WORKDIR /app
COPY . ./
RUN yarn install --network-timeout 1000000000
RUN NODE_OPTIONS=--max_old_space_size=4096 yarn build

FROM node:${nodeVersion}-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public
EXPOSE 3000
CMD ["node", "server.js"]
`,
    };
  }

  if (type === 'html-css') {
    // Plain HTML — no build step, just serve with nginx
    return {
      expose: 80,
      content: `FROM nginx:alpine
COPY . /build
COPY nginx.conf /etc/nginx/nginx.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
`,
    };
  }

  // React / Vue / Angular — nginx-based static build (company standard)
  return {
    expose: 80,
    content: `FROM node:${nodeVersion}-alpine AS builder
RUN apk update && apk add yarn
WORKDIR /app
COPY . ./
RUN yarn install --network-timeout 1000000000
RUN NODE_OPTIONS=--max_old_space_size=4096 yarn build

FROM nginx:alpine
COPY --from=builder /app/dist /build
COPY nginx.conf /etc/nginx/nginx.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
`,
  };
}
