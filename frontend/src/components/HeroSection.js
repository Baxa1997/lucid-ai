"use client";

import {useState, useEffect, useRef} from "react";
import {ChevronRight, Terminal, Zap} from "lucide-react";
import Link from "next/link";
import {getSupabaseBrowserClient} from "@/lib/supabase/client";

// ── Timing constants ──────────────────────────────────────────────
const LINE_DELAY = 190; // ms between each line appearing
const CHAT_DELAY = 700; // ms pause after last line before AI reacts
const THINKING_DUR = 1100; // ms of thinking dots
const BUTTON_DELAY = 650; // ms between message → button appear
const PAUSE_DUR = 3800; // ms at final state before reset
const FADE_DUR = 550; // ms fade-out before hard reset

// ── Code data ─────────────────────────────────────────────────────
const CODE_LINES = [
  {
    parts: [
      {t: "async function ", c: "text-violet-400"},
      {t: "validateSession", c: "text-blue-400"},
      {t: "(id: ", c: "text-slate-300"},
      {t: "string", c: "text-orange-400"},
      {t: ") {", c: "text-slate-300"},
    ],
  },
  {
    pad: 16,
    parts: [
      {t: "const ", c: "text-violet-400"},
      {t: "user ", c: "text-slate-200"},
      {t: "= ", c: "text-slate-400"},
      {t: "await ", c: "text-violet-400"},
      {t: "db.find", c: "text-slate-200"},
      {t: "(id);", c: "text-slate-400"},
    ],
  },
  {parts: []},
  {
    pad: 16,
    parts: [
      {t: "// Lucid AI is refactoring this block", c: "text-slate-500 italic"},
    ],
  },
  {
    pad: 16,
    hl: true,
    parts: [
      {t: "if ", c: "text-violet-400"},
      {t: "(!user.isActive)", c: "text-indigo-300"},
      {t: " {", c: "text-slate-300"},
    ],
  },
  {
    pad: 32,
    hl: true,
    parts: [
      {t: "throw new ", c: "text-violet-400"},
      {t: "AuthError", c: "text-amber-400"},
      {t: "(", c: "text-slate-400"},
      {t: "'Account locked'", c: "text-orange-400"},
      {t: ");", c: "text-slate-400"},
    ],
  },
  {
    pad: 16,
    hl: true,
    parts: [{t: "}", c: "text-slate-300"}],
  },
  {
    pad: 16,
    parts: [
      {t: "return ", c: "text-violet-400"},
      {t: "user.token;", c: "text-slate-200"},
    ],
  },
  {parts: [{t: "}", c: "text-slate-300"}]},
];

// ── Blinking cursor ───────────────────────────────────────────────
function useCursor() {
  const [on, setOn] = useState(true);
  useEffect(() => {
    const id = setInterval(() => setOn((v) => !v), 530);
    return () => clearInterval(id);
  }, []);
  return on;
}

