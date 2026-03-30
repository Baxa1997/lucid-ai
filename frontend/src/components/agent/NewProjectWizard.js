'use client';

// ─────────────────────────────────────────────────────────
//  Lucid AI — NewProjectWizard (6-Step In-Workspace Panel)
//  Stack → Description → Figma → Backend → Confirm → Deploy
//  Full-viewport, centered, solid, production-ready
// ─────────────────────────────────────────────────────────

import { useState, useRef, useCallback } from 'react';
import { cn } from '@/lib/utils';
import {
  X, ArrowLeft, ArrowRight, Rocket, Upload, FileText,
  Link2, Check, Server, Globe, Sparkles, Shuffle,
  ChevronRight, Database, ExternalLink, Github,
  CheckCircle2, Circle, CloudUpload, Trash2, Zap, Loader2,
  AlertTriangle, RefreshCw, Bot,
} from 'lucide-react';

// ── Real SVG logo icons for each stack ──────────────────
const StackLogos = {
  'html-css': () => (
    <svg viewBox="0 0 32 32" className="w-6 h-6"><polygon fill="#E44D26" points="5.902 27.201 3.655 2 28.345 2 26.095 27.197 15.985 30"/><polygon fill="#F16529" points="16 27.858 24.17 25.593 26.092 4.061 16 4.061"/><path fill="#EBEBEB" d="M16 13.407H11.91l-.282-3.165H16V7.151H8.383l.074.83.759 8.517H16zm0 8.027l-.014.004-3.442-.929-.22-2.465H9.221l.433 4.852 6.332 1.758.014-.004z"/><path fill="#fff" d="M15.989 13.407v3.091h3.806l-.358 4.009-3.448.93v3.216l6.337-1.757.046-.522.726-8.137.076-.83h-.834zm0-6.256v3.091h7.466l.062-.694.141-1.567.074-.83z"/></svg>
  ),
  nextjs: () => (
    <svg viewBox="0 0 180 180" className="w-6 h-6"><mask id="nj" maskUnits="userSpaceOnUse" x="0" y="0" width="180" height="180"><circle cx="90" cy="90" r="90" fill="white"/></mask><g mask="url(#nj)"><circle cx="90" cy="90" r="90" fill="black" className="dark:fill-white"/><path d="M149.508 157.52L69.142 54H54v71.97h12.114V69.384l73.885 95.461A90.304 90.304 0 01149.508 157.52z" fill="url(#ng)" className="dark:[fill:url(#ng-dark)]"/><rect x="115" y="54" width="12" height="72" fill="url(#nr)"/></g><defs><linearGradient id="ng" x1="109" y1="116.5" x2="144.5" y2="160.5" gradientUnits="userSpaceOnUse"><stop stopColor="white"/><stop offset="1" stopColor="white" stopOpacity="0"/></linearGradient><linearGradient id="ng-dark" x1="109" y1="116.5" x2="144.5" y2="160.5" gradientUnits="userSpaceOnUse"><stop stopColor="black"/><stop offset="1" stopColor="black" stopOpacity="0"/></linearGradient><linearGradient id="nr" x1="121" y1="54" x2="120.799" y2="106.875" gradientUnits="userSpaceOnUse"><stop stopColor="white" className="dark:[stop-color:black]"/><stop offset="1" stopColor="white" stopOpacity="0" className="dark:[stop-color:black]"/></linearGradient></defs></svg>
  ),
  react: () => (
    <img src="/icons/reactjs.svg" alt="React" className="w-6 h-6" />
  ),
  fastapi: () => (
    <svg viewBox="0 0 32 32" className="w-6 h-6"><path fill="#009688" d="M16 2C8.268 2 2 8.268 2 16s6.268 14 14 14 14-6.268 14-14S23.732 2 16 2zm-.6 25.2V18h-4l5.2-13.2V14h4l-5.2 13.2z"/></svg>
  ),
  express: () => (
    <svg viewBox="0 0 32 32" className="w-6 h-6"><path d="M2 16c0-7.732 6.268-14 14-14s14 6.268 14 14-6.268 14-14 14S2 23.732 2 16z" fill="#333" className="dark:fill-slate-300"/><path d="M9.5 12h2v8h-2zm4 0l3.5 4-3.5 4h2.5l2.25-2.67L20.5 20H23l-3.5-4 3.5-4h-2.5l-2.25 2.67L16 12z" fill="white" className="dark:fill-slate-900"/></svg>
  ),
  django: () => (
    <svg viewBox="0 0 32 32" className="w-6 h-6"><rect x="2" y="2" width="28" height="28" rx="4" fill="#092E20"/><path d="M18.4 6h3.2v13.6c-1.648.312-2.856.424-4.176.424-3.928 0-5.976-1.776-5.976-5.176 0-3.256 2.16-5.36 5.504-5.36.568 0 1 .048 1.448.168V6zm0 6.96a2.856 2.856 0 00-1.08-.192c-1.624 0-2.568 1-2.568 2.728 0 1.68.904 2.608 2.536 2.608.36 0 .656-.024 1.112-.104V12.96zM22.56 6h3.2v4.072h-3.2V6zm0 5.504h3.2v9.296h-3.2v-9.296z" fill="white"/></svg>
  ),
  nestjs: () => (
    <svg viewBox="0 0 32 32" className="w-6 h-6"><path d="M18.744 2.641a2.985 2.985 0 00-1.019.218 2.215 2.215 0 01.855.764c.152.254.256.533.308.824.019.135.026.27.022.406a5.832 5.832 0 01.131 1.3c.053.712-.032 1.5-.616 2.013a2.135 2.135 0 01-.313.224 2.244 2.244 0 01.2-.907A2.4 2.4 0 0118.744 2.641zm-2.453 4.035c-.249.5-.187 1.089-.218 1.632a4.619 4.619 0 01-.153.95 1.837 1.837 0 01-.428.7 3.485 3.485 0 01-.692.553l-.163.109A8.577 8.577 0 0110.7 13.61a7.908 7.908 0 00-1.319 2.2 6.6 6.6 0 00-.434 2.656 9.023 9.023 0 004.074 7.276c-.009-.06-.024-.117-.03-.177a5.08 5.08 0 01.045-1.308 6.081 6.081 0 01.322-1.261 8.147 8.147 0 011.3-2.191c.368-.435.773-.837 1.152-1.262a14.233 14.233 0 001.663-2.2 6.148 6.148 0 00.836-3.2c-.024.081-.044.163-.07.244a3.975 3.975 0 01-1.258 1.827A3.449 3.449 0 0115.67 17a2.672 2.672 0 01-1.028-.4 2.656 2.656 0 01-.865-.911 3.141 3.141 0 01-.382-1.343 3.739 3.739 0 01.4-2.06 4.063 4.063 0 011.066-1.271c.163-.127.334-.242.51-.349a6.289 6.289 0 00-.006-.636 3.014 3.014 0 00-.26-1.06l-.035-.075a1.64 1.64 0 00-1.357-1.213z" fill="#E0234E"/><path d="M21.6 10.9a.591.591 0 00-.268.036c.181.128.288.321.389.506a3.6 3.6 0 01.348 2.112 4.244 4.244 0 01-.675 1.883A12.06 12.06 0 0119.9 17.4a13.6 13.6 0 00-1.538 2.194 7.27 7.27 0 00-.809 2.524 6.476 6.476 0 00.2 2.618A9 9 0 0024.2 18.51a8.892 8.892 0 00.715-4.4 5.389 5.389 0 00-1.247-3.017 2.77 2.77 0 00-1.571-.97 1.159 1.159 0 00-.496-.223z" fill="#E0234E"/></svg>
  ),
};

