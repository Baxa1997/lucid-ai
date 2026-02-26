'use client';

import { useState, useEffect } from 'react';
import { Box, Menu, X } from 'lucide-react';
import Link from 'next/link';
import { cn } from '@/lib/utils';
import ThemeModeSelector from '@/components/ThemeModeSelector';

export default function Navbar() {
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const [isScrolled, setIsScrolled] = useState(false);

  useEffect(() => {
    const handleScroll = () => {
      setIsScrolled(window.scrollY > 20);
    };
    window.addEventListener('scroll', handleScroll);
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  return (
    <>
      <div className={cn("fixed top-0 left-0 right-0 z-50 flex justify-center transition-all duration-700 ease-[cubic-bezier(0.25,0.1,0.25,1.0)]", isScrolled ? "pt-4" : "pt-0")}>
        <header className={cn(
            "flex items-center justify-between transition-all duration-700 ease-[cubic-bezier(0.25,0.1,0.25,1.0)] px-6 md:px-8",
            isScrolled 
               ? "w-full max-w-[95%] bg-white/70 dark:bg-slate-900/70 backdrop-blur-xl border border-slate-200/50 dark:border-slate-700/50 shadow-lg shadow-slate-200/20 dark:shadow-black/20 rounded-full py-3" 
               : "w-full bg-white/80 dark:bg-slate-950/80 backdrop-blur-md border-b border-transparent py-4"
        )}>
         {/* Logo Section */}
         <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 bg-gradient-to-br from-violet-600 to-indigo-600 rounded-lg flex items-center justify-center text-white shadow-md shadow-violet-200">
               <Box className="w-5 h-5 stroke-[2.5]" />
            </div>
            <span className="text-xl font-bold tracking-tight text-slate-900 dark:text-white">Lucid AI</span>
         </div>

         {/* Desktop Nav */}
         <nav className="hidden md:flex items-center gap-8 text-[15px] font-medium text-slate-500 dark:text-slate-400">
            <a href="#" className="hover:text-violet-600 dark:hover:text-violet-400 transition-colors">Features</a>
            <Link href="/docs" className="hover:text-violet-600 dark:hover:text-violet-400 transition-colors">Documentation</Link>
            <a href="#" className="hover:text-violet-600 dark:hover:text-violet-400 transition-colors">Software Engineer</a>
            <a href="#" className="hover:text-violet-600 dark:hover:text-violet-400 transition-colors">Integrations</a>
         </nav>

         {/* Right Actions */}
         <div className="hidden md:flex items-center gap-4">
            <ThemeModeSelector />
            <Link href="/login" className="text-[15px] font-bold text-slate-600 dark:text-slate-300 hover:text-slate-900 dark:hover:text-white transition-colors">Login</Link>
            <Link href="/login" className="bg-gradient-to-r from-violet-600 to-indigo-600 text-white text-[15px] font-semibold px-5 py-2.5 rounded-lg hover:shadow-lg hover:shadow-violet-500/30 transition-all duration-200 transform hover:-translate-y-0.5 flex items-center justify-center">
               Get Started
            </Link>
         </div>

         {/* Mobile Menu Toggle */}
         <button className="md:hidden text-slate-600 dark:text-slate-300" onClick={() => setIsMenuOpen(!isMenuOpen)}>
            {isMenuOpen ? <X /> : <Menu />}
         </button>
        </header>
      </div>

      {/* Mobile Menu */}
      {isMenuOpen && (
         <div className="md:hidden bg-white dark:bg-slate-900 border-b border-slate-100 dark:border-slate-800 p-4 flex flex-col gap-4 shadow-xl absolute w-full z-40 top-[22px]">
            <a href="#" className="text-slate-600 dark:text-slate-300 font-medium">Features</a>
            <Link href="/docs" className="text-slate-600 dark:text-slate-300 font-medium">Documentation</Link>
            <a href="#" className="text-slate-600 dark:text-slate-300 font-medium">Software Engineer</a>
            <a href="#" className="text-slate-600 dark:text-slate-300 font-medium">Integrations</a>
            <div className="h-px bg-slate-100 dark:bg-slate-800 w-full my-1"></div>
            <div className="flex items-center justify-between">
              <span className="text-slate-500 dark:text-slate-400 text-sm font-medium">Theme</span>
              <ThemeModeSelector />
            </div>
            <Link href="/login" className="text-slate-600 dark:text-slate-300 font-medium">Login</Link>
            <Link href="/login" className="bg-violet-600 text-white font-medium px-4 py-2 rounded-lg w-full text-center block">Get Started</Link>
         </div>
      )}
    </>
  );
}
