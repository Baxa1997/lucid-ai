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

export default function ThemeModeSelector({ className = '' }) {
  const { theme, setTheme, resolvedTheme } = useTheme();

  const handleClick = () => {
    const currentIndex = cycle.indexOf(theme);
    const nextIndex = (currentIndex + 1) % cycle.length;
    setTheme(cycle[nextIndex]);
  };

  // Show the icon that represents the CURRENT setting
  const Icon = icons[theme] || Sun;

  return (
    <button
      onClick={handleClick}
      aria-label={`Theme: ${theme}. Click to switch.`}
      id="theme-mode-selector"
      className={cn(
        "p-2 rounded-xl transition-all duration-200",
        "bg-slate-100 hover:bg-slate-200 text-slate-600",
        "dark:bg-slate-800 dark:hover:bg-slate-700 dark:text-slate-300",
        "border border-slate-200 dark:border-slate-700",
        "active:scale-95",
        className
      )}
    >
      <Icon className="w-4 h-4" />
    </button>
  );
}
