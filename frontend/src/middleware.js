// ─────────────────────────────────────────────────────────
//  Lucid AI — Next.js Middleware
//  Protects authenticated routes by checking Supabase auth cookies.
//
//  No @supabase/supabase-js import — Edge Middleware doesn't
//  support all Node.js APIs that the SDK needs.
//  We simply check if auth cookies exist. Actual JWT validation
//  happens server-side in API routes via gatekeeper.js.
// ─────────────────────────────────────────────────────────

import { NextResponse } from 'next/server';

/**
 * Check if the request has Supabase auth cookies.
 * We check for our explicit sb-access-token cookie (set by /auth/callback)
 * as well as the default Supabase cookie pattern.
 */
function hasSupabaseSession(request) {
  // Check our explicit cookie first
  if (request.cookies.get('sb-access-token')?.value) {
    return true;
  }
  // Fallback: check default Supabase cookie pattern
  const allCookies = request.cookies.getAll();
  return allCookies.some(
    (c) => c.name.startsWith('sb-') && c.name.includes('-auth-token')
  );
}

export function middleware(request) {
  const { pathname } = request.nextUrl;

  const isAuthenticated = hasSupabaseSession(request);

  // ── Protected routes: redirect to /login if not authenticated ──
  const protectedPaths = ['/dashboard', '/workspace', '/session', '/projects'];
  const isProtectedRoute = protectedPaths.some((p) => pathname.startsWith(p));

  if (isProtectedRoute && !isAuthenticated) {
    const loginUrl = request.nextUrl.clone();
    loginUrl.pathname = '/login';
    loginUrl.searchParams.set('redirect', pathname);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

// Only run middleware on relevant paths (skip static assets, images, etc.)
export const config = {
  matcher: [
    '/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)',
  ],
};
