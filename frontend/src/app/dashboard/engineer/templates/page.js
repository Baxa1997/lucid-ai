'use client';

// ─────────────────────────────────────────────────────────
//  Lucid AI — Templates Page (Base44-inspired)
//  Category-filtered grid of app templates with previews
// ─────────────────────────────────────────────────────────

import {
  Search, ArrowRight, Eye, Sparkles, Globe,
  BarChart3, ShoppingCart, FileText, Users, Briefcase,
  Layout, Palette, Database, Shield, Rocket,
  Zap, Heart, Star, Clock, Code2,
} from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { cn } from '@/lib/utils';

// ── Categories ──────────────────────────────────
const CATEGORIES = [
  'All',
  'Marketing & Sales',
  'Operations',
  'Data & Analytics',
  'Content Generation',
  'HR & Legal',
  'Finance',
  'Education',
  'Community',
];

// ── Template Data ───────────────────────────────
const TEMPLATES = [
  {
    id: 'crm-dashboard',
    name: 'CRM Dashboard',
    description: 'Complete customer relationship management with pipeline view, contact management, deal tracking, and analytics.',
    category: 'Marketing & Sales',
    tags: ['Marketing & Sales', 'Operations'],
    icon: BarChart3,
    color: 'from-blue-500 to-indigo-600',
    prompt: 'A modern CRM dashboard with contact management, deal pipeline view, activity tracking, and analytics charts. Include sidebar navigation, search functionality, and data tables. Professional dark/light theme with clean typography.',
    uses: 28512,
    author: 'Lucid AI',
    free: true,
  },
  {
    id: 'ecommerce-store',
    name: 'E-Commerce Store',
    description: 'Full-featured online store with product catalog, cart, checkout, user accounts, and order management.',
    category: 'Marketing & Sales',
    tags: ['Marketing & Sales'],
    icon: ShoppingCart,
    color: 'from-emerald-500 to-teal-600',
    prompt: 'An e-commerce storefront with product catalog grid, product detail pages, shopping cart, checkout flow with form validation, user accounts, and order history. Modern design with hero banner, category filtering, and responsive layout.',
    uses: 19847,
    author: 'Lucid AI',
    free: true,
  },
  {
    id: 'portfolio-website',
    name: 'Portfolio Website',
    description: 'Minimal personal portfolio with project showcase, about section, skills display, and contact form.',
    category: 'Content Generation',
    tags: ['Content Generation', 'Community'],
    icon: Palette,
    color: 'from-violet-500 to-purple-600',
    prompt: 'A personal portfolio website with animated hero section, project showcase grid with filtering, about me page with timeline, skills section with progress bars, testimonials, and contact form. Minimal, elegant design with smooth scroll animations and dark mode support.',
    uses: 15230,
    author: 'Lucid AI',
    free: true,
  },
  {
    id: 'saas-landing',
    name: 'SaaS Landing Page',
    description: 'Conversion-optimized landing page with hero, features, pricing, testimonials, and FAQ sections.',
    category: 'Marketing & Sales',
    tags: ['Marketing & Sales'],
    icon: Rocket,
    color: 'from-orange-500 to-rose-600',
    prompt: 'A SaaS product landing page with animated hero section, feature grid with icons, tiered pricing table, customer testimonials carousel, FAQ accordion, newsletter signup, and professional footer. Modern gradient design with strong CTAs and responsive layout.',
    uses: 22100,
    author: 'Lucid AI',
    free: true,
  },
  {
    id: 'blog-platform',
    name: 'Blog Platform',
    description: 'Content management system with article listing, editor, categories, comments, and author profiles.',
    category: 'Content Generation',
    tags: ['Content Generation', 'Education'],
    icon: FileText,
    color: 'from-amber-500 to-orange-600',
    prompt: 'A blog platform with article listing page, rich content display, categories and tags sidebar, comment system, author profiles, search functionality, and reading time estimates. Clean typography-focused design with serif headings and sans-serif body.',
    uses: 12450,
    author: 'Lucid AI',
    free: true,
  },
  {
    id: 'admin-panel',
    name: 'Admin Dashboard',
    description: 'Enterprise admin panel with data tables, CRUD operations, user management, and role-based access.',
    category: 'Operations',
    tags: ['Operations', 'Data & Analytics'],
    icon: Shield,
    color: 'from-slate-600 to-slate-800',
    prompt: 'A full-featured admin dashboard with data tables with sorting/filtering/pagination, CRUD forms with validation, user management panel, role-based access controls, charts and analytics widgets, activity logs, and settings page. Professional enterprise design with sidebar navigation.',
    uses: 31200,
    author: 'Lucid AI',
    free: true,
  },
  {
    id: 'project-management',
    name: 'Project Management',
    description: 'Task tracking tool with kanban boards, timelines, team management, and progress reporting.',
    category: 'Operations',
    tags: ['Operations', 'HR & Legal'],
    icon: Layout,
    color: 'from-cyan-500 to-blue-600',
    prompt: 'A project management application with kanban board view, list view, calendar view, task detail modals with subtasks and comments, team member assignment, project timelines, and progress charts. Clean modern design similar to Linear or Asana.',
    uses: 18700,
    author: 'Lucid AI',
    free: true,
  },
  {
    id: 'analytics-dashboard',
    name: 'Analytics Dashboard',
    description: 'Data visualization platform with interactive charts, KPI cards, date filters, and export options.',
    category: 'Data & Analytics',
    tags: ['Data & Analytics'],
    icon: BarChart3,
    color: 'from-indigo-500 to-blue-600',
    prompt: 'An analytics dashboard with KPI summary cards, interactive line/bar/pie charts, date range picker, data table with export functionality, real-time metrics, and comparison views. Professional dark theme with vibrant chart colors and responsive grid layout.',
    uses: 14300,
    author: 'Lucid AI',
    free: true,
  },
  {
    id: 'hr-portal',
    name: 'HR Portal',
    description: 'Human resources platform with employee directory, leave management, and performance tracking.',
    category: 'HR & Legal',
    tags: ['HR & Legal', 'Operations'],
    icon: Users,
    color: 'from-pink-500 to-rose-600',
    prompt: 'An HR management portal with employee directory, leave request and approval workflow, performance review forms, onboarding checklists, department org charts, and attendance tracking. Professional enterprise design with role-based views.',
    uses: 9800,
    author: 'Lucid AI',
    free: true,
  },
  {
    id: 'invoice-generator',
    name: 'Invoice Generator',
    description: 'Financial tool for creating, managing, and tracking invoices with client management.',
    category: 'Finance',
    tags: ['Finance', 'Operations'],
    icon: Briefcase,
    color: 'from-green-500 to-emerald-600',
    prompt: 'An invoice management application with invoice creation form with line items and tax calculations, client management, invoice status tracking (draft/sent/paid/overdue), PDF preview, payment history, and financial dashboard with revenue charts.',
    uses: 11500,
    author: 'Lucid AI',
    free: true,
  },
  {
    id: 'learning-platform',
    name: 'Learning Platform',
    description: 'Online education site with course catalog, video lessons, quizzes, and progress tracking.',
    category: 'Education',
    tags: ['Education', 'Community'],
    icon: Star,
    color: 'from-yellow-500 to-amber-600',
    prompt: 'An online learning platform with course catalog with category filtering, course detail pages with curriculum outline, video lesson player, quiz modules with scoring, student progress tracking, certificates, and instructor dashboard. Modern engaging design.',
    uses: 8900,
    author: 'Lucid AI',
    free: true,
  },
  {
    id: 'social-platform',
    name: 'Social Community',
    description: 'Community platform with user profiles, posts, comments, messaging, and notifications.',
    category: 'Community',
    tags: ['Community'],
    icon: Heart,
    color: 'from-rose-500 to-pink-600',
    prompt: 'A social community platform with user profiles, news feed with posts and comments, direct messaging, notification center, user search and follow system, content sharing, and trending topics. Modern social media design similar to Reddit or Discord.',
    uses: 7600,
    author: 'Lucid AI',
    free: true,
  },
];

