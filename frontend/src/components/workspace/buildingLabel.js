// Shared status-label computation for the BuildingScreen + ChatPanel status
// bar. Both surfaces show during the same flow (BuildingScreen on the right,
// status pill above the chat input on the left), so they MUST agree on what
// the agent is doing — otherwise users see "Researching…" in the main panel
// and "Building your app…" in the chat at the same moment, which reads as a
// bug. Keep the source of truth here.

export function computeBuildLabel({
  status,
  phases = [],
  resolvingInfo,
  isWizardMode,
  convLoading,
  previewLoading = false,
  previewStatusMsg = "",
}) {
  const activePhase = phases.find((p) => p.status === "active");
  const maxDonePhase = phases
    .filter((p) => p.status === "done")
    .reduce((max, p) => Math.max(max, p.phase || 0), 0);
  const currentPhaseNum = activePhase?.phase || maxDonePhase || 0;

  let label = "Building App...";
  let subtext = "Setting up your workspace";

  if (
    !isWizardMode &&
    (status === "idle" || (convLoading && status === "connecting"))
  ) {
    label = "Loading your conversation...";
    subtext = "Retrieving chat history and workspace state";
  } else if (status === "connecting") {
    label = "Connecting...";
    subtext = "Opening secure connection to AI Engine";
  } else if (status === "preparing") {
    // Wizard mode: collapse "Building your app" into "Analyzing your request"
    // so new generations show only Connecting → Analyzing. Existing projects
    // keep the explicit prep label.
    label = isWizardMode ? "Analyzing your request..." : "Building your app...";
    subtext = isWizardMode
      ? "Understanding what you want to build"
      : "Setting up the project foundation";
  } else if (status === "cloning" && resolvingInfo?.path === "existing_repo") {
    label = `Cloning ${resolvingInfo.repoDisplay || "repository"}...`;
    subtext = `Fetching files · Branch: ${resolvingInfo.branch || "main"}`;
  } else if (status === "cloning") {
    label = isWizardMode ? "Analyzing your request..." : "Building your app...";
    subtext = isWizardMode
      ? "Understanding what you want to build"
      : "Setting up the project foundation";
  } else if (status === "installing") {
    label = "Installing dependencies...";
    subtext = "Running npm install — this takes up to 60 seconds";
  } else if (status === "starting") {
    label = "Running the code for Preview...";
    subtext = "Starting dev server — this takes a moment";
  } else if (status === "health_check") {
    label = "Running the code for Preview...";
    subtext = "Waiting for dev server to respond";
  } else if (status === "ready" && phases.length === 0) {
    // Wizard mode = brand-new project with a pending prompt in sessionStorage.
    // The 4-6s gap between "session ready" and "first task_phase" was reading
    // as a UX bug ("Workspace ready" while the user is actively waiting on
    // their prompt to be picked up). Jump straight to the analyzing copy so
    // the status accurately reflects what's happening next.
    if (isWizardMode) {
      label = "Analyzing your request...";
      subtext = "Understanding what you want to build";
    } else {
      label = "Workspace ready...";
      subtext = "Ready for your task";
    }
  }

  if (status === "running" && currentPhaseNum === 0) {
    // Status='running' but no task_phase events have arrived yet — this is
    // the intake/clarify window where the backend is deciding whether to
    // ask a clarifying question or kick off the pipeline. Both panels show
    // the same "Analyzing…" label so they don't disagree during this gap.
    label = "Analyzing your request...";
    subtext = "Understanding what you want to build";
  } else if (currentPhaseNum <= 1 && status === "running") {
    // Pre-research phases collapse to "Analyzing" for wizard mode so the
    // status sequence stays Connecting → Analyzing → Researching.
    label = isWizardMode ? "Analyzing your request..." : "Building App...";
    subtext = isWizardMode ? "Understanding what you want to build" : "Setting things up";
  } else if (
    currentPhaseNum === 2 &&
    activePhase?.title?.toLowerCase().includes("clone")
  ) {
    label = isWizardMode ? "Analyzing your request..." : "Building your app...";
    subtext = isWizardMode
      ? "Understanding what you want to build"
      : (activePhase?.description || "Setting up the project foundation");
  } else if (currentPhaseNum === 2) {
    label = isWizardMode ? "Analyzing your request..." : "Preparing workspace...";
    subtext = isWizardMode
      ? "Understanding what you want to build"
      : (activePhase?.description || "Setting up your project environment");
  } else if (
    currentPhaseNum === 3 &&
    (activePhase?.title || "").toLowerCase().includes("understanding")
  ) {
    label = "Analyzing your request...";
    subtext = activePhase?.description || "Checking whether I have enough detail";
  } else if (currentPhaseNum === 3) {
    label = "Researching your idea...";
    subtext = "Gemini is analyzing top products in this domain";
  } else if (
    currentPhaseNum === 4 &&
    activePhase?.title?.toLowerCase().includes("design")
  ) {
    label = "Choosing design style...";
    subtext =
      activePhase?.description || "Selecting colors, typography and layout";
  } else if (
    currentPhaseNum === 4 &&
    activePhase?.title?.toLowerCase().includes("research")
  ) {
    label = "Researching your idea...";
    subtext = "Analyzing top products in this domain";
  } else if (currentPhaseNum === 4) {
    label = "Planning code...";
    subtext = "Creating implementation plan from research insights";
  } else if (currentPhaseNum === 5) {
    label = "Writing your code...";
    subtext = "AI is generating components, pages, and logic";
  } else if (currentPhaseNum === 6) {
    label = "Verifying build...";
    subtext = "Running build checks to ensure everything compiles";
  } else if (currentPhaseNum === 7) {
    label = "Publishing project...";
    subtext = "Committing and pushing code to repository";
  } else if (currentPhaseNum >= 8) {
    label = "Deploying...";
    subtext = "Setting up live preview";
  }

  const researchDone = phases.find(
    (p) =>
      (p.phase === 3 || p.phase === 4) &&
      p.status === "done" &&
      p.title?.toLowerCase().includes("research"),
  );
  const designDone = phases.find(
    (p) =>
      p.phase === 4 &&
      p.status === "done" &&
      p.title?.toLowerCase().includes("design"),
  );
  const codingStarted = phases.find(
    (p) => p.phase === 5 && (p.status === "active" || p.status === "done"),
  );

  if (designDone && !codingStarted) {
    label = "Starting to code...";
    subtext = "Design and plan ready. Building your codebase now.";
  } else if (researchDone && !codingStarted) {
    label = "Planning project...";
    subtext = "Research complete. Choosing design style.";
  }

  if (previewLoading && previewStatusMsg && status !== "running") {
    const msg = previewStatusMsg.toLowerCase();
    if (msg.includes("cloning") || msg.includes("clone")) {
      label = "Building your app...";
    } else if (msg.includes("install")) {
      label = "Installing dependencies...";
    } else if (msg.includes("starting") || msg.includes("cached")) {
      label = "Running the code for Preview...";
    } else if (msg.includes("waiting") || msg.includes("health")) {
      label = "Waiting for dev server...";
    } else {
      label = "Setting up preview...";
    }
    subtext = previewStatusMsg;
  }

  return {label, subtext, currentPhaseNum};
}
