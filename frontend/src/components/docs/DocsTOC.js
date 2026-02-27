'use client';

import { useEffect, useState, useCallback } from 'react';
import { usePathname } from 'next/navigation';
import { AlignLeft } from 'lucide-react';
import { cn } from '@/lib/utils';

export default function DocsTOC() {
  const [activeId, setActiveId] = useState('');
  const [tocItems, setTocItems] = useState([]);
  const pathname = usePathname();

  // Scan headings from the page content on mount / route change
  useEffect(() => {
    const timer = setTimeout(() => {
      const content = document.querySelector('.docs-content');
      if (!content) return;

      const headings = content.querySelectorAll('h2[id], h3[id]');
      const items = Array.from(headings).map((heading) => ({
        id: heading.id,
        label: heading.textContent?.trim() || '',
        level: heading.tagName === 'H3' ? 3 : 2,
      }));
      setTocItems(items);
    }, 100);
    return () => clearTimeout(timer);
  }, [pathname]);

  // Observe headings for active state
  useEffect(() => {
    if (tocItems.length === 0) return;

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            setActiveId(entry.target.id);
          }
        });
      },
      { rootMargin: '-10% 0% -80% 0%' }
    );

    tocItems.forEach((item) => {
      const element = document.getElementById(item.id);
      if (element) observer.observe(element);
    });

    return () => observer.disconnect();
  }, [tocItems]);

  if (tocItems.length === 0) return null;

  return (
    <div>
      <div className="flex items-center gap-2 mb-4">
        <AlignLeft className="w-3.5 h-3.5 text-slate-400 dark:text-slate-500" />
        <span className="text-[12px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wide">On this page</span>
      </div>
      <ul className="space-y-2">
        {tocItems.map((item) => (
          <li key={item.id}>
            <a
              href={`#${item.id}`}
              style={{ paddingLeft: item.level === 3 ? '16px' : '0' }}
              className={cn(
                "text-[13px] font-medium transition-colors hover:text-violet-600 dark:hover:text-violet-400 block leading-snug",
                activeId === item.id 
                  ? "text-violet-600 dark:text-violet-400 font-semibold" 
                  : "text-slate-500 dark:text-slate-400"
              )}
              onClick={(e) => {
                e.preventDefault();
                document.getElementById(item.id)?.scrollIntoView({ behavior: 'smooth' });
              }}
            >
              {item.label}
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}
