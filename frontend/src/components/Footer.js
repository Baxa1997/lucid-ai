import Link from 'next/link';

/* Only links to real, shipping destinations.
   Sized + colored for readability — black text on white, larger than before. */

const footerColumns = [
  {
    title: 'Product',
    links: [
      { label: 'Features',     href: '/#features'     },
      { label: 'How it works', href: '/#how-it-works' },
      { label: 'Use cases',    href: '/#use-cases'    },
      { label: 'Templates',    href: '/#templates'    },
    ],
  },
  {
    title: 'Company',
    links: [
      { label: 'Pricing',  href: '/pricing' },
      { label: 'Docs',     href: '/docs'    },
      { label: 'Login',    href: '/login'   },
    ],
  },
];

export default function Footer() {
  return (
    <footer className="border-t border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-950 transition-colors">
      <div className="max-w-[1320px] mx-auto px-6 sm:px-10 pt-20 pb-10">
        {/* Top split — brand + columns */}
        <div className="grid lg:grid-cols-[1.4fr_1fr_1fr_auto] gap-12 lg:gap-16 mb-16">
          {/* Brand */}
          <div className="max-w-md">
            <Link href="/" className="inline-flex items-center gap-3 mb-5 no-underline">
              <span
                aria-hidden
                className="grid h-[42px] w-[42px] place-items-center rounded-[10px]"
                style={{
                  background:
                    "radial-gradient(120% 120% at 25% 18%, #FF8456 0%, #E85A2C 55%, #C8451B 100%)",
                  boxShadow:
                    "0 1px 0 rgba(255,255,255,.45) inset, 0 4px 12px -4px rgba(232,90,44,.55)",
                }}>
                <svg width="20" height="20" viewBox="0 0 16 16" fill="none">
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
                className="text-[22px] font-bold text-slate-900 dark:text-white"
                style={{ letterSpacing: '-0.025em' }}>
                Lucid AI
              </span>
            </Link>
            <p className="text-[16px] leading-relaxed font-medium text-slate-700 dark:text-slate-300 mb-6">
              Chat-first AI engineer. Connect your repo, describe a task — get a branch and a PR.
            </p>
            <Link
              href="/login"
              className="inline-flex items-center gap-2 rounded-full bg-[#15243F] px-5 py-2.5 text-[14px] font-semibold text-white shadow-[0_4px_12px_-4px_rgba(13,27,46,.45)] transition-[background] hover:bg-[#1E3457]">
              Start building
              <svg width="13" height="13" viewBox="0 0 14 14" fill="none" aria-hidden>
                <path
                  d="M3 7h7m0 0L7 4m3 3l-3 3"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </Link>
          </div>

          {/* Link columns */}
          {footerColumns.map((col) => (
            <div key={col.title}>
              <h4 className="text-[12px] font-bold text-slate-900 dark:text-slate-100 uppercase tracking-[0.14em] mb-5">
                {col.title}
              </h4>
              <ul className="space-y-3.5">
                {col.links.map((link) => (
                  <li key={link.label}>
                    <Link
                      href={link.href}
                      className="text-[16px] font-medium text-slate-700 dark:text-slate-300 hover:text-slate-900 dark:hover:text-white transition-colors">
                      {link.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          ))}

          {/* Status pill */}
          <div className="hidden lg:block">
            <h4 className="text-[12px] font-bold text-slate-900 dark:text-slate-100 uppercase tracking-[0.14em] mb-5">
              Status
            </h4>
            <div className="inline-flex items-center gap-2.5 rounded-full border border-emerald-200 dark:border-emerald-500/30 bg-emerald-50 dark:bg-emerald-500/[0.08] px-4 py-2">
              <span className="relative flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-60" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
              </span>
              <span className="text-[13px] font-semibold text-emerald-700 dark:text-emerald-300">
                All systems normal
              </span>
            </div>
          </div>
        </div>

        {/* Bottom bar */}
        <div className="border-t border-slate-200 dark:border-slate-800 pt-6 flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
          <p className="text-[14px] font-medium text-slate-700 dark:text-slate-300">
            © {new Date().getFullYear()} Lucid AI Inc.
          </p>
          <p className="text-[13px] font-medium text-slate-500 dark:text-slate-400">
            Built with chat-first agents. Sandboxed by default.
          </p>
        </div>
      </div>
    </footer>
  );
}
