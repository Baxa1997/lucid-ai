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

    supabase.auth.onAuthStateChange((event, session) => {
      if (event === 'SIGNED_IN' && session) {
        document.cookie = `sb-access-token=${session.access_token}; path=/; max-age=${60 * 60 * 24 * 7}; samesite=lax`;
        document.cookie = `sb-refresh-token=${session.refresh_token}; path=/; max-age=${60 * 60 * 24 * 7}; samesite=lax`;
        sessionStorage.setItem('lucid-just-signed-in', 'true');
        router.push('/dashboard/engineer');
      }
    });

    supabase.auth.getSession().then(({ data: { session } }) => {
      if (session) {
        document.cookie = `sb-access-token=${session.access_token}; path=/; max-age=${60 * 60 * 24 * 7}; samesite=lax`;
        document.cookie = `sb-refresh-token=${session.refresh_token}; path=/; max-age=${60 * 60 * 24 * 7}; samesite=lax`;
        sessionStorage.setItem('lucid-just-signed-in', 'true');
        router.push('/dashboard/engineer');
      }
    });

    const timeout = setTimeout(() => {
      setError('Authentication timed out. Please try again.');
    }, 15000);

    return () => clearTimeout(timeout);
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
