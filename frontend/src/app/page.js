'use client';

import Navbar from '@/components/Navbar';
import HeroSection from '@/components/HeroSection';
import UseCasesSection from '@/components/UseCasesSection';
import Footer from '@/components/Footer';

export default function LandingPage() {
  return (
    <div className="min-h-screen bg-[#FDFDFD] text-slate-900 font-sans selection:bg-violet-100 selection:text-violet-900 font-inter flex flex-col">
      
      <Navbar />

      <main className="flex-1 w-full">
        <HeroSection />
        <UseCasesSection />
      </main>

      <Footer />

    </div>
  );
}
