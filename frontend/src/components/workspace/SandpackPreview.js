'use client';

// ─────────────────────────────────────────────────────────
//  SandpackPreview — in-browser bundler preview via Sandpack.
//
//  Receives the full workspace file tree from the backend
//  (via preview_files WebSocket message) and renders a live
//  preview in an iframe — no install step, no server needed.
//
//  Template mapping:
//    nextjs → Sandpack 'nextjs' (Nodebox, ~15-30s first load)
//    vue    → Sandpack 'vite-vue' (~2-5s)
//    react  → Sandpack 'vite-react' (~2-5s)
// ─────────────────────────────────────────────────────────

import { useMemo } from 'react';
import { SandpackProvider, SandpackPreview as SPPreview } from '@codesandbox/sandpack-react';

const SANDPACK_TEMPLATE = {
  nextjs: 'nextjs',
  vue:    'vite-vue',
  react:  'vite-react',
};

export default function SandpackPreview({ files = {}, template = 'react' }) {
  // Normalize file paths: ensure leading slash, convert backslashes
  const sandpackFiles = useMemo(() => {
    const result = {};
    for (const [path, content] of Object.entries(files)) {
      const normalized = (path.startsWith('/') ? path : `/${path}`).replace(/\\/g, '/');
      result[normalized] = content;
    }
    return result;
  }, [files]);

  const sandpackTemplate = SANDPACK_TEMPLATE[template] || 'vite-react';

  // SandpackProvider is a context-only wrapper (no DOM node), so height styling
  // must go on the outer div. SPPreview's default height is 300px — override with
  // position:absolute so it fills whatever container RightPanel gives us.
  return (
    <div style={{ position: 'absolute', inset: 0, overflow: 'hidden' }}>
      <SandpackProvider
        template={sandpackTemplate}
        files={sandpackFiles}
        options={{
          externalResources: template !== 'nextjs'
            ? ['https://cdn.tailwindcss.com']
            : [],
          recompileMode: 'delayed',
          recompileDelay: 300,
        }}
        theme="light"
      >
        <SPPreview
          style={{ width: '100%', height: '100%' }}
          showNavigator
          showOpenInCodeSandbox={false}
        />
      </SandpackProvider>
    </div>
  );
}
