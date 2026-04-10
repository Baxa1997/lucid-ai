'use client';

// ─────────────────────────────────────────────────────────
//  Lucid AI — NewProjectWizard (6-Step In-Workspace Panel)
//  Stack → Description → Figma → Backend → Confirm → Deploy
//  Thin orchestrator: state, navigation, API calls, chrome.
// ─────────────────────────────────────────────────────────

import { useState, useCallback } from 'react';
import { cn } from '@/lib/utils';
import {
  X, ArrowLeft, ArrowRight, Rocket, Check,
  Sparkles, Shuffle, Loader2, AlertTriangle, RefreshCw,
} from 'lucide-react';

// ── Sub-components ────────────────────────────────────────
import CloseConfirmDialog from './wizard/CloseConfirmDialog';
import WizardStep0Stack from './wizard/WizardStep0Stack';
import WizardStep1Description from './wizard/WizardStep1Description';
import WizardStep2Figma from './wizard/WizardStep2Figma';
import WizardStep3Backend from './wizard/WizardStep3Backend';
import WizardStep4Confirm from './wizard/WizardStep4Confirm';
import WizardStep5Deployment from './wizard/WizardStep5Deployment';

const STEP_LABELS = ['Stack', 'Description', 'Figma', 'Backend', 'Confirm', 'Deploy'];