// ── Animated IDE mockup ───────────────────────────────────────────
function AnimatedIDE() {
  const [count, setCount] = useState(0);
  // phase: 'coding' | 'thinking' | 'message' | 'button'
  const [phase, setPhase] = useState("coding");
  const [fading, setFading] = useState(false);
  const cursorOn = useCursor();
  const t = useRef(null);
  const clear = () => clearTimeout(t.current);

  useEffect(() => {
    clear();
    if (fading) return; // fade-out timer runs independently

    if (phase === "coding") {
      if (count < CODE_LINES.length) {
        t.current = setTimeout(() => setCount((n) => n + 1), LINE_DELAY);
      } else {
        t.current = setTimeout(() => setPhase("thinking"), CHAT_DELAY);
      }
    } else if (phase === "thinking") {
      t.current = setTimeout(() => setPhase("message"), THINKING_DUR);
    } else if (phase === "message") {
      t.current = setTimeout(() => setPhase("button"), BUTTON_DELAY);
    } else if (phase === "button") {
      t.current = setTimeout(() => {
        setFading(true);
        // After fade-out completes, hard-reset state
        setTimeout(() => {
          setCount(0);
          setPhase("coding");
          setFading(false);
        }, FADE_DUR);
      }, PAUSE_DUR);
    }
    return clear;
  }, [phase, count, fading]);

  const coding = phase === "coding";
  const showBubble =
    phase === "thinking" || phase === "message" || phase === "button";
  const showMsg = phase === "message" || phase === "button";
  const showBtn = phase === "button";

  return (
    <div className="bg-[#1a1b26] rounded-xl shadow-[0_30px_60px_-12px_rgba(0,0,0,0.3)] border border-slate-800/50 overflow-hidden ring-1 ring-white/10 relative group select-none">
      {/* hover sheen */}
      <div className="absolute inset-0 bg-gradient-to-tr from-white/5 to-transparent opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none" />

      {/* title bar */}
      <div className="bg-[#16161e] border-b border-[#0d0d12] px-4 py-3 flex items-center gap-4">
        <div className="flex gap-1.5 shrink-0">
          <div className="w-3 h-3 rounded-full bg-[#ff5f57]" />
          <div className="w-3 h-3 rounded-full bg-[#febc2e]" />
          <div className="w-3 h-3 rounded-full bg-[#28c840]" />
        </div>
        <div className="flex-1 flex justify-center">
          <div className="bg-[#1a1b26] border border-white/5 rounded px-3 py-1 text-[11px] text-slate-500 font-medium max-w-[240px] w-full text-center">
            app.lucid.ai/projects/auth-handler
          </div>
        </div>
        <div className="w-[52px] shrink-0" />
      </div>

      {/* content — fade-out wrapper */}
      <div
        className="flex h-[450px]"
        style={{
          opacity: fading ? 0 : 1,
          transition: fading
            ? `opacity ${FADE_DUR}ms cubic-bezier(0.4,0,0.2,1)`
            : "none",
        }}>
        {/* ── Code pane ── */}
        <div className="flex-1 p-6 font-mono text-[13px] overflow-hidden border-r border-white/5 bg-[#1a1b26]">
          <div className="flex items-center gap-2 mb-6">
            <Terminal className="w-3.5 h-3.5 text-violet-400" />
            <span className="text-xs text-slate-300 font-medium">
              AuthMiddleware.ts
            </span>
          </div>

          <div className="space-y-[7px] leading-[1.55]">
            {CODE_LINES.slice(0, count).map((line, idx) => {
              const isActive = idx === count - 1 && coding;
              const inner = (
                <>
                  <span className="text-slate-600 w-6 select-none text-right pr-4 text-[11px]">
                    {idx + 1}
                  </span>
                  <div style={{paddingLeft: line.pad || 0}}>
                    {line.parts.map((p, i) => (
                      <span key={i} className={p.c}>
                        {p.t}
                      </span>
                    ))}
                    {/* blinking cursor on active line */}
                    {isActive && (
                      <span
                        className="inline-block w-[2px] h-[13px] bg-slate-300 ml-px align-middle"
                        style={{
                          opacity: cursorOn ? 1 : 0,
                          transition: "opacity 0.1s",
                        }}
                      />
                    )}
                  </div>
                </>
              );

              return line.hl ? (
                <div
                  key={idx}
                  className="relative"
                  style={{
                    animation: "lineIn 0.28s cubic-bezier(0.4,0,0.2,1) both",
                  }}>
                  <div className="absolute inset-0 bg-violet-500/10 -ml-12 w-[calc(100%+3rem)] border-l-2 border-violet-500/70" />
                  <div className="relative flex z-10">{inner}</div>
                </div>
              ) : (
                <div
                  key={idx}
                  className="flex"
                  style={{
                    animation: "lineIn 0.28s cubic-bezier(0.4,0,0.2,1) both",
                  }}>
                  {inner}
                </div>
              );
            })}

            {/* idle cursor after all lines typed */}
            {!coding && (
              <div
                className="flex"
                style={{animation: "lineIn 0.2s ease both"}}>
                <span className="text-slate-600 w-6 select-none text-right pr-4 text-[11px]">
                  {CODE_LINES.length + 1}
                </span>
                <span
                  className="inline-block w-[2px] h-[13px] bg-slate-300 align-middle"
                  style={{
                    opacity: cursorOn ? 1 : 0,
                    transition: "opacity 0.1s",
                  }}
                />
              </div>
            )}
          </div>
        </div>

        {/* ── AI pane ── */}
        <div className="w-[40%] bg-[#16161e] flex flex-col border-l border-white/5">
          {/* header */}
          <div className="h-10 border-b border-white/5 flex items-center px-4 gap-2 text-[10px] font-bold text-slate-400 bg-[#1a1b26] uppercase tracking-wider">
            <div className="w-4 h-4 bg-[#dc5426] rounded-[3px] flex items-center justify-center text-white">
              <Zap className="w-2.5 h-2.5 fill-current" />
            </div>
            AI Assistant
          </div>

          {/* chat */}
          <div className="flex-1 p-4 space-y-4 overflow-hidden">
            {/* AI bubble */}
            {showBubble && (
              <div
                key="bubble"
                className="bg-[#232433] p-3 rounded-lg border border-white/5 shadow-sm relative"
                style={{
                  animation: "fadeUp 0.35s cubic-bezier(0.4,0,0.2,1) both",
                }}>
                <div className="absolute -left-1.5 top-3 w-3 h-3 bg-[#232433] border-l border-b border-white/5 rotate-45" />

                {/* dots layer */}
                <div
                  style={{
                    opacity: showMsg ? 0 : 1,
                    transition: "opacity 0.25s ease",
                    position: showMsg ? "absolute" : "relative",
                    pointerEvents: "none",
                  }}>
                  <div className="flex gap-1.5 items-center py-0.5">
                    {[0, 160, 320].map((d) => (
                      <div
                        key={d}
                        className="w-1.5 h-1.5 rounded-full bg-slate-500"
                        style={{
                          animation: `dotBounce 0.9s ${d}ms ease-in-out infinite`,
                        }}
                      />
                    ))}
                  </div>
                </div>

                {/* message layer */}
                <div
                  style={{
                    opacity: showMsg ? 1 : 0,
                    transition: "opacity 0.35s ease",
                    transitionDelay: showMsg ? "0.1s" : "0s",
                  }}>
                  <p className="text-[12px] text-slate-300 leading-relaxed font-medium">
                    I&apos;ve identified a potential security flaw in the
                    session handler. Should I implement the fix?
                  </p>
                </div>
              </div>
            )}

            {/* confirm button */}
            {showBtn && (
              <div
                key="btn"
                className="flex justify-end"
                style={{
                  animation: "fadeUp 0.35s cubic-bezier(0.4,0,0.2,1) both",
                }}>
                <button className="bg-gradient-to-r from-[#dc5426] to-orange-500 text-white text-[12px] font-bold px-4 py-2 rounded-md shadow-lg shadow-orange-900/20">
                  Yes, proceed.
                </button>
              </div>
            )}
          </div>

          {/* input */}
          <div className="p-4 pt-2">
            <div className="bg-[#1a1b26] border border-white/10 rounded-lg p-2.5 flex items-center">
              <span className="text-[12px] text-slate-600 font-medium px-1 select-none">
                Type a command...
              </span>
            </div>
          </div>
        </div>
      </div>

      <style>{`
        @keyframes lineIn {
          from { opacity: 0; transform: translateY(5px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        @keyframes fadeUp {
          from { opacity: 0; transform: translateY(8px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        @keyframes dotBounce {
          0%, 80%, 100% { transform: translateY(0); }
          40%           { transform: translateY(-5px); }
        }
      `}</style>
    </div>
  );
}

