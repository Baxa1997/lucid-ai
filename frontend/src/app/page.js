'use client';

import Navbar from '@/components/Navbar';
import HeroSection from '@/components/HeroSection';
import FeaturesSection from '@/components/FeaturesSection';
import IntegrationsSection from '@/components/IntegrationsSection';
import TemplatesSection from '@/components/TemplatesSection';
import UseCasesSection from '@/components/UseCasesSection';
import Footer from '@/components/Footer';

export default function LandingPage() {
  return (
    <div
      className="relative min-h-screen text-[#15171C] dark:text-slate-100 flex flex-col selection:bg-orange-100 selection:text-[#E85A2C] dark:selection:bg-orange-900/40 dark:selection:text-orange-300 bg-[#FDFDFD] dark:bg-slate-950">
      {/* Only the Navbar is fixed; StatusRail rides inside the hero banner.
          top-4 gives the pill breathing room from the viewport edge. */}
      <div className="fixed inset-x-0 top-4 z-50">
        <Navbar />
      </div>

      <main className="flex-1 w-full">
        {/* Hero owns its own gradient + min-h-screen so the banner fills the
            viewport and its gradient terminates in #FDFDFD — the same color
            as the sections below, so the join is invisible. */}
        <HeroSection />
        <FeaturesSection />
        <IntegrationsSection />
        <TemplatesSection />
        <UseCasesSection />
      </main>

      <Footer />
    </div>
  );
}
