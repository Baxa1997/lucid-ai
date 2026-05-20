# Demo Readiness Report — 2026-05-20

**Scope:** 4 of 6 originally-spec'd generation tests (websites skipped per user direction). Goal: identify blockers before live customer demo.

**Tests run:**
- L1 — MailFlow (AI email marketing landing)
- L2 — Brooklyn dental clinic landing
- A1 — Fitness studio admin
- A2 — Medical clinic admin (interrupted at tenant provisioning)

**Tests skipped (per user):** W1 (Italian restaurant multi-page), W2 (real estate multi-page) — required `WEBSITE_PIPELINE_V2_ENABLED=true` which is off in host env.

---

## Section A — Cost Summary

Anthropic credits not directly tracked in pipeline logs; estimates are derived from wall-clock time and pipeline complexity (no per-call cost extraction available).

| Test | Wall clock | Est. Anthropic spend | Notes |
|---|---|---|---|
| L1 (MailFlow landing) | 6 m 20 s | ~$0.75 | Single landing, per-section parallel codegen |
| L2 (Dental landing) | 8 m 8 s | ~$1.00 | Single landing, 7 sections incl. SmileGallery |
| A1 (Fitness admin) | **35 m 25 s** ⚠️ | ~$2.50 | Legacy admin pipeline; Claude API timed out twice; retry with compact mode |
| A2 (Medical admin, attempt 1) | hung at gate, killed | ~$0.30 | Plan generated then auto-confirm key mismatch — fixed harness |
| A2 (Medical admin, attempt 2) | killed at ~7 m | ~$0.50 | V2 ran Stages 0.5→4 cleanly, blocked at Stage 4.6 tenant provisioning |
| **Total** | **~57 min** | **~$5.05** | Well under $15 budget |

Gemini cost (research, planning) is separate; ~$0.10–0.20 per admin per the `admin_data_model_planner` logs (~$0.02 per Gemini call, multiple per pipeline).

---

## Section B — Pass/Fail Per Test

### L1 — MailFlow AI email marketing landing

| Check | Result |
|---|---|
| Pipeline completed | ✅ Yes (`landing_pipeline`, 7 sections) |
| Plan card emitted | ✅ "I'll build **MailFlow** — AI-driven email marketing…" |
| Errors during run | 0 |
| Warnings during run | 1 ("Some issues remain — preview it and let me know what to fix.") |
| `npm install` + `next build` | ✅ Compiled successfully, 4 static pages, 125 kB First Load JS |
| `'use client'` boundaries | ✅ 7/7 sections client; page.jsx + layout.jsx server |
| Domain match | ✅ HeroAi, IntegrationsGrid, WorkflowDemo, PricingTiers, WallOfLove — all tech/SaaS-appropriate |
| Tailwind tokens | ✅ Uses `bg-background`, `text-foreground`, etc. |
| `landing.json` content store | ✅ Sections read from `@/content/landing.json` |
| **Quality rating** | **9/10** — sophisticated hero with typewriter, polished interactions |

**Workspace:** `generated_projects/demo_L1_mailflow/`

### L2 — Brooklyn dental clinic landing

| Check | Result |
|---|---|
| Pipeline completed | ✅ Yes (`landing_pipeline`, 7 sections) |
| Plan card emitted | ✅ "I'll build **Brooklyn Mint Dental** — Modern Dentistry with a Wellness Soul…" |
| Errors during run | 0 |
| Warnings during run | 1 (same template warning) |
| `npm install` + `next build` | ✅ Compiled successfully, 4 static pages, 120 kB First Load JS |
| Domain match | ✅ Hero, Philosophy, ServicesGrid, PressMentions, SmileGallery, MeetTheDoctors, Booking — all dental-appropriate |
| Brand differentiation from L1 | ✅ Sections, naming, palette all differ from MailFlow |
| **Quality rating** | **9/10** — domain-specific section names, dentist-oriented copy |

**Workspace:** `generated_projects/demo_L2_dental/`

### A1 — Fitness studio admin

| Check | Result |
|---|---|
| Pipeline completed | ✅ Yes (eventually) — **legacy pipeline**, NOT admin_pipeline_v2 |
| Plan card emitted | ⚠️ Generic — "I'll build **Admin** using the **'Iron Corner'** design system" (no fitness-specific brand) |
| `plan_pages_count` in card | **0** ⚠️ |
| `plan_entities_count` in card | **0** ⚠️ |
| Errors during run | **2 × Claude API timed out** ⚠️ |
| Warnings during run | 1 ("AI provider is slow — continuing with template foundation") |
| `pnpm install` + `vite build` | ✅ 2017 modules, 614 kB minified, 192 kB gzipped (passes but no code splitting) |
| Domain match | ❌ **Only `users` feature folder generated** — none of the prompted entities (members, classes, instructors, payments) |
| Quality score (pipeline-reported) | 84/100 (B+) — penalised on animations 10/100 |
| **Quality rating** | **4/10** — builds, but content does NOT match prompt |

