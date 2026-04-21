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
  Loader2,
  Check,
  ArrowRight,
  X,
} from "lucide-react";
import {useWorkspace} from "@/contexts/WorkspaceContext";
import FileViewer from "@/components/workspace/FileViewer";
import FileExplorer from "@/components/agent/FileExplorer";
import TaskProgress from "@/components/TaskProgress";
import BuildingScreen from "@/components/workspace/BuildingScreen";

// ── PlanReviewPanel — full right-panel plan review UI ────────
function PlanReviewPanel({planData, onConfirm, onReject}) {
  const [showCorrection, setShowCorrection] = useState(false);
  const [correctionText, setCorrectionText] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [rejected, setRejected] = useState(false);

  const pages = planData?.pages || [];
  const entities = planData?.entities || [];

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
        <div className="w-14 h-14 rounded-full bg-emerald-100 dark:bg-emerald-900/30 flex items-center justify-center">
          <Check className="w-7 h-7 text-emerald-500" />
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
        <div className="w-14 h-14 rounded-full bg-emerald-100 dark:bg-emerald-900/30 flex items-center justify-center">
          <Sparkles className="w-7 h-7 text-emerald-500" />
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
          <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-emerald-500 to-teal-600 flex items-center justify-center shadow-sm shadow-emerald-500/20">
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

        {/* Pages / Sections */}
        {pages.length > 0 && (
          <section>
            <p className="text-[11px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider mb-2">
              {entities.length > 0 ? "Pages" : "Sections"}
            </p>
            <div className="grid grid-cols-1 gap-1.5">
              {pages.map((p, i) => (
                <div
                  key={i}
                  className="flex items-start gap-2.5 px-3 py-2 rounded-lg bg-slate-50 dark:bg-[#161b22] border border-slate-100 dark:border-[#2d333b]">
                  <ArrowRight className="w-3.5 h-3.5 text-emerald-400 dark:text-emerald-500 shrink-0 mt-0.5" />
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
      </div>

      {/* Confirmation footer */}
      <div className="shrink-0 px-8 py-6 border-t border-slate-100 dark:border-[#1c2128] bg-slate-50/50 dark:bg-[#161b22]/50">
        {!showCorrection ? (
          <div className="flex items-center gap-3">
            <button
              onClick={handleConfirm}
              className="flex items-center justify-center gap-2 px-6 py-3 rounded-xl bg-emerald-500 hover:bg-emerald-600 text-white text-[14px] font-semibold transition-all shadow-sm shadow-emerald-500/20 hover:shadow-emerald-500/30">
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
              className="w-full px-3.5 py-2.5 rounded-xl border border-slate-200 dark:border-[#2d333b] bg-white dark:bg-[#0d1117] text-[13px] text-slate-700 dark:text-slate-200 placeholder:text-slate-400 dark:placeholder:text-slate-500 outline-none focus:border-emerald-400 dark:focus:border-emerald-500 resize-none min-h-[72px]"
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
                    ? "bg-emerald-500 hover:bg-emerald-600 text-white shadow-sm shadow-emerald-500/20"
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
    stopPreview,
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
  } = useWorkspace();

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
    <div className="flex-1 flex flex-col min-w-0 bg-[#f1f2f6] dark:bg-[#0d1117] m-[12px] border border-[#e3e5eb] dark:border-[#1c2128] rounded-xl overflow-hidden">
      {/* Preview toolbar — only shown when Preview tab is active */}
      {rightPanel === "preview" && (
        <div className="shrink-0 h-[42px] flex items-center justify-between px-3 bg-[#fff] dark:bg-[#161b22] border-b border-[#e3e5eb] dark:border-[#2d333b]">
          <div className="flex items-center gap-0.5 flex-1">
            <button className="flex items-center gap-1.5 h-7 px-2.5 rounded-md text-[13px] font-medium text-[#374151] dark:text-slate-200 hover:bg-black/5 dark:hover:bg-white/[0.06] transition-colors">
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
                  if (!iframe) return;
                  const src = iframe.src;
                  iframe.src = "";
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
                className="text-[#9ca3af] hover:text-[#6b7280] transition-colors"
                title="Open in new tab"
                onClick={() =>
                  repoInfo.vercelUrl &&
                  window.open(repoInfo.vercelUrl, "_blank")
                }>
                <ChevronDown className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
          <div className="flex items-center justify-end flex-1 gap-0.5">
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
            <button className="flex items-center gap-1 h-7 px-2 rounded-md text-[#6b7280] dark:text-slate-400 hover:bg-black/5 dark:hover:bg-white/[0.06] transition-colors">
              <Monitor className="w-3.5 h-3.5" />
              <ChevronDown className="w-3 h-3" />
            </button>
            <div className="h-4 w-px bg-[#d1d5db] dark:bg-[#2d333b] mx-0.5" />
            <button className="h-7 w-7 flex items-center justify-center rounded-md text-[#6b7280] dark:text-slate-400 hover:bg-black/5 dark:hover:bg-white/[0.06] transition-colors">
              <Maximize className="w-3.5 h-3.5" />
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
        ) : buildingActive && (!repoInfo.vercelUrl || previewLoading) && !previewFileMap && status !== "error" ? (
          <BuildingScreen
            status={status}
            phases={phases}
            resolvingInfo={resolvingInfo}
            resolvingProgress={resolvingProgress}
            isWizardMode={isWizardMode}
            convLoading={convLoading}
            previewLoading={previewLoading}
            previewStatusMsg={previewStatusMsg}
          />
        ) : (
          <>
            {/* DASHBOARD tab */}
            {rightPanel === "dashboard" && (
              <div className="h-full overflow-y-auto bg-white dark:bg-[#0d1117] p-6 lg:p-10 custom-scrollbar">
                <div className="max-w-4xl mx-auto space-y-8">
                  <div className="flex items-center gap-4 border-b border-slate-100 dark:border-[#1c2128] pb-6">
                    <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-emerald-500 to-teal-600 flex items-center justify-center shrink-0">
                      <Sparkles className="w-6 h-6 text-white" />
                    </div>
                    <div>
                      <h2 className="text-xl font-bold text-slate-800 dark:text-slate-100">
                        Building Your Project
                      </h2>
                      <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">
                        {status === "cloning"
                          ? "Cloning your codebase..."
                          : status === "installing"
                            ? "Installing dependencies..."
                            : status === "starting"
                              ? "Running the code for Preview..."
                              : status === "health_check"
                                ? "Running the code for Preview..."
                                : status === "preparing"
                                  ? "Preparing workspace..."
                                  : status === "running"
                                    ? "Agent actively working..."
                                    : phases.length > 0
                                      ? "Build in progress..."
                                      : "Waiting to start."}
                      </p>
                    </div>
                  </div>
                  <TaskProgress
                    phases={phases}
                    status={status}
                    completionSummary={completionSummary}
                  />
                </div>
              </div>
            )}

            {/* PREVIEW tab */}
            {rightPanel === "preview" && (
              <div className="h-full flex flex-col relative overflow-hidden">
                {status === "error" ? (
                  retryCount >= 3 ? (
                    <div className="flex flex-col items-center justify-center h-full text-center p-8 bg-white dark:bg-[#0d1117]">
                      <div className="w-16 h-16 rounded-2xl bg-red-50 dark:bg-red-900/20 flex items-center justify-center mb-5">
                        <AlertCircle className="w-8 h-8 text-red-400" />
                      </div>
                      <h3 className="text-lg font-bold text-slate-800 dark:text-slate-200 mb-2">
                        Still having trouble
                      </h3>
                      <p className="text-[13px] text-slate-500 dark:text-slate-400 max-w-sm mb-6 leading-relaxed">
                        {error ||
                          "The workspace could not be initialized after multiple attempts."}
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
                          className="text-[13px] text-emerald-600 dark:text-emerald-400 hover:underline">
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
                        {errorStage === "clone"
                          ? "Repository clone failed"
                          : "Workspace error"}
                      </h3>
                      <p className="relative z-10 text-[13px] text-slate-500 dark:text-slate-400 max-w-sm mb-1.5 leading-relaxed">
                        {error ||
                          "Something went wrong initializing your workspace."}
                      </p>
                      {errorStage === "clone" && (
                        <p className="relative z-10 text-[11px] text-slate-400 dark:text-slate-500 max-w-xs mb-6">
                          Check that your repository URL and access token are
                          correct, then retry.
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
                ) : repoInfo.vercelUrl ? (
                  /* ── Primary: local dev server or Vercel/deploy URL ── */
                  <div className="relative flex-1 min-h-0 flex flex-col bg-[#f1f2f6] dark:bg-[#161b22]">
                    {status === "running" && (
                      <div className="absolute top-0 left-0 right-0 h-[2px] overflow-hidden pointer-events-none z-20">
                        <div className="absolute inset-y-0 w-1/2 bg-gradient-to-r from-transparent via-emerald-400 to-transparent animate-hmr-slide" />
                      </div>
                    )}
                    <div className="relative flex-1 flex flex-col overflow-hidden">
                      <iframe
                        ref={iframeRef}
                        src={repoInfo.vercelUrl}
                        title="Live Preview"
                        className="flex-1 w-full border-0 bg-white"
                        sandbox="allow-same-origin allow-scripts allow-popups allow-forms"
                      />
                    </div>
                  </div>
                ) : previewError ? (
                  retryCount >= 3 ? (
                    <div className="flex flex-col items-center justify-center h-full text-center p-8 bg-white dark:bg-[#0d1117]">
                      <div className="w-14 h-14 rounded-2xl bg-slate-100 dark:bg-slate-800 flex items-center justify-center mb-4">
                        <Monitor className="w-7 h-7 text-slate-400" />
                      </div>
                      <h3 className="text-base font-bold text-slate-800 dark:text-slate-200 mb-2">
                        Preview unavailable
                      </h3>
                      <p className="text-[13px] text-slate-500 dark:text-slate-400 max-w-sm mb-5 leading-relaxed">
                        The preview server could not be started after several
                        attempts. You can still chat and edit code.
                      </p>
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
                      <p className="text-[13px] text-slate-500 dark:text-slate-400 max-w-sm mb-5 leading-relaxed">
                        {previewError.message}
                      </p>
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
                ) : previewLoading ? (
                  <div className="flex flex-col items-center justify-center h-full text-center p-8 bg-white dark:bg-[#0d1117]">
                    <div className="w-16 h-16 rounded-2xl bg-slate-100 dark:bg-slate-800 flex items-center justify-center mb-5">
                      <Loader2 className="w-8 h-8 text-blue-500 animate-spin" />
                    </div>
                    <h3 className="text-lg font-bold text-slate-800 dark:text-slate-200 mb-2">
                      Setting up preview…
                    </h3>
                    <p className="text-[14px] text-slate-500 dark:text-slate-400 max-w-sm">
                      {previewStatusMsg || 'Starting cloud sandbox and installing dependencies. This can take a minute.'}
                    </p>
                  </div>
                ) : isNewProject && files.length === 0 ? (
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
                ) : (
                  <div className="flex flex-col items-center justify-center h-full text-center p-8 bg-white dark:bg-[#0d1117]">
                    <div className="w-16 h-16 rounded-2xl bg-slate-100 dark:bg-slate-800 flex items-center justify-center mb-5">
                      <Monitor className="w-8 h-8 text-slate-400 dark:text-slate-500" />
                    </div>
                    <h3 className="text-lg font-bold text-slate-800 dark:text-slate-200 mb-2">
                      Preview Not Available
                    </h3>
                    <p className="text-[14px] text-slate-500 dark:text-slate-400 max-w-sm mb-6">
                      The preview server may have stopped. Try restarting it.
                    </p>
                    <div className="flex flex-col items-center gap-3 w-full max-w-xs">
                      <button
                        onClick={() => retry("preview")}
                        className="w-full flex items-center justify-center gap-2 px-5 py-2.5 rounded-xl bg-slate-900 dark:bg-white text-white dark:text-slate-900 text-[13px] font-semibold hover:bg-slate-800 dark:hover:bg-slate-100 transition-colors shadow-sm">
                        <RefreshCw className="w-4 h-4" />
                        Restart Preview
                      </button>
                      <button
                        onClick={() => setRightPanel("code")}
                        className="flex items-center gap-2 px-4 py-2 rounded-xl text-[13px] font-medium text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 transition-colors">
                        <Code2 className="w-4 h-4" />
                        View Source Code
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}

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
                      />
                    )}
                  </div>
                </div>

                {/* Editor area */}
                <div className="flex-1 min-w-0 bg-white flex flex-col">
                  {selectedFile && (
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
                    <FileViewer
                      path={selectedFile}
                      content={
                        editedFileContent !== null
                          ? editedFileContent
                          : fileContent
                      }
                      loading={fileLoading}
                      onContentChange={(val) => setEditedFileContent(val)}
                    />
                  </div>
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
