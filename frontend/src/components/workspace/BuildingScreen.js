"use client";

// ─────────────────────────────────────────────────────────
//  BuildingScreen — full-panel loading state shown while
//  the workspace is initialising, cloning, or generating.
//  Used by ALL tabs (Preview / Dashboard / Code / Terminal).
//  Extracted from workspace/[projectId]/page.js
// ─────────────────────────────────────────────────────────

import {useState, useEffect} from "react";
import {computeBuildLabel} from "@/components/workspace/buildingLabel";

// ── Building Tips carousel ────────────────────────────────
const BUILDING_TIPS = [
  {
    icon: "🔗",
    text: "Connect WhatsApp, Calendar, Notion, or Slack by asking the chat",
  },
  {
    icon: "🖼️",
    text: "Upload a screenshot to show the AI your design inspiration",
  },
  {
    icon: "💬",
    text: "Use Discussion mode to brainstorm ideas without spending credits",
  },
  {
    icon: "⚡",
    text: "Ask the AI to add animations, dark mode, or mobile responsiveness",
  },
  {
    icon: "🔀",
    text: "Your code is pushed to a Git branch after every generation",
  },
  {
    icon: "🎨",
    text: "Paste a Figma link to let the AI match your exact design spec",
  },
];

function BuildingTips() {
  const [idx, setIdx] = useState(0);
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    const timer = setInterval(() => {
      setVisible(false);
      setTimeout(() => {
        setIdx((i) => (i + 1) % BUILDING_TIPS.length);
        setVisible(true);
      }, 300);
    }, 5000);
    return () => clearInterval(timer);
  }, []);

  const tip = BUILDING_TIPS[idx];
  return (
    <div className="relative z-10 text-center mt-8 px-4">
      <p className="text-[11px] uppercase font-bold tracking-widest text-slate-300 dark:text-slate-600 mb-3">
        Did you know?
      </p>
      <div
        className="flex items-start gap-2 justify-center max-w-xs mx-auto transition-all duration-300"
        style={{
          opacity: visible ? 1 : 0,
          transform: visible ? "translateY(0)" : "translateY(4px)",
        }}>
        <span className="text-base shrink-0 mt-0.5">{tip.icon}</span>
        <p className="text-[13px] text-slate-500 dark:text-slate-400 text-left leading-relaxed">
          {tip.text}
        </p>
      </div>
    </div>
  );
}

// ── Main component ────────────────────────────────────────
export default function BuildingScreen({
  status,
  phases,
  resolvingInfo,
  resolvingProgress,
  isWizardMode,
  convLoading,
  previewLoading = false,
  previewStatusMsg = "",
}) {
  const {label: buildLabel, subtext: buildSubtext} = computeBuildLabel({
    status,
    phases,
    resolvingInfo,
    isWizardMode,
    convLoading,
    previewLoading,
    previewStatusMsg,
  });

  return (
    <div className="h-full flex flex-col items-center justify-center relative overflow-hidden bg-white dark:bg-[#0d1117]">
      {/* Animated orbs — neutral blue/indigo tones for dark mode visibility */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div className="absolute top-[12%] left-[8%] w-80 h-80 bg-orange-300/15 dark:bg-orange-500/8 rounded-full blur-3xl animate-orb-1" />
        <div className="absolute top-[35%] right-[6%] w-64 h-64 bg-orange-200/15 dark:bg-orange-500/8 rounded-full blur-3xl animate-orb-2" />
        <div className="absolute bottom-[15%] left-[28%] w-72 h-72 bg-orange-200/15 dark:bg-orange-600/6 rounded-full blur-3xl animate-orb-3" />
      </div>
      <div className="absolute bottom-0 left-0 right-0 h-[40%] bg-gradient-to-t from-slate-50/40 via-slate-50/15 to-transparent dark:from-slate-900/20 dark:via-slate-900/5 dark:to-transparent pointer-events-none" />

      {/* Logo with pulse rings */}
      <div className="relative z-10 mb-6">
        <div
          className="absolute -inset-5 rounded-full bg-orange-400/8 dark:bg-orange-400/5 animate-pulse"
          style={{animationDuration: "3s"}}
        />
        <div
          className="absolute -inset-3 rounded-full bg-orange-400/12 dark:bg-orange-400/8 animate-pulse"
          style={{animationDuration: "2.2s", animationDelay: "0.4s"}}
        />
        <div className="absolute -inset-1.5 rounded-full bg-orange-400/20 dark:bg-orange-400/10" />
        <div className="relative w-20 h-20 rounded-full bg-gradient-to-br from-[#dc5426] to-orange-600 flex items-center justify-center shadow-xl shadow-orange-500/25">
          <svg width="40" height="40" viewBox="0 0 40 40" fill="none">
            <rect
              x="8"
              y="10"
              width="24"
              height="3"
              rx="1.5"
              fill="white"
              opacity="0.9"
            />
            <rect
              x="8"
              y="16"
              width="24"
              height="3"
              rx="1.5"
              fill="white"
              opacity="0.7"
            />
            <rect
              x="8"
              y="22"
              width="24"
              height="3"
              rx="1.5"
              fill="white"
              opacity="0.5"
            />
            <rect
              x="12"
              y="28"
              width="16"
              height="3"
              rx="1.5"
              fill="white"
              opacity="0.3"
            />
          </svg>
        </div>
      </div>

      <h2 className="relative z-10 text-xl font-semibold text-slate-700 dark:text-slate-300 mb-2 transition-all duration-500">
        {buildLabel}
      </h2>
      <p className="relative z-10 text-[13px] text-slate-400 dark:text-slate-500 max-w-sm text-center transition-all duration-300">
        {buildSubtext}
      </p>

      {/* Animated dots — visible until workspace is ready */}
      {status !== "ready" && (
        <div className="relative z-10 flex items-center gap-1.5 mt-5">
          {[0, 1, 2].map((i) => (
            <div
              key={i}
              className="w-1.5 h-1.5 rounded-full bg-orange-400/70"
              style={{
                animation: "dot-bounce 1.4s ease-in-out infinite",
                animationDelay: `${i * 0.22}s`,
              }}
            />
          ))}
        </div>
      )}

      <BuildingTips />
    </div>
  );
}
