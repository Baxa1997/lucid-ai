"use client";

/* ───────────────────────────────────────────────────────────────────────
   Top status rail — per Lucid AI Hero.html design.
   Glass translucent bar above the nav with system status + meta links.
   ─────────────────────────────────────────────────────────────────────── */

import Link from "next/link";

export default function StatusRail() {
  return (
    <div
      className="bg-transparent"
      style={{
        fontFamily:
          "var(--font-geist-mono), ui-monospace, monospace",
      }}>
      <div
        className="mx-auto flex max-w-[1400px] items-center justify-between gap-4 px-8 py-2 text-[10.5px] uppercase text-[#5C616C] dark:text-slate-400"
        style={{letterSpacing: "0.14em"}}>
        <div className="flex items-center gap-[18px]">
          <span className="inline-flex items-center gap-2">
            <span
              aria-hidden
              className="inline-block h-1.5 w-1.5 rounded-full"
              style={{
                background: "#5B5BD6",
                boxShadow: "0 0 0 3px rgba(91,91,214,0.2)",
              }}
            />
            All systems normal
          </span>
          <span className="hidden sm:inline text-[rgba(21,23,28,0.20)] dark:text-white/15">/</span>
          <span className="hidden sm:inline">v2.5.0 · Build 1042</span>
          <span className="hidden md:inline text-[rgba(21,23,28,0.20)] dark:text-white/15">/</span>
          <span className="hidden md:inline">Avg deploy 4m 38s</span>
        </div>
        <div className="flex items-center gap-[14px]">
          <Link
            href="/docs"
            className="text-[#5C616C] no-underline transition-colors hover:text-[#15171C] dark:text-slate-400 dark:hover:text-white">
            Docs
          </Link>
          <span className="hidden sm:inline">Status</span>
          <span className="hidden md:inline">EN · ES · PT</span>
        </div>
      </div>
    </div>
  );
}
