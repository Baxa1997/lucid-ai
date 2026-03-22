// ─────────────────────────────────────────────────────────
//  Lucid AI — Next.js Middleware
//  Protects authenticated routes by checking Supabase auth cookies.
//
//  The browser client keeps sb-access-token cookie in sync
//  with Supabase's localStorage session via onAuthStateChange.
//  This middleware just checks if the cookie exists.
// ─────────────────────────────────────────────────────────

import { NextResponse } from 'next/server';

/**
 * Check if the request has Supabase auth cookies.
 * We check for our explicit sb-access-token cookie (set by /auth/callback
 * and kept in sync by the browser client's onAuthStateChange listener),
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

  // ── Skip middleware for internal Next.js requests ──
  // RSC (React Server Component) requests and prefetches should NOT redirect.
  // They use the `Sec-Fetch-Dest` and `Next-Router-Prefetch` headers.
  const isPrefetch = request.headers.get('next-router-prefetch') === '1';
  const isRSC = request.headers.get('rsc') === '1';
  if (isPrefetch || isRSC) {
    return NextResponse.next();
  }

  const isAuthenticated = hasSupabaseSession(request);
  const protectedPaths = ['/dashboard', '/workspace', '/session', '/projects'];
  const isProtectedRoutePath = protectedPaths.some((p) => pathname.startsWith(p));
  
  if (isProtectedRoutePath) {
    console.log(`[Middleware] Path: ${pathname}, Auth: ${isAuthenticated}`);
  }

  // ── Protected routes: redirect to /login if not authenticated ──
  if (isProtectedRoutePath && !isAuthenticated) {
    console.warn(`[Middleware] Unauthorized redirect to /login from ${pathname}`);
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
