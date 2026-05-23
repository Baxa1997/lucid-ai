'use client';

import { useState, useEffect, useRef } from 'react';
import {
  Box, Menu, X, ChevronDown,
  Zap, LayoutGrid, Rocket, GitBranch, Globe,
  PanelTop, ShoppingCart, Building2, Code2,
  FileText, Map, RefreshCw, Users, HelpCircle,
  Sun, Moon, Monitor
} from 'lucide-react';
import Link from 'next/link';
import { cn } from '@/lib/utils';
import ThemeModeSelector from '@/components/ThemeModeSelector';
import { useTheme } from '@/context/ThemeContext';
import { getSupabaseBrowserClient } from '@/lib/supabase/client';

const THEME_CYCLE = ['light', 'dark', 'system'];
const THEME_ICON = { light: Sun, dark: Moon, system: Monitor };

/* ── Dropdown Data ── */
const productItems = [
  { icon: Zap,        title: 'AI Code Generator',     desc: 'Generate production-ready apps from a text prompt.',          href: '#' },
  { icon: LayoutGrid,  title: 'Template Marketplace',   desc: 'Explore and customize ready-made starter templates.',         href: '#' },
  { icon: Rocket,      title: 'CI/CD Automation',       desc: 'Auto-deploy to GitHub, GitLab with pipelines built in.',      href: '#' },
  { icon: Globe,       title: 'Custom Domains',         desc: 'Connect GoDaddy or any domain to your deployed app.',         href: '#' },
];

const useCaseItems = [
  { icon: PanelTop,     title: 'Admin Panels',     desc: 'Full CRUD dashboards with role-based access.',               href: '#' },
  { icon: Code2,        title: 'Landing Pages',    desc: 'Beautiful marketing sites with animations.',                 href: '#' },
  { icon: Building2,    title: 'SaaS Applications', desc: 'Multi-tenant apps with auth, billing & APIs.',              href: '#' },
  { icon: ShoppingCart,  title: 'E-commerce',       desc: 'Storefronts with cart, checkout & inventory.',               href: '#' },
];

const resourceItems = [
  { icon: FileText,   title: 'Documentation',  desc: 'Guides, API reference and tutorials.',                        href: '/docs' },
  { icon: Map,         title: 'Roadmap',         desc: 'See what\'s coming next for Lucid AI.',                       href: '#' },
  { icon: RefreshCw,   title: 'Changelog',       desc: 'Track every update and improvement.',                         href: '#' },
  { icon: Users,       title: 'Community',       desc: 'Join our Discord and share feedback.',                        href: '#' },
  { icon: HelpCircle,  title: 'Support',         desc: 'Get help from the Lucid AI team.',                            href: '#' },
];

/* ── Mega-menu Dropdown Panel ── */
function DropdownPanel({ items, columns = 2 }) {
  return (
    <div className={cn(
      "grid gap-1 p-2",
      columns === 2 ? "grid-cols-2 w-[520px]" : "grid-cols-2 w-[520px]"
    )}>
      {items.map((item) => {
        const Icon = item.icon;
        return (
          <Link
            key={item.title}
            href={item.href}
            className="flex items-start gap-3 px-3 py-3 rounded-lg hover:bg-slate-50 dark:hover:bg-white/[0.04] transition-colors group"
          >
            <div className="w-9 h-9 rounded-lg bg-slate-100 dark:bg-white/[0.06] flex items-center justify-center shrink-0 group-hover:bg-orange-50 dark:group-hover:bg-orange-500/10 transition-colors">
              <Icon className="w-4 h-4 text-slate-500 dark:text-slate-400 group-hover:text-[#dc5426] dark:group-hover:text-orange-400 transition-colors" strokeWidth={1.75} />
            </div>
            <div className="min-w-0">
              <p className="text-[13px] font-semibold text-slate-800 dark:text-slate-100 leading-tight">{item.title}</p>
              <p className="text-[12px] text-slate-400 dark:text-slate-500 leading-snug mt-0.5">{item.desc}</p>
            </div>
          </Link>
        );
      })}
    </div>
  );
}

