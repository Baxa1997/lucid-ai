'use client';

// ─────────────────────────────────────────────────────────
//  Lucid AI — Engineer Dashboard (v3 — Production Grade)
//  Prompt Hero → Stats → Project Cards → Activity
// ─────────────────────────────────────────────────────────

import {
  Plus, Github, Rocket, Clock, Sparkles,
  MessageSquare, ArrowRight, Loader2, Layers,
  Globe, Zap, Activity, BarChart3,
  ExternalLink, ChevronDown, ChevronUp,
  MoreHorizontal, Code2, Play,
  Lightbulb, Eye, Paperclip, Link2, SlidersHorizontal, Image,
} from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useState, useEffect, useRef } from 'react';
import { cn } from '@/lib/utils';
import { listConversations } from '@/lib/conversations';
import { useWizard } from './layout';
import CustomSelect from '@/components/ui/CustomSelect';
import { getSupabaseBrowserClient } from '@/lib/supabase/client';

// ── Helpers ──────────────────────────────────────
function formatTime(dateStr) {
  if (!dateStr) return '';
  const diff = Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000);
  if (diff < 60) return 'Just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
  return new Date(dateStr).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

const IDEAS = [
  { label: 'CRM dashboard', emoji: '📊', prompt: 'A modern CRM dashboard with contact management, deal pipeline view, activity tracking, and analytics charts. Clean professional design with sidebar navigation.' },
  { label: 'E-commerce store', emoji: '🛒', prompt: 'An e-commerce storefront with product catalog, shopping cart, checkout flow, user accounts, and order history. Modern design with hero banner and category filtering.' },
  { label: 'Portfolio website', emoji: '🎨', prompt: 'A personal portfolio website with hero section, project showcase grid, about me page, skills section, and contact form. Minimal, elegant design with dark mode.' },
  { label: 'SaaS landing', emoji: '🚀', prompt: 'A SaaS product landing page with hero section, feature grid, pricing table, testimonials, FAQ accordion, and footer. Modern gradient design with CTAs.' },
  { label: 'Blog platform', emoji: '📝', prompt: 'A blog platform with article listing, rich text editor, categories/tags, comment system, and author profiles. Clean typography-focused design.' },
  { label: 'Admin panel', emoji: '⚙️', prompt: 'A full-featured admin dashboard with data tables, CRUD forms, user management, role-based access, charts/analytics, and settings page. Professional enterprise design.' },
];

// Emoji icons for projects (deterministic based on name hash)
const PROJECT_EMOJIS = ['🔥', '⚡', '🚀', '💎', '🎯', '🌟', '🎨', '🔮', '🌊', '🍀', '🦊', '🎪'];
const EMOJI_BG_COLORS = [
  'bg-blue-50 dark:bg-blue-500/10',
  'bg-amber-50 dark:bg-amber-500/10',
  'bg-blue-50 dark:bg-blue-500/10',
  'bg-violet-50 dark:bg-violet-500/10',
  'bg-emerald-50 dark:bg-emerald-500/10',
  'bg-rose-50 dark:bg-rose-500/10',
  'bg-cyan-50 dark:bg-cyan-500/10',
  'bg-indigo-50 dark:bg-indigo-500/10',
  'bg-teal-50 dark:bg-teal-500/10',
  'bg-pink-50 dark:bg-pink-500/10',
  'bg-lime-50 dark:bg-lime-500/10',
  'bg-fuchsia-50 dark:bg-fuchsia-500/10',
];

function getProjectHash(name) {
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = (hash << 5) - hash + name.charCodeAt(i);
    hash |= 0;
  }
  return Math.abs(hash);
}

/* ════════════════════════════════════════════════════════
   PROJECT CARD — Base44 clean "Apps" style
   ════════════════════════════════════════════════════════ */
