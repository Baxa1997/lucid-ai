"use client";

// ─────────────────────────────────────────────────────────
//  RightPanel — Preview / Code / Terminal / Dashboard panels.
//  Extracted from workspace/[projectId]/page.js.
//  Reads shared workspace state via useWorkspace().
// ─────────────────────────────────────────────────────────

import {useState, useRef, useEffect, useCallback} from "react";
import {cn} from "@/lib/utils";
import {
  Terminal,
  Code2,
  Monitor,
  Sparkles,
  AlertCircle,
  TriangleAlert,
  GitBranch,
  RefreshCw,
  Wand2,
  MessageCircle,
  Activity,
  Search,
  Download,
  Palette,
  MousePointer2,
  ChevronDown,
  Maximize,
  Minimize2,
  Tablet,
  Smartphone,
  Loader2,
  Check,
  ArrowRight,
  X,
  ExternalLink,
} from "lucide-react";
import {useWorkspace} from "@/contexts/WorkspaceContext";
import FileViewer from "@/components/workspace/FileViewer";
import FileExplorer from "@/components/agent/FileExplorer";
import TaskProgress from "@/components/TaskProgress";
import BuildingScreen from "@/components/workspace/BuildingScreen";
import ProjectDashboard from "@/components/ProjectDashboard";
import DiffViewer from "@/components/DiffViewer";
import PreviewEditOverlay from "@/components/workspace/PreviewEditOverlay";

