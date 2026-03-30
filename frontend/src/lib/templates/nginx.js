// ─────────────────────────────────────────────────────────
//  Lucid AI — Nginx Config Generator
//  Generates nginx.conf for SPA routing + static asset caching.
// ─────────────────────────────────────────────────────────

/**
 * Generate nginx.conf for the project.
 *
 * @param {object} opts
 * @param {boolean} opts.isAdmin — whether this is an admin panel
 * @returns {string}
 */
export function generateNginxConf({ isAdmin = false } = {}) {
  return `server {
    listen 80;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location ~* \\.(js|css|png|jpg|jpeg|gif|ico|svg)$ {
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
}
`;
}