function ProjectCard({ project, index, onClick, isLaunching }) {
  const [showMenu, setShowMenu] = useState(false);
  const hasDeployment = !!project.deployUrl;
  const rawName = project.projectName || project.repoName || 'Untitled';
  const displayName = rawName
    .replace(/[-_]/g, ' ')
    .replace(/\b\w/g, l => l.toUpperCase())
    .slice(0, 50);
  const hash = getProjectHash(rawName);
  const emoji = PROJECT_EMOJIS[hash % PROJECT_EMOJIS.length];
  const bgColor = EMOJI_BG_COLORS[hash % EMOJI_BG_COLORS.length];

  return (
    <div className="group bg-white dark:bg-[#161b22] rounded-2xl border border-slate-200/80 dark:border-[#2d333b] p-5 hover:shadow-md dark:hover:shadow-black/20 hover:border-slate-300 dark:hover:border-[#444c56] transition-all duration-200 cursor-pointer"
      onClick={() => !isLaunching && onClick()}>
      {/* Top row: icon + name + menu */}
      <div className="flex items-start gap-3 mb-2.5">
        <div className={cn("w-10 h-10 rounded-xl flex items-center justify-center shrink-0", bgColor)}>
          <span className="text-base leading-none">{emoji}</span>
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between">
            <h3 className="text-[14px] font-semibold text-slate-900 dark:text-white truncate leading-tight">{displayName}</h3>
            <div className="relative shrink-0 ml-2" onClick={(e) => e.stopPropagation()}>
              <button onClick={() => setShowMenu(!showMenu)} className="p-1 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 rounded-lg hover:bg-slate-100 dark:hover:bg-white/[0.06] transition-colors opacity-0 group-hover:opacity-100">
                <MoreHorizontal className="w-4 h-4" />
              </button>
              {showMenu && (
                <>
                  <div className="fixed inset-0 z-30" onClick={() => setShowMenu(false)} />
                  <div className="absolute right-0 top-full mt-1 z-40 w-44 bg-white dark:bg-[#1c2128] rounded-xl border border-slate-200 dark:border-[#444c56] shadow-xl dark:shadow-black/50 overflow-hidden py-1 animate-scale-in">
                    <button onClick={() => { setShowMenu(false); onClick(); }} className="w-full flex items-center gap-2.5 px-3.5 py-2.5 text-[12px] font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04]">
                      <Play className="w-3.5 h-3.5" /> Open Workspace
                    </button>
                    {hasDeployment && (
                      <a href={project.deployUrl} target="_blank" rel="noopener noreferrer" className="w-full flex items-center gap-2.5 px-3.5 py-2.5 text-[12px] font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04]">
                        <Eye className="w-3.5 h-3.5" /> View Live
                      </a>
                    )}
                    <button className="w-full flex items-center gap-2.5 px-3.5 py-2.5 text-[12px] font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04]">
                      <Code2 className="w-3.5 h-3.5" /> Export Code
                    </button>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Description */}
      <p className="text-[12px] text-slate-500 dark:text-slate-400 line-clamp-2 leading-relaxed mb-3">
        {project.description || `An AI-generated application based on ${rawName.replace(/[-_]/g, ' ')}.`}
      </p>

      {/* Footer */}
      <div className="flex items-center gap-1.5 text-[11px] text-slate-400 dark:text-slate-500">
        <span>By {project.ownerEmail || 'you'}</span>
        <span className="mx-0.5">·</span>
        <span>Created {formatTime(project.createdAt || project.updatedAt)}</span>
      </div>
    </div>
  );
}

/* ════════════════════════════════════════════════════════
   GHOST CARD (+ New Project)
   ════════════════════════════════════════════════════════ */
function GhostCard({ onClick }) {
  return (
    <button
      onClick={onClick}
      className="group rounded-2xl border-2 border-dashed border-slate-200 dark:border-[#2d333b] hover:border-blue-300 dark:hover:border-blue-500/30 hover:bg-slate-50/50 dark:hover:bg-[#161b22]/50 transition-all duration-200 flex flex-col items-center justify-center min-h-[160px] p-5"
    >
      <div className="w-10 h-10 rounded-xl bg-slate-100 dark:bg-[#21262d] flex items-center justify-center mb-2 group-hover:bg-blue-100 dark:group-hover:bg-blue-500/20 transition-colors">
        <Plus className="w-5 h-5 text-slate-400 group-hover:text-blue-500 transition-colors" />
      </div>
      <span className="text-[13px] font-semibold text-slate-500 dark:text-slate-400 group-hover:text-blue-600 dark:group-hover:text-blue-400 transition-colors">
        New Project
      </span>
    </button>
  );
}

/* ════════════════════════════════════════════════════════
   ACTIVITY ROW
   ════════════════════════════════════════════════════════ */
function ActivityRow({ conversation, onClick }) {
  return (
    <button onClick={onClick} className="w-full flex items-center gap-3.5 px-4 py-3.5 hover:bg-slate-50 dark:hover:bg-white/[0.015] transition-colors group text-left">
      <div className={cn("w-2 h-2 rounded-full shrink-0", conversation.status === 'active' ? 'bg-blue-500' : conversation.status === 'completed' ? 'bg-emerald-500' : 'bg-slate-400')} />
      <div className="flex-1 min-w-0">
      <p className="text-[13px] font-semibold text-slate-700 dark:text-slate-200 truncate group-hover:text-slate-900 dark:group-hover:text-white transition-colors">
          {conversation.title || conversation.repo_name || 'Conversation'}
        </p>
        <div className="flex items-center gap-2.5 mt-0.5 text-[11px] text-slate-400 dark:text-slate-500">
          {conversation.repo_name && (
            <span className="flex items-center gap-1 truncate max-w-[200px]"><Github className="w-3 h-3 shrink-0" />{conversation.repo_name}</span>
          )}
          <span className="flex items-center gap-1 shrink-0"><Clock className="w-3 h-3" />{formatTime(conversation.updated_at)}</span>
        </div>
      </div>
      <ArrowRight className="w-3.5 h-3.5 text-slate-300 dark:text-slate-700 shrink-0 opacity-0 group-hover:opacity-100 group-hover:translate-x-0.5 transition-all" />
    </button>
  );
}

/* ════════════════════════════════════════════════════════
   HOW IT WORKS
   ════════════════════════════════════════════════════════ */
function HowItWorks() {
  const steps = [
    { n: '01', label: 'Describe your idea', desc: 'Type what you want in plain English', icon: Lightbulb, color: 'from-violet-500 to-indigo-500' },
    { n: '02', label: 'AI generates it', desc: 'Full-stack app built in minutes', icon: Sparkles, color: 'from-blue-500 to-cyan-500' },
    { n: '03', label: 'Deploy live', desc: 'Ship to production in one click', icon: Rocket, color: 'from-emerald-500 to-teal-500' },
  ];
  return (
    <div className="grid grid-cols-3 gap-4 stagger-children">
      {steps.map((s) => (
        <div key={s.n} className="relative p-6 rounded-2xl bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] text-center">
          <div className={`w-11 h-11 rounded-xl bg-gradient-to-br ${s.color} mx-auto mb-3 flex items-center justify-center shadow-md`}>
            <s.icon className="w-5 h-5 text-white" />
          </div>
          <span className="text-[10px] font-extrabold text-slate-300 dark:text-slate-600 uppercase tracking-widest">{s.n}</span>
          <h4 className="text-[14px] font-bold text-slate-800 dark:text-slate-100 mt-1">{s.label}</h4>
          <p className="text-[11px] text-slate-400 dark:text-slate-500 mt-1.5 leading-relaxed">{s.desc}</p>
        </div>
      ))}
    </div>
  );
}


/* ════════════════════════════════════════════════════════
   MAIN PAGE
   ════════════════════════════════════════════════════════ */
export default function EngineerDashboardPage() {
  const router = useRouter();
  const { setShowWizard } = useWizard();
  const promptRef = useRef(null);

  const [isLaunching, setIsLaunching] = useState(false);
  const [platformRepos, setPlatformRepos] = useState([]);
  const [platformLoading, setPlatformLoading] = useState(true);
  const [conversations, setConversations] = useState([]);
  const [convoLoading, setConvoLoading] = useState(true);
  const [user, setUser] = useState(null);
  const [promptText, setPromptText] = useState('');
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [showAttachMenu, setShowAttachMenu] = useState(false);
  const fileInputRef = useRef(null);
  const [advancedOpts, setAdvancedOpts] = useState({ stack: 'auto', backend: 'none', figmaUrl: '' });

  useEffect(() => {
    const supabase = getSupabaseBrowserClient();
    supabase.auth.getUser().then(({ data: { user: u } }) => { if (u) setUser(u); });
    fetch('/api/platform-repos').then(r => r.json()).then(d => setPlatformRepos(d.repos || [])).catch(() => setPlatformRepos([])).finally(() => setPlatformLoading(false));
    listConversations().then(data => setConversations(data || [])).catch(() => setConversations([])).finally(() => setConvoLoading(false));
    // Check for pre-filled template prompt
    try {
      const templatePrompt = sessionStorage.getItem('lucid_template_prompt');
      if (templatePrompt) {
        setPromptText(templatePrompt);
        sessionStorage.removeItem('lucid_template_prompt');
        setTimeout(() => promptRef.current?.focus(), 100);
      }
    } catch {}
  }, []);

  const totalProjects = platformRepos.length;
  const activeCount = conversations.filter(c => c.status === 'active').length;
  const deployedCount = platformRepos.filter(pr => pr.deployUrl).length;
  const thisMonthCount = (() => {
    const s = new Date(); s.setDate(1); s.setHours(0,0,0,0);
    return conversations.filter(c => new Date(c.created_at) >= s).length;
  })();

  /* ── Build from prompt ── */
  const handleBuildFromPrompt = async () => {
    const text = promptText.trim();
    if (!text) return;
    setIsLaunching(true);
    try {
      // Resolve stack synchronously from user selection (no API call)
      // 'auto' defaults to 'nextjs' — the backend Gemini research step
      // handles intelligent stack detection as part of the pipeline.
      const resolvedStack = advancedOpts.stack === 'auto' ? 'nextjs' : advancedOpts.stack;

      // Generate a stable UUID for this workspace — no DB round-trip needed
      const cid = crypto.randomUUID();

      // Store raw prompt in sessionStorage — the backend pipeline's
      // Gemini research phase replaces the old frontend prompt enhancement.
      try {
        sessionStorage.setItem(`wizard_prompt_${cid}`, text);
        sessionStorage.setItem(`wizard_meta_${cid}`, JSON.stringify({ stack: resolvedStack, projectType: null, backend: advancedOpts.backend, deployment: 'hosted', figmaUrl: advancedOpts.figmaUrl || '' }));
        sessionStorage.setItem(`wizard_desc_${cid}`, text);
      } catch {}

      // Navigate IMMEDIATELY — user enters workspace in < 300ms
      router.replace(`/dashboard/engineer/workspace/${cid}`);
    } catch (err) {
      console.error('[Dashboard] Build error:', err);
      setIsLaunching(false);
    }
  };

  const handleLaunchProject = (pr) => {
    if (!pr.projectId) return;
    setIsLaunching(true);
    router.push(`/dashboard/engineer/workspace/${pr.projectId}`);
  };

  const hasProjects = platformRepos.length > 0 || conversations.length > 0;
  const isLoaded = !platformLoading && !convoLoading;

  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  return (
    <div className="h-full bg-white dark:bg-[#0d1117] flex flex-col">
      <div className="flex-1 overflow-y-auto">

        {/* ═══════════════════════════════════════════
            HERO — Full-width gradient band
            ═══════════════════════════════════════════ */}
        <section className="relative bg-gradient-to-b from-slate-50 via-white to-white dark:from-[#161b22] dark:via-[#0d1117] dark:to-[#0d1117]">
          <div className="max-w-[860px] mx-auto px-8 lg:px-10 pt-16 pb-10">
            {/* Heading */}
            <div className="text-center mb-10">
              <h1 className="text-[38px] sm:text-[46px] font-extrabold text-slate-900 dark:text-white tracking-tight leading-[1.1]">
                What will you{' '}
                <span className="text-gradient-accent">build next</span>?
              </h1>
              <p className="text-[16px] text-slate-500 dark:text-slate-400 mt-4 max-w-lg mx-auto">
                Describe your app idea below or get inspired by our{' '}
                <button onClick={() => router.push('/dashboard/engineer/templates')} className="text-blue-600 dark:text-blue-400 underline underline-offset-2 hover:text-blue-700 transition-colors">templates</button>.
              </p>
            </div>

            {/* ── Big Prompt Box (Base44 style) ── */}
            <div className="relative bg-white dark:bg-[#161b22] rounded-2xl border border-slate-200 dark:border-[#2d333b] shadow-[0_2px_20px_rgba(0,0,0,0.04)] dark:shadow-[0_2px_20px_rgba(0,0,0,0.3)] focus-within:border-blue-300 dark:focus-within:border-blue-500/40 focus-within:shadow-[0_2px_24px_rgba(59,130,246,0.06)] transition-all duration-200 z-10">
              <textarea
                ref={promptRef}
                value={promptText}
                onChange={(e) => setPromptText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey && promptText.trim()) {
                    e.preventDefault();
                    handleBuildFromPrompt();
                  }
                }}
                rows={5}
                placeholder="Describe the app you want to create..."
                className="w-full px-6 pt-6 pb-3 text-[16px] leading-relaxed bg-transparent text-slate-800 dark:text-slate-200 placeholder:text-slate-400 dark:placeholder:text-slate-500 outline-none resize-none relative z-10"
              />

              {/* Bottom Toolbar */}
              <div className="flex items-center justify-between px-5 pb-4 pt-1 relative z-10">
                <div className="flex items-center gap-1">
                  {/* Attach button (+) */}
                  <div className="relative">
                    <button
                      onClick={() => { setShowAttachMenu(!showAttachMenu); setShowAdvanced(false); }}
                      type="button"
                      className="flex items-center justify-center w-8 h-8 text-slate-400 dark:text-slate-500 hover:text-slate-600 dark:hover:text-slate-300 hover:bg-slate-100 dark:hover:bg-white/[0.06] rounded-lg transition-colors border border-slate-200 dark:border-[#2d333b]"
                      title="Attach"
                    >
                      <Plus className="w-4 h-4" />
                    </button>
                    {showAttachMenu && (
                      <>
                        <div className="fixed inset-0 z-30" onClick={() => setShowAttachMenu(false)} />
                        <div className="absolute left-0 bottom-full mb-2 z-40 w-52 bg-white dark:bg-[#1c2128] rounded-xl border border-slate-200 dark:border-[#444c56] shadow-xl dark:shadow-black/50 overflow-hidden py-1 animate-scale-in">
                          <button
                            onClick={() => { setShowAttachMenu(false); fileInputRef.current?.click(); }}
                            className="w-full flex items-center gap-3 px-4 py-2.5 text-[13px] font-medium text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04] transition-colors"
                          >
                            <Paperclip className="w-4 h-4 text-slate-400" />
                            Attach
                          </button>
                          <button
                            onClick={() => { setShowAttachMenu(false); const url = prompt('Enter a URL to clone:'); if (url) setPromptText(`I need website like this: ${url}`); }}
                            className="w-full flex items-center gap-3 px-4 py-2.5 text-[13px] font-medium text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-white/[0.04] transition-colors"
                          >
                            <Link2 className="w-4 h-4 text-slate-400" />
                            Start from URL
                          </button>
                        </div>
                      </>
                    )}
                  </div>
                  <input ref={fileInputRef} type="file" accept="image/*,video/*" className="hidden" onChange={(e) => { const file = e.target.files?.[0]; if (file) setPromptText(prev => prev + `\n[Attached: ${file.name}]`); }} />
                  {/* Settings button */}
                  <button
                    onClick={() => { setShowAdvanced(!showAdvanced); setShowAttachMenu(false); }}
                    type="button"
                    className={cn(
                      "flex items-center justify-center w-8 h-8 hover:bg-slate-100 dark:hover:bg-white/[0.06] rounded-lg transition-colors border border-slate-200 dark:border-[#2d333b]",
                      showAdvanced ? "text-slate-700 dark:text-white bg-slate-100 dark:bg-white/[0.06]" : "text-slate-400 dark:text-slate-500 hover:text-slate-600 dark:hover:text-slate-300"
                    )}
                    title="Advanced settings"
                  >
                    <SlidersHorizontal className="w-4 h-4" />
                  </button>
                </div>

                <div className="flex items-center gap-3">
                  <button
                    onClick={() => setShowWizard(true)}
                    type="button"
                    className="text-[13px] font-semibold text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-white transition-colors"
                  >
                    Plan
                  </button>
                  <button
                    onClick={handleBuildFromPrompt}
                    type="button"
                    disabled={!promptText.trim() || isLaunching}
                    className={cn(
                      "flex items-center justify-center w-9 h-9 rounded-full transition-all duration-200 relative z-20",
                      promptText.trim() && !isLaunching
                        ? "bg-slate-900 dark:bg-white text-white dark:text-slate-900 hover:bg-slate-800 dark:hover:bg-slate-100 shadow-md active:scale-[0.95]"
                        : "bg-slate-200 dark:bg-[#21262d] text-slate-400 dark:text-slate-600 cursor-not-allowed"
                    )}
                  >
                    {isLaunching ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <ArrowRight className="w-4 h-4" />
                    )}
                  </button>
                </div>
              </div>

              {/* Advanced panel */}
              {showAdvanced && (
                <div className="mx-5 mb-4 p-4 bg-slate-50 dark:bg-[#0d1117] rounded-xl border border-slate-200 dark:border-[#21262d] animate-slide-up relative z-10">
                  <div className="grid grid-cols-3 gap-3">
                    <div>
                      <label className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1.5 block">Stack</label>
                      <CustomSelect
                        value={advancedOpts.stack}
                        onChange={(v) => setAdvancedOpts(p => ({ ...p, stack: v }))}
                        size="sm"
                        options={[
                          { value: 'auto', label: '✨ Auto-detect' },
                          { value: 'nextjs', label: 'Next.js' },
                          { value: 'react', label: 'React' },
                          { value: 'vue', label: 'Vue.js' },
                          { value: 'html-css', label: 'HTML & CSS' },
                        ]}
                      />
                    </div>
                    <div>
                      <label className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1.5 block">Backend</label>
                      <CustomSelect
                        value={advancedOpts.backend}
                        onChange={(v) => setAdvancedOpts(p => ({ ...p, backend: v }))}
                        size="sm"
                        options={[
                          { value: 'none', label: 'No backend' },
                          { value: 'supabase', label: 'Supabase' },
                          { value: 'own', label: 'Own backend (MCP)' },
                        ]}
                      />
                    </div>
                    <div>
                      <label className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1.5 block">Figma URL</label>
                      <input type="url" value={advancedOpts.figmaUrl} onChange={(e) => setAdvancedOpts(p => ({ ...p, figmaUrl: e.target.value }))}
                        placeholder="Optional..."
                        className="w-full px-3 py-2 text-[12px] bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] rounded-lg text-slate-700 dark:text-slate-300 placeholder:text-slate-400 outline-none focus:border-blue-400" />
                    </div>
                  </div>
                </div>
              )}
            </div>

            {/* Chips — Base44 style "What would you like to create?" */}
            <div className="mt-5">
              <p className="text-[13px] text-slate-500 dark:text-slate-400 mb-3">
                What would you like to create?
              </p>
              <div className="flex flex-wrap items-center gap-2">
                {IDEAS.map((chip) => (
                  <button key={chip.label} type="button" onClick={() => { setPromptText(chip.prompt); promptRef.current?.focus(); }}
                    className="px-4 py-2 text-[13px] font-medium text-slate-700 dark:text-slate-300 bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] rounded-full hover:border-slate-400 dark:hover:border-[#444c56] hover:shadow-sm transition-all">
                    {chip.label}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </section>

        {/* ═══════════════════════════════════════════
            MAIN CONTENT
            ═══════════════════════════════════════════ */}
        <div className="px-8 lg:px-10 py-8">

          {isLoaded && !hasProjects ? (
            /* ── EMPTY STATE ── */
            <div>
              <h3 className="text-[12px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-[0.12em] mb-5 text-center">
                Or start from a template
              </h3>
              <div className="grid grid-cols-3 sm:grid-cols-6 gap-3 mb-10 stagger-children">
                {IDEAS.map((chip) => (
                  <button key={chip.label} type="button" onClick={() => { setPromptText(chip.prompt); promptRef.current?.focus(); window.scrollTo({ top: 0, behavior: 'smooth' }); }}
                    className="group p-5 rounded-xl bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] hover:border-blue-300 dark:hover:border-blue-500/30 hover:shadow-lg transition-all text-center">
                    <span className="text-2xl mb-2 block">{chip.emoji}</span>
                    <span className="text-[11px] font-bold text-slate-600 dark:text-slate-300 group-hover:text-blue-600 dark:group-hover:text-blue-400 transition-colors">{chip.label}</span>
                  </button>
                ))}
              </div>
              <HowItWorks />
            </div>
          ) : (
            <>
              {/* Stats — inline */}
              <div className="flex items-center gap-8 mb-8 px-1">
                {[
                  { label: 'Projects', value: totalProjects, icon: Layers, color: 'text-indigo-500' },
                  { label: 'Active', value: activeCount, icon: Activity, color: 'text-emerald-500' },
                  { label: 'Deployed', value: deployedCount, icon: Globe, color: 'text-violet-500' },
                  { label: 'This Month', value: thisMonthCount, icon: BarChart3, color: 'text-amber-500' },
                ].map((s) => (
                  <div key={s.label} className="flex items-center gap-2.5">
                    <s.icon className={cn("w-4 h-4", s.color)} />
                    <span className="text-[20px] font-extrabold text-slate-800 dark:text-white leading-none">{platformLoading ? '·' : s.value}</span>
                    <span className="text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider">{s.label}</span>
                  </div>
                ))}
              </div>

              {/* Projects Grid */}
              <div className="mb-10">
                <div className="flex items-center justify-between mb-5">
                  <h2 className="text-[18px] font-extrabold text-slate-800 dark:text-white tracking-tight">Your Projects</h2>
                  {platformRepos.length > 8 && (
                    <button onClick={() => router.push('/dashboard/engineer/projects')} className="text-[12px] font-bold text-blue-500 hover:text-blue-700 dark:hover:text-blue-400 transition-colors">View all →</button>
                  )}
                </div>

                {platformLoading ? (
                  <div className="max-w-sm">
                    <div className="bg-white dark:bg-[#161b22] rounded-2xl border border-slate-200/80 dark:border-[#2d333b] p-5">
                      <div className="flex items-start gap-3 mb-2.5">
                        <div className="w-10 h-10 rounded-xl bg-slate-100 dark:bg-[#21262d] animate-pulse shrink-0" />
                        <div className="flex-1 space-y-2 pt-1">
                          <div className="h-4 bg-slate-100 dark:bg-[#21262d] rounded w-3/5 animate-pulse" />
                        </div>
                      </div>
                      <div className="space-y-2 mb-3">
                        <div className="h-3 bg-slate-100 dark:bg-[#21262d] rounded w-4/5 animate-pulse" />
                        <div className="h-3 bg-slate-100 dark:bg-[#21262d] rounded w-2/3 animate-pulse" />
                      </div>
                      <div className="h-3 bg-slate-100 dark:bg-[#21262d] rounded w-1/2 animate-pulse" />
                    </div>
                  </div>
                ) : (
                  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
                    {platformRepos.slice(0, 8).map((pr, idx) => (
                      <ProjectCard key={pr.projectId} project={pr} index={idx} onClick={() => handleLaunchProject(pr)} isLaunching={isLaunching} />
                    ))}
                    <GhostCard onClick={() => setShowWizard(true)} />
                  </div>
                )}
              </div>

              {/* Activity */}
              <div className="mb-8">
                <div className="flex items-center justify-between mb-4">
                  <h2 className="text-[18px] font-extrabold text-slate-800 dark:text-white tracking-tight">Recent Activity</h2>
                  <button onClick={() => router.push('/dashboard/engineer/conversations')} className="text-[12px] font-bold text-blue-500 hover:text-blue-700 dark:hover:text-blue-400 transition-colors">
                    All conversations →
                  </button>
                </div>
                <div className="bg-white dark:bg-[#161b22] rounded-2xl border border-slate-200 dark:border-[#2d333b] overflow-hidden">
                  {convoLoading ? (
                    <div className="flex items-center justify-center py-10"><Loader2 className="w-5 h-5 text-indigo-500 animate-spin" /></div>
                  ) : conversations.length === 0 ? (
                    <div className="flex flex-col items-center justify-center py-10 text-center">
                      <MessageSquare className="w-8 h-8 text-slate-200 dark:text-slate-700 mb-2" />
                      <p className="text-[13px] font-medium text-slate-400 dark:text-slate-500">No conversations yet</p>
                    </div>
                  ) : (
                    <div className="divide-y divide-slate-100 dark:divide-[#21262d]">
                      {conversations.slice(0, 8).map((conv) => (
                        <ActivityRow key={conv.id} conversation={conv} onClick={() => router.push(`/dashboard/engineer/workspace/${conv.id}`)} />
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
