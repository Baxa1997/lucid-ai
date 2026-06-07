// Shared status-label computation for the BuildingScreen + ChatPanel status
// bar. Both surfaces show during the same flow (BuildingScreen on the right,
// status pill above the chat input on the left), so they MUST agree on what
// the agent is doing.
//
// ─── Architecture (Phase 2 honest-labels refactor) ──────────────────────
//
//   label = f(state, phases, previewStatusMsg, projectIntakeStatus)
//
// The backend remains the source of truth once a task starts. Before that,
// the client-side intake gate is authoritative about whether the builder is
// still waiting for a complete project description:
//   • status            ← workspace_state / status events
//   • phases            ← task_phase events  (title/description verbatim)
//   • previewStatusMsg  ← preview_status events  (verbatim subtitle)
//   • projectIntakeStatus ← client intent gate (checking / clarifying / handoff)
//
// The function NEVER:
//   • anticipates a future event (no "Analyzing your request…" while the
//     workspace is just cloning the template)
//   • derives a label from sessionStorage / isWizardMode / route data
//   • uses isNewGeneration to lie about what the agent is doing
//
// `isWizardMode` is still accepted as a prop for backwards compatibility
// with two callers (BuildingScreen, ChatPanel) but only affects the IDLE
// label (showing "Loading conversation" for existing workspaces) — every
// other branch ignores it.
//
// ─── Why this matters ───────────────────────────────────────────────────
//
// Before: wizard-mode + state=ready showed "Analyzing your request…", the
// preview cloning showed "Building your app…", and Phase 1/2 collapsed to
// "Analyzing…" even though the workspace was just installing deps. When
// the agent rejected a gibberish prompt with a clarify question, the
// panel kept claiming to analyse for ~3 more seconds while the user
// stared at a UI that had no idea the agent was idle.
//
// After: every label says what the backend is actually doing. State=ready
// → "Workspace ready". State=cloning → "Cloning template". phase 5 active
// → use the phase title. Preview cloning → "Setting up workspace". No
// pre-empting, no lies.

