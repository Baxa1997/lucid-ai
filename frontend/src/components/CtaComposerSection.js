'use client';

import { useState, useEffect, useRef } from 'react';
import { useRouter } from 'next/navigation';
import { getSupabaseBrowserClient } from '@/lib/supabase/client';

/* Repeated composer near the page bottom. Reuses the same sessionStorage
   handoff keys as the hero so a prompt entered here lands in the workspace
   identically — no extra wiring on the workspace side. */

const GEIST_FAMILY =
  "var(--font-geist), ui-sans-serif, system-ui, -apple-system, sans-serif";

export default function CtaComposerSection() {
  const router = useRouter();
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [promptText, setPromptText] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const textareaRef = useRef(null);

  useEffect(() => {
    const sb = getSupabaseBrowserClient();
    sb.auth.getSession().then(({ data: { session } }) => {
      setIsLoggedIn(!!session);
    });
  }, []);

  const submit = () => {
    const text = promptText.trim();
    if (!text || submitting) return;
    setSubmitting(true);
    try {
      sessionStorage.setItem('lucid_template_prompt', text);
      sessionStorage.setItem('lucid_hero_autostart', '1');
    } catch {}
    router.push(isLoggedIn ? '/dashboard' : '/login');
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey && promptText.trim()) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <section className="px-6 sm:px-10 py-24 lg:py-28 max-w-[1100px] mx-auto w-full">
      <div className="relative overflow-hidden rounded-[28px] border border-slate-200 dark:border-slate-800 bg-gradient-to-br from-[#A4CFD6] via-[#C2DDE0] to-[#E5EAE7] dark:from-[#0d2830] dark:via-[#0a1d24] dark:to-[#020617] p-10 sm:p-14">
        {/* Atmosphere */}
        <div
          aria-hidden
          className="absolute inset-0 pointer-events-none"
          style={{
            background:
              'radial-gradient(60% 50% at 50% 0%, rgba(255,255,255,.25), transparent 65%), radial-gradient(45% 40% at 12% 90%, rgba(232,90,44,.10), transparent 70%)',
          }}
        />

        <div className="relative text-center">
          <h2
            className="font-semibold text-[#15171C] dark:text-slate-50 mb-3"
            style={{
              fontFamily: GEIST_FAMILY,
              fontSize: 'clamp(32px, 4.6vw, 52px)',
              letterSpacing: '-0.035em',
              lineHeight: 1.05,
            }}>
            Ready when you are.
          </h2>
          <p className="text-[16px] sm:text-[17px] text-[#2A2D34]/80 dark:text-slate-300/90 max-w-[52ch] mx-auto mb-8 leading-relaxed">
            Drop a task here. We&apos;ll spin up a sandbox, write the code, run the tests, and open a PR on your repo.
          </p>

          <div className="mx-auto w-full max-w-[640px]">
            <div className="overflow-hidden rounded-[22px] border border-white/80 bg-white/90 backdrop-blur-[18px] shadow-[0_30px_60px_-28px_rgba(21,23,28,.30),0_12px_28px_-12px_rgba(21,23,28,.10)] focus-within:border-[#E85A2C]/45 dark:border-white/40 dark:bg-white">
              <div className="px-[20px] pt-[16px] pb-1">
                <label htmlFor="cta-prompt" className="sr-only">
                  Describe what to build
                </label>
                <textarea
                  id="cta-prompt"
                  ref={textareaRef}
                  value={promptText}
                  onChange={(e) => setPromptText(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="Add OAuth login to my Next.js app…"
                  rows={2}
                  className="m-0 w-full resize-none border-0 bg-transparent p-0 text-[16px] font-normal leading-[1.5] text-[#15171C] outline-none placeholder:text-[#ADB1BB]"
                  style={{ fontFamily: GEIST_FAMILY, letterSpacing: '-0.005em' }}
                />
              </div>
              <div className="flex items-center justify-between border-t border-[rgba(21,23,28,0.06)] px-[10px] py-[8px]">
                <span className="px-2 text-[11.5px] font-mono text-[#8B909B]">
                  Enter to send
                </span>
                <button
                  type="button"
                  onClick={submit}
                  disabled={!promptText.trim() || submitting}
                  className="inline-flex items-center gap-2 rounded-full bg-[#15243F] px-5 py-2 text-[14px] font-semibold text-white shadow-[0_4px_12px_-4px_rgba(13,27,46,.45)] transition-[background,opacity] hover:bg-[#1E3457] disabled:opacity-40 disabled:cursor-not-allowed">
                  {isLoggedIn ? 'Go to workspace' : 'Start building'}
                  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden>
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
      </div>
    </section>
  );
}
