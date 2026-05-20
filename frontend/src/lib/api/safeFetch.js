// ─────────────────────────────────────────────────────────
//  safeJsonFetch — fetch wrapper that survives empty / non-JSON bodies.
//
//  Fixes a production crash where `fetch(...).then(r => r.json())` blew
//  up with "Unexpected end of JSON input" any time Stripe / a proxy
//  returned an empty body (502 from edge, 204 on portal create, etc.).
//
//  Returns null for empty bodies, throws an Error with a useful message
//  on non-2xx, and parses JSON safely on success.
// ─────────────────────────────────────────────────────────

export async function safeJsonFetch(url, options = {}) {
  const response = await fetch(url, options);

  // 204 No Content — no body to parse.
  if (response.status === 204) return null;

  const text = await response.text();

  if (!response.ok) {
    let errorMessage = `HTTP ${response.status}`;
    if (text) {
      try {
        const parsed = JSON.parse(text);
        errorMessage = parsed.error || parsed.detail || parsed.message || errorMessage;
      } catch {
        errorMessage = text;
      }
    }
    const err = new Error(errorMessage);
    err.status = response.status;
    err.response = response;
    throw err;
  }

  if (!text) return null;

  try {
    return JSON.parse(text);
  } catch {
    throw new Error('Invalid JSON response from server');
  }
}

export default safeJsonFetch;
