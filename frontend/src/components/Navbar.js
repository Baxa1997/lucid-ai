'use client';

import { useState, useEffect } from 'react';
import { Box, Menu, X } from 'lucide-react';
import Link from 'next/link';
import { cn } from '@/lib/utils';

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
               ? "w-full max-w-[95%] bg-white/70 backdrop-blur-xl border border-slate-200/50 shadow-lg shadow-slate-200/20 rounded-full py-3" 
               : "w-full bg-white/80 backdrop-blur-md border-b border-transparent py-4"
        )}>
         {/* Logo Section */}
         <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 bg-gradient-to-br from-violet-600 to-indigo-600 rounded-lg flex items-center justify-center text-white shadow-md shadow-violet-200">
               <Box className="w-5 h-5 stroke-[2.5]" />
            </div>
            <span className="text-xl font-bold tracking-tight text-slate-900">Lucid AI</span>
         </div>

         {/* Desktop Nav */}
         <nav className="hidden md:flex items-center gap-8 text-[15px] font-medium text-slate-500">
            <a href="#" className="hover:text-violet-600 transition-colors">Features</a>
            <a href="#" className="hover:text-violet-600 transition-colors">Documentation</a>
            <a href="#" className="hover:text-violet-600 transition-colors">Software Engineer</a>
            <a href="#" className="hover:text-violet-600 transition-colors">Integrations</a>
         </nav>

         {/* Right Actions */}
         <div className="hidden md:flex items-center gap-6">
            <Link href="/login" className="text-[15px] font-bold text-slate-600 hover:text-slate-900 transition-colors">Login</Link>
            <Link href="/login" className="bg-gradient-to-r from-violet-600 to-indigo-600 text-white text-[15px] font-semibold px-5 py-2.5 rounded-lg hover:shadow-lg hover:shadow-violet-500/30 transition-all duration-200 transform hover:-translate-y-0.5 flex items-center justify-center">
               Get Started
            </Link>
         </div>

         {/* Mobile Menu Toggle */}
         <button className="md:hidden text-slate-600" onClick={() => setIsMenuOpen(!isMenuOpen)}>
            {isMenuOpen ? <X /> : <Menu />}
         </button>
        </header>
      </div>

      {/* Mobile Menu */}
      {isMenuOpen && (
         <div className="md:hidden bg-white border-b border-slate-100 p-4 flex flex-col gap-4 shadow-xl absolute w-full z-40 top-[22px]">
            <a href="#" className="text-slate-600 font-medium">Features</a>
            <a href="#" className="text-slate-600 font-medium">Documentation</a>
            <a href="#" className="text-slate-600 font-medium">Software Engineer</a>
            <a href="#" className="text-slate-600 font-medium">Integrations</a>
            <div className="h-px bg-slate-100 w-full my-1"></div>
            <Link href="/login" className="text-slate-600 font-medium">Login</Link>
            <Link href="/login" className="bg-violet-600 text-white font-medium px-4 py-2 rounded-lg w-full text-center block">Get Started</Link>
         </div>
      )}
    </>
  );
}