// ── Page hero ─────────────────────────────────────────────────────
export default function HeroSection() {
  const [isLoggedIn, setIsLoggedIn] = useState(false);

  useEffect(() => {
    const sb = getSupabaseBrowserClient();
    sb.auth.getSession().then(({data: {session}}) => setIsLoggedIn(!!session));
  }, []);

  return (
    <section className="flex flex-col lg:flex-row items-center justify-center gap-12 lg:gap-20 px-6 sm:px-10 pt-24 pb-12 lg:pt-32 lg:pb-24 max-w-[1400px] mx-auto w-full">
      {/* LEFT */}
      <div className="flex-1 max-w-xl self-center">
        {/* badge */}
        <div className="inline-flex items-center gap-2 px-2.5 py-1 rounded-full bg-[#1e293b] text-slate-300 text-[11px] font-medium mb-6 -mt-8 cursor-pointer hover:bg-[#334155] transition-colors shadow-sm w-fit border border-slate-700/50">
          <span className="bg-[#0f172a] text-orange-400 px-1.5 py-0.5 rounded text-[10px] font-bold tracking-wide border border-slate-700">
            New
          </span>
          <span className="text-white">Introducing Lucid Review</span>
          <ChevronRight className="w-3 h-3 text-slate-500" />
        </div>

        <h1 className="text-4xl sm:text-5xl lg:text-[3rem] font-bold tracking-tight text-slate-900 dark:text-slate-100 leading-[1.1] mb-6">
          {/* <span className="text-transparent bg-clip-text bg-gradient-to-r from-[#dc5426] to-orange-500 dark:from-[#dc5426] dark:to-orange-400">
            LucidAI
          </span> */}
          The AI Software engineer
        </h1>

        <p className="text-lg text-slate-500 dark:text-slate-400 leading-relaxed mb-8 max-w-lg font-medium">
          An autonomous engineering partner that understands your codebase,
          builds features, and fixes bugs.
        </p>

        <div className="flex items-center gap-4 mb-10">
          <Link
            href={isLoggedIn ? "/dashboard/engineer" : "/login"}
            className="bg-gradient-to-r from-[#dc5426] to-orange-500 text-white text-[15px] font-semibold px-8 py-3.5 rounded-lg shadow-lg shadow-orange-500/25 hover:shadow-orange-600/40 hover:-translate-y-0.5 transition-all duration-200 inline-block text-center">
            {isLoggedIn ? "Go to Dashboard" : "Get Started"}
          </Link>
          <button className="bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-200 text-[15px] font-bold px-8 py-3.5 rounded-lg border border-slate-200 dark:border-slate-700 hover:border-slate-300 dark:hover:border-slate-600 hover:bg-slate-50 dark:hover:bg-slate-700 transition-all shadow-sm">
            Book a Demo
          </button>
        </div>

        {/* steps */}
        <div className="flex flex-col gap-2 w-full max-w-lg">
          <div className="flex items-center gap-4 p-4 rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 shadow-md transform hover:-translate-y-0.5 transition-all cursor-pointer relative overflow-hidden group">
            <div className="absolute left-0 top-0 bottom-0 w-1 bg-[#dc5426]" />
            <div className="w-6 h-6 rounded-lg bg-slate-900 dark:bg-white text-white dark:text-slate-900 flex items-center justify-center text-sm font-bold shadow-sm shrink-0">
              1
            </div>
            <div className="flex flex-col">
              <span className="text-slate-900 dark:text-slate-100 font-bold text-[15px]">
                Planning
              </span>
              <span className="text-slate-500 dark:text-slate-400 text-[13px]">
                Plan your roadmap and architecture
              </span>
            </div>
            <ChevronRight className="w-4 h-4 text-slate-300 dark:text-slate-600 ml-auto" />
          </div>

          {[
            {
              n: 2,
              title: "Professional Documentation",
              sub: "Generate enterprise-grade docs",
            },
            {
              n: 3,
              title: "Integrations",
              sub: "Connect with GitHub, Linear & Slack",
            },
          ].map(({n, title, sub}) => (
            <div
              key={n}
              className="flex items-center gap-4 p-4 rounded-xl border border-transparent hover:bg-slate-50 dark:hover:bg-slate-800/50 transition-all cursor-pointer group">
              <div className="w-6 h-6 rounded-lg bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400 flex items-center justify-center text-sm font-bold shrink-0 group-hover:bg-white dark:group-hover:bg-slate-700 group-hover:shadow-sm transition-all border border-transparent group-hover:border-slate-200 dark:group-hover:border-slate-600">
                {n}
              </div>
              <div className="flex flex-col">
                <span className="text-slate-600 dark:text-slate-300 font-bold text-[15px] group-hover:text-slate-900 dark:group-hover:text-white transition-colors">
                  {title}
                </span>
                <span className="text-slate-400 dark:text-slate-500 text-[13px] group-hover:text-slate-500 dark:group-hover:text-slate-400 transition-colors">
                  {sub}
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* RIGHT — animated IDE */}
      <div className="flex-1 w-full max-w-2xl">
        <AnimatedIDE />
      </div>
    </section>
  );
}
