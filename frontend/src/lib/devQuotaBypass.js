// Local-development escape hatch for the monthly TOKEN cap only.
//
// Set NEXT_PUBLIC_DEV_BYPASS_QUOTA=1 in frontend/.env.local to keep
// editing existing projects (and other token-consuming actions like
// intent-check + enhance-prompt) after the monthly token quota is
// spent. Only honored outside production builds so it cannot ship.
//
// The PROJECT-creation cap is intentionally NOT bypassed — even in
// dev, /api/projects/check-create still enforces canCreateProject so
// you can verify the real lock behavior. The server still records
// real usage either way; the bypass only relaxes the UI/API gate.

export function isTokenBypassActive() {
  if (typeof process === "undefined") return false;
  if (process.env.NODE_ENV === "production") return false;
  return process.env.NEXT_PUBLIC_DEV_BYPASS_QUOTA === "1";
}