export default function NewProjectWizard({ onClose, onWizardComplete }) {
  const [step, setStep] = useState(0);
  const [direction, setDirection] = useState('forward');
  const [showCloseConfirm, setShowCloseConfirm] = useState(false);

  const [isEnhancing, setIsEnhancing] = useState(false);
  const [enhanceError, setEnhanceError] = useState(null);
  const [autoRecommendation, setAutoRecommendation] = useState(null);
  const [isRecommending, setIsRecommending] = useState(false);

  const [wizardState, setWizardState] = useState({
    stack: 'auto', description: '', descriptionFile: null,
    figmaUrl: '', skipFigma: true, backend: 'none', mcpUrl: '', deployment: 'hosted',
  });

  const update = useCallback((patch) => {
    if (patch.stack && patch.stack !== 'auto') setAutoRecommendation(null);
    setWizardState((s) => ({ ...s, ...patch }));
  }, []);

  // ── Step validation ───────────────────────────────────
  const isStepValid = (s) => {
    switch (s) {
      case 0: return !!wizardState.stack;
      case 1: return !!(wizardState.description.trim() || wizardState.descriptionFile);
      case 2: return true;
      case 3: return wizardState.backend === 'own' ? !!wizardState.mcpUrl.trim() : true;
      case 4: case 5: return true;
      default: return false;
    }
  };
  const canContinue = isStepValid(step);

  // ── Navigation ────────────────────────────────────────
  const extractFileText = async (f) => {
    if (!f?.file) return null;
    return f.name.split('.').pop().toLowerCase() === 'txt' ? await f.file.text() : `[Uploaded file: ${f.name}]`;
  };

  const recommendStack = async (descriptionText) => {
    setIsRecommending(true);
    try {
      const res = await fetch('/api/recommend-stack', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description: descriptionText }),
      });
      if (!res.ok) throw new Error('Failed');
      const { stack, reason } = await res.json();
      setAutoRecommendation({ stack, reason });
      setWizardState((s) => ({ ...s, stack }));
      setDirection('forward'); setStep(2);
    } catch {
      setDirection('forward'); setStep(2);
    } finally {
      setIsRecommending(false);
    }
  };

  const goNext = async () => {
    if (step >= 5 || !isStepValid(step)) return;

    if (step === 1 && wizardState.stack === 'auto' && !autoRecommendation) {
      let d = wizardState.description.trim();
      if (!d && wizardState.descriptionFile) d = await extractFileText(wizardState.descriptionFile) || '';
      if (d) { await recommendStack(d); return; }
    }
    setDirection('forward'); setStep((s) => s + 1);
  };

  const goBack = () => { if (step <= 0) return; setDirection('back'); setStep((s) => s - 1); };

  const goToStep = (index) => { setDirection('back'); setStep(index); };

  // ── Submit ────────────────────────────────────────────
  const handleSubmit = async () => {
    setIsEnhancing(true); setEnhanceError(null);
    try {
      let d = wizardState.description.trim();
      if (!d && wizardState.descriptionFile) d = await extractFileText(wizardState.descriptionFile) || '';
      const res = await fetch('/api/enhance-prompt', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stack: wizardState.stack, backend: wizardState.backend, description: d, figmaUrl: wizardState.figmaUrl || undefined }),
      });
      if (!res.ok) { const e = await res.json().catch(() => ({ error: 'Unknown error' })); throw new Error(e.error || `HTTP ${res.status}`); }
      const { enhancedPrompt, hasDesignTokens } = await res.json();
      onWizardComplete?.({ ...wizardState, enhancedPrompt, hasDesignTokens: !!hasDesignTokens });
    } catch (err) {
      setEnhanceError(err.message || 'Failed to generate spec');
      setIsEnhancing(false);
    }
  };

  // ── Close guard ───────────────────────────────────────
  const handleCloseClick = () => {
    const hasProgress = wizardState.stack || wizardState.description.trim() || wizardState.descriptionFile || wizardState.figmaUrl;
    hasProgress ? setShowCloseConfirm(true) : onClose?.();
  };

  // ── Step registry ─────────────────────────────────────
  const STEPS = [
    <WizardStep0Stack key={0} stack={wizardState.stack} onUpdate={update} />,
    <WizardStep1Description key={1} description={wizardState.description} descriptionFile={wizardState.descriptionFile} onUpdate={update} />,
    <WizardStep2Figma key={2} figmaUrl={wizardState.figmaUrl} skipFigma={wizardState.skipFigma} onUpdate={update} />,
    <WizardStep3Backend key={3} backend={wizardState.backend} mcpUrl={wizardState.mcpUrl} onUpdate={update} />,
    <WizardStep4Confirm key={4} wizardState={wizardState} autoRecommendation={autoRecommendation} onGoToStep={goToStep} />,
    <WizardStep5Deployment key={5} deployment={wizardState.deployment} onUpdate={update} />,
  ];

  // ═══════════════════════════════════════════════════════
  //  RENDER
  // ═══════════════════════════════════════════════════════

  return (
    <div className="h-full flex flex-col bg-[#f8f9fc] dark:bg-[#0d1117] overflow-hidden relative">

      {showCloseConfirm && (
        <CloseConfirmDialog
          onConfirm={() => { setShowCloseConfirm(false); onClose?.(); }}
          onCancel={() => setShowCloseConfirm(false)}
        />
      )}

      {/* Enhancing overlay */}
      {isEnhancing && (
        <div className="absolute inset-0 z-20 bg-white/95 dark:bg-slate-950/95 backdrop-blur-sm flex flex-col items-center justify-center animate-fade-in">
          <div className="relative mb-6">
            <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-blue-500 to-violet-600 flex items-center justify-center shadow-lg shadow-blue-500/20"><Sparkles className="w-7 h-7 text-white" /></div>
            <div className="absolute -bottom-1 -right-1 w-6 h-6 rounded-full bg-white dark:bg-slate-900 border-2 border-blue-500 flex items-center justify-center"><Loader2 className="w-3.5 h-3.5 text-blue-600 animate-spin" /></div>
          </div>
          <h3 className="text-lg font-bold text-slate-900 dark:text-slate-100 mb-1">Generating build spec</h3>
          <p className="text-sm text-slate-500 dark:text-slate-400 text-center max-w-xs">Expanding into a detailed technical specification…</p>
          <div className="mt-6 flex items-center gap-1.5">{[0,1,2].map((i) => <div key={i} className="w-2 h-2 rounded-full bg-blue-500 animate-pulse" style={{ animationDelay: `${i*200}ms` }} />)}</div>
        </div>
      )}

      {/* Recommending stack overlay */}
      {isRecommending && (
        <div className="absolute inset-0 z-20 bg-white/95 dark:bg-slate-950/95 backdrop-blur-sm flex flex-col items-center justify-center animate-fade-in">
          <div className="relative mb-6">
            <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-violet-500 to-blue-600 flex items-center justify-center shadow-lg shadow-violet-500/20"><Shuffle className="w-7 h-7 text-white" /></div>
            <div className="absolute -bottom-1 -right-1 w-6 h-6 rounded-full bg-white dark:bg-slate-900 border-2 border-violet-500 flex items-center justify-center"><Loader2 className="w-3.5 h-3.5 text-violet-600 animate-spin" /></div>
          </div>
          <h3 className="text-lg font-bold text-slate-900 dark:text-slate-100 mb-1">Choosing the best stack</h3>
          <p className="text-sm text-slate-500 dark:text-slate-400 text-center max-w-xs">Analyzing your project to find the perfect technology stack…</p>
          <div className="mt-6 flex items-center gap-1.5">{[0,1,2].map((i) => <div key={i} className="w-2 h-2 rounded-full bg-violet-500 animate-pulse" style={{ animationDelay: `${i*200}ms` }} />)}</div>
        </div>
      )}

      {/* ── Top heading bar ── */}
      <div className="shrink-0 flex items-center justify-between px-5 h-12 bg-white dark:bg-[#0d1117] border-b border-slate-200/80 dark:border-slate-800/60">
        <div className="flex items-center gap-2.5">
          <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-violet-500 to-blue-600 flex items-center justify-center">
            <Sparkles className="w-3.5 h-3.5 text-white" />
          </div>
          <span className="text-[14px] font-semibold text-slate-900 dark:text-slate-100">New Project</span>
        </div>
        {!isEnhancing ? (
          <button onClick={handleCloseClick} className="p-1.5 text-slate-400 dark:text-slate-500 hover:text-slate-600 dark:hover:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-lg transition-colors">
            <X className="w-4 h-4" />
          </button>
        ) : <div className="w-7" />}
      </div>

      {/* ── Stepper row ── */}
      <div className="shrink-0 flex items-center justify-center gap-1 px-4 py-2.5 bg-white dark:bg-[#0d1117] border-b border-slate-100 dark:border-slate-800/40">
        {STEP_LABELS.map((label, i) => (
          <div key={i} className="flex items-center">
            <div className="flex items-center gap-1.5 px-1">
              <div className={cn(
                'w-[22px] h-[22px] rounded-full flex items-center justify-center text-[10px] font-bold transition-all duration-500',
                i < step ? 'bg-blue-600 text-white' : i === step ? 'bg-blue-600 text-white ring-2 ring-blue-200 dark:ring-blue-500/30' : 'bg-slate-100 dark:bg-slate-800 text-slate-400 dark:text-slate-500 border border-slate-200 dark:border-slate-700',
              )}>
                {i < step ? <Check className="w-2.5 h-2.5" /> : i + 1}
              </div>
              <span className={cn('text-[11px] font-medium hidden sm:block',
                i === step ? 'text-blue-600 dark:text-blue-400' : i < step ? 'text-slate-500 dark:text-slate-400' : 'text-slate-400 dark:text-slate-500',
              )}>{label}</span>
            </div>
            {i < STEP_LABELS.length - 1 && (
              <div className="w-5 h-[1.5px] bg-slate-200 dark:bg-slate-700 rounded-full overflow-hidden mx-0.5">
                <div className="h-full bg-blue-500 rounded-full transition-all duration-500" style={{ width: i < step ? '100%' : '0%' }} />
              </div>
            )}
          </div>
        ))}
      </div>

      {/* ── Step content ── */}
      <div className="flex-1 min-h-0 px-6 overflow-y-auto">
        <div key={step} className={cn('h-full', direction === 'forward' ? 'animate-wizard-slide-in' : 'animate-wizard-slide-back')}>
          {STEPS[step]}
        </div>
      </div>

      {/* ── Error banner ── */}
      {enhanceError && (
        <div className="shrink-0 mx-6 mb-2">
          <div className="flex items-center gap-3 px-4 py-2.5 bg-red-50 dark:bg-red-500/10 border border-red-200 dark:border-red-500/20 rounded-xl animate-fade-in">
            <AlertTriangle className="w-4 h-4 text-red-500 shrink-0" />
            <p className="text-xs text-red-600 dark:text-red-400 flex-1 truncate">{enhanceError}</p>
            <button onClick={handleSubmit} className="flex items-center gap-1 px-2 py-1 text-xs font-semibold text-red-600 dark:text-red-400 hover:bg-red-100 dark:hover:bg-red-500/20 rounded-lg transition-colors shrink-0">
              <RefreshCw className="w-3 h-3" /> Retry
            </button>
          </div>
        </div>
      )}

      {/* ── Footer navigation ── */}
      <div className="shrink-0 px-6 py-4 bg-white dark:bg-[#0d1117] border-t border-slate-200/80 dark:border-slate-800/60">
        <div className="max-w-2xl mx-auto flex items-center justify-between">
          {step > 0 ? (
            <button onClick={goBack} disabled={isEnhancing} className="flex items-center gap-2 px-5 py-2.5 text-sm font-medium text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-xl transition-all disabled:opacity-50 border border-slate-200 dark:border-slate-700">
              <ArrowLeft className="w-4 h-4" /> Back
            </button>
          ) : <div />}

          {step < 5 ? (
            <button onClick={goNext} disabled={!canContinue} className={cn(
              'flex items-center gap-2 px-8 py-2.5 rounded-xl text-sm font-semibold transition-all duration-200',
              canContinue ? 'bg-blue-600 text-white hover:bg-blue-700 shadow-sm shadow-blue-600/20 active:scale-[0.98]' : 'bg-slate-200 dark:bg-slate-800 text-slate-400 dark:text-slate-600 cursor-not-allowed',
            )}>
              Continue <ArrowRight className="w-4 h-4" />
            </button>
          ) : (
            <button onClick={handleSubmit} disabled={isEnhancing} className="flex items-center gap-2 px-8 py-2.5 rounded-xl text-sm font-semibold bg-gradient-to-r from-blue-600 to-blue-700 text-white hover:from-blue-700 hover:to-blue-800 shadow-sm shadow-blue-600/20 active:scale-[0.98] transition-all disabled:opacity-50">
              {isEnhancing ? <><Loader2 className="w-4 h-4 animate-spin" /> Generating…</> : <><Rocket className="w-4 h-4" /> Create Project</>}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
