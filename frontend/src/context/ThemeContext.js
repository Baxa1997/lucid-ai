'use client';

import { createContext, useContext, useEffect, useState, useCallback } from 'react';

const ThemeContext = createContext({
  theme: 'system',      // 'light' | 'dark' | 'system'
  resolvedTheme: 'light', // 'light' | 'dark' — what's actually applied
  setTheme: () => {},
});

export function useTheme() {
  return useContext(ThemeContext);
}

export function ThemeProvider({ children }) {
  const [theme, setThemeState] = useState('system');
  const [resolvedTheme, setResolvedTheme] = useState('light');
  const [mounted, setMounted] = useState(false);

  // Get system preference
  const getSystemTheme = useCallback(() => {
    if (typeof window === 'undefined') return 'light';
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }, []);

  // Apply theme to DOM
  const applyTheme = useCallback((resolved) => {
    const html = document.documentElement;
    if (resolved === 'dark') {
      html.classList.add('dark');
    } else {
      html.classList.remove('dark');
    }
    // Set a color-scheme so browser UI elements match
    html.style.colorScheme = resolved;
    setResolvedTheme(resolved);
  }, []);

  // Set theme and persist
  const setTheme = useCallback((newTheme) => {
    setThemeState(newTheme);
    localStorage.setItem('lucid-docs-theme', newTheme);
    const resolved = newTheme === 'system' ? getSystemTheme() : newTheme;
    applyTheme(resolved);
  }, [getSystemTheme, applyTheme]);

  // Initialize on mount
  useEffect(() => {
    const stored = localStorage.getItem('lucid-docs-theme') || 'system';
    setThemeState(stored);
    const resolved = stored === 'system' ? getSystemTheme() : stored;
    applyTheme(resolved);
    setMounted(true);
  }, [getSystemTheme, applyTheme]);

  // Listen for system theme changes when in 'system' mode
  useEffect(() => {
    if (!mounted) return;

    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
    const handler = (e) => {
      if (theme === 'system') {
        applyTheme(e.matches ? 'dark' : 'light');
      }
    };

    mediaQuery.addEventListener('change', handler);
    return () => mediaQuery.removeEventListener('change', handler);
  }, [theme, mounted, applyTheme]);

  // Prevent flash — render nothing until mounted
  // The inline script in layout.js handles the initial class application
  const value = { theme, resolvedTheme, setTheme };

  return (
    <ThemeContext.Provider value={value}>
      {children}
    </ThemeContext.Provider>
  );
}