export function computeBuildLabel({
  status,
  phases = [],
  resolvingInfo,
  isWizardMode,
  convLoading,
  previewLoading = false,
  previewStatusMsg = "",
  // Structured background-preview stage from the backend's preview_status
  // event (msg.status). Preferred over `previewStatusMsg` for label
  // derivation — string-matching the message text was the source of the
  // "Building your app" lie (it triggered on any message containing
  // "clone", regardless of intent).
  previewStage = "",
  // Client-side new-project intake gate. This deliberately overrides the
  // connected workspace state because no build may start until the user has
  // supplied a complete project description or the backend has acknowledged it.
  projectIntakeStatus = null,
  // Canonical current status emitted by the prompt router / pipeline.
  // When present, this is rendered verbatim and wins over all inferred state.
  agentStatus = null,
  // Phase 2 Step 2 — backend-declared phase list (preferred chart shape).
  // Currently not consulted here (phase title drives the label already),
  // but kept as a prop for future use.
  declaredPipeline = null,
}) {
  const activePhase = phases.find((p) => p.status === "active");
  const maxDonePhase = phases
    .filter((p) => p.status === "done")
    .reduce((max, p) => Math.max(max, p.phase || 0), 0);
  const currentPhaseNum = activePhase?.phase || maxDonePhase || 0;
  const latestPhase = activePhase || phases.find((p) => p.phase === currentPhaseNum);

  // ── Top-level state → label mapping (HONEST, no anticipation) ───
  let label = "Workspace ready...";
  let subtext = "Ready for your task";

  if (status === "idle" || (convLoading && status === "connecting")) {
    // Edit-mode load: there is a conversation to hydrate from Supabase.
    // Wizard mode (no prior conversation) never sees `idle` for long —
    // the auto-connect kicks it straight to `connecting`.
    label = "Loading your conversation...";
    subtext = "Retrieving chat history and workspace state";
  } else if (status === "connecting") {
    label = "Connecting...";
    subtext = "Opening secure connection to AI Engine";
  } else if (status === "preparing") {
    label = "Preparing workspace...";
    subtext = "Setting up the project foundation";
  } else if (status === "cloning" && resolvingInfo?.path === "existing_repo") {
    label = `Cloning ${resolvingInfo.repoDisplay || "repository"}...`;
    subtext = `Fetching files · Branch: ${resolvingInfo.branch || "main"}`;
  } else if (status === "cloning") {
    label = "Cloning template...";
    subtext = "Fetching the starter project";
  } else if (status === "installing") {
    label = "Installing dependencies...";
    subtext = "Running npm install — this takes up to 60 seconds";
  } else if (status === "starting") {
    label = "Starting dev server...";
    subtext = "Spinning up the local preview";
  } else if (status === "health_check") {
    label = "Waiting for dev server...";
    subtext = "Checking that the preview responds";
  } else if (status === "ready" && phases.length === 0) {
    // Agent is genuinely idle (or just landed on the workspace before the
    // first user task). The chat panel's own isWaiting override turns
    // this into "Waiting for your message…" when messages exist.
    label = "Workspace ready...";
    subtext = "Ready for your task";
  }

  // Legacy-backend fallback only: render the backend phase title verbatim.
  // Current backends emit `agent.status`, handled below.
  if (status === "running" && currentPhaseNum === 0) {
    label = "Working on your request...";
    subtext = "Waiting for the agent's next status";
  } else if (status === "running" && currentPhaseNum >= 1) {
    const title = (latestPhase?.title || "").trim();
    label = title ? `${title.replace(/\.+$/, "")}...` : "Working on your request...";
    subtext = latestPhase?.description || "Processing the task";
  }

  // ── Background-preview overlay (independent of agent task) ─────
  //
  // The workspace's dev server starts up in parallel with the agent.
  // While that happens, the preview pane shows status messages — but
  // those messages must NOT pretend to be the agent's progress. Hence
  // "Setting up workspace…" for clone, not "Building your app…".
  //
  // Stage source-of-truth: the backend's preview_status event carries a
  // `status` field with values { cloning | installing | starting |
  // preparing | health_check }. We prefer that structured field over
  // string-matching the human-readable previewStatusMsg — the grep
  // approach broke when message text varied between code paths.
  if (previewLoading && status !== "running") {
    const stage = (previewStage || "").toLowerCase();
    const msg = (previewStatusMsg || "").toLowerCase();
    if (stage === "cloning" || (!stage && msg.includes("clon"))) {
      label = "Setting up workspace...";
    } else if (stage === "installing" || (!stage && msg.includes("install"))) {
      label = "Installing dependencies...";
    } else if (
      stage === "starting" ||
      stage === "preparing" ||
      (!stage && (msg.includes("starting") || msg.includes("cached")))
    ) {
      label = "Starting the dev server...";
    } else if (
      stage === "health_check" ||
      (!stage && (msg.includes("waiting") || msg.includes("health")))
    ) {
      label = "Waiting for dev server...";
    } else if (previewStatusMsg) {
      // Unknown stage from a future backend — fall back to a safe
      // generic label and let the subtext convey the detail.
      label = "Setting up preview...";
    }
    if (previewStatusMsg) {
      subtext = previewStatusMsg;
    }
  }

  // Canonical prompt-router / agent status. No translation, regex mapping, or
  // phase-number guessing is allowed here.
  if (agentStatus?.label) {
    label = agentStatus.label;
    subtext = agentStatus.description || "";
  }

  // Intake is the user-visible source of truth while it is active. A socket
  // can truthfully be "preparing" in the background, but telling the user
  // that the preview is being prepared while chat is asking a question is
  // misleading. Both ChatPanel and BuildingScreen consume this same override.
  if (
    !agentStatus?.label &&
    (projectIntakeStatus === "checking" || projectIntakeStatus === "handoff")
  ) {
    label = "Analyzing your prompt...";
    subtext = projectIntakeStatus === "handoff"
      ? "Starting your project"
      : "Checking whether there is enough information to start";
  } else if (!agentStatus?.label && projectIntakeStatus === "clarifying") {
    label = "Waiting for project details...";
    subtext = "Answer the question in chat to continue";
  }

  return { label, subtext, currentPhaseNum };
}
