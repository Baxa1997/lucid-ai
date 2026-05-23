'use client';

import { useState, useEffect } from 'react';
import { Menu, X, Sun, Moon, Monitor } from 'lucide-react';
import Link from 'next/link';
import { useTheme } from '@/context/ThemeContext';
import { getSupabaseBrowserClient } from '@/lib/supabase/client';

const THEME_CYCLE = ['light', 'dark', 'system'];
const THEME_ICON = { light: Sun, dark: Moon, system: Monitor };

/* Flat anchor links — no mega-menus. Everything routes to a real on-page
   section anchor or a real route. Mobile menu mirrors these. */
const NAV_LINKS = [
  { label: 'Features',     href: '/#features'      },
  { label: 'How it works', href: '/#how-it-works'  },
  { label: 'Use cases',    href: '/#use-cases'     },
  { label: 'Templates',    href: '/#templates'     },
  { label: 'Pricing',      href: '/pricing'        },
  { label: 'Docs',         href: '/docs'           },
];

export default function Navbar() {
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const { theme, setTheme } = useTheme();
  const ThemeIcon = THEME_ICON[theme] || Sun;
  const cycleTheme = () => {
    const idx = THEME_CYCLE.indexOf(theme);
    setTheme(THEME_CYCLE[(idx + 1) % THEME_CYCLE.length]);
  };

  useEffect(() => {
    const supabase = getSupabaseBrowserClient();
    supabase.auth.getSession().then(({ data: { session } }) => {
      setIsLoggedIn(!!session);
    });
    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, session) => {
      setIsLoggedIn(!!session);
    });
    return () => subscription.unsubscribe();
  }, []);

  return (
    <>
      <header
        className="relative z-40 px-3 sm:px-6 pt-1"
        style={{
          fontFamily:
            "var(--font-geist), ui-sans-serif, system-ui, -apple-system, sans-serif",
        }}>
        <div className="mx-auto grid max-w-[1320px] grid-cols-[1fr_auto_1fr] items-center gap-4 rounded-full border border-white/70 bg-white px-5 sm:px-7 py-2 shadow-none backdrop-blur-[16px] backdrop-saturate-150 dark:border-white/15 dark:bg-slate-900/95">
          {/* Brand */}
          <Link href="/" className="inline-flex items-center gap-2.5 no-underline">
            <span
              aria-hidden
              className="grid h-[34px] w-[34px] place-items-center rounded-[9px]"
              style={{
                background:
                  "radial-gradient(120% 120% at 25% 18%, #FF8456 0%, #E85A2C 55%, #C8451B 100%)",
                boxShadow:
                  "0 1px 0 rgba(255,255,255,.45) inset, 0 4px 12px -4px rgba(232,90,44,.55)",
              }}>
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                <path
                  d="M3 4.5L8 2l5 2.5v7L8 14 3 11.5v-7z"
                  stroke="rgba(255,255,255,.95)"
                  strokeWidth="1.3"
                  strokeLinejoin="round"
                />
                <path
                  d="M3 4.5L8 7l5-2.5M8 7v7"
                  stroke="rgba(255,255,255,.95)"
                  strokeWidth="1.3"
                  strokeLinejoin="round"
                />
              </svg>
            </span>
            <span
              className="text-[18px] font-bold text-[#15171C] dark:text-white"
              style={{letterSpacing: "-0.025em"}}>
              Lucid AI
            </span>
          </Link>

          {/* Center — flat anchor links */}
          <nav
            className="hidden md:inline-flex items-center justify-center gap-0.5"
            aria-label="Primary">
            {NAV_LINKS.map((l) => (
              <Link
                key={l.label}
                href={l.href}
                className="inline-flex items-center whitespace-nowrap rounded-lg px-[12px] py-2 text-[15px] font-medium text-[#2A2D34] no-underline transition-colors hover:bg-black/[0.06] hover:text-[#15171C] dark:text-slate-200 dark:hover:bg-white/[0.06] dark:hover:text-white"
                style={{letterSpacing: "-0.005em"}}>
                {l.label}
              </Link>
            ))}
          </nav>

          {/* Right cluster */}
          <div className="hidden md:flex items-center justify-end gap-2">
            <button
              type="button"
              onClick={cycleTheme}
              aria-label={`Theme: ${theme}. Click to switch.`}
              title={`Theme: ${theme}`}
              className="grid h-9 w-9 place-items-center rounded-full text-[#2A2D34] transition-colors hover:bg-black/[0.05] dark:text-slate-300 dark:hover:bg-white/[0.06]">
              <ThemeIcon className="w-[18px] h-[18px]" />
            </button>
            <Link
              href={isLoggedIn ? "/dashboard" : "/login"}
              className="inline-flex items-center rounded-full border border-[#0D1B2E] bg-[#15243F] px-[18px] py-[8px] text-[14px] font-semibold text-white no-underline shadow-[0_1px_0_rgba(255,255,255,.12)_inset,0_4px_12px_-4px_rgba(13,27,46,.45)] transition-[background,transform] hover:bg-[#1E3457] active:translate-y-px"
              style={{letterSpacing: "-0.005em"}}>
              {isLoggedIn ? "Dashboard" : "Start building"}
            </Link>
          </div>

          {/* Mobile toggle */}
          <button
            className="md:hidden col-start-2 justify-self-end text-[#2A2D34] dark:text-slate-200"
            onClick={() => setIsMenuOpen(!isMenuOpen)}
            aria-label={isMenuOpen ? "Close menu" : "Open menu"}>
            {isMenuOpen ? <X /> : <Menu />}
          </button>
        </div>
      </header>

      {/* Mobile menu */}
      {isMenuOpen && (
        <div className="md:hidden fixed inset-0 top-[60px] bg-white dark:bg-slate-900 z-40 overflow-y-auto p-6">
          <div className="space-y-2">
            {NAV_LINKS.map((l) => (
              <Link
                key={l.label}
                href={l.href}
                onClick={() => setIsMenuOpen(false)}
                className="block px-3 py-3 rounded-lg text-[16px] font-medium text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-white/[0.04]">
                {l.label}
              </Link>
            ))}
            <div className="pt-4 border-t border-slate-100 dark:border-slate-800 space-y-3">
              <div className="flex items-center justify-between px-3">
                <span className="text-sm text-slate-500">Theme</span>
                <button
                  type="button"
                  onClick={cycleTheme}
                  className="inline-flex items-center gap-2 rounded-full bg-slate-100 dark:bg-white/[0.06] px-3 py-1.5 text-[13px] font-medium text-slate-700 dark:text-slate-300">
                  <ThemeIcon className="w-4 h-4" />
                  {theme}
                </button>
              </div>
              <Link
                href={isLoggedIn ? '/dashboard' : '/login'}
                onClick={() => setIsMenuOpen(false)}
                className="block bg-[#15243F] text-white font-semibold px-4 py-3 rounded-xl text-center text-sm">
                {isLoggedIn ? 'Dashboard' : 'Start building'}
              </Link>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
