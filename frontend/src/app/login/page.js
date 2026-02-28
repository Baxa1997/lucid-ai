'use client';

import Link from 'next/link';
import { Zap } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useState, useEffect } from 'react';
import ThemeModeSelector from '@/components/ThemeModeSelector';
import Toast from '@/components/Toast';
import { getSupabaseBrowserClient } from '@/lib/supabase/client';

export default function LoginPage() {
  const router = useRouter();
  const [isLoading, setIsLoading] = useState(null); // tracks which provider is loading
  const [error, setError] = useState('');
  const [toast, setToast] = useState(null);

  // Check for one-time logout toast
  useEffect(() => {
    if (typeof window !== 'undefined') {
      const justSignedOut = sessionStorage.getItem('lucid-just-signed-out');
      if (justSignedOut) {
        sessionStorage.removeItem('lucid-just-signed-out');
        setToast({ message: 'Signed out successfully.', type: 'logout' });
      }
    }
  }, []);

  const supabase = getSupabaseBrowserClient();

  const handleOAuthLogin = async (provider) => {
    setIsLoading(provider);
    setError('');

    const { error } = await supabase.auth.signInWithOAuth({
      provider,
      options: {
        redirectTo: `${window.location.origin}/auth/callback`,
      },
    });

    if (error) {
      setError(error.message);
      setIsLoading(null);
    }
    // On success, Supabase redirects the browser — no further action needed
  };

  return (
    <div className="min-h-screen bg-[#f0f4f9] dark:bg-slate-950 flex flex-col items-center justify-center p-4 relative overflow-hidden transition-colors duration-200">

      {/* Toast notification */}
      {toast && (
        <Toast
          message={toast.message}
          type={toast.type}
          onDone={() => setToast(null)}
        />
      )}

      {/* Theme Toggle */}
      <div className="absolute top-5 right-5 z-20">
        <ThemeModeSelector />
      </div>

      {/* Main Container */}
      <div className="w-full max-w-[400px] flex flex-col items-center relative z-10 animate-slide-up">

        {/* Logo */}
        <div className="mb-8 flex flex-col items-center text-center">
          <div className="relative mb-4">
            <div className="w-14 h-14 bg-gradient-to-br from-blue-600 to-blue-700 rounded-2xl flex items-center justify-center shadow-lg shadow-blue-600/20">
              <Zap className="w-7 h-7 text-white fill-current" />
            </div>
          </div>
          <h1 className="text-2xl font-extrabold text-slate-900 dark:text-slate-100 tracking-tight">Welcome to Lucid AI</h1>
          <p className="text-slate-500 dark:text-slate-400 text-sm mt-1.5 font-medium">Sign in to your workspace</p>
        </div>

        {/* Login Card */}
        <div className="w-full bg-white dark:bg-slate-900 rounded-2xl shadow-soft border border-slate-200 dark:border-slate-800 p-6 sm:p-8">

          {error && (
            <div className="mb-4 p-3 rounded-lg bg-red-50 dark:bg-red-500/10 border border-red-100 dark:border-red-500/20 text-red-600 dark:text-red-400 text-xs font-medium text-center">
              {error}
            </div>
          )}

          {/* OAuth Buttons */}
          <div className="flex flex-col gap-3">

            {/* Google */}
            <button
              id="login-google"
              onClick={() => handleOAuthLogin('google')}
              disabled={isLoading}
              className="flex items-center justify-center gap-3 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-700 hover:border-slate-300 dark:hover:border-slate-600 text-slate-700 dark:text-slate-300 font-bold py-3 rounded-xl transition-all text-sm group active:scale-[0.98] disabled:opacity-60 disabled:cursor-not-allowed"
            >
              {isLoading === 'google' ? (
                <div className="w-4 h-4 border-2 border-slate-300 border-t-slate-600 rounded-full animate-spin" />
              ) : (
                <svg className="w-5 h-5" viewBox="0 0 24 24">
                  <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4" />
                  <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853" />
                  <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.26.81-.58z" fill="#FBBC05" />
                  <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335" />
                </svg>
              )}
              <span>Continue with Google</span>
            </button>

            {/* GitHub */}
            <button
              id="login-github"
              onClick={() => handleOAuthLogin('github')}
              disabled={isLoading}
              className="flex items-center justify-center gap-3 bg-slate-900 dark:bg-slate-100 border border-slate-900 dark:border-slate-100 hover:bg-slate-800 dark:hover:bg-slate-200 text-white dark:text-slate-900 font-bold py-3 rounded-xl transition-all text-sm group active:scale-[0.98] disabled:opacity-60 disabled:cursor-not-allowed"
            >
              {isLoading === 'github' ? (
                <div className="w-4 h-4 border-2 border-slate-500 border-t-white dark:border-t-slate-900 rounded-full animate-spin" />
              ) : (
                <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M12 0c-6.626 0-12 5.373-12 12 0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23.957-.266 1.983-.399 3.003-.404 1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576 4.765-1.589 8.199-6.086 8.199-11.386 0-6.627-5.373-12-12-12z" />
                </svg>
              )}
              <span>Continue with GitHub</span>
            </button>

            {/* GitLab */}
            <button
              id="login-gitlab"
              onClick={() => handleOAuthLogin('gitlab')}
              disabled={isLoading}
              className="flex items-center justify-center gap-3 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-700 hover:border-slate-300 dark:hover:border-slate-600 text-slate-700 dark:text-slate-300 font-bold py-3 rounded-xl transition-all text-sm group active:scale-[0.98] disabled:opacity-60 disabled:cursor-not-allowed"
            >
              {isLoading === 'gitlab' ? (
                <div className="w-4 h-4 border-2 border-slate-300 border-t-slate-600 rounded-full animate-spin" />
              ) : (
                <svg className="w-5 h-5" viewBox="0 0 24 24">
                  <path d="M22.65 14.39L12 22.13 1.35 14.39a.84.84 0 01-.3-.94l1.22-3.78 2.44-7.51A.42.42 0 014.82 2a.43.43 0 01.58 0 .42.42 0 01.11.18l2.44 7.49h8.1l2.44-7.51A.42.42 0 0118.6 2a.43.43 0 01.58 0 .42.42 0 01.11.18l2.44 7.51L23 13.45a.84.84 0 01-.35.94z" fill="#E24329" />
                  <path d="M12 22.13L16.05 9.67H7.95z" fill="#FC6D26" />
                  <path d="M12 22.13L7.95 9.67H3.16z" fill="#FCA326" />
                  <path d="M3.16 9.67L1.35 14.39a.84.84 0 00.3.94L12 22.13z" fill="#E24329" />
                  <path d="M3.16 9.67h4.79L5.51 2.18a.42.42 0 00-.8 0z" fill="#FC6D26" />
                  <path d="M12 22.13l4.05-12.46h4.79z" fill="#FCA326" />
                  <path d="M20.84 9.67l1.81 4.72a.84.84 0 01-.3.94L12 22.13z" fill="#E24329" />
                  <path d="M20.84 9.67h-4.79l2.44-7.49a.42.42 0 01.8 0z" fill="#FC6D26" />
                </svg>
              )}
              <span>Continue with GitLab</span>
            </button>
          </div>

          <div className="mt-8 text-center pt-6 border-t border-slate-50 dark:border-slate-800">
            <p className="text-xs text-slate-500 dark:text-slate-400">
              New to Lucid AI? Sign in with any provider to create your account automatically.
            </p>
          </div>
        </div>

        {/* Bottom Footer Links */}
        <div className="mt-8 flex gap-8 text-[11px] font-bold tracking-widest text-slate-400 dark:text-slate-500 uppercase">
          <Link href="#" className="hover:text-slate-600 dark:hover:text-slate-300 transition-colors">Privacy</Link>
          <Link href="#" className="hover:text-slate-600 dark:hover:text-slate-300 transition-colors">Terms</Link>
          <Link href="#" className="hover:text-slate-600 dark:hover:text-slate-300 transition-colors">Support</Link>
        </div>
      </div>
    </div>
  );
}