/* ════════════════════════════════════════════════════════
   TEMPLATE CARD — Base44 style with large preview,
   title, author line, category tags
   ════════════════════════════════════════════════════════ */
function TemplateCard({ template, onUse }) {
  const Icon = template.icon;
  return (
    <div className="group cursor-pointer bg-white dark:bg-[#161b22] rounded-2xl border border-slate-200 dark:border-[#2d333b] overflow-hidden hover:shadow-lg dark:hover:shadow-black/30 hover:border-slate-300 dark:hover:border-[#444c56] transition-all duration-200">
      {/* Preview area */}
      <div className="relative h-[200px] overflow-hidden" onClick={onUse}>
        <div className={`absolute inset-0 bg-gradient-to-br ${template.color} opacity-90`} />
        {/* Grid pattern */}
        <div className="absolute inset-0 opacity-[0.08]" style={{
          backgroundImage: `linear-gradient(rgba(255,255,255,0.4) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.4) 1px, transparent 1px)`,
          backgroundSize: '24px 24px',
        }} />
        {/* Mock UI elements */}
        <div className="absolute inset-4 flex flex-col gap-2 opacity-60">
          <div className="flex gap-1.5">
            <div className="w-2 h-2 rounded-full bg-white/40" />
            <div className="w-2 h-2 rounded-full bg-white/40" />
            <div className="w-2 h-2 rounded-full bg-white/40" />
          </div>
          <div className="flex-1 flex gap-2">
            <div className="w-1/4 bg-white/10 rounded-lg" />
            <div className="flex-1 space-y-2">
              <div className="h-4 bg-white/15 rounded w-2/3" />
              <div className="h-3 bg-white/10 rounded w-1/2" />
              <div className="flex gap-2 mt-3">
                <div className="h-12 flex-1 bg-white/10 rounded-lg" />
                <div className="h-12 flex-1 bg-white/10 rounded-lg" />
                <div className="h-12 flex-1 bg-white/10 rounded-lg" />
              </div>
              <div className="h-16 bg-white/10 rounded-lg mt-2" />
            </div>
          </div>
        </div>
        {/* Center icon */}
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="w-14 h-14 rounded-2xl bg-white/20 backdrop-blur-sm flex items-center justify-center border border-white/20 shadow-lg opacity-0 group-hover:opacity-100 transition-opacity duration-300">
            <ArrowRight className="w-6 h-6 text-white" />
          </div>
        </div>
      </div>

      {/* Info */}
      <div className="p-4" onClick={onUse}>
        <div className="flex items-start justify-between mb-2">
          <h3 className="text-[15px] font-bold text-slate-900 dark:text-white leading-tight">{template.name}</h3>
          <span className="text-[11px] font-medium text-slate-500 dark:text-slate-400 shrink-0 ml-2">
            {template.free ? 'Free' : '$9.99'}
          </span>
        </div>
        <p className="text-[12px] text-slate-500 dark:text-slate-400 line-clamp-2 leading-relaxed mb-3">
          {template.description}
        </p>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-[11px] text-slate-400 dark:text-slate-500">
            <span className="font-medium">{template.author}</span>
            <span>•</span>
            <span className="flex items-center gap-1">
              <Eye className="w-3 h-3" />
              {template.uses.toLocaleString()}
            </span>
          </div>
        </div>
        {/* Tags */}
        <div className="flex flex-wrap gap-1.5 mt-3">
          {template.tags.map(tag => (
            <span key={tag} className="px-2 py-0.5 text-[10px] font-medium text-slate-500 dark:text-slate-400 bg-slate-100 dark:bg-[#21262d] rounded-md">
              {tag}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ════════════════════════════════════════════════════════
   TEMPLATES PAGE
   ════════════════════════════════════════════════════════ */
export default function TemplatesPage() {
  const router = useRouter();
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('All');

  const handleUseTemplate = (template) => {
    // Navigate to home with pre-filled prompt
    if (typeof window !== 'undefined') {
      sessionStorage.setItem('lucid_template_prompt', template.prompt);
    }
    router.push('/dashboard/engineer');
  };

  const filtered = TEMPLATES.filter(t => {
    if (search && !t.name.toLowerCase().includes(search.toLowerCase()) && !t.description.toLowerCase().includes(search.toLowerCase())) return false;
    if (category !== 'All' && !t.tags.includes(category)) return false;
    return true;
  });

  return (
    <div className="h-full bg-white dark:bg-[#0d1117] overflow-y-auto">
      <div className="px-8 lg:px-10 py-10">
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-[32px] font-extrabold text-slate-900 dark:text-white tracking-tight">App Templates</h1>
          <p className="text-[15px] text-slate-500 dark:text-slate-400 mt-2">
            Explore a curated collection of applications built by our community.
          </p>
        </div>

        {/* Search + Filters Bar */}
        <div className="flex items-center gap-4 mb-6">
          <div className="relative flex-1 max-w-sm">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
            <input
              value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder="Search apps"
              className="w-full pl-10 pr-4 py-2.5 text-[13px] bg-white dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] rounded-xl text-slate-700 dark:text-slate-300 placeholder:text-slate-400 outline-none focus:border-orange-300 dark:focus:border-orange-500/40 transition"
            />
          </div>
        </div>

        {/* Category Pills */}
        <div className="flex flex-wrap items-center gap-2 mb-8">
          {CATEGORIES.map(cat => (
            <button
              key={cat}
              onClick={() => setCategory(cat)}
              className={cn(
                "px-4 py-2 text-[13px] font-medium rounded-full border transition-all",
                category === cat
                  ? "bg-slate-900 dark:bg-white text-white dark:text-slate-900 border-slate-900 dark:border-white"
                  : "bg-white dark:bg-[#161b22] text-slate-600 dark:text-slate-400 border-slate-200 dark:border-[#2d333b] hover:border-slate-400 dark:hover:border-[#444c56]"
              )}
            >
              {cat}
            </button>
          ))}
        </div>

        {/* Grid */}
        {filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <Sparkles className="w-10 h-10 text-slate-200 dark:text-slate-700 mb-3" />
            <h3 className="text-[16px] font-bold text-slate-800 dark:text-white mb-1">No matching templates</h3>
            <p className="text-[13px] text-slate-400 dark:text-slate-500">Try a different search or category filter.</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
            {filtered.map(t => (
              <TemplateCard key={t.id} template={t} onUse={() => handleUseTemplate(t)} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