**Workspace:** `generated_projects/demo_A1_fitness/`

### A2 — Medical clinic admin

| Check | Result |
|---|---|
| Pipeline completed | ❌ No (interrupted) |
| V2 pipeline reached | ✅ Stages 0.5, 1, 2, 3, 4.5, 4 all completed cleanly |
| Stage 2 research | ✅ 11 entity-research sources + 7 ops-research sources via parallel Gemini grounded calls |
| Stage 3 visual DNA | ✅ `primary_color=#007bff, voice=professional, intensity=calm, density=spacious, motif=minimal` (medical-blue) |
| Stage 4.5 data model | ✅ **6 tables: patients, providers, locations, appointment_types, appointments, insurances** — exact domain match |
| Stage 4 plan card | ✅ Emitted with 6 entities, 24 pages, 7 nav items |
| Stage 4.6 tenant provisioning | ❌ **FAILED: `tenant_schema is None`** — requires real Supabase, test env has fake creds |
| Fell through to legacy pipeline | ⚠️ Yes (then killed by me at ~7 min to avoid another 30-min legacy run) |
| **Quality rating (V2 portion)** | **9/10 up to Stage 4.6** — research + data model is excellent |
| **Quality rating (E2E)** | **0/10** — never produced a working app |

**Workspace:** `generated_projects/demo_A2_medical_INCOMPLETE.txt` (template-only)

---

## Section C — Patterns Across Tests

1. **Landing pipeline is mature and fast.** Both landings completed in 6–8 min, emit a proper plan card with branded intro, generate domain-appropriate section names ("SmileGallery" for dentist vs. "IntegrationsGrid" for SaaS), and produce buildable Next.js apps with proper server/client boundaries.

2. **Admin V2 pipeline (Stages 0.5 → 4) is *excellent* — but its Stage 4.6 blocks on real Supabase.** When A2's V2 ran, it produced a domain-perfect 6-table medical data model in ~70 seconds with real grounded research (18 sources). Brand extraction yielded medical-blue `#007bff` with `professional/calm/spacious` voice. Stage 4.6 (`tenant_schema`) is the only gap; everything before it is high-quality.

3. **Legacy admin pipeline is a poor fallback for domain-specific prompts.** A1's legacy run generated a generic React scaffold with a single `users` feature folder and ignored the actual prompted entities (members, classes, instructors, payments). It produced 88 files but didn't solve the user's problem.

4. **Claude API instability shows up only on the long admin runs.** L1 / L2 never timed out. A1 (legacy, one large Claude call for all admin code) timed out twice on Phase 1, retried with compact mode, and took 35 min total. This is inherent to the legacy pipeline's "one giant call" pattern.

5. **Auto-confirm key mismatch in V2.** My test harness initially used `ws-{id}` keys (website pipeline pattern); admin V2 keys by `project_id` (the slug). Fix was trivial (grab any pending key on a timer), but this would affect any automated test of V2 admin.

6. **Plan-card UI works for landings but didn't fully render entity/page metadata for the legacy admin path.** L1/L2 show pages & description; A1's card shows `pages=0, entities=0` because the legacy pipeline doesn't populate those fields.

7. **Build validator in the pipeline can't find `pnpm` on the host.** Both landing runs logged `BuildValidator: install failed: [Errno 2] No such file or directory: 'pnpm'`, even though pnpm IS installed at `/opt/homebrew/opt/node@22/bin/pnpm`. PATH propagation issue. Pipeline still completed; just skipped its own build check.

---

## Section D — Demo Readiness Assessment

| Product type | Recommendation | Reasoning |
|---|---|---|
| **Landing pages** | ✅ **SAFE TO DEMO LIVE** | Both L1 and L2 completed cleanly in 6–8 min, produced polished sections, built without issue. Domain-specific section naming. Low risk. |
| **Multi-page websites** | ❓ **UNTESTED — DO NOT DEMO LIVE** | `WEBSITE_PIPELINE_V2_ENABLED` is off in host env (skipped per user). If V2 is off in the live demo container too, you'd get the same legacy issues as A1. Verify container env first. |
| **Admin panels** | ⚠️ **DEMO WITH PRE-GENERATED EXAMPLES ONLY** | V2 needs real Supabase tenant provisioning to complete. Legacy fallback produces generic scaffolds that don't match prompts. 35-min wall clock with timeouts is unacceptable for a live demo. |

---

## Section E — Specific Blockers Identified

### Critical (would block / embarrass during live demo)

1. **Admin V2 Stage 4.6 fails without real Supabase.** `tenant_schema is None` → V2 falls through to legacy, which then produces generic output and takes 30+ min. Fix: ensure the demo Supabase project is reachable, RLS service role key works, and tenant provisioning has been smoke-tested end-to-end against the live demo container. **ETA to fix: 1–2 h to verify, longer if RLS policies need adjusting.**