/* ── NavDropdown trigger + panel ── */
function NavDropdown({ label, items, columns, number }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const timeout = useRef(null);

  const enter = () => { clearTimeout(timeout.current); setOpen(true); };
  const leave = () => { timeout.current = setTimeout(() => setOpen(false), 30); };

  // Close on outside click
  useEffect(() => {
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  return (
    <div ref={ref} className="relative" onMouseEnter={enter} onMouseLeave={leave}>
      <button
        onClick={() => setOpen(!open)}
        className={cn(
          "inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg px-[14px] py-2 text-[17px] font-medium transition-colors",
          open
            ? "bg-black/[0.06] text-[#15171C] dark:bg-white/[0.06] dark:text-white"
            : "text-[#2A2D34] hover:bg-black/[0.06] hover:text-[#15171C] dark:text-slate-200 dark:hover:bg-white/[0.06] dark:hover:text-white"
        )}
        style={{letterSpacing: "-0.005em"}}
      >
        {number && (
          <span
            className="font-normal text-[10.5px] text-[#8B909B]"
            style={{
              fontFamily: "var(--font-geist-mono), ui-monospace, monospace",
              letterSpacing: "0.06em",
            }}>
            {number}
          </span>
        )}
        {label}
        <ChevronDown className={cn("w-[11px] h-[11px] opacity-55 transition-transform duration-200", open && "rotate-180")} />
      </button>

      {/* Panel */}
      {open && (
        <div className="absolute top-full left-1/2 -translate-x-1/2 pt-3 z-50">
          <div className="bg-white dark:bg-[#1c2128] rounded-xl border border-slate-200/80 dark:border-slate-700/50 shadow-xl dark:shadow-black/40 overflow-hidden">
            <DropdownPanel items={items} columns={columns} />
          </div>
        </div>
      )}
    </div>
  );
}

export default function Navbar() {
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const [isScrolled, setIsScrolled] = useState(false);
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const { theme, setTheme } = useTheme();
  const ThemeIcon = THEME_ICON[theme] || Sun;
  const cycleTheme = () => {
    const idx = THEME_CYCLE.indexOf(theme);
    setTheme(THEME_CYCLE[(idx + 1) % THEME_CYCLE.length]);
  };

  useEffect(() => {
    const handleScroll = () => setIsScrolled(window.scrollY > 20);
    window.addEventListener('scroll', handleScroll);
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

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
      {/* Base44-style island nav: always white pill, floating over gradient.
          Positioned by its fixed wrapper in page.js — header itself is in flow. */}
      <header
        className="relative z-40 px-3 sm:px-6 pt-1"
        style={{
          fontFamily:
            "var(--font-geist), ui-sans-serif, system-ui, -apple-system, sans-serif",
        }}>
        <div
          className="mx-auto grid max-w-[1320px] grid-cols-[1fr_auto_1fr] items-center gap-4 rounded-full border border-white/70 bg-white px-6 sm:px-8 py-2.5 shadow-none backdrop-blur-[16px] backdrop-saturate-150 dark:border-white/15 dark:bg-slate-900/95">
          {/* Brand stack */}
          <Link href="/" className="inline-flex items-center gap-3 no-underline">
            <span
              aria-hidden
              className="grid h-[38px] w-[38px] place-items-center rounded-[9px]"
              style={{
                background:
                  "radial-gradient(120% 120% at 25% 18%, #FF8456 0%, #E85A2C 55%, #C8451B 100%)",
                boxShadow:
                  "0 1px 0 rgba(255,255,255,.45) inset, 0 4px 12px -4px rgba(232,90,44,.55)",
              }}>
              <svg width="18" height="18" viewBox="0 0 16 16" fill="none">
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
              className="text-[20px] font-bold text-[#15171C] dark:text-white"
              style={{letterSpacing: "-0.025em"}}>
              Lucid AI
            </span>
          </Link>

          {/* Center nav links — plain text, Base44-style */}
          <nav
            className="hidden md:inline-flex items-center justify-center gap-1"
            aria-label="Primary">
            <NavDropdown label="Product"   items={productItems}  columns={2} />
            <NavDropdown label="Use Cases" items={useCaseItems}  columns={2} />
            <NavDropdown label="Resources" items={resourceItems} columns={2} />
            <Link
              href="/pricing"
              className="inline-flex items-center whitespace-nowrap rounded-lg px-[14px] py-2 text-[17px] font-medium text-[#2A2D34] no-underline transition-colors hover:bg-black/[0.06] hover:text-[#15171C] dark:text-slate-200 dark:hover:bg-white/[0.06] dark:hover:text-white"
              style={{letterSpacing: "-0.005em"}}>
              Pricing
            </Link>
            <Link
              href="/pricing#enterprise"
              className="inline-flex items-center whitespace-nowrap rounded-lg px-[14px] py-2 text-[17px] font-medium text-[#2A2D34] no-underline transition-colors hover:bg-black/[0.06] hover:text-[#15171C] dark:text-slate-200 dark:hover:bg-white/[0.06] dark:hover:text-white"
              style={{letterSpacing: "-0.005em"}}>
              Enterprise
            </Link>
          </nav>

          {/* Right cluster — Base44 style: bare theme icon + lime-green CTA */}
          <div className="hidden md:flex items-center justify-end gap-3">
            <button
              type="button"
              onClick={cycleTheme}
              aria-label={`Theme: ${theme}. Click to switch.`}
              title={`Theme: ${theme}`}
              className="grid h-9 w-9 place-items-center rounded-full text-[#2A2D34] transition-colors hover:bg-black/[0.05] dark:text-slate-300 dark:hover:bg-white/[0.06]">
              <ThemeIcon className="w-[18px] h-[18px]" />
            </button>
            <Link
              href={isLoggedIn ? "/dashboard/engineer" : "/login"}
              className="inline-flex items-center rounded-full border border-[#0D1B2E] bg-[#15243F] px-[22px] py-[10px] text-[15px] font-semibold text-white no-underline shadow-[0_1px_0_rgba(255,255,255,.12)_inset,0_4px_12px_-4px_rgba(13,27,46,.45)] transition-[background,transform] hover:bg-[#1E3457] active:translate-y-px"
              style={{letterSpacing: "-0.005em"}}>
              {isLoggedIn ? "Go to Dashboard" : "Start Building"}
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

      {/* Mobile Menu */}
      {isMenuOpen && (
        <div className="md:hidden fixed inset-0 top-[60px] bg-white dark:bg-slate-900 z-40 overflow-y-auto p-6">
          <div className="space-y-6">
            {/* Product */}
            <div>
              <p className="text-[11px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider mb-2">Product</p>
              <div className="space-y-1">
                {productItems.map(item => (
                  <Link key={item.title} href={item.href} className="flex items-center gap-3 px-3 py-2.5 rounded-lg hover:bg-slate-50 dark:hover:bg-white/[0.04]">
                    <item.icon className="w-4 h-4 text-slate-400" />
                    <span className="text-sm font-medium text-slate-700 dark:text-slate-200">{item.title}</span>
                  </Link>
                ))}
              </div>
            </div>
            {/* Use Cases */}
            <div>
              <p className="text-[11px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider mb-2">Use Cases</p>
              <div className="space-y-1">
                {useCaseItems.map(item => (
                  <Link key={item.title} href={item.href} className="flex items-center gap-3 px-3 py-2.5 rounded-lg hover:bg-slate-50 dark:hover:bg-white/[0.04]">
                    <item.icon className="w-4 h-4 text-slate-400" />
                    <span className="text-sm font-medium text-slate-700 dark:text-slate-200">{item.title}</span>
                  </Link>
                ))}
              </div>
            </div>
            {/* Resources */}
            <div>
              <p className="text-[11px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider mb-2">Resources</p>
              <div className="space-y-1">
                {resourceItems.map(item => (
                  <Link key={item.title} href={item.href} className="flex items-center gap-3 px-3 py-2.5 rounded-lg hover:bg-slate-50 dark:hover:bg-white/[0.04]">
                    <item.icon className="w-4 h-4 text-slate-400" />
                    <span className="text-sm font-medium text-slate-700 dark:text-slate-200">{item.title}</span>
                  </Link>
                ))}
              </div>
            </div>
            <Link href="/pricing" className="block text-sm font-semibold text-slate-700 dark:text-slate-200 px-3 py-2.5">Pricing</Link>
            <div className="pt-4 border-t border-slate-100 dark:border-slate-800 space-y-3">
              <div className="flex items-center justify-between px-3">
                <span className="text-sm text-slate-500">Theme</span>
                <ThemeModeSelector />
              </div>
              {isLoggedIn ? (
                <Link href="/dashboard/engineer" className="block bg-gradient-to-r from-[#dc5426] to-orange-500 text-white font-semibold px-4 py-3 rounded-xl text-center text-sm">Go to Dashboard</Link>
              ) : (
                <>
                  <Link href="/login" className="block text-sm font-medium text-slate-600 dark:text-slate-300 px-3 py-2">Login</Link>
                  <Link href="/login" className="block bg-gradient-to-r from-[#dc5426] to-orange-500 text-white font-semibold px-4 py-3 rounded-xl text-center text-sm">Start Building</Link>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
