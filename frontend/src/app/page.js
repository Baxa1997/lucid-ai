'use client';

import Navbar from '@/components/Navbar';
import HeroSection from '@/components/HeroSection';
import FeaturesSection from '@/components/FeaturesSection';
import IntegrationsSection from '@/components/IntegrationsSection';
import AIPowerSection from '@/components/AIPowerSection';
import UseCasesSection from '@/components/UseCasesSection';
import Footer from '@/components/Footer';

export default function LandingPage() {
  return (
    <div className="min-h-screen bg-[#FDFDFD] dark:bg-slate-950 text-slate-900 dark:text-slate-100 font-sans selection:bg-violet-100 selection:text-violet-900 dark:selection:bg-violet-900 dark:selection:text-violet-100 font-inter flex flex-col transition-colors duration-200">
      
      <Navbar />

      <main className="flex-1 w-full">
        <HeroSection />
        <FeaturesSection />
        <IntegrationsSection />
        <AIPowerSection />
        <UseCasesSection />
      </main>

      <Footer />

    </div>
  );
}
