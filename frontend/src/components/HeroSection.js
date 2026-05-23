"use client";

import {useState, useEffect, useRef} from "react";
import Link from "next/link";
import {useRouter} from "next/navigation";
import {getSupabaseBrowserClient} from "@/lib/supabase/client";
import StatusRail from "@/components/StatusRail";

/* ───────────────────────────────────────────────────────────────────────
   Lucid AI Hero — port of Claude Design "Lucid AI Hero.html" (v2).
   Editorial layout · §/Workspace markers · serif-italic ember "ideas"
   in headline · glass composer (22px radius) · template chips · user FAB.
   The body gradient lives on the landing page wrapper.
   ─────────────────────────────────────────────────────────────────────── */

const TEMPLATES = [
  "Reporting Dashboard",
  "E-commerce Store",
  "SaaS Landing",
  "CRM Dashboard",
  "Admin Panel",
  "Room Visualizer",
  "Networking App",
];

const TEMPLATE_PROMPTS = {
  "Reporting Dashboard":
    "A modern reporting dashboard with KPI tiles, time-series charts, a filterable data table, and a slide-over filter panel. Clean sidebar navigation, dark mode support.",
  "E-commerce Store":
    "A modern e-commerce storefront with product catalog, search and filters, product detail pages, shopping cart, checkout flow, and user account area.",
  "SaaS Landing":
    "A SaaS landing page with hero section, feature grid, pricing table, social proof, FAQ accordion, and footer. Gradient hero, clear CTAs.",
  "CRM Dashboard":
    "A CRM dashboard with contact management, deal pipeline view (kanban), activity timeline, and analytics charts.",
  "Admin Panel":
    "A full-featured admin panel with users table, role-based access control, audit log, CRUD forms, analytics charts, and settings page.",
  "Room Visualizer":
    "A room visualizer where users upload a photo of their room and try out different furniture, paint colors, and decor styles with AI.",
  "Networking App":
    "A professional networking app with profiles, mutual-connection introductions, event-based matchmaking, and a chat thread for each connection.",
};

const GEIST_FAMILY =
  "var(--font-geist), ui-sans-serif, system-ui, -apple-system, sans-serif";
const GEIST_MONO_FAMILY = "var(--font-geist-mono), ui-monospace, monospace";
// Modern, serious geometric sans for the headline — matches Base44's
// editorial feel. Falls back through Inter / system sans for parity.
const HEADLINE_FAMILY =
  'var(--font-geist), "Inter", -apple-system, BlinkMacSystemFont, "Helvetica Neue", "Segoe UI", sans-serif';
const FUTURA_FAMILY =
  '"Futura", "Futura PT", "Trebuchet MS", "Century Gothic", ui-sans-serif, sans-serif';

// Trigger prompts cycled in the composer placeholder via a typewriter effect.
// Kept short so the line never wraps and feels like a suggestion, not noise.
const PLACEHOLDER_PROMPTS = [
  "Describe what you want, we'll turn it into an app",
  "Build a budget tracker with charts",
  "Make a CRM with a kanban pipeline",
  "Create a SaaS landing page",
  "Design an admin dashboard with auth",
];

