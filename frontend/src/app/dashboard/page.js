'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';

/**
 * Root Dashboard — Redirects to the engineer dashboard.
 * The old "What would you like to do?" selector has been removed.
 * Documentation is now accessible from the sidebar.
 */
export default function DashboardPage() {
  const router = useRouter();

  useEffect(() => {
    router.replace('/dashboard/engineer');
  }, [router]);

  return (
    <div className="min-h-screen bg-white dark:bg-[#0d1117] flex items-center justify-center">
      <div className="w-6 h-6 rounded-full border-2 border-[#dc5426] border-t-transparent animate-spin" />
    </div>
  );
}
