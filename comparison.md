# Lucid AI vs Devin — Gap Analysis

Features Devin has that are not currently present in this project.
Items marked ⏳ are already on our roadmap (see `plan.md`); items marked ❌ are not planned yet.

---

## Agent Capabilities

| Feature | What Devin does | Our status |
|---------|----------------|-----------|
| **Web browsing** | Agent opens URLs, reads documentation, searches the web, and cites sources | ❌ Not planned |
| **Browser automation** | Agent clicks UI elements, fills forms, screenshots pages, and tests web apps in a real browser | ❌ Not planned |
| **Screenshot / design to code** | User pastes a Figma screenshot or image; agent implements the UI | ❌ Not planned |
| **Long-term memory** | Devin stores facts about the codebase, team conventions, and recurring errors across all sessions — recalled automatically on the next task | ❌ Not planned |
| **Knowledge base (per-repo instructions)** | Per-repo system prompt: team uploads architecture notes, coding standards, "never touch X file" rules; agent reads them before every task | ❌ Not planned |
| **Mid-task clarification** | Agent pauses and sends the user a question when it hits an ambiguity or blocker; user answers; agent continues | ❌ Not planned |
| **PR review** | Agent reads an open PR diff, leaves inline comments, and approves or requests changes | ❌ Not planned |
| **Token-by-token streaming** | LLM output streams word-by-word; user sees the agent "thinking" in real-time | ⏳ Planned (B5) |

---

## Session Management

| Feature | What Devin does | Our status |
|---------|----------------|-----------|
| **Session resume** | Browser refresh or disconnect does not lose the session; agent machine keeps running and reconnects | ⏳ Planned (B4) |
| **Machine snapshots** | Full environment state (installed packages, checked-out repo, env vars) is snapshot-saved between tasks; next task on the same repo resumes from the snapshot instead of cloning from scratch | ❌ Not planned |
| **Parallel sessions** | Multiple independent agent sessions run simultaneously on separate machines, each with its own sandbox | ❌ Not planned |
| **Pause and resume** | User can pause a running task (agent suspends), then resume from the same point later — distinct from stop (which discards state) | ❌ Not planned |

---

## Integrations

| Feature | What Devin does | Our status |
|---------|----------------|-----------|
| **Slack** | User sends tasks to Devin via Slack DM or channel mention; Devin posts progress updates and PR links back to the channel | ❌ Not planned |
| **Linear** | Devin picks up tasks from a Linear board, updates status as it works, links PR to the issue | ❌ Not planned |
| **Jira** | Same as Linear but for Jira boards | ❌ Not planned |
| **Notion** | Task picker from Notion databases; write PR link back to the Notion page | ⏳ Planned (Phase 2) |
| **GitHub Issues trigger** | Agent is automatically triggered when a new GitHub Issue is opened or a comment says `@devin` | ⏳ Planned (Phase 3, P6) |
| **Secret management vault** | Encrypted secrets (API keys, credentials) stored per-team; agent gets them injected as env vars without ever seeing them in plaintext in the chat | ❌ Not planned |
| **Deployment integrations** | Agent can deploy the finished code to Vercel, Railway, AWS, etc. as part of the task | ❌ Not planned |

---

## Infrastructure & Operations

| Feature | What Devin does | Our status |
|---------|----------------|-----------|
| **Usage / cost tracking** | Per-task compute cost tracked in ACUs (Agent Compute Units); visible in dashboard; budgets enforced | ⏳ Planned (Phase 3, P3) |
| **Rate limiting / quotas** | Per-user and per-organization limits on concurrent sessions and monthly usage | ⏳ Planned (Phase 3, P1) |
| **Redis session store** | Sessions survive server restarts; horizontal scaling across multiple ai_engine instances | ⏳ Planned (Phase 3, P5) |
| **Container auto-cleanup** | Idle sandbox containers are automatically destroyed after a configurable timeout | ⏳ Planned (Phase 3, P2) |

---

## Collaboration & UX

| Feature | What Devin does | Our status |
|---------|----------------|-----------|
| **Organization / team management** | Multiple users share one organization; admin assigns seats, views all sessions, controls access | ❌ Not planned |
| **Playbooks** | Reusable workflow templates: "whenever a bug issue is opened, clone repo → reproduce → fix → PR" defined once and triggered repeatedly | ❌ Not planned |
| **Scheduled runs** | Agent tasks run on a cron schedule without user input (e.g. "every Monday, run the test suite and summarize failures") | ❌ Not planned |
| **Notification system** | Email or Slack notification when a long-running task completes, fails, or needs the user's input | ❌ Not planned |
| **Session replay** | Full recording of every agent action — commands run, files changed, browser clicks — replayable step-by-step for audit or debugging | ❌ Not planned |
| **Stop agent button** | Button in the UI to interrupt the agent mid-task | ⏳ Planned (F6) |

---

## Summary

| | Count |
|--|--|
| Already on our roadmap (⏳) | 8 |
| Not yet planned (❌) | 16 |
| **Total gaps** | **24** |

The 5 highest-leverage gaps to close after Phase 1 is complete, ranked by user impact:

1. **Knowledge base** — every serious team has coding standards; without this the agent ignores them
2. **Mid-task clarification** — without this the agent silently guesses wrong instead of asking
3. **Machine snapshots** — re-cloning and reinstalling on every task is slow and expensive
4. **Slack integration** — where most engineering teams already live
5. **Secret management vault** — required before any production use; hardcoding tokens in the chat is a security risk