export default function HeroSection() {
  const router = useRouter();
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [user, setUser] = useState(null);
  const [promptText, setPromptText] = useState("");
  const [planOn, setPlanOn] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [phIdx, setPhIdx] = useState(0);
  const [phText, setPhText] = useState("");
  const [phPhase, setPhPhase] = useState("typing"); // typing | holding | erasing
  const [inputFocused, setInputFocused] = useState(false);
  const textareaRef = useRef(null);
  const sendBtnRef = useRef(null);

  useEffect(() => {
    const sb = getSupabaseBrowserClient();
    sb.auth.getSession().then(({data: {session}}) => {
      setIsLoggedIn(!!session);
      setUser(session?.user || null);
    });
  }, []);

  // Typewriter for the empty-input placeholder. Pauses while the input is
  // focused or the user has typed anything.
  useEffect(() => {
    if (promptText || inputFocused) return;
    const current = PLACEHOLDER_PROMPTS[phIdx];

    if (phPhase === "typing") {
      if (phText.length < current.length) {
        const t = setTimeout(
          () => setPhText(current.slice(0, phText.length + 1)),
          38 + Math.random() * 32,
        );
        return () => clearTimeout(t);
      }
      const t = setTimeout(() => setPhPhase("holding"), 0);
      return () => clearTimeout(t);
    }

    if (phPhase === "holding") {
      const t = setTimeout(() => setPhPhase("erasing"), 1600);
      return () => clearTimeout(t);
    }

    // erasing
    if (phText.length > 0) {
      const t = setTimeout(() => setPhText(phText.slice(0, -1)), 22);
      return () => clearTimeout(t);
    }
    setPhIdx((i) => (i + 1) % PLACEHOLDER_PROMPTS.length);
    setPhPhase("typing");
  }, [phText, phPhase, phIdx, promptText]);

  const handleSubmit = () => {
    const text = promptText.trim();
    if (!text || submitting) return;
    setSubmitting(true);
    try {
      sessionStorage.setItem("lucid_template_prompt", text);
      sessionStorage.setItem("lucid_hero_autostart", "1");
      // Carry the Plan-first preference into the workspace flow so a future
      // backend handoff can read it. Dashboard doesn't consume this yet.
      sessionStorage.setItem("lucid_plan_mode", planOn ? "1" : "0");
    } catch {}
    router.push(isLoggedIn ? "/dashboard/engineer" : "/login");
  };

  const handleTemplate = (label) => {
    setPromptText(TEMPLATE_PROMPTS[label] || label);
    textareaRef.current?.focus();
  };

  const handleKeyDown = (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      sendBtnRef.current?.animate(
        [
          {transform: "scale(1)"},
          {transform: "scale(0.92)"},
          {transform: "scale(1)"},
        ],
        {duration: 260, easing: "cubic-bezier(.3,1.3,.4,1)"},
      );
      handleSubmit();
    } else if (e.key === "Enter" && !e.shiftKey && promptText.trim()) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const userInitial = (
    user?.user_metadata?.full_name ||
    user?.user_metadata?.name ||
    user?.email ||
    "U"
  )
    .trim()
    .charAt(0)
    .toUpperCase();

  const sendIdle = !promptText.trim();

  return (
    <section
      style={{fontFamily: GEIST_FAMILY}}
      className="lucid-hero-bg relative w-full min-h-screen overflow-hidden text-[#15171C] dark:text-slate-100">
      {/* Gradient lives on this section (lucid-hero-bg) and terminates at
          #FDFDFD / #020617 so the next section continues seamlessly. */}
      {/* Atmosphere layer (radial highlights + noise). Masked to fade out
          by ~50% so it never colors the bottom of the banner, which would
          otherwise create a visible band where the gradient hits white. */}
      <div
        aria-hidden
        className="absolute inset-0 pointer-events-none overflow-hidden"
        style={{
          maskImage:
            "linear-gradient(to bottom, #000 0%, #000 45%, transparent 75%)",
          WebkitMaskImage:
            "linear-gradient(to bottom, #000 0%, #000 45%, transparent 75%)",
        }}>
        <div
          className="absolute -inset-[10%]"
          style={{
            background:
              "radial-gradient(60% 40% at 50% 14%, rgba(255,255,255,.20), transparent 65%), radial-gradient(55% 45% at 86% 18%, rgba(120,170,180,.18), transparent 70%), radial-gradient(55% 45% at 14% 18%, rgba(100,160,175,.16), transparent 70%)",
          }}
        />
        <div
          className="absolute inset-0 opacity-[0.16] mix-blend-multiply"
          style={{
            backgroundImage:
              "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='240' height='240'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='1.4' numOctaves='2' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 0.09  0 0 0 0 0.08  0 0 0 0 0.07  0 0 0 0.5 0'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>\")",
          }}
        />
      </div>

      {/* pt-[104px] clears the fixed navbar (which now sits at top-4).
          StatusRail sits at the top of the banner (in flow) so it shares the
          gradient and scrolls naturally. */}
      <div className="relative pb-14 pt-[104px]">
        {/* <StatusRail /> */}
        {/* ── Hero text ── */}
        <section className="relative mx-auto flex max-w-[1180px] flex-col items-center px-8 pt-14 text-center">
          {/* §/Workspace markers (desktop only) */}
          {/* <span
            aria-hidden
            className="absolute left-8 top-6 hidden md:block text-[10.5px] uppercase text-[#8B909B] dark:text-slate-400"
            style={{
              fontFamily: GEIST_MONO_FAMILY,
              letterSpacing: "0.14em",
            }}>
            § 01 / Studio
          </span>
          <span
            aria-hidden
            className="absolute right-8 top-6 hidden md:block text-[10.5px] uppercase text-[#8B909B] dark:text-slate-400"
            style={{
              fontFamily: GEIST_MONO_FAMILY,
              letterSpacing: "0.14em",
            }}>
            Workspace · revision 2.5
          </span> */}

          <Link
            href="/pricing"
            className="group inline-flex items-center gap-3 rounded-full border border-white/70 bg-white/55 py-[5px] pr-4 pl-[5px] text-[13.5px] font-medium text-[#15171C] shadow-[0_1px_0_rgba(255,255,255,.7)_inset,0_6px_16px_-10px_rgba(21,23,28,.20)] backdrop-blur-[14px] backdrop-saturate-150 transition-transform hover:-translate-y-px dark:border-white/10 dark:bg-white/[0.06] dark:text-slate-100 dark:shadow-[0_1px_0_rgba(255,255,255,.08)_inset,0_6px_16px_-10px_rgba(0,0,0,.5)]"
            style={{letterSpacing: "-0.005em"}}>
            <span
              className="rounded-full px-[11px] py-[5px] text-[10.5px] font-bold uppercase tracking-[0.12em] text-white shadow-[0_1px_0_rgba(255,255,255,.3)_inset]"
              style={{
                background: "linear-gradient(180deg, #FF8456, #E85A2C)",
                fontFamily: GEIST_MONO_FAMILY,
              }}>
              New
            </span>
            Say hello to Lycid Review
            <svg
              className="opacity-55 transition-all group-hover:translate-x-[3px] group-hover:opacity-100"
              width="14"
              height="14"
              viewBox="0 0 14 14"
              fill="none"
              aria-hidden>
              <path
                d="M3 7h7m0 0L7 4m3 3l-3 3"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </Link>

          <h1
            className="mb-[22px] mt-8 font-[400] text-[#15171C] dark:text-slate-50 whitespace-normal md:whitespace-nowrap"
            style={{
              fontFamily: HEADLINE_FAMILY,
              fontSize: "clamp(54px, 7.8vw, 64px)",
              lineHeight: "1.02",
              letterSpacing: "-0.04em",
              textWrap: "balance",
            }}>
            Where ideas come to life
          </h1>

          <p
            className="m-0 max-w-[68ch] text-[18px] leading-[1.5] font-normal text-[#2A2D34]/85 dark:text-slate-300/90"
            style={{letterSpacing: "-0.01em", textWrap: "pretty"}}>
            LucidAI lets you build fully-functional apps in minutes with just
            your words.
            <br />
            <span className="block mt-1 font-medium text-[#15171C] dark:text-white">
              No coding necessary.
            </span>
          </p>
        </section>

        {/* ── Composer ── */}
        <div className="relative mx-auto mt-11 w-full max-w-[660px] px-6">
          <div className="overflow-hidden rounded-[22px] border border-white/80 bg-white/[0.78] shadow-[0_1px_0_rgba(255,255,255,.85)_inset,0_30px_60px_-28px_rgba(21,23,28,.30),0_12px_28px_-12px_rgba(21,23,28,.10)] backdrop-blur-[22px] backdrop-saturate-150 transition-[border-color,box-shadow] focus-within:border-[#E85A2C]/45 focus-within:shadow-[0_1px_0_rgba(255,255,255,.85)_inset,0_30px_60px_-28px_rgba(21,23,28,.30),0_12px_28px_-12px_rgba(21,23,28,.10),0_0_0_4px_rgba(232,90,44,.12)] dark:border-white/40 dark:bg-white dark:shadow-[0_1px_0_rgba(255,255,255,.85)_inset,0_30px_60px_-28px_rgba(0,0,0,.8),0_12px_28px_-12px_rgba(0,0,0,.6)]">
            <div className="relative px-[22px] pb-1 pt-[18px]">
              <label htmlFor="prompt" className="sr-only">
                Describe what to build
              </label>
              <textarea
                id="prompt"
                ref={textareaRef}
                value={promptText}
                onChange={(e) => setPromptText(e.target.value)}
                onKeyDown={handleKeyDown}
                onFocus={() => setInputFocused(true)}
                onBlur={() => setInputFocused(false)}
                aria-label="Describe what you want to build"
                placeholder=" "
                className="relative z-[1] m-0 min-h-[56px] w-full resize-none border-0 bg-transparent p-0 text-[16px] font-normal leading-[1.5] text-[#15171C] outline-none"
                style={{
                  fontFamily: GEIST_FAMILY,
                  letterSpacing: "-0.005em",
                }}
              />
              {!promptText && !inputFocused && (
                <div
                  aria-hidden
                  className="pointer-events-none absolute left-[22px] top-[18px] z-0 text-[16px] leading-[1.5] text-[#ADB1BB]"
                  style={{
                    fontFamily: GEIST_FAMILY,
                    letterSpacing: "-0.005em",
                  }}>
                  {phText}
                  <span className="ml-[1px] inline-block w-[1px] h-[1.15em] align-[-0.18em] bg-[#ADB1BB] animate-typewriter-caret" />
                </div>
              )}
            </div>

            <div className="mt-2 flex items-center justify-between gap-[10px] border-t border-[rgba(21,23,28,0.06)] px-[10px] pb-[10px] pt-2">
              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  aria-label="Attach (sign in to add files)"
                  title="Sign in to attach files"
                  onClick={() => textareaRef.current?.focus()}
                  className="inline-flex h-9 w-9 items-center justify-center rounded-[9px] border border-transparent bg-transparent text-[#2A2D34] transition-[background,border-color] hover:border-[#E5EAF0] hover:bg-[rgba(21,23,28,0.04)]">
                  <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
                    <path
                      d="M9 4v10M4 9h10"
                      stroke="currentColor"
                      strokeWidth="1.6"
                      strokeLinecap="round"
                    />
                  </svg>
                </button>
                <button
                  type="button"
                  aria-pressed={planOn}
                  onClick={() => setPlanOn((v) => !v)}
                  className="inline-flex cursor-pointer items-center gap-[10px] rounded-full border border-[rgba(21,23,28,0.08)] bg-white/55 py-[6px] pl-[6px] pr-[14px] text-[14px] font-medium text-[#2A2D34] transition-[background,border-color] hover:border-[rgba(21,23,28,0.14)] hover:bg-white/85"
                  style={{fontFamily: "inherit"}}>
                  <span
                    aria-hidden
                    className="relative h-[18px] w-[30px] rounded-full transition-colors"
                    style={{background: planOn ? "#E85A2C" : "#D0D6DE"}}>
                    <span
                      className="absolute top-[2px] h-[14px] w-[14px] rounded-full bg-white shadow-[0_1px_2px_rgba(0,0,0,.18)] transition-[left]"
                      style={{left: planOn ? "14px" : "2px"}}
                    />
                  </span>
                  Plan
                  <span
                    aria-hidden
                    className="grid h-4 w-4 place-items-center rounded-full border-[1.2px] border-[#8B909B] text-[10px] font-semibold text-[#8B909B]"
                    style={{fontFamily: GEIST_FAMILY}}>
                    i
                  </span>
                </button>
              </div>

              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  aria-label="Voice input (coming soon)"
                  title="Voice input — coming soon"
                  onClick={() => textareaRef.current?.focus()}
                  className="inline-flex h-9 w-9 items-center justify-center rounded-[9px] border border-transparent bg-transparent text-[#2A2D34] transition-[background,border-color] hover:border-[#E5EAF0] hover:bg-[rgba(21,23,28,0.04)]">
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                    <rect
                      x="6"
                      y="2"
                      width="4"
                      height="8"
                      rx="2"
                      stroke="currentColor"
                      strokeWidth="1.5"
                    />
                    <path
                      d="M3.5 7.5a4.5 4.5 0 009 0M8 12v2.2M5.8 14.2h4.4"
                      stroke="currentColor"
                      strokeWidth="1.5"
                      strokeLinecap="round"
                    />
                  </svg>
                </button>
                <button
                  ref={sendBtnRef}
                  type="button"
                  onClick={handleSubmit}
                  aria-label="Send prompt"
                  className="grid h-[42px] w-[42px] place-items-center rounded-full border-0 bg-[#E85A2C] text-white shadow-[0_1px_0_rgba(255,255,255,.3)_inset,0_10px_22px_-8px_rgba(232,90,44,.6)] transition-[background,transform,opacity] hover:bg-[#d8501f] active:translate-y-px"
                  style={{opacity: sendIdle ? 0.8 : 1}}>
                  <svg width="18" height="18" viewBox="0 0 16 16" fill="none">
                    <path
                      d="M8 13V3m0 0L4 7m4-4l4 4"
                      stroke="currentColor"
                      strokeWidth="1.9"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                </button>
              </div>
            </div>
          </div>
        </div>

        {/* ── Templates ── */}
        <section
          className="mx-auto mt-12 max-w-[880px] px-8 text-center"
          aria-label="Starter templates">
          <div
            className="mb-[18px] text-[11px] uppercase text-[#5C616C] dark:text-slate-400"
            style={{
              fontFamily: GEIST_MONO_FAMILY,
              letterSpacing: "0.2em",
            }}>
            Not sure where to start? Try one of these:
          </div>
          <div className="mx-auto flex max-w-[760px] flex-wrap justify-center gap-[10px]">
            {TEMPLATES.map((label) => (
              <button
                key={label}
                type="button"
                onClick={() => handleTemplate(label)}
                className="cursor-pointer rounded-full border border-[rgba(21,23,28,0.08)] bg-white/70 px-[22px] py-[11px] text-[14.5px] font-medium leading-none text-[#2A2D34] shadow-[0_1px_0_rgba(255,255,255,.7)_inset,0_4px_12px_-8px_rgba(21,23,28,.16)] backdrop-blur-[12px] backdrop-saturate-150 transition-[border-color,background,color,transform] hover:-translate-y-px hover:border-[rgba(232,90,44,0.4)] hover:bg-white/95 hover:text-[#6B2810] active:translate-y-0 dark:border-white/10 dark:bg-white/[0.05] dark:text-slate-200 dark:shadow-[0_1px_0_rgba(255,255,255,.04)_inset,0_4px_12px_-8px_rgba(0,0,0,.5)] dark:hover:border-[#FF7A4E]/40 dark:hover:bg-white/[0.1] dark:hover:text-[#FFA88C]"
                style={{
                  fontFamily: GEIST_FAMILY,
                  letterSpacing: "-0.005em",
                }}>
                {label}
              </button>
            ))}
          </div>
        </section>
      </div>

      {/* ── Floating account bubble (only when signed in) ── */}
      {isLoggedIn && (
        <Link
          href="/dashboard/engineer"
          aria-label="Your account"
          className="fixed bottom-6 left-6 z-30 grid h-11 w-11 place-items-center rounded-full border-2 border-white/70 bg-[#15171C] text-[16px] font-semibold text-[#FBFAF7] shadow-[0_10px_28px_-10px_rgba(21,23,28,.55)] dark:border-white/20 dark:bg-white dark:text-[#15171C] dark:shadow-[0_10px_28px_-10px_rgba(0,0,0,.7)]"
          style={{fontFamily: GEIST_FAMILY}}>
          {userInitial}
        </Link>
      )}
    </section>
  );
}