// ── PlanReviewPanel — full right-panel plan review UI ────────
function PlanReviewPanel({planData, onConfirm, onReject}) {
  const [showCorrection, setShowCorrection] = useState(false);
  const [correctionText, setCorrectionText] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [rejected, setRejected] = useState(false);

  const pages = planData?.pages || [];
  const pagesNested = planData?.pages_nested || [];
  const entities = planData?.entities || [];
  const planSummary = Array.isArray(planData?.planSummary) ? planData.planSummary : [];
  const research = planData?.research || null;
  const buildSteps = Array.isArray(planData?.buildSteps) ? planData.buildSteps : [];
  const assumptions = Array.isArray(planData?.assumptions) ? planData.assumptions : [];

  // Multi-page when nested data exists OR flat pages carry routes.
  // Landing pages emit sections-disguised-as-pages without routes.
  const flatHasRoutes = pages.some(
    (p) => typeof p === "object" && (p.route || p.path),
  );
  const isMultiPage = pagesNested.length > 0 || flatHasRoutes;
  const groupLabel = isMultiPage ? "Pages" : "Sections";

  const handleConfirm = () => {
    setConfirmed(true);
    onConfirm?.();
  };

  const handleReject = () => {
    if (!correctionText.trim()) return;
    setRejected(true);
    onReject?.(correctionText.trim());
  };

  // Render the intro with **bold** support
  const renderIntro = (text) => {
    if (!text) return null;
    return text.split(/(\*\*.*?\*\*)/).map((part, i) =>
      part.startsWith("**") && part.endsWith("**") ? (
        <strong
          key={i}
          className="font-semibold text-slate-900 dark:text-white">
          {part.slice(2, -2)}
        </strong>
      ) : (
        part
      ),
    );
  };

  if (confirmed) {
    return (
      <div className="flex flex-col items-center justify-center h-full bg-white dark:bg-[#0d1117] gap-4">
        <div className="w-14 h-14 rounded-full bg-orange-100 dark:bg-orange-900/30 flex items-center justify-center">
          <Check className="w-7 h-7 text-[#dc5426]" />
        </div>
        <p className="text-[15px] font-semibold text-slate-800 dark:text-slate-100">
          Plan confirmed — building your project...
        </p>
        <p className="text-[13px] text-slate-400 dark:text-slate-500">
          This may take a few minutes
        </p>
      </div>
    );
  }

  if (rejected) {
    return (
      <div className="flex flex-col items-center justify-center h-full bg-white dark:bg-[#0d1117] gap-4">
        <div className="w-14 h-14 rounded-full bg-orange-100 dark:bg-orange-900/30 flex items-center justify-center">
          <Sparkles className="w-7 h-7 text-[#dc5426]" />
        </div>
        <p className="text-[15px] font-semibold text-slate-800 dark:text-slate-100">
          Re-researching with your direction...
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full bg-white dark:bg-[#0d1117] overflow-y-auto">
      {/* Header */}
      <div className="shrink-0 px-8 pt-10 pb-6 border-b border-slate-100 dark:border-[#1c2128]">
        <div className="flex items-center gap-3 mb-2">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-[#dc5426] to-orange-600 flex items-center justify-center shadow-sm shadow-orange-500/20">
            <Sparkles className="w-4.5 h-4.5 text-white" />
          </div>
          <span className="text-[11px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-widest">
            Generation Plan
          </span>
        </div>
        <h2 className="text-[20px] font-bold text-slate-900 dark:text-white leading-snug">
          {renderIntro(planData?.intro)}
        </h2>
      </div>

      {/* Plan body */}
      <div className="flex-1 px-8 py-6 space-y-6">
        {/* About */}
        {planData?.description && (
          <section>
            <p className="text-[11px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider mb-2">
              About
            </p>
            <p className="text-[14px] text-slate-600 dark:text-slate-300 leading-relaxed">
              {planData.description.split(/(\*\*.*?\*\*)/).map((part, i) =>
                part.startsWith("**") && part.endsWith("**") ? (
                  <strong
                    key={i}
                    className="font-semibold text-slate-800 dark:text-slate-100">
                    {part.slice(2, -2)}
                  </strong>
                ) : (
                  part
                ),
              )}
            </p>
          </section>
        )}

        {planSummary.length > 0 && (
          <section>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
              {planSummary.slice(0, 3).map((item, i) => (
                <div
                  key={i}
                  className="rounded-lg border border-slate-100 dark:border-[#2d333b] bg-slate-50 dark:bg-[#161b22] px-3 py-2.5">
                  <p className="text-[10px] font-bold uppercase tracking-wider text-slate-400 dark:text-slate-500">
                    {item.label}
                  </p>
                  <p className="mt-1 text-[13px] font-semibold text-slate-800 dark:text-slate-100 leading-snug">
                    {item.value}
                  </p>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Pages / Sections — nested when available, flat fallback otherwise */}
        {pagesNested.length > 0 ? (
          <section>
            <p className="text-[11px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider mb-2">
              {groupLabel}
            </p>
            <div className="space-y-3">
              {pagesNested.map((page, pi) => (
                <div
                  key={pi}
                  className="px-3 py-2.5 rounded-lg bg-slate-50 dark:bg-[#161b22] border border-slate-100 dark:border-[#2d333b]">
                  <div className="flex items-baseline gap-2">
                    <ArrowRight className="w-3.5 h-3.5 text-orange-400 dark:text-[#dc5426] shrink-0 self-center" />
                    <span className="text-[13px] font-semibold text-slate-800 dark:text-slate-100">
                      {page.name}
                    </span>
                    {page.route && (
                      <span className="text-[11px] font-mono text-slate-400 dark:text-slate-500">
                        {page.route}
                      </span>
                    )}
                  </div>
                  {page.purpose && (
                    <p className="ml-5 mt-1 text-[12px] text-slate-500 dark:text-slate-400 leading-snug">
                      {page.purpose}
                    </p>
                  )}
                  {(page.sections || []).length > 0 && (
                    <div className="ml-5 mt-2 space-y-0.5 border-l border-slate-200 dark:border-[#2d333b] pl-3">
                      {(page.sections || []).map((s, si) => (
                        <div key={si} className="flex items-start gap-2">
                          <span className="text-slate-300 dark:text-slate-600 text-[12px] mt-0.5 shrink-0">·</span>
                          <span className="text-[12.5px] leading-snug">
                            <span className="font-medium text-slate-700 dark:text-slate-200">
                              {(s.type || "section").replace(/_/g, " ")}
                            </span>
                            {s.headline && (
                              <span className="text-slate-500 dark:text-slate-400">
                                {" "}— {s.headline}
                              </span>
                            )}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </section>
        ) : pages.length > 0 && (
          <section>
            <p className="text-[11px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider mb-2">
              {groupLabel}
            </p>
            <div className="grid grid-cols-1 gap-1.5">
              {pages.map((p, i) => (
                <div
                  key={i}
                  className="flex items-start gap-2.5 px-3 py-2 rounded-lg bg-slate-50 dark:bg-[#161b22] border border-slate-100 dark:border-[#2d333b]">
                  <ArrowRight className="w-3.5 h-3.5 text-orange-400 dark:text-[#dc5426] shrink-0 mt-0.5" />
                  <span className="text-[13px] leading-snug">
                    <span className="font-medium text-slate-800 dark:text-slate-100">
                      {typeof p === "string" ? p : p.name}
                    </span>
                    {typeof p === "object" && p.desc && (
                      <span className="text-slate-400 dark:text-slate-500">
                        {" "}
                        — {p.desc}
                      </span>
                    )}
                  </span>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Data Models */}
        {entities.length > 0 && (
          <section>
            <p className="text-[11px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider mb-2">
              Data Models
            </p>
            <div className="grid grid-cols-1 gap-1.5">
              {entities.map((e, i) => (
                <div
                  key={i}
                  className="flex items-start gap-2.5 px-3 py-2 rounded-lg bg-slate-50 dark:bg-[#161b22] border border-slate-100 dark:border-[#2d333b]">
                  <ArrowRight className="w-3.5 h-3.5 text-purple-400 dark:text-purple-500 shrink-0 mt-0.5" />
                  <span className="text-[13px] leading-snug">
                    <span className="font-medium text-slate-800 dark:text-slate-100">
                      {e.name}
                    </span>
                    {e.fields && (
                      <span className="text-slate-400 dark:text-slate-500">
                        {" "}
                        — {e.fields}
                      </span>
                    )}
                  </span>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Design */}
        {planData?.design && (
          <section>
            <p className="text-[11px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider mb-2">
              Design System
            </p>
            <p className="text-[13px] text-slate-500 dark:text-slate-400 leading-relaxed">
              {planData.design}
            </p>
          </section>
        )}

        {research && (
          <section>
            <p className="text-[11px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider mb-2">
              Research
            </p>
            <div className="rounded-lg bg-slate-50 dark:bg-[#161b22] border border-slate-100 dark:border-[#2d333b] px-3 py-2.5">
              <p className="text-[13px] font-semibold text-slate-800 dark:text-slate-100">
                {research.confidence || "Checked"} confidence
                {Number.isFinite(Number(research.sources)) ? ` · ${research.sources} sources` : ""}
              </p>
              {(research.notes || []).length > 0 && (
                <div className="mt-1.5 space-y-1">
                  {(research.notes || []).slice(0, 3).map((note, i) => (
                    <p key={i} className="text-[12.5px] text-slate-500 dark:text-slate-400 leading-snug">
                      {note}
                    </p>
                  ))}
                </div>
              )}
            </div>
          </section>
        )}

        {buildSteps.length > 0 && (
          <section>
            <p className="text-[11px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider mb-2">
              Build Approach
            </p>
            <div className="space-y-1.5">
              {buildSteps.slice(0, 3).map((step, i) => (
                <div
                  key={i}
                  className="flex items-start gap-2.5 px-3 py-2 rounded-lg bg-slate-50 dark:bg-[#161b22] border border-slate-100 dark:border-[#2d333b]">
                  <span className="text-[12px] font-semibold text-[#dc5426] mt-0.5">{i + 1}</span>
                  <p className="text-[13px] text-slate-600 dark:text-slate-300 leading-snug">
                    {step}
                  </p>
                </div>
              ))}
            </div>
          </section>
        )}

        {assumptions.length > 0 && (
          <section>
            <p className="text-[11px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider mb-2">
              Assumptions
            </p>
            <div className="space-y-1">
              {assumptions.slice(0, 3).map((item, i) => (
                <p key={i} className="text-[12.5px] text-slate-500 dark:text-slate-400 leading-snug">
                  {item}
                </p>
              ))}
            </div>
          </section>
        )}
      </div>

      {/* Confirmation footer */}
      <div className="shrink-0 px-8 py-6 border-t border-slate-100 dark:border-[#1c2128] bg-slate-50/50 dark:bg-[#161b22]/50">
        {!showCorrection ? (
          <div className="flex items-center gap-3">
            <button
              onClick={handleConfirm}
              className="flex items-center justify-center gap-2 px-6 py-3 rounded-xl bg-[#dc5426] hover:bg-[#b8421e] text-white text-[14px] font-semibold transition-all shadow-sm shadow-orange-500/20 hover:shadow-orange-500/30">
              <Check className="w-4 h-4" />
              Confirm
            </button>
            <button
              onClick={() => setShowCorrection(true)}
              className="px-5 py-3 rounded-xl border border-slate-200 dark:border-[#2d333b] text-slate-600 dark:text-slate-300 text-[13px] font-medium hover:bg-slate-100 dark:hover:bg-[#21262d] transition-all">
              Change Direction
            </button>
          </div>
        ) : (
          <div className="space-y-3">
            <p className="text-[12px] font-medium text-slate-500 dark:text-slate-400">
              Describe what you'd like instead:
            </p>
            <textarea
              value={correctionText}
              onChange={(e) => setCorrectionText(e.target.value)}
              autoFocus
              placeholder="e.g. Make it a professional accounting body website like acca.org, not an online course platform..."
              className="w-full px-3.5 py-2.5 rounded-xl border border-slate-200 dark:border-[#2d333b] bg-white dark:bg-[#0d1117] text-[13px] text-slate-700 dark:text-slate-200 placeholder:text-slate-400 dark:placeholder:text-slate-500 outline-none focus:border-[#dc5426] dark:focus:border-[#dc5426] resize-none min-h-[72px]"
              rows={3}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  handleReject();
                }
              }}
            />
            <div className="flex items-center gap-2">
              <button
                onClick={handleReject}
                disabled={!correctionText.trim()}
                className={cn(
                  "flex-1 px-5 py-2.5 rounded-xl text-[13px] font-semibold transition-all",
                  correctionText.trim()
                    ? "bg-[#dc5426] hover:bg-[#b8421e] text-white shadow-sm shadow-orange-500/20"
                    : "bg-slate-100 dark:bg-[#21262d] text-slate-400 dark:text-slate-500 cursor-not-allowed",
                )}>
                Re-research with this direction
              </button>
              <button
                onClick={() => {
                  setShowCorrection(false);
                  setCorrectionText("");
                }}
                className="px-4 py-2.5 rounded-xl text-[13px] text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200 transition-colors">
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Preview "booting" panel ────────────────────────────────────────────────
// Shown in the preview pane while the dev server is being prepared. Mirrors
// the BuildingScreen visual (orange gradient logo, pulse rings, three orange
// dots) so the workspace feels like one continuous loading state.
function PreviewBootingPanel({
  previewStatusMsg,
  workspaceStatus,
}) {
  const fallbackMsg =
    workspaceStatus === "connecting" ? "Connecting to the agent…" :
    workspaceStatus === "cloning"    ? "Cloning the repository…" :
    workspaceStatus === "installing" ? "Installing dependencies…" :
    workspaceStatus === "starting"   ? "Starting the dev server…" :
    workspaceStatus === "health_check" ? "Waiting for the dev server to come up…" :
    workspaceStatus === "running"    ? "Generating your project — preview will start shortly." :
    "Booting workspace — the preview will appear here as soon as the dev server is ready.";

  return (
    <div className="h-full flex flex-col items-center justify-center relative overflow-hidden bg-white dark:bg-[#0d1117]">
      {/* Animated orange orbs */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div className="absolute top-[12%] left-[8%] w-80 h-80 bg-orange-300/15 dark:bg-orange-500/8 rounded-full blur-3xl animate-orb-1" />
        <div className="absolute top-[35%] right-[6%] w-64 h-64 bg-orange-200/15 dark:bg-orange-500/8 rounded-full blur-3xl animate-orb-2" />
        <div className="absolute bottom-[15%] left-[28%] w-72 h-72 bg-orange-200/15 dark:bg-orange-600/6 rounded-full blur-3xl animate-orb-3" />
      </div>
      <div className="absolute bottom-0 left-0 right-0 h-[40%] bg-gradient-to-t from-slate-50/40 via-slate-50/15 to-transparent dark:from-slate-900/20 dark:via-slate-900/5 dark:to-transparent pointer-events-none" />

      {/* Orange logo with pulse rings */}
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
            <rect x="8" y="10" width="24" height="3" rx="1.5" fill="white" opacity="0.9" />
            <rect x="8" y="16" width="24" height="3" rx="1.5" fill="white" opacity="0.7" />
            <rect x="8" y="22" width="24" height="3" rx="1.5" fill="white" opacity="0.5" />
            <rect x="12" y="28" width="16" height="3" rx="1.5" fill="white" opacity="0.3" />
          </svg>
        </div>
      </div>

      <h2 className="relative z-10 text-xl font-semibold text-slate-700 dark:text-slate-300 mb-2 transition-all duration-500">
        Preparing Preview
      </h2>
      <p className="relative z-10 text-[13px] text-slate-400 dark:text-slate-500 max-w-sm text-center transition-all duration-300">
        {previewStatusMsg || fallbackMsg}
      </p>

      {/* Animated orange dots */}
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
    </div>
  );
}


export default function RightPanel() {
  const {
    rightPanel,
    setRightPanel,
    repoInfo,
    status,
    phases,
    resolvingInfo,
    resolvingProgress,
    isWizardMode,
    convLoading,
    buildingActive,
    projectIntakeStatus,
    agentStatus,
    files,
    conversation,
    terminalLogs,
    sendMessage,
    error,
    errorStage,
    retryCount,
    retry,
    previewError,
    previewLoading,
    previewStatusMsg,
    previewStage,
    previewStartedAt,
    previewEverReady,
    stopPreview,
    sendManualEdit,
    completionSummary,
    isNewProject,
    iframeRef,
    setShowExportModal,
    sessionId,
    panelOverrideRef,
    planAwaiting,
    currentPlanData,
    confirmPlan,
    rejectPlan,
    previewFileMap,
    // ── Project-dashboard surface (rendered inside the "dashboard" tab) ──
    conversationId,
    subscription,
    handleProjectRename,
    handleProjectDelete,
    router,
    vercelDeployUrl,
    // ── Per-file content + metrics (powers DiffViewer in the Code tab) ──
    fileContents,
    fileMetrics,
    // Click-to-edit (Base44 flow)
    editSelection,
    setEditSelection,
    editSelectMode,
    setEditSelectMode,
    clearEditSelection,
  } = useWorkspace();

  // ── Code-tab view mode toggle: 'diff' (live agent edits) vs 'source' (raw)
  // Defaults to diff when the agent has touched a file, source otherwise.
  const [codeViewMode, setCodeViewMode] = useState("diff");

  // ── Transient hover overlay for the Base44-style inline editor.
  // The iframe streams hover events; we render a thin outline that
  // tracks the cursor inside the preview while select mode is on.
  const [editHover, setEditHover] = useState(null);

  // ── Preview viewport size + fullscreen ─────────────────────────
  // The toolbar exposes two display controls:
  //   • Sizes dropdown — Desktop (fill) / Tablet (768×1024) / Mobile (375×667).
  //     Constrains the iframe so the user can sanity-check responsive layouts
  //     without resizing the whole workspace.
  //   • Fullscreen toggle — promotes the right panel to a viewport-filling
  //     overlay so the preview gets the whole screen; Esc / the toolbar's
  //     "Exit Preview" button restore the normal split layout.
  const [previewSize, setPreviewSize] = useState("desktop");
  const [previewFullscreen, setPreviewFullscreen] = useState(false);
  const [sizeMenuOpen, setSizeMenuOpen] = useState(false);
  const sizeMenuRef = useRef(null);

  // Esc exits fullscreen. Skipped when the user is mid-edit (the
  // PreviewEditOverlay also listens for Esc to clear its selection —
  // we only swallow Esc here when fullscreen is the only active mode).
  useEffect(() => {
    if (!previewFullscreen) return undefined;
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      if (editSelection || editSelectMode) return;
      setPreviewFullscreen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [previewFullscreen, editSelection, editSelectMode]);

  // Close the sizes dropdown on outside click.
  useEffect(() => {
    if (!sizeMenuOpen) return undefined;
    const onPointer = (e) => {
      if (sizeMenuRef.current && !sizeMenuRef.current.contains(e.target)) {
        setSizeMenuOpen(false);
      }
    };
    window.addEventListener("mousedown", onPointer);
    return () => window.removeEventListener("mousedown", onPointer);
  }, [sizeMenuOpen]);

  // Switching away from the Preview tab leaves fullscreen — otherwise
  // the Code/Terminal panes would inherit the viewport-filling overlay.
  useEffect(() => {
    if (rightPanel !== "preview" && previewFullscreen) {
      setPreviewFullscreen(false);
    }
  }, [rightPanel, previewFullscreen]);

  // Preset viewport dimensions for the iframe device frame.
  const PREVIEW_VIEWPORTS = {
    desktop: { label: "Desktop", icon: Monitor, width: null, height: null },
    tablet:  { label: "Tablet",  icon: Tablet,  width: 768,  height: 1024 },
    mobile:  { label: "Mobile",  icon: Smartphone, width: 375, height: 667 },
  };
  const activeViewport = PREVIEW_VIEWPORTS[previewSize] || PREVIEW_VIEWPORTS.desktop;
  const ActiveViewportIcon = activeViewport.icon;

  // ── Click-to-edit (Base44 flow) ────────────────────────────────
  // The "Edit" button in the preview toolbar enters a selection mode.
  // We postMessage the iframe so the generated site's listener can
  // intercept clicks; the site posts back the editable target which
  // we surface as a chip above the chat input.
  //
  // Toggling the mode also posts immediately so the iframe enables/
  // disables its click handlers in real time.
  const toggleEditSelectMode = useCallback(() => {
    setEditSelectMode((prev) => {
      const next = !prev;
      try {
        const win = iframeRef?.current?.contentWindow;
        if (win) {
          win.postMessage(
            { type: "lucid_set_edit_select_mode", enabled: next },
            "*",
          );
        }
      } catch {
        /* cross-origin postMessage can never throw under this signature, but be safe */
      }
      return next;
    });
  }, [iframeRef, setEditSelectMode]);

  // Re-post the mode after the iframe (re)loads so a refresh / nav
  // doesn't leave the toolbar out of sync with the page's listener.
  useEffect(() => {
    const iframe = iframeRef?.current;
    if (!iframe) return undefined;
    const sync = () => {
      try {
        iframe.contentWindow?.postMessage(
          { type: "lucid_set_edit_select_mode", enabled: editSelectMode },
          "*",
        );
      } catch {
        /* no-op */
      }
    };
    iframe.addEventListener("load", sync);
    return () => iframe.removeEventListener("load", sync);
  }, [iframeRef, editSelectMode]);

  // ── Base44-style overlay handlers ───────────────────────────
  // Close = clear the selection on both sides AND leave select mode.
  // The iframe-side listener tears down its hover handlers and the
  // selected outline when it receives the matching messages.
  const closeEditOverlay = useCallback(() => {
    setEditSelection(null);
    setEditHover(null);
    setEditSelectMode(false);
    try {
      const win = iframeRef?.current?.contentWindow;
      if (win) {
        win.postMessage({ type: "lucid_clear_selection" }, "*");
        win.postMessage(
          { type: "lucid_set_edit_select_mode", enabled: false },
          "*",
        );
      }
    } catch {
      /* no-op */
    }
  }, [iframeRef, setEditSelection, setEditSelectMode]);

  // Submit from the overlay's inline "What to change?" input. Forwards
  // the text into the main chat with the current selection attached as
  // editable_target so the backend pipeline edits exactly that element.
  // After firing we clear the overlay — the chat panel takes over.
  const submitInlineEdit = useCallback(
    (text) => {
      if (!text || !editSelection) return;
      sendMessage?.(text, [], {
        mode: "edit",
        editableTarget: editSelection,
      });
      closeEditOverlay();
    },
    [editSelection, sendMessage, closeEditOverlay],
  );

  const applyManualEdit = useCallback(
    (patch) => {
      if (!editSelection || !patch || typeof patch !== "object") return;
      const outgoing = {
        ...patch,
        path: patch.path || editSelection.path,
      };

      try {
        const win = iframeRef?.current?.contentWindow;
        if (win) {
          win.postMessage(
            { type: "lucid_apply_manual_edit", patch: outgoing },
            "*",
          );
        }
      } catch {
        /* no-op */
      }

      setEditSelection((prev) => {
        if (!prev) return prev;
        if (outgoing.kind === "image") {
          return {
            ...prev,
            src: typeof outgoing.src === "string" ? outgoing.src : prev.src,
            alt: typeof outgoing.alt === "string" ? outgoing.alt : prev.alt,
            text: typeof outgoing.alt === "string" ? outgoing.alt : prev.text,
          };
        }
        if (outgoing.kind === "link") {
          return {
            ...prev,
            text: typeof outgoing.text === "string" ? outgoing.text : prev.text,
            href: typeof outgoing.href === "string" ? outgoing.href : prev.href,
          };
        }
        return {
          ...prev,
          text: typeof outgoing.text === "string" ? outgoing.text : prev.text,
        };
      });

      sendManualEdit?.(outgoing, editSelection);
    },
    [editSelection, iframeRef, sendManualEdit, setEditSelection],
  );

  // Listen for selection events from the generated site. Keyed by a
  // ``lucid_`` prefix so we never collide with messages from unrelated
  // origins (Vercel preview banners, Stripe iframes, etc.).
  useEffect(() => {
    const onMessage = (e) => {
      const data = e?.data;
      if (!data || typeof data !== "object") return;

      // ── Persistent selection (click) ──────────────────────
      if (data.type === "lucid_element_selected") {
        const path = typeof data.path === "string" ? data.path.trim() : "";
        if (!path) return;
        // Two payload shapes coexist:
        //   • Editable-wrapped element → {path, editableType, text, rect}
        //   • Fuzzy fallback           → +{fuzzy, tag, className, src}
        // Backend decodes both via _intent_from_editable_target.
        setEditSelection({
          path,
          type: typeof data.editableType === "string" ? data.editableType : "text",
          file: typeof data.file === "string" ? data.file : "",
          text: typeof data.text === "string" ? data.text.slice(0, 200) : "",
          fuzzy: Boolean(data.fuzzy),
          tag: typeof data.tag === "string" ? data.tag : "",
          className: typeof data.className === "string" ? data.className : "",
          src: typeof data.src === "string" ? data.src : "",
          alt: typeof data.alt === "string" ? data.alt : "",
          href: typeof data.href === "string" ? data.href : "",
          route: typeof data.route === "string" ? data.route : "",
          rect: (data.rect && typeof data.rect === "object") ? {
            x: Number(data.rect.x) || 0,
            y: Number(data.rect.y) || 0,
            width: Number(data.rect.width) || 0,
            height: Number(data.rect.height) || 0,
          } : null,
        });
        // Hover state is moot once the user has clicked.
        setEditHover(null);
        // Stay in select mode — the overlay's X button is the explicit
        // way to leave (matches Base44 behaviour).
        return;
      }

      // ── Hover preview outline (live) ──────────────────────
      if (data.type === "lucid_element_hover") {
        if (!data.rect) return;
        setEditHover({
          rect: {
            x: Number(data.rect.x) || 0,
            y: Number(data.rect.y) || 0,
            width: Number(data.rect.width) || 0,
            height: Number(data.rect.height) || 0,
          },
          tag: typeof data.tag === "string" ? data.tag : "",
          hasEditablePath: Boolean(data.hasEditablePath),
        });
        return;
      }
      if (data.type === "lucid_element_hover_clear") {
        setEditHover(null);
        return;
      }

      // ── Selection moved (iframe scroll/resize) ─────────────
      if (data.type === "lucid_selection_rect_update") {
        if (!data.rect) return;
        setEditSelection((prev) => (prev ? {
          ...prev,
          rect: {
            x: Number(data.rect.x) || 0,
            y: Number(data.rect.y) || 0,
            width: Number(data.rect.width) || 0,
            height: Number(data.rect.height) || 0,
          },
        } : prev));
        return;
      }

      // ── User pressed Esc inside the iframe ─────────────────
      if (data.type === "lucid_element_deselected") {
        setEditSelection(null);
        setEditHover(null);
        return;
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [iframeRef, setEditSelection, setEditSelectMode]);

  // ── Latched live-preview URL ─────────────────────────────────────
  // The iframe must NOT unmount on every transient null (auto-restart, WS
  // reconnect, single dropped frame from the agent). React re-mounts iframes
  // when their `src` changes to/from falsy, which kills HMR socket, scroll
  // position, and the user's interaction state. We latch the last known live
  // URL and only clear it on a hard reset (conversation change / explicit
  // stop / fatal terminal error). Transient states layer as overlays on top.
  const [latchedPreviewUrl, setLatchedPreviewUrl] = useState(repoInfo.vercelUrl || null);
  useEffect(() => {
    if (repoInfo.vercelUrl) setLatchedPreviewUrl(repoInfo.vercelUrl);
  }, [repoInfo.vercelUrl]);
  // Reset latch when the user navigates between workspaces — otherwise an
  // old project's iframe would briefly show up in the new one.
  useEffect(() => {
    setLatchedPreviewUrl(null);
  }, [conversationId]);
  // After 3 failed retries we surface "Preview unavailable" — drop the latch
  // so the iframe doesn't sit there pointing at a dead URL behind the error.
  useEffect(() => {
    if (previewError && retryCount >= 3) setLatchedPreviewUrl(null);
  }, [previewError, retryCount]);

  // ── Preview phase — single source of truth for the preview tab UI ──
  // Replaces the previous chain of nested ternaries on individual flags
  // (previewLoading || previewError || previewEverReady || ...). One value,
  // one switch, no inconsistent renders.
  const previewPhase = (() => {
    if (status === "error") return "fatal";
    if (latchedPreviewUrl) {
      // Once we have a latched URL the iframe is always mounted.
      // Overlays layer on top for transient states only.
      if (previewError) return "live-with-error-overlay";
      if (previewLoading) return "live-with-restart-overlay";
      return "live";
    }
    if (previewError) return "crashed";
    if (previewLoading) return "booting";
    // Agent has handed control back to the user (asked a clarification,
    // greeting, etc.) — show the "describe your project" canvas instead of
    // the misleading "Preparing Preview" panel. Reuses the wizard-empty
    // copy because the UX is identical: the agent is idle, the user must
    // type something for anything to happen.
    if (agentStatus?.state === "waiting") return "wizard-empty";
    if (isNewProject && files.length === 0) return "wizard-empty";
    if (previewEverReady) return "stopped";
    // status=ready + no preview activity = workspace is idle, agent is
    // idle, but the dev server hasn't been spun up yet. This is what the
    // user sees right after a clarify response was sent — the previous
    // "preparing" default lied that something was happening.
    if (status === "ready") return "wizard-empty";
    return "preparing";
  })();

  // ── File-viewer local state ─────────────────────────────
  const [selectedFile, setSelectedFile] = useState(null);
  const [fileContent, setFileContent] = useState("");
  const [fileLoading, setFileLoading] = useState(false);
  const [editedFileContent, setEditedFileContent] = useState(null);

  // ── Terminal auto-scroll ────────────────────────────────
  const logsEndRef = useRef(null);
  useEffect(() => {
    logsEndRef.current?.scrollIntoView({behavior: "smooth"});
  }, [terminalLogs]);

  // ── File select handler ─────────────────────────────────
  const handleFileSelect = useCallback(
    async (path) => {
      setSelectedFile(path);
      setEditedFileContent(null);
      setRightPanel("code");
      setFileLoading(true);
      setFileContent("");

      if (sessionId) {
        try {
          const res = await fetch(
            `/api/files/read?session_id=${encodeURIComponent(sessionId)}&path=${encodeURIComponent(path)}`,
          );
          const data = await res.json();
          if (res.ok && data.content) {
            setFileContent(data.content);
            setFileLoading(false);
            return;
          }
        } catch {
          /* fall through */
        }
      }

      const repoName = conversation?.repo_name;
      if (repoName) {
        try {
          const res = await fetch(
            `/api/gitlab/file?repo=${encodeURIComponent(repoName)}&path=${encodeURIComponent(path)}`,
          );
          if (res.ok) {
            const data = await res.json();
            if (data.content) {
              const decoded =
                data.encoding === "base64" ? atob(data.content) : data.content;
              setFileContent(decoded);
              setFileLoading(false);
              return;
            }
          }
        } catch {
          /* fall through */
        }
      }

      setFileContent(
        `// File content not available yet\n// Path: ${path}\n\n// The file will be viewable once the build completes.`,
      );
      setFileLoading(false);
    },
    [sessionId, conversation?.repo_name, setRightPanel],
  );

  // ── Render ──────────────────────────────────────────────
  return (
    <div
      className={cn(
        "flex flex-col min-w-0 bg-[#f1f2f6] dark:bg-[#0d1117] overflow-hidden",
        previewFullscreen
          ? "fixed inset-0 z-[200] m-0 border-0 rounded-none"
          : "flex-1 m-[12px] border border-[#e3e5eb] dark:border-[#1c2128] rounded-xl",
      )}>
      {/* Preview toolbar — only shown when Preview tab is active */}
      {rightPanel === "preview" && (
        <div className="shrink-0 h-[42px] flex items-center justify-between px-3 bg-[#fff] dark:bg-[#161b22] border-b border-[#e3e5eb] dark:border-[#2d333b]">
          <div className="flex items-center gap-0.5 flex-1">
            <button
              type="button"
              onClick={toggleEditSelectMode}
              title={
                editSelectMode
                  ? "Click an element in the preview to edit it"
                  : "Pick an element to edit"
              }
              className={cn(
                "flex items-center gap-1.5 h-7 px-2.5 rounded-md text-[13px] font-medium transition-colors",
                editSelectMode
                  ? "bg-blue-600 text-white hover:bg-blue-700"
                  : "text-[#374151] dark:text-slate-200 hover:bg-black/5 dark:hover:bg-white/[0.06]",
              )}>
              <MousePointer2 className="w-3.5 h-3.5" />
              Edit
            </button>
            <div className="w-px h-4 bg-[#d1d5db] dark:bg-[#2d333b] mx-0.5" />
            <button
              className="h-7 w-7 flex items-center justify-center rounded-md text-[#6b7280] hover:text-[#374151] dark:text-slate-400 dark:hover:text-slate-200 hover:bg-black/5 dark:hover:bg-white/[0.06] transition-colors"
              title="Design tools">
              <Palette className="w-3.5 h-3.5" />
            </button>
          </div>
          <div className="flex justify-center flex-1">
            <div className="flex items-center w-full max-w-[320px] h-[30px] bg-white dark:bg-[#21262d] rounded-lg px-2.5 border border-[#d1d5db] dark:border-[#2d333b] shadow-sm">
              <button
                className="text-[#9ca3af] hover:text-[#6b7280] transition-colors"
                title="Refresh preview"
                onClick={() => {
                  const iframe = iframeRef?.current;
                  if (!iframe || !iframe.src) return;
                  const src = iframe.src;
                  iframe.src = "about:blank";
                  setTimeout(() => {
                    iframe.src = src;
                  }, 50);
                }}>
                <RefreshCw className="w-3 h-3" />
              </button>
              <div className="flex-1 text-center font-medium text-[13px] text-[#374151] dark:text-slate-200 px-2 cursor-text select-none truncate">
                {repoInfo.vercelUrl
                  ? (() => {
                      try {
                        const u = new URL(repoInfo.vercelUrl);
                        if (u.hostname === "localhost" || u.hostname === "127.0.0.1") {
                          return "Preview";
                        }
                        return u.hostname;
                      } catch {
                        return "Preview";
                      }
                    })()
                  : "Preview"}
              </div>
              <button
                className={cn(
                  "transition-colors",
                  repoInfo.vercelUrl
                    ? "text-[#9ca3af] hover:text-[#6b7280]"
                    : "text-[#d1d5db] dark:text-slate-600 cursor-not-allowed",
                )}
                title="Open live preview in new tab"
                disabled={!repoInfo.vercelUrl}
                onClick={() =>
                  repoInfo.vercelUrl &&
                  window.open(repoInfo.vercelUrl, "_blank")
                }>
                <ChevronDown className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
          <div className="flex items-center justify-end flex-1 gap-0.5">
            {/* Open deployed site — shown only when a live deployment exists.
                The iframe always mirrors local source; this is the explicit
                escape hatch to view the published Vercel/custom-domain URL. */}
            {repoInfo.deployedUrl && (
              <>
                <button
                  onClick={() => window.open(repoInfo.deployedUrl, "_blank")}
                  title={`Open deployed site (${repoInfo.deployedUrl})`}
                  className="flex items-center gap-1 h-7 px-2 rounded-md text-[#6b7280] dark:text-slate-400 hover:bg-black/5 dark:hover:bg-white/[0.06] text-[12px] font-medium transition-colors">
                  <ExternalLink className="w-3.5 h-3.5" />
                  Deployed
                </button>
                <div className="h-4 w-px bg-[#d1d5db] dark:bg-[#2d333b] mx-0.5" />
              </>
            )}
            {previewLoading && (
              <>
                <button
                  onClick={stopPreview}
                  title="Stop preview sandbox"
                  className="flex items-center gap-1 h-7 px-2 rounded-md text-red-500 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 text-[12px] font-medium transition-colors">
                  <X className="w-3.5 h-3.5" />
                  Stop
                </button>
                <div className="h-4 w-px bg-[#d1d5db] dark:bg-[#2d333b] mx-0.5" />
              </>
            )}
            {/* Viewport size dropdown — Desktop / Tablet / Mobile.
                Constrains the iframe to common device widths so the
                user can verify the preview's responsive behaviour
                without resizing the whole workspace. */}
            <div className="relative" ref={sizeMenuRef}>
              <button
                type="button"
                onClick={() => setSizeMenuOpen((v) => !v)}
                title={`Viewport: ${activeViewport.label}`}
                className={cn(
                  "flex items-center gap-1 h-7 px-2 rounded-md text-[12px] font-medium transition-colors",
                  sizeMenuOpen || previewSize !== "desktop"
                    ? "bg-black/5 dark:bg-white/[0.06] text-slate-800 dark:text-slate-100"
                    : "text-[#6b7280] dark:text-slate-400 hover:bg-black/5 dark:hover:bg-white/[0.06]",
                )}>
                <ActiveViewportIcon className="w-3.5 h-3.5" />
                <ChevronDown className="w-3 h-3" />
              </button>
              {sizeMenuOpen && (
                <div className="absolute right-0 top-[calc(100%+4px)] w-40 bg-white dark:bg-[#1c2128] border border-slate-200 dark:border-[#2d333b] rounded-lg shadow-[0_8px_24px_rgba(15,23,42,0.12)] z-30 py-1">
                  {Object.entries(PREVIEW_VIEWPORTS).map(([key, vp]) => {
                    const Icon = vp.icon;
                    const active = previewSize === key;
                    return (
                      <button
                        key={key}
                        type="button"
                        onClick={() => {
                          setPreviewSize(key);
                          setSizeMenuOpen(false);
                        }}
                        className={cn(
                          "w-full flex items-center gap-2 px-3 py-1.5 text-[13px] text-left transition-colors",
                          active
                            ? "bg-blue-50 dark:bg-blue-500/15 text-blue-600 dark:text-blue-300 font-semibold"
                            : "text-slate-700 dark:text-slate-200 hover:bg-slate-100 dark:hover:bg-[#21262d]",
                        )}>
                        <Icon className="w-3.5 h-3.5" />
                        <span className="flex-1">{vp.label}</span>
                        {vp.width && (
                          <span className="text-[10px] font-mono text-slate-400 dark:text-slate-500">
                            {vp.width}
                          </span>
                        )}
                        {active && <Check className="w-3 h-3 shrink-0" />}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
            <div className="h-4 w-px bg-[#d1d5db] dark:bg-[#2d333b] mx-0.5" />
            {/* Fullscreen toggle — promotes the right panel to a viewport
                overlay. In fullscreen mode the button expands with a
                visible "Exit Preview" label to telegraph the affordance
                (Esc also works, but the label is the discoverable one). */}
            <button
              type="button"
              onClick={() => setPreviewFullscreen((v) => !v)}
              title={
                previewFullscreen
                  ? "Exit fullscreen preview (Esc)"
                  : "Full width preview"
              }
              className={cn(
                "flex items-center h-7 rounded-md transition-colors",
                previewFullscreen
                  ? "gap-1.5 px-2.5 bg-slate-900 dark:bg-white text-white dark:text-slate-900 hover:bg-slate-700 dark:hover:bg-slate-100 text-[12px] font-semibold"
                  : "w-7 justify-center text-[#6b7280] dark:text-slate-400 hover:bg-black/5 dark:hover:bg-white/[0.06]",
              )}>
              {previewFullscreen ? (
                <>
                  <Minimize2 className="w-3.5 h-3.5" />
                  Exit Preview
                </>
              ) : (
                <Maximize className="w-3.5 h-3.5" />
              )}
            </button>
          </div>
        </div>
      )}

      {/* Panel content */}
      <div className="flex-1 overflow-hidden">
        {/* Plan review screen — supersedes BuildingScreen when plan awaits confirmation */}
        {planAwaiting && currentPlanData ? (
          <PlanReviewPanel
            planData={currentPlanData}
            onConfirm={confirmPlan}
            onReject={rejectPlan}
          />
        ) : (
          // Settings tab (internal key still "dashboard") is project-management;
          // it never depends on the build, so let it render even while the
          // preview is still being prepared.
          rightPanel !== "dashboard" &&
          buildingActive && (!repoInfo.vercelUrl || previewLoading) && !previewFileMap && status !== "error"
        ) ? (
          <BuildingScreen
            status={status}
            phases={phases}
            resolvingInfo={resolvingInfo}
            resolvingProgress={resolvingProgress}
            isWizardMode={isWizardMode}
            convLoading={convLoading}
            previewLoading={previewLoading}
            previewStatusMsg={previewStatusMsg}
            previewStage={previewStage}
            projectIntakeStatus={projectIntakeStatus}
            agentStatus={agentStatus}
          />
        ) : (
          <>
            {/* SETTINGS tab (internal key still "dashboard" — label renamed
                 in the parent's tab switcher). Sub-nav inside: General /
                 Users / Billing / Danger Zone. */}
            {rightPanel === "dashboard" && (
              <ProjectDashboard
                project={{
                  // UUID PK — what project_members / project_invites FK ref.
                  // Required for Users sub-tab API calls.
                  id: conversation?.db_id || null,
                  // URL slug (TEXT) — what the workspace route is keyed by.
                  project_id: conversationId,
                  title: conversation?.title,
                  description: conversation?.description,
                  project_type: conversation?.project_type,
                  created_at: conversation?.created_at,
                }}
                builtInUrl={vercelDeployUrl || repoInfo?.deployedUrl || ''}
                subscription={subscription}
                // While the chat session row is still being fetched OR the
                // user has navigated to a brand-new (wizard-mode) project
                // that hasn't been written to the DB yet, show a skeleton.
                loading={convLoading || (!conversation && !isWizardMode)}
                onRename={handleProjectRename}
                onDelete={handleProjectDelete}
                onOpenApp={() => {
                  const u = vercelDeployUrl || repoInfo?.deployedUrl;
                  if (u) window.open(u, '_blank');
                }}
                onUpgradeClick={() => router?.push('/dashboard/billing')}
              />
            )}

            {/* PREVIEW tab — always mounted, CSS-hidden when not active.
                 The iframe holds dev-server state (HMR socket, scroll
                 position, imperative reload via iframeRef), so unmounting
                 and remounting it on every tab switch was breaking the
                 live preview. Keep it in the DOM and just toggle visibility. */}
            <div className={cn(
              "h-full flex-col relative overflow-hidden",
              rightPanel === "preview" ? "flex" : "hidden",
            )}>
                {/* Stable iframe layer — mounted once a live URL is latched and
                     kept mounted across transient state churn (auto-restart, WS
                     reconnects). Overlays render on top for non-live phases. */}
                {latchedPreviewUrl && previewPhase !== "fatal" && (
                  <div className="relative flex-1 min-h-0 flex flex-col bg-[#f1f2f6] dark:bg-[#161b22]">
                    {status === "running" && (
                      <div className="absolute top-0 left-0 right-0 h-[2px] overflow-hidden pointer-events-none z-20">
                        <div className="absolute inset-y-0 w-1/2 bg-gradient-to-r from-transparent via-orange-400 to-transparent animate-hmr-slide" />
                      </div>
                    )}
                    <div
                      className={cn(
                        "relative flex-1",
                        previewSize === "desktop"
                          ? "flex flex-col overflow-hidden"
                          : "flex items-start justify-center overflow-auto bg-[#e5e7eb] dark:bg-[#0d1117] p-6",
                      )}>
                      <iframe
                        ref={iframeRef}
                        src={latchedPreviewUrl}
                        title="Live Preview"
                        className={cn(
                          "border-0 bg-white",
                          previewSize === "desktop"
                            ? "flex-1 w-full"
                            : "shrink-0 rounded-lg border border-slate-300 dark:border-slate-700 shadow-xl",
                        )}
                        style={
                          previewSize === "desktop"
                            ? undefined
                            : {
                                width: activeViewport.width,
                                height: activeViewport.height,
                                maxWidth: "100%",
                              }
                        }
                        sandbox="allow-same-origin allow-scripts allow-popups allow-forms"
                      />
                      {/* Base44-style inline editor — selection box,
                          tag badge, action toolbar, and inline AI
                          input. Position-fixed so it floats over the
                          iframe wherever it ends up on screen. */}
                      <PreviewEditOverlay
                        iframeRef={iframeRef}
                        selection={editSelection}
                        hover={editHover}
                        active={editSelectMode}
                        onClose={closeEditOverlay}
                        onSubmit={submitInlineEdit}
                        onManualApply={applyManualEdit}
                      />
                      {previewPhase === "live-with-restart-overlay" && (
                        <div className="absolute inset-0 flex flex-col items-center justify-center bg-white/85 dark:bg-[#0d1117]/85 backdrop-blur-sm z-30">
                          <Loader2 className="w-8 h-8 text-blue-500 animate-spin mb-3" />
                          <p className="text-[13px] font-semibold text-slate-700 dark:text-slate-200">
                            Restarting preview…
                          </p>
                          <p className="text-[12px] text-slate-500 dark:text-slate-400 max-w-sm mt-1 text-center px-6">
                            {previewStatusMsg || "Dev server is restarting — your preview will be back in a few seconds."}
                          </p>
                        </div>
                      )}
                      {previewPhase === "live-with-error-overlay" && (
                        <div className="absolute inset-0 flex flex-col items-center justify-center bg-white/95 dark:bg-[#0d1117]/95 backdrop-blur-sm z-30 p-6">
                          <div className="w-12 h-12 rounded-2xl bg-amber-50 dark:bg-amber-900/20 border border-amber-100 dark:border-amber-800/30 flex items-center justify-center mb-3">
                            <TriangleAlert className="w-6 h-6 text-amber-400" />
                          </div>
                          <p className="text-[14px] font-bold text-slate-800 dark:text-slate-200 mb-2">
                            Preview failed
                          </p>
                          <pre className="text-[11px] font-mono text-slate-600 dark:text-slate-400 max-w-2xl max-h-48 overflow-auto mb-4 text-left whitespace-pre-wrap break-words bg-slate-50 dark:bg-slate-900/50 rounded-lg p-3 border border-slate-200 dark:border-slate-800">
                            {previewError?.message || "Dev server stopped responding."}
                          </pre>
                          <button
                            onClick={() => retry("preview")}
                            className="flex items-center gap-2 px-4 py-2 rounded-xl bg-slate-900 dark:bg-white text-white dark:text-slate-900 text-[12px] font-semibold hover:bg-slate-800 dark:hover:bg-slate-100 transition-colors shadow-sm">
                            <RefreshCw className="w-3.5 h-3.5" />
                            Restart Preview
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {/* Full-screen panels — only shown when there's no latched URL.
                     Once we have a URL the iframe handles rendering and overlays
                     handle transient states, so these never re-mount. */}
                {!latchedPreviewUrl && previewPhase === "fatal" && (
                  retryCount >= 3 ? (
                    <div className="flex flex-col items-center justify-center h-full text-center p-8 bg-white dark:bg-[#0d1117]">
                      <div className="w-16 h-16 rounded-2xl bg-red-50 dark:bg-red-900/20 flex items-center justify-center mb-5">
                        <AlertCircle className="w-8 h-8 text-red-400" />
                      </div>
                      <h3 className="text-lg font-bold text-slate-800 dark:text-slate-200 mb-2">
                        Still having trouble
                      </h3>
                      <p className="text-[13px] text-slate-500 dark:text-slate-400 max-w-sm mb-6 leading-relaxed">
                        {error || "The workspace could not be initialized after multiple attempts."}
                      </p>
                      <div className="flex flex-col items-center gap-3 w-full max-w-xs">
                        {isNewProject && (
                          <button
                            onClick={() => retry()}
                            className="w-full flex items-center justify-center gap-2 px-5 py-2.5 rounded-xl bg-slate-900 dark:bg-white text-white dark:text-slate-900 text-[13px] font-semibold hover:bg-slate-800 dark:hover:bg-slate-100 transition-colors shadow-sm">
                            <RefreshCw className="w-4 h-4" />
                            Try a different template
                          </button>
                        )}
                        <a
                          href="mailto:support@lucid.ai"
                          className="text-[13px] text-[#dc5426] dark:text-orange-400 hover:underline">
                          Contact support
                        </a>
                      </div>
                    </div>
                  ) : (
                    <div className="flex flex-col items-center justify-center h-full text-center p-8 bg-white dark:bg-[#0d1117]">
                      <div className="absolute bottom-0 left-0 right-0 h-[40%] bg-gradient-to-t from-red-50/60 to-transparent dark:from-red-950/20 dark:to-transparent pointer-events-none" />
                      <div className="relative z-10 w-16 h-16 rounded-2xl bg-red-50 dark:bg-red-900/20 border border-red-100 dark:border-red-800/30 flex items-center justify-center mb-5 shadow-sm">
                        {errorStage === "clone" ? (
                          <GitBranch className="w-7 h-7 text-red-400" />
                        ) : (
                          <AlertCircle className="w-7 h-7 text-red-400" />
                        )}
                      </div>
                      <h3 className="relative z-10 text-lg font-bold text-slate-800 dark:text-slate-200 mb-2">
                        {errorStage === "clone" ? "Repository clone failed" : "Workspace error"}
                      </h3>
                      <p className="relative z-10 text-[13px] text-slate-500 dark:text-slate-400 max-w-sm mb-1.5 leading-relaxed">
                        {error || "Something went wrong initializing your workspace."}
                      </p>
                      {errorStage === "clone" && (
                        <p className="relative z-10 text-[11px] text-slate-400 dark:text-slate-500 max-w-xs mb-6">
                          Check that your repository URL and access token are correct, then retry.
                        </p>
                      )}
                      {!errorStage && <div className="mb-6" />}
                      <div className="relative z-10 flex items-center gap-2 mt-2">
                        <button
                          onClick={() => retry()}
                          className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-slate-900 dark:bg-white text-white dark:text-slate-900 text-[13px] font-semibold hover:bg-slate-800 dark:hover:bg-slate-100 transition-colors shadow-sm">
                          <RefreshCw className="w-4 h-4" />
                          {errorStage === "clone" ? "Retry clone" : "Retry"}
                        </button>
                      </div>
                      {retryCount > 0 && (
                        <p className="relative z-10 text-[11px] text-slate-400 dark:text-slate-500 mt-3">
                          Attempt {retryCount + 1} of 3
                        </p>
                      )}
                    </div>
                  )
                )}

                {!latchedPreviewUrl && previewPhase === "crashed" && (
                  retryCount >= 3 ? (
                    <div className="flex flex-col items-center justify-center h-full text-center p-8 bg-white dark:bg-[#0d1117]">
                      <div className="w-14 h-14 rounded-2xl bg-slate-100 dark:bg-slate-800 flex items-center justify-center mb-4">
                        <Monitor className="w-7 h-7 text-slate-400" />
                      </div>
                      <h3 className="text-base font-bold text-slate-800 dark:text-slate-200 mb-2">
                        Preview unavailable
                      </h3>
                      <pre className="text-[11px] font-mono text-slate-600 dark:text-slate-400 max-w-2xl max-h-56 overflow-auto mb-5 text-left whitespace-pre-wrap break-words bg-slate-50 dark:bg-slate-900/50 rounded-lg p-3 border border-slate-200 dark:border-slate-800">
                        {previewError?.message || "The preview server could not be started after several attempts."}
                      </pre>
                      <button
                        onClick={() => setRightPanel("code")}
                        className="flex items-center gap-2 px-4 py-2 rounded-xl bg-slate-900 dark:bg-white text-white dark:text-slate-900 text-[13px] font-semibold hover:bg-slate-800 dark:hover:bg-slate-100 transition-colors shadow-sm">
                        <Code2 className="w-4 h-4" />
                        View Source Code
                      </button>
                    </div>
                  ) : (
                    <div className="flex flex-col items-center justify-center h-full text-center p-8 bg-white dark:bg-[#0d1117]">
                      <div className="w-14 h-14 rounded-2xl bg-amber-50 dark:bg-amber-900/20 border border-amber-100 dark:border-amber-800/30 flex items-center justify-center mb-4">
                        <TriangleAlert className="w-7 h-7 text-amber-400" />
                      </div>
                      <h3 className="text-base font-bold text-slate-800 dark:text-slate-200 mb-2">
                        Preview failed
                      </h3>
                      <pre className="text-[11px] font-mono text-slate-600 dark:text-slate-400 max-w-2xl max-h-56 overflow-auto mb-5 text-left whitespace-pre-wrap break-words bg-slate-50 dark:bg-slate-900/50 rounded-lg p-3 border border-slate-200 dark:border-slate-800">
                        {previewError?.message || "Dev server failed to start."}
                      </pre>
                      <button
                        onClick={() => retry("preview")}
                        className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-slate-900 dark:bg-white text-white dark:text-slate-900 text-[13px] font-semibold hover:bg-slate-800 dark:hover:bg-slate-100 transition-colors shadow-sm">
                        <RefreshCw className="w-4 h-4" />
                        Restart Preview
                      </button>
                      {retryCount > 0 && (
                        <p className="text-[11px] text-slate-400 dark:text-slate-500 mt-3">
                          Attempt {retryCount + 1} of 3
                        </p>
                      )}
                    </div>
                  )
                )}

                {!latchedPreviewUrl && previewPhase === "wizard-empty" && (
                  <div className="flex flex-col items-center justify-center h-full text-center p-8 bg-white dark:bg-[#0d1117]">
                    <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-indigo-500 to-blue-600 flex items-center justify-center mb-5 shadow-lg shadow-blue-500/20">
                      <Wand2 className="w-8 h-8 text-white" />
                    </div>
                    <h3 className="text-lg font-bold text-slate-800 dark:text-slate-200 mb-2">
                      Your canvas is ready
                    </h3>
                    <p className="text-[14px] text-slate-500 dark:text-slate-400 max-w-sm mb-6">
                      Describe your project in the chat to get started.
                    </p>
                    <div className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800/40 text-blue-600 dark:text-blue-400 text-[13px] font-semibold">
                      <MessageCircle className="w-4 h-4" />
                      Chat to generate code
                    </div>
                  </div>
                )}

                {!latchedPreviewUrl && previewPhase === "stopped" && (
                  <div className="flex flex-col items-center justify-center h-full text-center p-8 bg-white dark:bg-[#0d1117]">
                    <div className="w-16 h-16 rounded-2xl bg-slate-100 dark:bg-slate-800 flex items-center justify-center mb-5">
                      <Monitor className="w-8 h-8 text-slate-400 dark:text-slate-500" />
                    </div>
                    <h3 className="text-lg font-bold text-slate-800 dark:text-slate-200 mb-2">
                      Preview stopped
                    </h3>
                    <p className="text-[14px] text-slate-500 dark:text-slate-400 max-w-sm mb-6">
                      The dev server is no longer running. Restart it to bring the preview back.
                    </p>
                    <div className="flex flex-col items-center gap-3 w-full max-w-xs">
                      <button
                        onClick={() => retry("preview")}
                        className="w-full flex items-center justify-center gap-2 px-5 py-2.5 rounded-xl bg-slate-900 dark:bg-white text-white dark:text-slate-900 text-[13px] font-semibold hover:bg-slate-800 dark:hover:bg-slate-100 transition-colors shadow-sm">
                        <RefreshCw className="w-4 h-4" />
                        Restart Preview
                      </button>
                    </div>
                  </div>
                )}

                {/* "preparing" + "booting" share the same UI — the only difference
                     is the sub-text source. Single block, no flicker between them. */}
                {!latchedPreviewUrl && (previewPhase === "preparing" || previewPhase === "booting") && (
                  <PreviewBootingPanel
                    previewPhase={previewPhase}
                    previewStage={previewStage}
                    previewStatusMsg={previewStatusMsg}
                    previewStartedAt={previewStartedAt}
                    workspaceStatus={status}
                  />
                )}
              </div>

            {/* CODE tab */}
            {rightPanel === "code" && (
              <div className="flex h-full w-full">
                {/* File sidebar */}
                <div className="w-[240px] shrink-0 border-r border-slate-200 dark:border-[#2d333b] bg-white dark:bg-[#0f1118] flex flex-col">
                  <div className="h-11 flex items-center justify-between px-4 border-b border-slate-200 dark:border-[#1c2128]">
                    <span className="text-[14px] font-bold text-slate-800 dark:text-white">
                      Code
                    </span>
                    <div className="flex items-center gap-0.5">
                      <button
                        className="p-1.5 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-white/[0.06] rounded transition-colors"
                        title="Activity">
                        <Activity className="w-3.5 h-3.5" />
                      </button>
                      <button
                        className="p-1.5 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-white/[0.06] rounded transition-colors"
                        title="Download">
                        <Download className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                  <div className="h-10 flex items-center justify-between px-3 border-b border-slate-100 dark:border-[#1c2128]">
                    <span className="text-[12px] font-bold text-slate-500 dark:text-slate-400">
                      Code files
                    </span>
                    <div className="flex items-center gap-0.5">
                      <button
                        className="p-1.5 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded transition-colors"
                        title="Search">
                        <Search className="w-3.5 h-3.5" />
                      </button>
                      <button
                        className="p-1.5 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded transition-colors"
                        title="Toggle">
                        <Code2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                  <div className="flex-1 overflow-hidden">
                    {[
                      "connecting",
                      "idle",
                      "cloning",
                      "installing",
                      "starting",
                      "health_check",
                      "preparing",
                    ].includes(status) &&
                    files.length === 0 &&
                    !isNewProject ? (
                      <div className="flex flex-col items-center justify-center h-full gap-3 px-4 py-8">
                        <Loader2 className="w-5 h-5 text-blue-400 animate-spin" />
                        <div className="text-center space-y-1">
                          <p className="text-[12px] font-medium text-slate-500 dark:text-slate-400">
                            {status === "cloning"
                              ? "Cloning repository…"
                              : status === "installing"
                                ? "Installing dependencies…"
                                : status === "starting"
                                  ? "Running the code for Preview…"
                                  : status === "health_check"
                                    ? "Running the code for Preview…"
                                    : "Preparing workspace…"}
                          </p>
                          <p className="text-[11px] text-slate-400 dark:text-slate-500">
                            Files will appear here shortly
                          </p>
                        </div>
                        <div className="w-full mt-2 space-y-2 px-2 animate-pulse">
                          {[70, 55, 80, 45, 65, 50, 72].map((w, i) => (
                            <div key={i} className="flex items-center gap-2">
                              <div className="w-3.5 h-3.5 rounded bg-slate-200 dark:bg-slate-700 shrink-0" />
                              <div
                                className="h-2.5 rounded bg-slate-200 dark:bg-slate-700"
                                style={{width: `${w}%`}}
                              />
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : resolvingInfo?.path === "new_project" &&
                      files.length === 0 ? (
                      <div className="flex flex-col items-center justify-center h-full gap-3 px-4 py-8 text-center">
                        <div className="w-10 h-10 rounded-xl bg-blue-50 dark:bg-blue-900/20 flex items-center justify-center">
                          <Wand2 className="w-5 h-5 text-blue-400" />
                        </div>
                        <div className="space-y-1">
                          <p className="text-[12px] font-semibold text-slate-600 dark:text-slate-300">
                            Waiting for AI
                          </p>
                          <p className="text-[11px] text-slate-400 dark:text-slate-500 leading-relaxed">
                            Files will appear here once the agent starts
                            generating code
                          </p>
                        </div>
                      </div>
                    ) : (
                      <FileExplorer
                        files={files}
                        selectedFile={selectedFile}
                        onFileSelect={handleFileSelect}
                        projectName={conversation?.title || "Project"}
                        fileMetrics={fileMetrics}
                      />
                    )}
                  </div>
                </div>

                {/* Editor area */}
                <div className="flex-1 min-w-0 bg-white dark:bg-[#0d1117] flex flex-col">
                  {(() => {
                    const snapshot = selectedFile && fileContents ? fileContents[selectedFile] : null;
                    const metric = selectedFile && fileMetrics ? fileMetrics[selectedFile] : null;
                    const hasDiff = !!snapshot;
                    const showDiff = hasDiff && codeViewMode === "diff";

                    return (
                      <>
                        {selectedFile && hasDiff && (
                          <div className="shrink-0 flex items-center justify-between px-4 py-2 border-b border-slate-200 dark:border-[#2d333b] bg-slate-50/60 dark:bg-[#0f1118]">
                            <div className="inline-flex items-center bg-slate-200/60 dark:bg-[#21262d] rounded-md p-0.5">
                              <button
                                onClick={() => setCodeViewMode("diff")}
                                className={cn(
                                  "px-2.5 h-6 rounded text-[11px] font-semibold transition-colors",
                                  codeViewMode === "diff"
                                    ? "bg-white dark:bg-[#0d1117] text-slate-900 dark:text-white shadow-sm"
                                    : "text-slate-500 dark:text-slate-400 hover:text-slate-700",
                                )}>
                                Diff
                              </button>
                              <button
                                onClick={() => setCodeViewMode("source")}
                                className={cn(
                                  "px-2.5 h-6 rounded text-[11px] font-semibold transition-colors",
                                  codeViewMode === "source"
                                    ? "bg-white dark:bg-[#0d1117] text-slate-900 dark:text-white shadow-sm"
                                    : "text-slate-500 dark:text-slate-400 hover:text-slate-700",
                                )}>
                                Source
                              </button>
                            </div>
                          </div>
                        )}

                        {selectedFile && !hasDiff && (
                          <div className="shrink-0 px-4 py-2 bg-amber-50 dark:bg-amber-900/10 border-b border-amber-200/70 dark:border-amber-700/30 flex items-center gap-1">
                            <span className="text-[13px] text-amber-800 dark:text-amber-300">
                              Code editing is only available on paid plans.{" "}
                              <button
                                onClick={() => setShowExportModal(true)}
                                className="font-semibold text-blue-500 hover:text-blue-600 hover:underline transition-colors">
                                Upgrade your plan
                              </button>
                            </span>
                          </div>
                        )}

                        <div className="flex-1 min-h-0">
                          {showDiff ? (
                            <DiffViewer
                              filename={selectedFile}
                              previous={snapshot.previous || ""}
                              current={snapshot.current || ""}
                              truncated={!!snapshot.truncated}
                              metrics={metric}
                            />
                          ) : (
                            <FileViewer
                              path={selectedFile}
                              content={
                                // If we have a fresh snapshot from the agent,
                                // prefer it over re-fetching from disk.
                                editedFileContent !== null
                                  ? editedFileContent
                                  : (snapshot?.current ?? fileContent)
                              }
                              loading={fileLoading && !snapshot}
                              onContentChange={(val) => setEditedFileContent(val)}
                            />
                          )}
                        </div>
                      </>
                    );
                  })()}
                </div>
              </div>
            )}

            {/* TERMINAL tab */}
            {rightPanel === "terminal" && (
              <div className="h-full flex flex-col bg-[#1e1e2e]">
                <div className="flex-1 overflow-y-auto p-4 font-mono text-xs leading-relaxed custom-scrollbar">
                  {terminalLogs.length === 0 ? (
                    <div className="h-full flex flex-col items-center justify-center text-slate-600 space-y-3">
                      <Terminal className="w-8 h-8 opacity-20" />
                      <p>Ready to execute commands...</p>
                      <span className="text-[10px] bg-slate-800/50 px-2 py-1 rounded text-slate-500">
                        Waiting for agent
                      </span>
                    </div>
                  ) : (
                    terminalLogs
                      .filter((log) => log.type !== "user")
                      .map((log) => (
                        <div key={log.id} className="mb-2 break-all group">
                          <span
                            className={cn(
                              "whitespace-pre-wrap",
                              log.type === "error" ||
                                log.content?.includes("[ERROR]")
                                ? "text-red-400"
                                : log.content?.startsWith("$")
                                  ? "text-emerald-400 font-bold"
                                  : log.type === "file_write"
                                    ? "text-amber-400"
                                    : "text-slate-300",
                            )}>
                            {log.content}
                          </span>
                        </div>
                      ))
                  )}
                  <div ref={logsEndRef} />
                </div>
                <div className="p-3 bg-[#181825] border-t border-slate-800">
                  <div className="flex items-center gap-2 px-3 py-2 bg-[#1e1e2e] rounded-lg border border-slate-700 focus-within:border-emerald-500/50 focus-within:ring-1 focus-within:ring-emerald-500/20 transition-all">
                    <span className="text-emerald-500 font-mono">$</span>
                    <input
                      type="text"
                      placeholder="Run command..."
                      className="flex-1 bg-transparent border-none outline-none text-emerald-100 text-xs font-mono placeholder:text-slate-600"
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && e.currentTarget.value.trim()) {
                          sendMessage(e.currentTarget.value.trim());
                          e.currentTarget.value = "";
                        }
                      }}
                    />
                  </div>
                </div>
              </div>
            )}

            {/* Fallback — no panel selected */}
            {!rightPanel && (
              <div className="h-full flex flex-col items-center justify-center text-center p-8 bg-[#f8f9fb] dark:bg-[#0d1117]">
                <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-indigo-500 to-blue-600 flex items-center justify-center mb-5 shadow-lg shadow-blue-500/20">
                  <Monitor className="w-7 h-7 text-white" />
                </div>
                <h3 className="text-[18px] font-bold text-slate-800 dark:text-white mb-2">
                  Preview & Build
                </h3>
                <p className="text-[13px] text-slate-400 dark:text-slate-500 max-w-sm">
                  Start a conversation to generate your app.
                </p>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
