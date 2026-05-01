import './globals.css';
import { Inter } from 'next/font/google';
import { Analytics } from '@vercel/analytics/next';
import { ThemeProvider } from '@/context/ThemeContext';

const inter = Inter({ subsets: ['latin'] });

export const metadata = {
  title: 'Lucid AI - AI Developer Platform',
  description: 'Build faster with autonomous AI agents',
};

// Inline script to prevent flash of wrong theme on load
const themeScript = `
(function() {
  try {
    var stored = localStorage.getItem('lucid-docs-theme') || 'system';
    var resolved = stored;
    if (stored === 'system') {
      resolved = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }
    if (resolved === 'dark') {
      document.documentElement.classList.add('dark');
    } else {
      document.documentElement.classList.remove('dark');
    }
    document.documentElement.style.colorScheme = resolved;
  } catch(e) {}
})();
`;

// Inline script to prevent the engineer-layout sidebar from flashing
// expanded→collapsed on refresh. SSR can't know the user's stored
// preference, so it always renders the expanded width (260px) and the
// browser re-snaps to 60px after hydration — visible as a "gap." This
// runs before paint, sets a CSS variable + data attribute, and the
// sidebar reads its width from the variable.
const sidebarScript = `
(function() {
  try {
    var collapsed = localStorage.getItem('lucid-sidebar-collapsed') === 'true';
    document.documentElement.style.setProperty('--lucid-sidebar-w', collapsed ? '60px' : '260px');
    document.documentElement.dataset.sidebarCollapsed = collapsed ? 'true' : 'false';
  } catch(e) {
    document.documentElement.style.setProperty('--lucid-sidebar-w', '260px');
  }
})();
`;

export default function RootLayout({ children }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
        <script dangerouslySetInnerHTML={{ __html: sidebarScript }} />
      </head>
      <body className={inter.className} suppressHydrationWarning>
        <ThemeProvider>
          {children}
        </ThemeProvider>
        <Analytics />
      </body>
    </html>
  );
}
