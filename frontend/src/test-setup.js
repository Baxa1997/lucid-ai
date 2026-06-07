// Test setup shared by every Vitest run.
//
// Loaded once before the test files via vitest.config.js `setupFiles`.
// Anything global (jsdom polyfills, jest-dom matchers, fetch mocks) lives here.

import '@testing-library/jest-dom/vitest';
import { afterEach, vi } from 'vitest';
import { cleanup } from '@testing-library/react';

// React Testing Library leaks DOM between tests unless we clean up.
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// jsdom doesn't ship sessionStorage / localStorage on its own — provide a
// minimal in-memory shim so useAgentSession's wizard_prompt_* reads/writes
// behave correctly under test.
function createStorageMock() {
  const store = new Map();
  return {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
    clear: () => store.clear(),
    key: (i) => Array.from(store.keys())[i] ?? null,
    get length() {
      return store.size;
    },
  };
}

if (typeof window !== 'undefined') {
  if (!('sessionStorage' in window)) {
    Object.defineProperty(window, 'sessionStorage', {
      value: createStorageMock(),
      writable: true,
    });
  }
  if (!('localStorage' in window)) {
    Object.defineProperty(window, 'localStorage', {
      value: createStorageMock(),
      writable: true,
    });
  }
}

// jsdom doesn't ship a WebSocket — useAgentSession touches the constructor
// even when it doesn't connect, so provide a stub that records open/send/close
// calls. Tests can swap this for a richer mock when they need behavior.
if (typeof globalThis.WebSocket === 'undefined') {
  globalThis.WebSocket = class StubWebSocket {
    static OPEN = 1;
    static CLOSED = 3;
    readyState = 0;
    constructor(url) {
      this.url = url;
      this.sent = [];
      queueMicrotask(() => {
        this.readyState = 1;
        this.onopen?.({ type: 'open' });
      });
    }
    send(data) {
      this.sent.push(data);
    }
    close() {
      this.readyState = 3;
      this.onclose?.({ type: 'close', code: 1000 });
    }
  };
}
