'use client';

import { useState, useEffect, useRef } from 'react';
import {
  Box, Menu, X, ChevronDown,
  Zap, LayoutGrid, Rocket, GitBranch, Globe,
  PanelTop, ShoppingCart, Building2, Code2,
  FileText, Map, RefreshCw, Users, HelpCircle
} from 'lucide-react';
import Link from 'next/link';
import { cn } from '@/lib/utils';
import ThemeModeSelector from '@/components/ThemeModeSelector';
import { getSupabaseBrowserClient } from '@/lib/supabase/client';

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
            <div className="w-9 h-9 rounded-lg bg-slate-100 dark:bg-white/[0.06] flex items-center justify-center shrink-0 group-hover:bg-emerald-50 dark:group-hover:bg-emerald-500/10 transition-colors">
              <Icon className="w-4 h-4 text-slate-500 dark:text-slate-400 group-hover:text-emerald-600 dark:group-hover:text-emerald-400 transition-colors" strokeWidth={1.75} />
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
function NavDropdown({ label, items, columns }) {
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
          "flex items-center gap-1 text-[15px] font-medium transition-colors",
          open
            ? "text-slate-900 dark:text-white"
            : "text-slate-500 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white"
        )}
      >
        {label}
        <ChevronDown className={cn("w-3.5 h-3.5 transition-transform duration-200", open && "rotate-180")} />
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
      <div className={cn("fixed top-0 left-0 right-0 z-50 flex justify-center transition-all duration-700 ease-[cubic-bezier(0.25,0.1,0.25,1.0)]", isScrolled ? "pt-4" : "pt-0")}>
        <header className={cn(
          "flex items-center justify-between transition-all duration-700 ease-[cubic-bezier(0.25,0.1,0.25,1.0)] px-6 md:px-8",
          isScrolled 
            ? "w-full max-w-[95%] bg-white/70 dark:bg-slate-900/70 backdrop-blur-xl border border-slate-200/50 dark:border-slate-700/50 shadow-lg shadow-slate-200/20 dark:shadow-black/20 rounded-full py-3" 
            : "w-full bg-white/90 dark:bg-slate-950/90 backdrop-blur-md border-b border-slate-100 dark:border-slate-800/50 py-4"
        )}>
          {/* Logo */}
          <Link href="/" className="flex items-center gap-2.5">
            <div className="w-8 h-8 bg-gradient-to-br from-emerald-500 to-teal-600 rounded-lg flex items-center justify-center text-white shadow-md shadow-emerald-200 dark:shadow-emerald-900/30">
              <Box className="w-5 h-5 stroke-[2.5]" />
            </div>
            <span className="text-xl font-bold tracking-tight text-slate-900 dark:text-white">Lucid AI</span>
          </Link>

          {/* Desktop Nav */}
          <nav className="hidden md:flex items-center gap-7">
            <NavDropdown label="Product"   items={productItems}  columns={2} />
            <NavDropdown label="Use Cases" items={useCaseItems}  columns={2} />
            <NavDropdown label="Resources" items={resourceItems} columns={2} />
            <Link href="/pricing" className="text-[15px] font-medium text-slate-500 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white transition-colors">
              Pricing
            </Link>
          </nav>

          {/* Right Actions */}
          <div className="hidden md:flex items-center gap-4">
            <ThemeModeSelector />
            {isLoggedIn ? (
              <Link href="/dashboard/engineer" className="bg-gradient-to-r from-emerald-500 to-teal-600 text-white text-[15px] font-semibold px-5 py-2.5 rounded-lg hover:shadow-lg hover:shadow-emerald-500/30 transition-all duration-200 transform hover:-translate-y-0.5">
                Go to Dashboard
              </Link>
            ) : (
              <>
                <Link href="/login" className="text-[15px] font-bold text-slate-600 dark:text-slate-300 hover:text-slate-900 dark:hover:text-white transition-colors">Login</Link>
                <Link href="/login" className="bg-gradient-to-r from-emerald-500 to-teal-600 text-white text-[15px] font-semibold px-5 py-2.5 rounded-lg hover:shadow-lg hover:shadow-emerald-500/30 transition-all duration-200 transform hover:-translate-y-0.5">
                  Start Building
                </Link>
              </>
            )}
          </div>

          {/* Mobile Toggle */}
          <button className="md:hidden text-slate-600 dark:text-slate-300" onClick={() => setIsMenuOpen(!isMenuOpen)}>
            {isMenuOpen ? <X /> : <Menu />}
          </button>
        </header>
      </div>

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
                <Link href="/dashboard/engineer" className="block bg-gradient-to-r from-emerald-500 to-teal-600 text-white font-semibold px-4 py-3 rounded-xl text-center text-sm">Go to Dashboard</Link>
              ) : (
                <>
                  <Link href="/login" className="block text-sm font-medium text-slate-600 dark:text-slate-300 px-3 py-2">Login</Link>
                  <Link href="/login" className="block bg-gradient-to-r from-emerald-500 to-teal-600 text-white font-semibold px-4 py-3 rounded-xl text-center text-sm">Start Building</Link>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
