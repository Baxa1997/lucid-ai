import { Inter } from 'next/font/google';
import DocsHeader from '@/components/docs/DocsHeader';
import DocsSidebar from '@/components/docs/DocsSidebar';
import DocsTOC from '@/components/docs/DocsTOC';

const inter = Inter({ subsets: ['latin'] });

export const metadata = {
  title: 'Documentation - Lucid AI',
  description: 'Learn how to build with Lucid AI',
};

export default function DocsLayout({ children }) {
  return (
    <div className={`${inter.className} min-h-screen bg-white dark:bg-slate-950 text-slate-900 dark:text-slate-100 transition-colors duration-200`}>
      <DocsHeader />
      {/* Main 3-column grid — matches Devin proportions */}
      <div className="flex w-full mx-auto" style={{ paddingTop: '108px' }}>
        {/* Left sidebar */}
        <aside className="hidden lg:block w-[240px] flex-shrink-0 border-r border-slate-100 dark:border-slate-800">
          <div className="sticky top-[108px] h-[calc(100vh-108px)] overflow-y-auto px-5 py-6 scrollbar-hide">
            <DocsSidebar />
          </div>
        </aside>

        {/* Center content */}
        <main className="flex-1 min-w-0 px-10 xl:px-16 py-8 pb-24">
          <div className="max-w-[720px] docs-content">
            {children}
          </div>
        </main>

        {/* Right TOC sidebar */}
        <aside className="hidden xl:block w-[220px] flex-shrink-0 border-l border-slate-100 dark:border-slate-800">
          <div className="sticky top-[108px] h-[calc(100vh-108px)] overflow-y-auto px-5 py-6 scrollbar-hide">
            <DocsTOC />
          </div>
        </aside>
      </div>
    </div>
  );
}
