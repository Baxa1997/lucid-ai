'use client';

// ─────────────────────────────────────────────────────────
//  Lucid AI — Supabase Auth Callback (Client-Side)
//  Handles the OAuth code exchange for login (Google, etc.)
// ─────────────────────────────────────────────────────────

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { getSupabaseBrowserClient } from '@/lib/supabase/client';

export default function AuthCallbackPage() {
  const router = useRouter();
  const [error, setError] = useState(null);

  useEffect(() => {
    const supabase = getSupabaseBrowserClient();
    let redirected = false;

    const doRedirect = (session) => {
      if (redirected) return;
      redirected = true;
      // Cookies are kept in sync by the global onAuthStateChange listener
      // in client.js. We just need to set the toast flag and redirect.
      sessionStorage.setItem('lucid-just-signed-in', 'true');
      router.push('/dashboard/engineer');
    };

    // Listen for the SIGNED_IN event (OAuth code exchange triggers this)
    const { data: { subscription } } = supabase.auth.onAuthStateChange((event, session) => {
      if (event === 'SIGNED_IN' && session) {
        doRedirect(session);
      }
    });

    // Also check if session already exists (e.g. page reload during callback)
    supabase.auth.getSession().then(({ data: { session } }) => {
      if (session) {
        doRedirect(session);
      }
    });

    const timeout = setTimeout(() => {
      setError('Authentication timed out. Please try again.');
    }, 15000);

    return () => {
      clearTimeout(timeout);
      subscription?.unsubscribe();
    };
  }, [router]);

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-white dark:bg-[#0d1117]">
        <div className="text-center">
          <p className="text-red-500 text-sm font-medium mb-4">{error}</p>
          <a href="/login" className="text-blue-600 text-sm font-medium hover:underline">
            Back to login
          </a>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-white dark:bg-[#0d1117]">
      <div className="text-center">
        <div className="w-8 h-8 border-3 border-blue-200 border-t-blue-600 rounded-full animate-spin mx-auto mb-4" />
        <p className="text-slate-500 dark:text-slate-400 text-sm font-medium">
          Completing sign in...
        </p>
      </div>
    </div>
  );
}