// ── Stack metadata ──────────────────────────────────────
const STACKS = [
  { id: 'html-css', name: 'HTML & CSS', description: 'Pure static site — no framework', tag: 'Simple' },
  { id: 'nextjs', name: 'Next.js', description: 'Full-stack React with SSR', tag: 'Recommended' },
  { id: 'react', name: 'React', description: 'Component-based UI library', tag: 'Popular' },
  { id: 'fastapi', name: 'FastAPI', description: 'Modern Python backend', tag: 'Backend' },
  { id: 'express', name: 'Node.js + Express', description: 'Lightweight Node.js server', tag: 'Backend' },
  { id: 'django', name: 'Django', description: 'Batteries-included Python', tag: 'Backend' },
  { id: 'nestjs', name: 'NestJS', description: 'TypeScript Node.js framework', tag: 'Backend' },
];

const STEP_LABELS = ['Stack', 'Description', 'Figma', 'Backend', 'Confirm', 'Deploy'];
const ACCEPTED_FILE_TYPES = '.pdf,.docx,.doc,.txt';

// ── Close confirmation ──────────────────────────────────
function CloseConfirmDialog({ onConfirm, onCancel }) {
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 dark:bg-black/60 backdrop-blur-sm animate-fade-in">
      <div className="bg-white dark:bg-slate-900 rounded-2xl border border-slate-200 dark:border-slate-800 shadow-2xl w-[380px] mx-4 animate-slide-up overflow-hidden">
        <div className="px-6 pt-6 pb-5">
          <div className="w-10 h-10 rounded-xl bg-red-50 dark:bg-red-500/10 flex items-center justify-center mb-4">
            <Trash2 className="w-5 h-5 text-red-500" />
          </div>
          <h3 className="text-[16px] font-bold text-slate-900 dark:text-slate-100 mb-1">Discard this project?</h3>
          <p className="text-[13px] text-slate-500 dark:text-slate-400">All your progress will be permanently lost.</p>
        </div>
        <div className="flex items-center justify-end gap-2.5 px-6 py-4 bg-slate-50 dark:bg-slate-800/50 border-t border-slate-100 dark:border-slate-800">
          <button onClick={onCancel} className="px-4 py-2 text-[13px] font-medium text-slate-600 dark:text-slate-400 hover:bg-white dark:hover:bg-slate-700 border border-slate-200 dark:border-slate-700 rounded-lg transition-colors">Keep editing</button>
          <button onClick={onConfirm} className="px-4 py-2 text-[13px] font-semibold text-white bg-red-500 hover:bg-red-600 rounded-lg transition-colors shadow-sm">Discard</button>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────
//  Main component
// ─────────────────────────────────────────────────────────

export default function NewProjectWizard({ onClose, onWizardComplete }) {
  const [step, setStep] = useState(0);
  const [direction, setDirection] = useState('forward');
  const [showCloseConfirm, setShowCloseConfirm] = useState(false);

  const [isEnhancing, setIsEnhancing] = useState(false);
  const [enhanceError, setEnhanceError] = useState(null);
  const [autoRecommendation, setAutoRecommendation] = useState(null);
  const [isRecommending, setIsRecommending] = useState(false);

  const [wizardState, setWizardState] = useState({
    stack: null, description: '', descriptionFile: null,
    figmaUrl: '', skipFigma: true, backend: 'none', mcpUrl: '', deployment: 'hosted',
  });

  const fileInputRef = useRef(null);

  const update = useCallback((patch) => {
    if (patch.stack && patch.stack !== 'auto') setAutoRecommendation(null);
    setWizardState((s) => ({ ...s, ...patch }));
  }, []);

  const recommendStack = async (descriptionText) => {
    setIsRecommending(true);
    try {
      const res = await fetch('/api/recommend-stack', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ description: descriptionText }) });
      if (!res.ok) throw new Error('Failed');
      const { stack, reason } = await res.json();
      setAutoRecommendation({ stack, reason });
      setWizardState((s) => ({ ...s, stack }));
      // Go forward to Figma (step 2) — user already provided description on step 1
      setDirection('forward'); setStep(2);
    } catch (err) { console.error('[NewProjectWizard] Recommend failed:', err); setDirection('forward'); setStep(2); }
    finally { setIsRecommending(false); }
  };

  const goNext = async () => {
    if (step >= 5) return;
    // Gate: never advance if current step isn't valid
    const stepValid = (() => {
      switch (step) {
        case 0: return !!wizardState.stack;
        case 1: return !!(wizardState.description.trim() || wizardState.descriptionFile);
        case 2: return true;
        case 3: return wizardState.backend === 'own' ? !!wizardState.mcpUrl.trim() : true;
        case 4: case 5: return true;
        default: return false;
      }
    })();
    if (!stepValid) return;

    // "Choose for me" flow: trigger recommendation once when user completes Description
    if (step === 1 && wizardState.stack === 'auto' && !autoRecommendation) {
      let d = wizardState.description.trim();
      if (!d && wizardState.descriptionFile) d = await extractFileText(wizardState.descriptionFile) || '';
      if (d) { await recommendStack(d); return; }
    }
    setDirection('forward'); setStep((s) => s + 1);
  };

  const goBack = () => { if (step <= 0) return; setDirection('back'); setStep((s) => s - 1); };

  const extractFileText = async (f) => {
    if (!f?.file) return null;
    return f.name.split('.').pop().toLowerCase() === 'txt' ? await f.file.text() : `[Uploaded file: ${f.name}]`;
  };

  const handleSubmit = async () => {
    setIsEnhancing(true); setEnhanceError(null);
    try {
      let d = wizardState.description.trim();
      if (!d && wizardState.descriptionFile) d = await extractFileText(wizardState.descriptionFile) || '';
      const res = await fetch('/api/enhance-prompt', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stack: wizardState.stack, backend: wizardState.backend, description: d, figmaUrl: wizardState.figmaUrl || undefined }),
      });
      if (!res.ok) { const e = await res.json().catch(() => ({ error: 'Unknown error' })); throw new Error(e.error || `HTTP ${res.status}`); }
      const { enhancedPrompt, hasDesignTokens } = await res.json();
      // DO NOT set isEnhancing=false here — keep the loading overlay
      // visible while handleWizardComplete creates the conversation
      // and navigates to the workspace. The wizard unmounts on navigation.
      onWizardComplete?.({ ...wizardState, enhancedPrompt, hasDesignTokens: !!hasDesignTokens });
    } catch (err) {
      console.error('[NewProjectWizard] Enhance failed:', err);
      setEnhanceError(err.message || 'Failed to generate spec');
      // Only reset loading state on ERROR — user needs to see
      // the Deploy step again to retry
      setIsEnhancing(false);
    }
  };

  const canContinue = (() => {
    switch (step) {
      case 0: return !!wizardState.stack;
      case 1: return !!(wizardState.description.trim() || wizardState.descriptionFile);
      case 2: return true;
      case 3: return wizardState.backend === 'own' ? !!wizardState.mcpUrl.trim() : true;
      case 4: case 5: return true;
      default: return false;
    }
  })();

  const handleFileUpload = (e) => { const f = e.target.files?.[0]; if (!f) return; update({ descriptionFile: { name: f.name, file: f } }); if (fileInputRef.current) fileInputRef.current.value = ''; };
  const handleDrop = (e) => { e.preventDefault(); e.stopPropagation(); const f = e.dataTransfer?.files?.[0]; if (!f) return; const ext = f.name.split('.').pop().toLowerCase(); if (['pdf','docx','doc','txt'].includes(ext)) update({ descriptionFile: { name: f.name, file: f } }); };
  const handleDragOver = (e) => { e.preventDefault(); e.stopPropagation(); };
  const handleCloseClick = () => { const p = wizardState.stack || wizardState.description.trim() || wizardState.descriptionFile || wizardState.figmaUrl; p ? setShowCloseConfirm(true) : onClose?.(); };

  // ═══════════════════════════════════════════════════════
  //  STEP 0: Stack — centered two-column grid
  // ═══════════════════════════════════════════════════════

  const renderStep0_Stack = () => (
    <div className="h-full flex flex-col items-center justify-center">
      <div className="w-full max-w-[720px]">
        <div className="text-center mb-8">
          <h2 className="text-[28px] font-bold text-slate-900 dark:text-slate-100 tracking-tight">What would you like to build?</h2>
          <p className="text-[15px] text-slate-400 dark:text-slate-500 mt-2">Choose a stack to get started. We’ll set up everything for you.</p>
        </div>



        <div className="grid grid-cols-2 gap-3">
          {STACKS.map((s) => (
            <button
              key={s.id}
              onClick={() => update({ stack: s.id })}
              className={cn(
                "group relative flex items-center gap-4 px-5 py-4 rounded-2xl text-left transition-all duration-200",
                wizardState.stack === s.id
                  ? 'bg-blue-50/80 dark:bg-blue-500/10 border-2 border-blue-500 dark:border-blue-400 shadow-[0_0_0_3px_rgba(59,130,246,0.08)]'
                  : 'bg-white dark:bg-slate-800/60 border border-slate-200 dark:border-slate-700/80 shadow-sm hover:shadow-md hover:border-slate-300 dark:hover:border-slate-600 hover:-translate-y-[1px]'
              )}
            >
              {/* Icon */}
              <div className={cn(
                "w-11 h-11 rounded-xl flex items-center justify-center shrink-0 transition-colors",
                wizardState.stack === s.id
                  ? 'bg-white dark:bg-blue-500/20'
                  : 'bg-slate-50 dark:bg-slate-700/80'
              )}>{StackLogos[s.id] ? StackLogos[s.id]() : <Globe className="w-5 h-5 text-slate-400" />}</div>
              {/* Text */}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className={cn("text-[14px] font-semibold truncate", wizardState.stack === s.id ? 'text-blue-700 dark:text-blue-300' : 'text-slate-800 dark:text-slate-200')}>{s.name}</span>
                  {s.tag && (
                    <span className={cn("px-1.5 py-0.5 text-[9px] font-bold rounded uppercase tracking-wider shrink-0",
                      s.tag === 'Recommended' ? 'bg-blue-100 dark:bg-blue-500/20 text-blue-600 dark:text-blue-400'
                        : s.tag === 'Popular' ? 'bg-emerald-100 dark:bg-emerald-500/20 text-emerald-600 dark:text-emerald-400'
                        : s.tag === 'Backend' ? 'bg-slate-100 dark:bg-slate-600/40 text-slate-500 dark:text-slate-400'
                        : 'bg-slate-200 dark:bg-slate-600 text-slate-500'
                    )}>{s.tag}</span>
                  )}
                </div>
                <p className="text-[12px] text-slate-400 dark:text-slate-500 truncate mt-0.5">{s.description}</p>
              </div>
              {/* Checkmark */}
              {wizardState.stack === s.id && (
                <div className="absolute top-2.5 right-2.5">
                  <CheckCircle2 className="w-5 h-5 text-blue-500 dark:text-blue-400" />
                </div>
              )}
            </button>
          ))}

          {/* Choose for me */}
          <button
            onClick={() => update({ stack: 'auto' })}
            className={cn(
              "group relative flex items-center gap-4 px-5 py-4 rounded-2xl text-left transition-all duration-200",
              wizardState.stack === 'auto'
                ? 'bg-violet-50/80 dark:bg-violet-500/10 border-2 border-violet-400 dark:border-violet-500 shadow-[0_0_0_3px_rgba(139,92,246,0.08)]'
                : 'bg-white dark:bg-slate-800/60 border border-slate-200 dark:border-slate-700/80 shadow-sm hover:shadow-md hover:border-violet-300 dark:hover:border-violet-600 hover:-translate-y-[1px]'
            )}
          >
            <div className={cn("w-11 h-11 rounded-xl flex items-center justify-center shrink-0 transition-colors",
              wizardState.stack === 'auto'
                ? 'bg-gradient-to-br from-violet-100 to-blue-100 dark:from-violet-500/20 dark:to-blue-500/20'
                : 'bg-slate-50 dark:bg-slate-700/80'
            )}>
              <Shuffle className={cn("w-5 h-5", wizardState.stack === 'auto' ? 'text-violet-600 dark:text-violet-400' : 'text-slate-400 group-hover:text-violet-400')} />
            </div>
            <div className="flex-1 min-w-0">
              <span className={cn("text-[14px] font-semibold", wizardState.stack === 'auto' ? 'text-violet-700 dark:text-violet-400' : 'text-slate-600 dark:text-slate-300')}>Choose for me</span>
              <p className="text-[12px] text-slate-400 dark:text-slate-500 mt-0.5">AI picks the best stack</p>
            </div>
            {wizardState.stack === 'auto' && (
              <div className="absolute top-2.5 right-2.5">
                <CheckCircle2 className="w-5 h-5 text-violet-500" />
              </div>
            )}
          </button>
        </div>
      </div>
    </div>
  );

  // ═══════════════════════════════════════════════════════
  //  STEP 1: Description — centered side-by-side
  // ═══════════════════════════════════════════════════════

  const renderStep1_Description = () => (
    <div className="h-full flex flex-col items-center justify-center">
      <div className="w-full max-w-[600px]">
        <div className="text-center mb-6">
          <h2 className="text-[24px] font-bold text-slate-900 dark:text-slate-100 tracking-tight">Describe your project</h2>
          <p className="text-[14px] text-slate-400 dark:text-slate-500 mt-1.5">Tell us what you want to build. Be as detailed as you like.</p>
        </div>

        {/* Main textarea card */}
        <div className="bg-white dark:bg-slate-800/60 rounded-2xl border border-slate-200 dark:border-slate-700/80 shadow-sm overflow-hidden">
          <textarea
            value={wizardState.description}
            onChange={(e) => update({ description: e.target.value })}
            onKeyDown={(e) => {
              if (e.key !== 'Enter') return;
              const ta = e.target;
              const val = ta.value;
              const pos = ta.selectionStart;
              const beforeCursor = val.slice(0, pos);
              const currentLine = beforeCursor.split('\n').pop() || '';

              // Numbered list: "1. text" → "2. "
              const numMatch = currentLine.match(/^(\s*)(\d+)\.\s(.*)$/);
              if (numMatch) {
                const [, indent, num, content] = numMatch;
                // If line content is empty, remove the empty list item
                if (!content.trim()) {
                  e.preventDefault();
                  const lineStart = pos - currentLine.length;
                  const newVal = val.slice(0, lineStart) + '\n' + val.slice(pos);
                  update({ description: newVal });
                  setTimeout(() => { ta.selectionStart = ta.selectionEnd = lineStart + 1; }, 0);
                  return;
                }
                e.preventDefault();
                const next = `\n${indent}${parseInt(num) + 1}. `;
                const newVal = val.slice(0, pos) + next + val.slice(pos);
                update({ description: newVal });
                setTimeout(() => { ta.selectionStart = ta.selectionEnd = pos + next.length; }, 0);
                return;
              }

              // Bullet list: "- text" or "• text" → "- " or "• "
              const bulletMatch = currentLine.match(/^(\s*)([-•])\s(.*)$/);
              if (bulletMatch) {
                const [, indent, bullet, content] = bulletMatch;
                if (!content.trim()) {
                  e.preventDefault();
                  const lineStart = pos - currentLine.length;
                  const newVal = val.slice(0, lineStart) + '\n' + val.slice(pos);
                  update({ description: newVal });
                  setTimeout(() => { ta.selectionStart = ta.selectionEnd = lineStart + 1; }, 0);
                  return;
                }
                e.preventDefault();
                const next = `\n${indent}${bullet} `;
                const newVal = val.slice(0, pos) + next + val.slice(pos);
                update({ description: newVal });
                setTimeout(() => { ta.selectionStart = ta.selectionEnd = pos + next.length; }, 0);
                return;
              }
            }}
            placeholder="Describe your project — features, target users, design preferences, technical requirements..."
            rows={8}
            className="w-full px-5 py-4 text-[13px] leading-relaxed bg-transparent text-slate-800 dark:text-slate-200 placeholder:text-slate-400 dark:placeholder:text-slate-600 outline-none resize-none border-b border-slate-100 dark:border-slate-700/50"
          />

          {/* File upload strip */}
          <div
            onDrop={handleDrop} onDragOver={handleDragOver}
            onClick={() => fileInputRef.current?.click()}
            className={cn(
              "flex items-center gap-3 px-5 py-3 cursor-pointer transition-colors",
              wizardState.descriptionFile
                ? 'bg-emerald-50/50 dark:bg-emerald-500/5'
                : 'hover:bg-slate-50 dark:hover:bg-slate-700/30'
            )}
          >
            {wizardState.descriptionFile ? (
              <>
                <div className="w-8 h-8 rounded-lg bg-emerald-100 dark:bg-emerald-500/20 flex items-center justify-center shrink-0">
                  <FileText className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-[12px] font-medium text-emerald-700 dark:text-emerald-400 truncate">{wizardState.descriptionFile.name}</p>
                  <p className="text-[10px] text-emerald-500">Uploaded successfully</p>
                </div>
                <button onClick={(e) => { e.stopPropagation(); update({ descriptionFile: null }); }}
                  className="p-1.5 text-slate-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-500/10 rounded-lg transition-colors shrink-0">
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </>
            ) : (
              <>
                <div className="w-8 h-8 rounded-lg bg-slate-100 dark:bg-slate-700/80 flex items-center justify-center shrink-0">
                  <CloudUpload className="w-4 h-4 text-slate-400 dark:text-slate-500" />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-[12px] font-medium text-slate-500 dark:text-slate-400">Attach a spec document</p>
                  <p className="text-[10px] text-slate-400 dark:text-slate-500">PDF, DOCX, or TXT</p>
                </div>
                <span className="text-[11px] text-slate-400 dark:text-slate-500 font-medium shrink-0">Browse</span>
              </>
            )}
            <input ref={fileInputRef} type="file" accept={ACCEPTED_FILE_TYPES} onChange={handleFileUpload} className="hidden" />
          </div>
        </div>
      </div>
    </div>
  );

  // ═══════════════════════════════════════════════════════
  //  STEP 2: Figma — centered
  // ═══════════════════════════════════════════════════════

  const renderStep2_Figma = () => (
    <div className="h-full flex flex-col items-center justify-center">
      <div className="w-full max-w-[552px]">
        <div className="text-center mb-6">
          <h2 className="text-[24px] font-bold text-slate-900 dark:text-slate-100 tracking-tight">Figma design</h2>
          <p className="text-[14px] text-slate-400 dark:text-slate-500 mt-1.5">Import your design or let AI generate the UI for you.</p>
        </div>

        <div className="space-y-3">
          {/* Option 1: Generate from scratch (default) */}
          <button
            onClick={() => update({ figmaUrl: '', skipFigma: true })}
            className={cn(
              "w-full flex items-center gap-4 px-5 py-4 rounded-2xl text-left transition-all duration-200",
              wizardState.skipFigma
                ? 'bg-violet-50/80 dark:bg-violet-500/10 border-2 border-violet-400 dark:border-violet-500 shadow-[0_0_0_3px_rgba(139,92,246,0.08)]'
                : 'bg-white dark:bg-slate-800/60 border border-slate-200 dark:border-slate-700/80 shadow-sm hover:shadow-md hover:border-slate-300 dark:hover:border-slate-600 hover:-translate-y-[1px]'
            )}
          >
            <div className={cn("w-5 h-5 rounded-full border-2 flex items-center justify-center shrink-0 transition-colors",
              wizardState.skipFigma ? 'border-violet-500 bg-violet-500' : 'border-slate-300 dark:border-slate-600')}>
              {wizardState.skipFigma && <div className="w-2 h-2 rounded-full bg-white" />}
            </div>
            <div className={cn("w-10 h-10 rounded-xl flex items-center justify-center shrink-0",
              wizardState.skipFigma ? 'bg-gradient-to-br from-violet-100 to-blue-100 dark:from-violet-500/20 dark:to-blue-500/20' : 'bg-slate-50 dark:bg-slate-700/80'
            )}>
              <Sparkles className={cn("w-5 h-5", wizardState.skipFigma ? 'text-violet-600 dark:text-violet-400' : 'text-slate-400')} />
            </div>
            <div className="flex-1 min-w-0">
              <span className={cn("text-[14px] font-semibold", wizardState.skipFigma ? 'text-violet-700 dark:text-violet-400' : 'text-slate-800 dark:text-slate-200')}>Generate from scratch</span>
              <p className="text-[12px] text-slate-400 dark:text-slate-500 mt-0.5">AI will create the UI based on your description</p>
            </div>
            {wizardState.skipFigma && <Check className="w-4 h-4 text-violet-600 dark:text-violet-400 shrink-0" />}
          </button>

          {/* Option 2: Paste Figma URL */}
          <div
            onClick={() => update({ skipFigma: false })}
            className={cn(
              "rounded-2xl transition-all duration-200 cursor-pointer",
              !wizardState.skipFigma
                ? 'bg-blue-50/80 dark:bg-blue-500/10 border-2 border-blue-500 dark:border-blue-400 shadow-[0_0_0_3px_rgba(59,130,246,0.08)]'
                : 'bg-white dark:bg-slate-800/60 border border-slate-200 dark:border-slate-700/80 shadow-sm hover:shadow-md hover:border-slate-300 dark:hover:border-slate-600'
            )}
          >
            <div className="flex items-center gap-4 px-5 pt-4 pb-3">
              <div className={cn("w-5 h-5 rounded-full border-2 flex items-center justify-center shrink-0 transition-colors",
                !wizardState.skipFigma ? 'border-blue-500 bg-blue-500' : 'border-slate-300 dark:border-slate-600')}>
                {!wizardState.skipFigma && <div className="w-2 h-2 rounded-full bg-white" />}
              </div>
              <div className="w-10 h-10 rounded-xl bg-white dark:bg-slate-700/80 border border-slate-100 dark:border-slate-600 flex items-center justify-center shrink-0">
                <img src="/icons/figma.svg" alt="Figma" className="w-6 h-6" />
              </div>
              <div className="flex-1 min-w-0">
                <span className={cn("text-[14px] font-semibold", !wizardState.skipFigma ? 'text-blue-700 dark:text-blue-300' : 'text-slate-800 dark:text-slate-200')}>Paste Figma URL</span>
                <p className="text-[12px] text-slate-400 dark:text-slate-500 mt-0.5">We'll extract your design tokens and layout</p>
              </div>
            </div>
            {!wizardState.skipFigma && (
              <div className="px-5 pb-4 pt-1" onClick={(e) => e.stopPropagation()}>
                <div className="relative">
                  <Link2 className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                  <input type="url" value={wizardState.figmaUrl}
                    onChange={(e) => update({ figmaUrl: e.target.value, skipFigma: false })}
                    placeholder="https://www.figma.com/design/..."
                    className="w-full pl-10 pr-4 py-2.5 text-[13px] border border-slate-200 dark:border-slate-600 rounded-xl bg-white dark:bg-slate-800 text-slate-800 dark:text-slate-200 placeholder:text-slate-400 dark:placeholder:text-slate-600 outline-none focus:border-blue-400 dark:focus:border-blue-500 focus:shadow-[0_0_0_3px_rgba(59,130,246,0.08)] transition-all"
                  />
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );

  // ═══════════════════════════════════════════════════════
  //  STEP 3: Backend
  // ═══════════════════════════════════════════════════════

  const renderStep3_Backend = () => {
    const opts = [
      { id: 'none', icon: <Globe className="w-5 h-5" />, label: 'No backend', sub: 'Frontend only — static site', bg: 'bg-slate-50 dark:bg-slate-700/80', color: 'text-slate-500 dark:text-slate-400' },
      { id: 'supabase', icon: <Database className="w-5 h-5" />, label: 'Use Supabase', sub: 'Auth, database, storage included', bg: 'bg-emerald-50 dark:bg-emerald-500/10', color: 'text-emerald-600 dark:text-emerald-400', badge: '+$25/mo' },
      { id: 'own', icon: <Server className="w-5 h-5" />, label: 'Own backend', sub: 'Connect via MCP server', bg: 'bg-blue-50 dark:bg-blue-500/10', color: 'text-blue-600 dark:text-blue-400' },
    ];

    return (
      <div className="h-full flex flex-col items-center justify-center">
        <div className="w-full max-w-[552px]">
          <div className="text-center mb-8">
            <h2 className="text-[28px] font-bold text-slate-900 dark:text-slate-100 tracking-tight">Backend setup</h2>
            <p className="text-[15px] text-slate-400 dark:text-slate-500 mt-2">How should your project handle data?</p>
          </div>
          <div className="space-y-3">
            {opts.map((o) => (
              <button key={o.id} onClick={() => update({ backend: o.id })}
                className={cn("w-full flex items-center gap-4 px-5 py-4 rounded-2xl transition-all duration-200 text-left",
                  wizardState.backend === o.id
                    ? 'bg-blue-50/80 dark:bg-blue-500/10 border-2 border-blue-500 dark:border-blue-400 shadow-[0_0_0_3px_rgba(59,130,246,0.08)]'
                    : 'bg-white dark:bg-slate-800/60 border border-slate-200 dark:border-slate-700/80 shadow-sm hover:shadow-md hover:border-slate-300 dark:hover:border-slate-600 hover:-translate-y-[1px]'
                )}>
                <div className={cn("w-5 h-5 rounded-full border-2 flex items-center justify-center shrink-0 transition-colors",
                  wizardState.backend === o.id ? 'border-blue-500 bg-blue-500' : 'border-slate-300 dark:border-slate-600')}>
                  {wizardState.backend === o.id && <div className="w-2 h-2 rounded-full bg-white" />}
                </div>
                <div className={cn("w-10 h-10 rounded-xl flex items-center justify-center shrink-0", o.bg)}>
                  <span className={o.color}>{o.icon}</span>
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className={cn("text-[14px] font-semibold", wizardState.backend === o.id ? 'text-blue-700 dark:text-blue-300' : 'text-slate-800 dark:text-slate-200')}>{o.label}</span>
                    {o.badge && <span className="px-1.5 py-0.5 bg-emerald-100 dark:bg-emerald-500/20 text-emerald-700 dark:text-emerald-400 text-[10px] font-bold rounded">{o.badge}</span>}
                  </div>
                  <p className="text-[12px] text-slate-400 dark:text-slate-500 mt-0.5">{o.sub}</p>
                </div>
              </button>
            ))}
          </div>
          {wizardState.backend === 'own' && (
            <div className="mt-4 animate-fade-in">
              <label className="block text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-1.5">MCP Server URL</label>
              <div className="relative">
                <Server className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                <input type="url" value={wizardState.mcpUrl} onChange={(e) => update({ mcpUrl: e.target.value })}
                  placeholder="https://api.yourbackend.com/mcp"
                  className="w-full pl-11 pr-4 py-3 text-sm border border-slate-200 dark:border-slate-700 rounded-2xl bg-white dark:bg-slate-800/60 text-slate-800 dark:text-slate-200 placeholder:text-slate-400 dark:placeholder:text-slate-600 outline-none focus:border-blue-400 dark:focus:border-blue-500 focus:shadow-[0_0_0_3px_rgba(59,130,246,0.08)] transition-all shadow-sm"
                />
              </div>
            </div>
          )}
        </div>
      </div>
    );
  };

  // ═══════════════════════════════════════════════════════
  //  STEP 4: Confirm
  // ═══════════════════════════════════════════════════════

  const renderStep4_Confirm = () => {
    const resolvedStack = wizardState.stack === 'auto' && autoRecommendation ? autoRecommendation.stack : wizardState.stack;
    const stackLabel = STACKS.find((s) => s.id === resolvedStack)?.name || (wizardState.stack === 'auto' ? 'Auto-selected' : '—');
    const backendLabel = { none: 'No backend', supabase: 'Supabase', own: 'Own backend' }[wizardState.backend] || '—';
    const descLabel = wizardState.descriptionFile ? wizardState.descriptionFile.name
      : wizardState.description ? (wizardState.description.length > 80 ? wizardState.description.slice(0, 80) + '…' : wizardState.description) : '—';
    const figmaLabel = wizardState.skipFigma ? 'Generate from scratch' : wizardState.figmaUrl || '—';
    const rows = [
      { label: 'Stack', value: stackLabel, icon: <Zap className="w-4 h-4" /> },
      { label: 'Description', value: descLabel, icon: <FileText className="w-4 h-4" /> },
      { label: 'Figma', value: figmaLabel, icon: <Link2 className="w-4 h-4" />, truncate: true },
      { label: 'Backend', value: backendLabel, icon: <Server className="w-4 h-4" /> },
    ];
    if (wizardState.backend === 'own' && wizardState.mcpUrl) rows.push({ label: 'MCP URL', value: wizardState.mcpUrl, icon: <Globe className="w-4 h-4" />, truncate: true });

    return (
      <div className="h-full flex flex-col items-center justify-center">
        <div className="w-full max-w-[560px]">
          <div className="text-center mb-6">
            <h2 className="text-[24px] font-bold text-slate-900 dark:text-slate-100 tracking-tight">Confirm your setup</h2>
            <p className="text-[14px] text-slate-400 dark:text-slate-500 mt-1.5">Review your configuration before we start building.</p>
          </div>
          <div className="bg-white dark:bg-slate-800/60 rounded-2xl border border-slate-200 dark:border-slate-700/80 shadow-sm overflow-hidden">
            {rows.map((r, i) => (
              <div key={i} className={cn("flex items-center gap-3.5 px-5 py-3.5", i > 0 && 'border-t border-slate-100 dark:border-slate-700/50')}>
                <div className="w-8 h-8 rounded-lg bg-slate-50 dark:bg-slate-700/80 flex items-center justify-center shrink-0 text-slate-400 dark:text-slate-500">{r.icon}</div>
                <div className="flex-1 min-w-0">
                  <p className="text-[10px] font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-wider mb-0.5">{r.label}</p>
                  <p className={cn("text-[13px] font-medium text-slate-700 dark:text-slate-300", r.truncate && 'truncate')}>{r.value}</p>
                </div>
                <button onClick={() => { const m = { Stack: 0, Description: 1, Figma: 2, Backend: 3, 'MCP URL': 3 }; setDirection('back'); setStep(m[r.label] ?? 0); }}
                  className="text-[12px] text-slate-400 dark:text-slate-500 hover:text-blue-600 dark:hover:text-blue-400 font-medium shrink-0 px-2 py-1 rounded-lg hover:bg-slate-50 dark:hover:bg-slate-700/50 transition-colors">Edit</button>
              </div>
            ))}
          </div>
        </div>
      </div>
    );
  };

  // ═══════════════════════════════════════════════════════
  //  STEP 5: Deploy
  // ═══════════════════════════════════════════════════════

  const renderStep5_Deployment = () => {
    const opts = [
      { id: 'hosted', icon: <Rocket className="w-5 h-5" />, label: 'Host it for me', sub: 'Free subdomain — instant deploy', bg: 'bg-blue-50 dark:bg-blue-500/10', color: 'text-blue-600 dark:text-blue-400', badge: 'Free', bc: 'bg-blue-100 dark:bg-blue-500/20 text-blue-700 dark:text-blue-400' },
      { id: 'own', icon: <Github className="w-5 h-5" />, label: 'Deploy to my account', sub: 'Export to GitHub after build', bg: 'bg-slate-50 dark:bg-slate-700/80', color: 'text-slate-600 dark:text-slate-400', badge: 'Later', bc: 'bg-amber-100 dark:bg-amber-500/20 text-amber-700 dark:text-amber-400' },
    ];

    return (
      <div className="h-full flex flex-col items-center justify-center">
        <div className="w-full max-w-[552px]">
          <div className="text-center mb-8">
            <h2 className="text-[24px] font-bold text-slate-900 dark:text-slate-100 tracking-tight">Deployment</h2>
            <p className="text-[14px] text-slate-400 dark:text-slate-500 mt-1.5">Where should we deploy your project?</p>
          </div>
          <div className="space-y-3">
            {opts.map((o) => (
              <button key={o.id} onClick={() => update({ deployment: o.id })}
                className={cn("w-full flex items-center gap-4 px-5 py-4 rounded-2xl transition-all duration-200 text-left",
                  wizardState.deployment === o.id
                    ? 'bg-blue-50/80 dark:bg-blue-500/10 border-2 border-blue-500 dark:border-blue-400 shadow-[0_0_0_3px_rgba(59,130,246,0.08)]'
                    : 'bg-white dark:bg-slate-800/60 border border-slate-200 dark:border-slate-700/80 shadow-sm hover:shadow-md hover:border-slate-300 dark:hover:border-slate-600 hover:-translate-y-[1px]'
                )}>
                <div className={cn("w-5 h-5 rounded-full border-2 flex items-center justify-center shrink-0 transition-colors",
                  wizardState.deployment === o.id ? 'border-blue-500 bg-blue-500' : 'border-slate-300 dark:border-slate-600')}>
                  {wizardState.deployment === o.id && <div className="w-2 h-2 rounded-full bg-white" />}
                </div>
                <div className={cn("w-10 h-10 rounded-xl flex items-center justify-center shrink-0", o.bg)}>
                  <span className={o.color}>{o.icon}</span>
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className={cn("text-[14px] font-semibold", wizardState.deployment === o.id ? 'text-blue-700 dark:text-blue-300' : 'text-slate-800 dark:text-slate-200')}>{o.label}</span>
                    <span className={cn("px-1.5 py-0.5 text-[10px] font-bold rounded", o.bc)}>{o.badge}</span>
                  </div>
                  <p className="text-[12px] text-slate-400 dark:text-slate-500 mt-0.5">{o.sub}</p>
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>
    );
  };

  const STEPS = [renderStep0_Stack, renderStep1_Description, renderStep2_Figma, renderStep3_Backend, renderStep4_Confirm, renderStep5_Deployment];

  // ═══════════════════════════════════════════════════════
  //  RENDER
  // ═══════════════════════════════════════════════════════

  return (
    <div className="h-full flex flex-col bg-[#f8f9fc] dark:bg-[#0d1117] overflow-hidden relative">

      {showCloseConfirm && <CloseConfirmDialog onConfirm={() => { setShowCloseConfirm(false); onClose?.(); }} onCancel={() => setShowCloseConfirm(false)} />}

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
            <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-violet-500 to-blue-600 flex items-center justify-center shadow-lg shadow-violet-500/20">
              <Shuffle className="w-7 h-7 text-white" />
            </div>
            <div className="absolute -bottom-1 -right-1 w-6 h-6 rounded-full bg-white dark:bg-slate-900 border-2 border-violet-500 flex items-center justify-center">
              <Loader2 className="w-3.5 h-3.5 text-violet-600 animate-spin" />
            </div>
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

      {/* ── Compact stepper row ── */}
      <div className="shrink-0 flex items-center justify-center gap-1 px-4 py-2.5 bg-white dark:bg-[#0d1117] border-b border-slate-100 dark:border-slate-800/40">
        {STEP_LABELS.map((label, i) => (
          <div key={i} className="flex items-center">
            <div className="flex items-center gap-1.5 px-1">
              <div className={cn(
                "w-[22px] h-[22px] rounded-full flex items-center justify-center text-[10px] font-bold transition-all duration-500",
                i < step
                  ? 'bg-blue-600 text-white'
                  : i === step
                    ? 'bg-blue-600 text-white ring-2 ring-blue-200 dark:ring-blue-500/30'
                    : 'bg-slate-100 dark:bg-slate-800 text-slate-400 dark:text-slate-500 border border-slate-200 dark:border-slate-700'
              )}>
                {i < step ? <Check className="w-2.5 h-2.5" /> : i + 1}
              </div>
              <span className={cn("text-[11px] font-medium hidden sm:block",
                i === step ? 'text-blue-600 dark:text-blue-400'
                  : i < step ? 'text-slate-500 dark:text-slate-400'
                  : 'text-slate-400 dark:text-slate-500'
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

      {/* ── Content ── */}
      <div className="flex-1 min-h-0 px-6 overflow-y-auto">
        <div key={step} className={cn("h-full", direction === 'forward' ? 'animate-wizard-slide-in' : 'animate-wizard-slide-back')}>
          {STEPS[step]()}
        </div>
      </div>

      {/* ── Error ── */}
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

      {/* ── Footer ── */}
      <div className="shrink-0 px-6 py-4 bg-white dark:bg-[#0d1117] border-t border-slate-200/80 dark:border-slate-800/60">
        <div className="max-w-2xl mx-auto flex items-center justify-between">
          {step > 0 ? (
            <button onClick={goBack} disabled={isEnhancing}
              className="flex items-center gap-2 px-5 py-2.5 text-sm font-medium text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-xl transition-all disabled:opacity-50 border border-slate-200 dark:border-slate-700">
              <ArrowLeft className="w-4 h-4" /> Back
            </button>
          ) : <div />}

          {step < 5 ? (
            <button onClick={goNext} disabled={!canContinue}
              className={cn(
                "flex items-center gap-2 px-8 py-2.5 rounded-xl text-sm font-semibold transition-all duration-200",
                canContinue
                  ? "bg-blue-600 text-white hover:bg-blue-700 shadow-sm shadow-blue-600/20 active:scale-[0.98]"
                  : "bg-slate-200 dark:bg-slate-800 text-slate-400 dark:text-slate-600 cursor-not-allowed"
              )}>
              Continue <ArrowRight className="w-4 h-4" />
            </button>
          ) : (
            <button onClick={handleSubmit} disabled={isEnhancing}
              className="flex items-center gap-2 px-8 py-2.5 rounded-xl text-sm font-semibold bg-gradient-to-r from-blue-600 to-blue-700 text-white hover:from-blue-700 hover:to-blue-800 shadow-sm shadow-blue-600/20 active:scale-[0.98] transition-all disabled:opacity-50">
              {isEnhancing ? (<><Loader2 className="w-4 h-4 animate-spin" /> Generating…</>) : (<><Rocket className="w-4 h-4" /> Create Project</>)}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
