'use client';

import Link from 'next/link';
import { Github, Zap, Eye, EyeOff } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import ThemeModeSelector from '@/components/ThemeModeSelector';
import { signIn } from 'next-auth/react';

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState('dev@lucid.ai');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');

  const handleGoogleLogin = async () => {
    setIsLoading(true);
    await signIn('google', { callbackUrl: '/dashboard/engineer' });
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setIsLoading(true);
    setError('');

    try {
      const res = await signIn('credentials', {
        email,
        password, // Ignored by mock provider
        redirect: false,
      });

      if (res?.error) {
        setError('Login failed. Please try again.');
        setIsLoading(false);
      } else {
         router.push('/dashboard/engineer');
      }
    } catch (err) {
      setError('An error occurred.');
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#f0f4f9] dark:bg-slate-950 flex flex-col items-center justify-center p-4 relative overflow-hidden transition-colors duration-200">

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
          <h1 className="text-2xl font-extrabold text-slate-900 dark:text-slate-100 tracking-tight">Welcome back</h1>
          <p className="text-slate-500 dark:text-slate-400 text-sm mt-1.5 font-medium">Log in to your Lucid AI workspace</p>
        </div>

        {/* Login Card */}
        <div className="w-full bg-white dark:bg-slate-900 rounded-2xl shadow-soft border border-slate-200 dark:border-slate-800 p-6 sm:p-8">

          {/* Social Buttons */}
          <div className="flex gap-3 mb-6">
            <button className="flex-1 flex items-center justify-center gap-2.5 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-700 hover:border-slate-300 dark:hover:border-slate-600 text-slate-700 dark:text-slate-300 font-bold py-2.5 rounded-xl transition-all text-xs group active:scale-[0.98]">
              <Github className="w-4 h-4 text-slate-900 dark:text-slate-100" />
              <span>GitHub</span>
            </button>
            <button 
              onClick={handleGoogleLogin}
              className="flex-1 flex items-center justify-center gap-2.5 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-700 hover:border-slate-300 dark:hover:border-slate-600 text-slate-700 dark:text-slate-300 font-bold py-2.5 rounded-xl transition-all text-xs group active:scale-[0.98]"
            >
              <svg className="w-4 h-4" viewBox="0 0 24 24">
                <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4" />
                <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853" />
                <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.26.81-.58z" fill="#FBBC05" />
                <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335" />
              </svg>
              <span>Google</span>
            </button>
          </div>

          <div className="relative mb-6">
            <div className="absolute inset-0 flex items-center">
              <div className="w-full border-t border-slate-100 dark:border-slate-800"></div>
            </div>
            <div className="relative flex justify-center text-[11px] uppercase tracking-wider font-bold">
              <span className="bg-white dark:bg-slate-900 px-3 text-slate-400 dark:text-slate-500">Or continue with Dev Account</span>
            </div>
          </div>

          {error && (
            <div className="mb-4 p-3 rounded-lg bg-red-50 dark:bg-red-500/10 border border-red-100 dark:border-red-500/20 text-red-600 dark:text-red-400 text-xs font-medium text-center">
              {error}
            </div>
          )}

          <form className="space-y-4" onSubmit={handleSubmit}>
            <div>
              <label htmlFor="email" className="block text-[11px] font-bold text-slate-500 dark:text-slate-400 uppercase tracking-wide mb-1.5 pl-0.5">Email Address (Dev)</label>
              <input
                type="text"
                id="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full px-4 py-3 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 placeholder:text-slate-400 dark:placeholder:text-slate-500 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 outline-none transition-all text-sm font-medium"
                placeholder="dev@lucid.ai"
              />
              <p className="text-[10px] text-slate-400 dark:text-slate-500 mt-1 pl-1">For dev, any email will create an account automatically.</p>
            </div>

            <div>
              <div className="flex items-center justify-between mb-1.5 pl-0.5">
                <label htmlFor="password" className="block text-[11px] font-bold text-slate-500 dark:text-slate-400 uppercase tracking-wide">Password (Optional)</label>
              </div>
              <div className="relative">
                <input
                  type={showPassword ? 'text' : 'password'}
                  id="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full px-4 py-3 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 placeholder:text-slate-400 dark:placeholder:text-slate-500 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 outline-none transition-all text-sm font-medium pr-10"
                  placeholder="Any password"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-3.5 top-1/2 -translate-y-1/2 text-slate-400 dark:text-slate-500 hover:text-slate-600 dark:hover:text-slate-300 transition-colors"
                >
                  {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
            </div>

            <button
              type="submit"
              disabled={isLoading}
              className="w-full mt-2 bg-gradient-to-r from-blue-600 to-blue-700 hover:from-blue-700 hover:to-blue-800 text-white font-bold py-3 rounded-xl shadow-lg shadow-blue-600/20 hover:shadow-blue-600/30 transition-all transform hover:-translate-y-0.5 active:translate-y-0 text-sm disabled:opacity-60 disabled:cursor-not-allowed disabled:transform-none flex items-center justify-center gap-2"
            >
              {isLoading ? (
                <>
                  <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  Signing in...
                </>
              ) : (
                'Continue to Workspace'
              )}
            </button>
          </form>

          <div className="mt-8 text-center pt-6 border-t border-slate-50 dark:border-slate-800">
            <p className="text-xs text-slate-500 dark:text-slate-400">
              New to Lucid AI? <Link href="#" className="font-bold text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 transition-colors">Create an account</Link>
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
