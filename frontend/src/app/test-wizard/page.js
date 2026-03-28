'use client';

import { useState } from 'react';
import NewProjectWizard from '@/components/agent/NewProjectWizard';

export default function TestWizardPage() {
  const [isOpen, setIsOpen] = useState(true);
  const [result, setResult] = useState(null);

  const handleComplete = (state) => {
    console.log('Wizard complete:', state);
    setResult(state);
    setIsOpen(false);
  };

  return (
    <div className="min-h-screen bg-slate-100 flex items-center justify-center p-8">
      <div className="text-center">
        <h1 className="text-2xl font-bold text-slate-900 mb-4">Wizard Test Page</h1>
        <button
          onClick={() => { setIsOpen(true); setResult(null); }}
          className="px-6 py-3 bg-blue-600 text-white rounded-xl font-bold hover:bg-blue-700 transition-colors"
        >
          Open Wizard
        </button>

        {result && (
          <pre className="mt-6 p-4 bg-white rounded-xl border text-left text-sm text-slate-700 max-w-lg mx-auto overflow-auto">
            {JSON.stringify(result, null, 2)}
          </pre>
        )}
      </div>

      <NewProjectWizard
        isOpen={isOpen}
        onClose={() => setIsOpen(false)}
        onWizardComplete={handleComplete}
      />
    </div>
  );
}
