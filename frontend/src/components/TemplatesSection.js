"use client";

import {useRouter} from "next/navigation";
import {ArrowRight} from "lucide-react";
import {cn} from "@/lib/utils";

const TEMPLATE_CARDS = [
  {
    label: "Reporting Dashboard",
    blurb: "KPI tiles, time-series charts, a filterable data table.",
    accent: "from-sky-400/30 to-blue-500/20",
    badge: "Dashboards",
    prompt:
      "A modern reporting dashboard with KPI tiles, time-series charts, a filterable data table, and a slide-over filter panel. Clean sidebar navigation, dark mode support.",
  },
  {
    label: "E-commerce Store",
    blurb: "Catalog, cart, checkout, account area. Production-ready.",
    accent: "from-amber-400/30 to-orange-500/20",
    badge: "Commerce",
    prompt:
      "A modern e-commerce storefront with product catalog, search and filters, product detail pages, shopping cart, checkout flow, and user account area. Hero banner and category grid on the homepage.",
  },
  {
    label: "SaaS Landing",
    blurb: "Hero, features, pricing, FAQ — conversion-optimized.",
    accent: "from-violet-400/30 to-fuchsia-500/20",
    badge: "Marketing",
    prompt:
      "A SaaS landing page with hero section, feature grid, pricing table, social proof, FAQ accordion, and footer. Gradient hero, clear CTAs, conversion-optimized.",
  },
  {
    label: "CRM Dashboard",
    blurb: "Contacts, deals pipeline, activity timeline, analytics.",
    accent: "from-emerald-400/30 to-teal-500/20",
    badge: "Internal tools",
    prompt:
      "A CRM dashboard with contact management, deal pipeline view (kanban), activity timeline, and analytics charts. Professional design with sidebar navigation.",
  },
  {
    label: "Portfolio Website",
    blurb: "Hero, project showcase, about, contact. Minimal & fast.",
    accent: "from-rose-400/30 to-pink-500/20",
    badge: "Personal",
    prompt:
      "A personal portfolio website with hero section, project showcase grid, about me page, skills section, and contact form. Minimal, elegant design with dark mode.",
  },
  {
    label: "Admin Panel",
    blurb: "Users table, RBAC, audit log, CRUD forms, settings.",
    accent: "from-indigo-400/30 to-blue-500/20",
    badge: "Internal tools",
    prompt:
      "A full-featured admin panel with users table, role-based access control, audit log, CRUD forms, analytics charts, and settings page. Enterprise design.",
  },
];

function MockChrome({accent, label}) {
  return (
    <div
      className={cn(
        "relative w-full aspect-[16/10] rounded-2xl overflow-hidden border border-white/60 dark:border-white/10 bg-gradient-to-br",
        accent,
      )}>
      {/* Window chrome */}
      <div className="absolute inset-x-0 top-0 h-7 bg-white/70 dark:bg-slate-900/70 backdrop-blur-md border-b border-white/40 dark:border-white/5 flex items-center px-3 gap-1.5">
        <span className="w-2 h-2 rounded-full bg-[#ff5f57]" />
        <span className="w-2 h-2 rounded-full bg-[#febc2e]" />
        <span className="w-2 h-2 rounded-full bg-[#28c840]" />
        <span className="ml-2 text-[10px] font-medium text-slate-500 dark:text-slate-400 truncate">
          {label.toLowerCase().replace(/\s+/g, "-")}.lucid.app
        </span>
      </div>
      {/* Fake content */}
      <div className="absolute inset-x-3 bottom-3 top-10 flex gap-2">
        <div className="w-[26%] rounded-md bg-white/60 dark:bg-white/[0.06] backdrop-blur-sm flex flex-col gap-1.5 p-2">
          <div className="h-2.5 rounded bg-slate-300/70 dark:bg-white/10" />
          <div className="h-2 rounded bg-slate-300/40 dark:bg-white/5 w-3/4" />
          <div className="h-2 rounded bg-slate-300/40 dark:bg-white/5 w-2/3" />
          <div className="h-2 rounded bg-slate-300/40 dark:bg-white/5 w-1/2" />
          <div className="h-2 rounded bg-slate-300/40 dark:bg-white/5 w-3/5" />
        </div>
        <div className="flex-1 flex flex-col gap-2">
          <div className="grid grid-cols-3 gap-2">
            <div className="h-10 rounded-md bg-white/60 dark:bg-white/[0.06] backdrop-blur-sm" />
            <div className="h-10 rounded-md bg-white/60 dark:bg-white/[0.06] backdrop-blur-sm" />
            <div className="h-10 rounded-md bg-white/60 dark:bg-white/[0.06] backdrop-blur-sm" />
          </div>
          <div className="flex-1 rounded-md bg-white/60 dark:bg-white/[0.06] backdrop-blur-sm" />
        </div>
      </div>
    </div>
  );
}

export default function TemplatesSection() {
  const router = useRouter();

  const handleUseTemplate = (prompt) => {
    try {
      sessionStorage.setItem("lucid_template_prompt", prompt);
      sessionStorage.setItem("lucid_hero_autostart", "1");
    } catch {}
    router.push("/dashboard");
  };

  return (
    <section id="templates" className="relative w-full py-20 lg:py-28 px-6 sm:px-8">
      <div className="max-w-[1200px] mx-auto">
        {/* Header */}
        <div className="text-center mb-14 max-w-2xl mx-auto">
          <span className="inline-block text-[11px] uppercase tracking-[0.18em] font-semibold text-[#dc5426] mb-3">
            Templates
          </span>
          <h2 className="text-[36px] sm:text-[44px] lg:text-[52px] font-normal text-slate-900 dark:text-slate-100 tracking-[-0.04em] leading-[1.1] mb-4">
            Start from a template
          </h2>
          <p className="text-[16px] text-slate-500 dark:text-slate-400 leading-relaxed font-medium">
            Pick a starting point. Lucid customizes it to your idea — content,
            styling, and integrations included.
          </p>
        </div>

        {/* Cards */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
          {TEMPLATE_CARDS.map((tpl) => (
            <button
              key={tpl.label}
              type="button"
              onClick={() => handleUseTemplate(tpl.prompt)}
              className="group text-left bg-white dark:bg-[#161b22] rounded-2xl border border-slate-200/80 dark:border-[#2d333b] p-4 hover:border-slate-300 dark:hover:border-[#444c56] hover:shadow-lg dark:hover:shadow-black/30 transition-all duration-200">
              <MockChrome accent={tpl.accent} label={tpl.label} />
              <div className="flex items-start justify-between gap-3 mt-4">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 mb-1.5">
                    <span className="text-[10px] uppercase tracking-wider font-semibold text-slate-400 dark:text-slate-500">
                      {tpl.badge}
                    </span>
                  </div>
                  <h3 className="text-[15px] font-semibold text-slate-900 dark:text-white leading-tight">
                    {tpl.label}
                  </h3>
                  <p className="text-[12.5px] text-slate-500 dark:text-slate-400 leading-relaxed mt-1">
                    {tpl.blurb}
                  </p>
                </div>
                <div className="shrink-0 w-9 h-9 rounded-full bg-slate-100 dark:bg-white/[0.06] group-hover:bg-[#dc5426] flex items-center justify-center transition-colors">
                  <ArrowRight className="w-4 h-4 text-slate-500 dark:text-slate-400 group-hover:text-white transition-colors group-hover:translate-x-0.5 duration-200" />
                </div>
              </div>
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}
