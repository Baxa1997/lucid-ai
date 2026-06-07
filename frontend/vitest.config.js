// Vitest configuration for the frontend (Next.js + React + Tailwind).
//
// Why Vitest (not Jest):
//   • Uses Vite for transforms — handles JSX/ESM out of the box without
//     babel.config wrangling that Jest needs in a Next.js project.
//   • Faster cold start, friendlier watcher.
//
// jsdom is the DOM environment used by @testing-library/react. The Next.js
// App Router and most of the lib/ utilities don't need real-DOM features, so
// jsdom is sufficient — playwright/puppeteer would be overkill for unit tests.
//
// The `@/` path alias mirrors next.config.js + jsconfig.json so test imports
// match the runtime resolution.

import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

// The project convention is `.js` for everything, but a couple of
// component test files (and the components they test) use `.jsx`
// because Vite's SSR parser can't recognise JSX in `.js` and there is
// no way to configure that without dropping into babel-mode for the
// whole build. Tests + components agree on the `.jsx` extension; Next.js
// Turbopack resolves it natively when extensionless imports are used.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test-setup.js'],
    include: ['src/**/*.{test,spec}.{js,jsx,ts,tsx}'],
    css: false,
    // Per-test timeout — keep tight so a hang surfaces fast in CI.
    testTimeout: 10_000,
  },
});
