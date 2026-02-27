'use client';

import { Sun, Moon, Monitor } from 'lucide-react';
import { useTheme } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';

// Cycle order: light → dark → system → light …
const cycle = ['light', 'dark', 'system'];

const icons = {
  light: Sun,
  dark: Moon,
  system: Monitor,
};

export default function ThemeToggle() {
  const { theme, setTheme } = useTheme();

  const handleClick = () => {
    const currentIndex = cycle.indexOf(theme);
    const nextIndex = (currentIndex + 1) % cycle.length;
    setTheme(cycle[nextIndex]);
  };

  const Icon = icons[theme] || Sun;

  return (
    <button
      onClick={handleClick}
      aria-label={`Theme: ${theme}. Click to switch.`}
      className={cn(
        "p-1.5 rounded-lg transition-all duration-200",
        "text-slate-400 hover:text-slate-600 hover:bg-slate-50",
        "dark:text-slate-500 dark:hover:text-slate-300 dark:hover:bg-slate-800",
        "active:scale-90"
      )}
    >
      <Icon className="w-4 h-4" />
    </button>
  );
}