2. **Legacy admin pipeline produces generic scaffolds.** A1's prompt asked for "members, class schedules, instructors, and membership payments" — the legacy pipeline generated a single `users` feature. If V2 isn't reaching codegen, the customer will see a useless admin. **Fix: same as #1 (get V2 end-to-end working).**

3. **35-min admin runs with Claude API timeouts.** Even when legacy admin "works," the wall clock is far too long for a live demo. **Fix: must demo admin via pre-generated example, not live.**

4. **Auto-confirm key drift between pipelines.** Admin V2 keys by `project_id`; landing keys by `ws-{id}`. Affects any internal test automation. Production WebSocket flow likely fine (ws.py forwards correctly), but worth a single integration test before demo.

### Major (would degrade demo quality)

5. **Build validator can't find pnpm.** Pipeline's in-loop build check is silently skipped. Pre-built apps still compile manually. **Fix: a one-line PATH inject in the pipeline's subprocess.run() call, or document the gap.**

6. **L1 / L2 emit "Some issues remain — preview it and let me know what to fix"** as a final warning. Code builds fine; the warning is from the quality gate flagging cosmetic issues (37 hardcoded color signals on L1). **Fix: tune the quality gate to not surface this when build passes, OR rephrase the warning to be more confidence-inspiring.**

7. **A1 quality score 84/100 dragged down by animations 10/100.** Generated admin has no framer-motion, only 1 loading state. Not a blocker but visually flat.

### Minor (worth noting, not blocking)

8. **Vite build emits 614 kB chunk warning.** No code splitting in admin template. Not a runtime issue.

9. **Supabase async client deprecation warnings.** `'timeout'` and `'verify'` parameters deprecated. Cosmetic.

---

## Section F — Recommended Demo Strategy

### What to demo LIVE

1. **One landing page generation from a fresh prompt.** Pick a vertical the user already showed interest in (or "modern landing page for [their company]"). Expected: 6–10 min, plan card → confirm → polished landing. Have a backup pre-generated landing in case Claude is flaky.

2. **The plan-card / confirm step is itself a feature** — show the user what the AI plans to build before it builds. Both landings demonstrate this cleanly.

### What to show as PRE-GENERATED examples

1. **All admin panel work.** Use `generated_projects/demo_A1_fitness/` or any prior V2-completed admin. Open it locally, walk through entity pages, table layouts, RLS-aware patterns. **Do NOT regenerate live.**

2. **One multi-page website example** (if you have one from a prior run). Acknowledge that V2 website is a feature-flag rollout and not yet in customer's stack.

### What to AVOID

1. Regenerating an admin live (35-min worst case with timeouts is fatal in a demo room).
2. Demoing the V2 website pipeline without first verifying `WEBSITE_PIPELINE_V2_ENABLED=true` in the actual demo-time container env.
3. Showing the "Some issues remain" warning in a wide shot — narrate over it.

### What to tell the customer before the demo

> "Today I'll show you the landing-page flow end-to-end live — that's the most mature pipeline. For admin dashboards, I'll walk you through a recent generation showing the data-model intelligence (research from real sources, domain-specific tables), then we can talk about timelines for getting your team into the live admin generator."

### Pre-demo checklist (1 hour before)

- [ ] Verify `ADMIN_PIPELINE_V2_ENABLED=true` in demo container (✅ confirmed in our docker-compose)
- [ ] Verify Supabase tenant provisioning works end-to-end in demo container (`pipeline_tenant` Stage 4.6 must succeed) — **OPEN ITEM**
- [ ] Verify `WEBSITE_PIPELINE_V2_ENABLED=true` if you plan to demo websites — **CURRENTLY OFF in container**
- [ ] Rehearse landing prompt with the actual customer name pre-typed
- [ ] Have `generated_projects/demo_L1_mailflow/` open in a second window as fallback
- [ ] Confirm Anthropic API quota healthy (status.anthropic.com)

---

## Artifacts saved locally

```
generated_projects/
├── demo_L1_mailflow/              # 69 files, builds (next build ✓)
├── demo_L2_dental/                # 69 files, builds (next build ✓)
├── demo_A1_fitness/               # 88 files, builds (vite build ✓), generic
└── demo_A2_medical_INCOMPLETE.txt # V2 blocked at Stage 4.6

/tmp/
├── demo_L1/, demo_L2/, demo_A1/   # summary.json + events.json + files.txt each
└── demo_L1_stdout.log, demo_L2_stdout.log, demo_A1_stdout.log, demo_A2_stdout.log
```

## What was NOT tested (gaps for follow-up)

- Multi-page website pipeline (V2 flag off in host env)
- Live frontend → WebSocket → backend path (tests run on host, not via ws.py)
- Real Supabase tenant provisioning (Stage 4.6) — would require valid SUPABASE_SERVICE_KEY pointing at a writable project
- Image binding / Unsplash pool usage (no errors seen but not deeply inspected)
- Mobile / responsive rendering (only build verification, no visual inspection)
- WebSocket disconnect recovery during a long admin run
