'use client';

// ─────────────────────────────────────────────────────────
//  RightPanel — Preview / Code / Terminal / Dashboard panels.
//  Extracted from workspace/[projectId]/page.js.
//  Reads shared workspace state via useWorkspace().
// ─────────────────────────────────────────────────────────

import { useState, useRef, useEffect, useCallback } from 'react';
import { cn } from '@/lib/utils';
import {
  Terminal, Code2, Monitor, Sparkles, AlertCircle, TriangleAlert,
  GitBranch, RefreshCw, Wand2, MessageCircle, Activity, Search,
  Download, Palette, MousePointer2, ChevronDown, Maximize, Loader2,
} from 'lucide-react';
import { useWorkspace } from '@/contexts/WorkspaceContext';
import FileViewer from '@/components/workspace/FileViewer';
import FileExplorer from '@/components/agent/FileExplorer';
import TaskProgress from '@/components/TaskProgress';
import BuildingScreen from '@/components/workspace/BuildingScreen';

export default function RightPanel() {
  const {
    rightPanel, setRightPanel,
    repoInfo,
    status,
    phases, resolvingInfo, resolvingProgress,
    isWizardMode,
    buildingActive,
    files,
    conversation,
    terminalLogs,
    sendMessage,
    error, errorStage, retryCount, retry,
    previewError,
    completionSummary,
    isNewProject,
    iframeRef,
    setShowExportModal,
    sessionId,
    panelOverrideRef,
  } = useWorkspace();

  // ── File-viewer local state ─────────────────────────────
  const [selectedFile, setSelectedFile] = useState(null);
  const [fileContent, setFileContent] = useState('');
  const [fileLoading, setFileLoading] = useState(false);
  const [editedFileContent, setEditedFileContent] = useState(null);

  // ── Terminal auto-scroll ────────────────────────────────
  const logsEndRef = useRef(null);
  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [terminalLogs]);

  // ── File select handler ─────────────────────────────────
  const handleFileSelect = useCallback(async (path) => {
    setSelectedFile(path);
    setEditedFileContent(null);
    setRightPanel('code');
    setFileLoading(true);
    setFileContent('');

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
      } catch { /* fall through */ }
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
            const decoded = data.encoding === 'base64' ? atob(data.content) : data.content;
            setFileContent(decoded);
            setFileLoading(false);
            return;
          }
        }
      } catch { /* fall through */ }
    }

    setFileContent(
      `// File content not available yet\n// Path: ${path}\n\n// The file will be viewable once the build completes.`,
    );
    setFileLoading(false);
  }, [sessionId, conversation?.repo_name, setRightPanel]);

  // ── Render ──────────────────────────────────────────────
  return (
    <div className="flex-1 flex flex-col min-w-0 bg-[#f1f2f6] dark:bg-[#0d1117] m-[12px] border border-[#e3e5eb] dark:border-[#1c2128] rounded-xl overflow-hidden">
      {/* Preview toolbar — only shown when Preview tab is active */}
      {rightPanel === 'preview' && (
        <div className="shrink-0 h-[42px] flex items-center justify-between px-3 bg-[#fff] dark:bg-[#161b22] border-b border-[#e3e5eb] dark:border-[#2d333b]">
          <div className="flex items-center gap-0.5 flex-1">
            <button className="flex items-center gap-1.5 h-7 px-2.5 rounded-md text-[13px] font-medium text-[#374151] dark:text-slate-200 hover:bg-black/5 dark:hover:bg-white/[0.06] transition-colors">
              <MousePointer2 className="w-3.5 h-3.5" />
              Edit
            </button>
            <div className="w-px h-4 bg-[#d1d5db] dark:bg-[#2d333b] mx-0.5" />
            <button className="h-7 w-7 flex items-center justify-center rounded-md text-[#6b7280] hover:text-[#374151] dark:text-slate-400 dark:hover:text-slate-200 hover:bg-black/5 dark:hover:bg-white/[0.06] transition-colors" title="Design tools">
              <Palette className="w-3.5 h-3.5" />
            </button>
          </div>
          <div className="flex justify-center flex-1">
            <div className="flex items-center w-full max-w-[320px] h-[30px] bg-white dark:bg-[#21262d] rounded-lg px-2.5 border border-[#d1d5db] dark:border-[#2d333b] shadow-sm">
              <button className="text-[#9ca3af] hover:text-[#6b7280] transition-colors">
                <RefreshCw className="w-3 h-3" />
              </button>
              <div className="flex-1 text-center font-medium text-[13px] text-[#374151] dark:text-slate-200 px-2 cursor-text select-none">/</div>
              <button className="text-[#9ca3af] hover:text-[#6b7280] transition-colors">
                <ChevronDown className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
          <div className="flex items-center justify-end flex-1 gap-0.5">
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
        {/* Universal building screen — shown while workspace is initializing */}
        {buildingActive && !repoInfo.vercelUrl && status !== 'error' ? (
          <BuildingScreen
            status={status}
            phases={phases}
            resolvingInfo={resolvingInfo}
            resolvingProgress={resolvingProgress}
            isWizardMode={isWizardMode}
          />
        ) : (
          <>
            {/* DASHBOARD tab */}
            {rightPanel === 'dashboard' && (
              <div className="h-full overflow-y-auto bg-white dark:bg-[#0d1117] p-6 lg:p-10 custom-scrollbar">
                <div className="max-w-4xl mx-auto space-y-8">
                  <div className="flex items-center gap-4 border-b border-slate-100 dark:border-[#1c2128] pb-6">
                    <div className="w-12 h-12 rounded-xl bg-blue-500 flex items-center justify-center shrink-0">
                      <Sparkles className="w-6 h-6 text-white" />
                    </div>
                    <div>
                      <h2 className="text-xl font-bold text-slate-800 dark:text-slate-100">Building Your Project</h2>
                      <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">
                        {status === 'cloning' ? 'Cloning your codebase...'
                          : status === 'installing' ? 'Installing dependencies...'
                          : status === 'starting' ? 'Starting dev server...'
                          : status === 'health_check' ? 'Connecting preview...'
                          : status === 'preparing' ? 'Preparing workspace...'
                          : status === 'running' ? 'Agent actively working...'
                          : phases.length > 0 ? 'Build in progress...'
                          : 'Waiting to start.'}
                      </p>
                    </div>
                  </div>
                  <TaskProgress phases={phases} status={status} completionSummary={completionSummary} />
                </div>
              </div>
            )}

            {/* PREVIEW tab */}
            {rightPanel === 'preview' && (
              <div className="h-full flex flex-col relative overflow-hidden">
                {status === 'error' ? (
                  retryCount >= 3 ? (
                    <div className="flex flex-col items-center justify-center h-full text-center p-8 bg-white dark:bg-[#0d1117]">
                      <div className="w-16 h-16 rounded-2xl bg-red-50 dark:bg-red-900/20 flex items-center justify-center mb-5">
                        <AlertCircle className="w-8 h-8 text-red-400" />
                      </div>
                      <h3 className="text-lg font-bold text-slate-800 dark:text-slate-200 mb-2">Still having trouble</h3>
                      <p className="text-[13px] text-slate-500 dark:text-slate-400 max-w-sm mb-6 leading-relaxed">
                        {error || 'The workspace could not be initialized after multiple attempts.'}
                      </p>
                      <div className="flex flex-col items-center gap-3 w-full max-w-xs">
                        {isNewProject && (
                          <button onClick={() => retry()} className="w-full flex items-center justify-center gap-2 px-5 py-2.5 rounded-xl bg-slate-900 dark:bg-white text-white dark:text-slate-900 text-[13px] font-semibold hover:bg-slate-800 dark:hover:bg-slate-100 transition-colors shadow-sm">
                            <RefreshCw className="w-4 h-4" />
                            Try a different template
                          </button>
                        )}
                        <a href="mailto:support@lucid.ai" className="text-[13px] text-blue-600 dark:text-blue-400 hover:underline">Contact support</a>
                      </div>
                    </div>
                  ) : (
                    <div className="flex flex-col items-center justify-center h-full text-center p-8 bg-white dark:bg-[#0d1117]">
                      <div className="absolute bottom-0 left-0 right-0 h-[40%] bg-gradient-to-t from-red-50/60 to-transparent dark:from-red-950/20 dark:to-transparent pointer-events-none" />
                      <div className="relative z-10 w-16 h-16 rounded-2xl bg-red-50 dark:bg-red-900/20 border border-red-100 dark:border-red-800/30 flex items-center justify-center mb-5 shadow-sm">
                        {errorStage === 'clone' ? <GitBranch className="w-7 h-7 text-red-400" /> : <AlertCircle className="w-7 h-7 text-red-400" />}
                      </div>
                      <h3 className="relative z-10 text-lg font-bold text-slate-800 dark:text-slate-200 mb-2">
                        {errorStage === 'clone' ? 'Repository clone failed' : 'Workspace error'}
                      </h3>
                      <p className="relative z-10 text-[13px] text-slate-500 dark:text-slate-400 max-w-sm mb-1.5 leading-relaxed">
                        {error || 'Something went wrong initializing your workspace.'}
                      </p>
                      {errorStage === 'clone' && (
                        <p className="relative z-10 text-[11px] text-slate-400 dark:text-slate-500 max-w-xs mb-6">
                          Check that your repository URL and access token are correct, then retry.
                        </p>
                      )}
                      {!errorStage && <div className="mb-6" />}
                      <div className="relative z-10 flex items-center gap-2 mt-2">
                        <button onClick={() => retry()} className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-slate-900 dark:bg-white text-white dark:text-slate-900 text-[13px] font-semibold hover:bg-slate-800 dark:hover:bg-slate-100 transition-colors shadow-sm">
                          <RefreshCw className="w-4 h-4" />
                          {errorStage === 'clone' ? 'Retry clone' : 'Retry'}
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
                  <div className="relative flex-1 min-h-0 flex flex-col bg-[#f1f2f6] dark:bg-[#161b22] p-3">
                    <div className="relative flex-1 flex flex-col rounded-xl overflow-hidden shadow-sm border border-slate-200 dark:border-[#2d333b]">
                      <iframe
                        ref={iframeRef}
                        src={repoInfo.vercelUrl}
                        title="Live Preview"
                        className="flex-1 w-full border-0 bg-white"
                        sandbox="allow-same-origin allow-scripts allow-popups allow-forms"
                      />
                      {status === 'running' && (
                        <>
                          <div className="absolute top-0 left-0 right-0 h-[2px] overflow-hidden pointer-events-none z-10">
                            <div className="absolute inset-y-0 w-1/2 bg-gradient-to-r from-transparent via-orange-400 to-transparent animate-hmr-slide" />
                          </div>
                          <div className="absolute top-2 right-2 z-10">
                            <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-black/40 backdrop-blur-sm">
                              <div className="w-1.5 h-1.5 rounded-full bg-orange-400 animate-pulse" />
                              <span className="text-[10px] font-semibold text-white/90">Updating</span>
                            </div>
                          </div>
                        </>
                      )}
                    </div>
                  </div>
                ) : previewError ? (
                  retryCount >= 3 ? (
                    <div className="flex flex-col items-center justify-center h-full text-center p-8 bg-white dark:bg-[#0d1117]">
                      <div className="w-14 h-14 rounded-2xl bg-slate-100 dark:bg-slate-800 flex items-center justify-center mb-4">
                        <Monitor className="w-7 h-7 text-slate-400" />
                      </div>
                      <h3 className="text-base font-bold text-slate-800 dark:text-slate-200 mb-2">Preview unavailable</h3>
                      <p className="text-[13px] text-slate-500 dark:text-slate-400 max-w-sm mb-5 leading-relaxed">
                        The preview server could not be started after several attempts. You can still chat and edit code.
                      </p>
                      <button onClick={() => setRightPanel('code')} className="flex items-center gap-2 px-4 py-2 rounded-xl bg-slate-900 dark:bg-white text-white dark:text-slate-900 text-[13px] font-semibold hover:bg-slate-800 dark:hover:bg-slate-100 transition-colors shadow-sm">
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
                        {previewError.stage === 'health_check' ? 'Preview tunnel failed' : 'Preview server failed'}
                      </h3>
                      <p className="text-[13px] text-slate-500 dark:text-slate-400 max-w-sm mb-5 leading-relaxed">
                        {previewError.message}
                      </p>
                      <button onClick={() => retry('preview')} className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-slate-900 dark:bg-white text-white dark:text-slate-900 text-[13px] font-semibold hover:bg-slate-800 dark:hover:bg-slate-100 transition-colors shadow-sm">
                        <RefreshCw className="w-4 h-4" />
                        {previewError.stage === 'health_check' ? 'Restart with fresh port' : 'Restart Preview'}
                      </button>
                      {retryCount > 0 && (
                        <p className="text-[11px] text-slate-400 dark:text-slate-500 mt-3">Attempt {retryCount + 1} of 3</p>
                      )}
                    </div>
                  )
                ) : isNewProject && files.length === 0 ? (
                  <div className="flex flex-col items-center justify-center h-full text-center p-8 bg-white dark:bg-[#0d1117]">
                    <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-orange-400 to-orange-600 flex items-center justify-center mb-5 shadow-lg shadow-orange-500/20">
                      <Wand2 className="w-8 h-8 text-white" />
                    </div>
                    <h3 className="text-lg font-bold text-slate-800 dark:text-slate-200 mb-2">Your canvas is ready</h3>
                    <p className="text-[14px] text-slate-500 dark:text-slate-400 max-w-sm mb-6">
                      Describe your project in the chat to get started.
                    </p>
                    <div className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-orange-50 dark:bg-orange-900/20 border border-orange-200 dark:border-orange-800/40 text-orange-600 dark:text-orange-400 text-[13px] font-semibold">
                      <MessageCircle className="w-4 h-4" />
                      Chat to generate code
                    </div>
                  </div>
                ) : (
                  <div className="flex flex-col items-center justify-center h-full text-center p-8 bg-white dark:bg-[#0d1117]">
                    <div className="w-16 h-16 rounded-2xl bg-slate-100 dark:bg-slate-800 flex items-center justify-center mb-5">
                      <Monitor className="w-8 h-8 text-slate-400 dark:text-slate-500" />
                    </div>
                    <h3 className="text-lg font-bold text-slate-800 dark:text-slate-200 mb-2">Preview Not Available</h3>
                    <p className="text-[14px] text-slate-500 dark:text-slate-400 max-w-sm mb-6">
                      This project has not been deployed yet, or the preview URL is missing.
                    </p>
                    <button onClick={() => setRightPanel('code')} className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-slate-900 dark:bg-white text-white dark:text-slate-900 text-[13px] font-semibold hover:bg-slate-800 dark:hover:bg-slate-100 transition-colors shadow-sm">
                      <Code2 className="w-4 h-4" />
                      View Source Code
                    </button>
                  </div>
                )}
              </div>
            )}

            {/* CODE tab */}
            {rightPanel === 'code' && (
              <div className="flex h-full w-full">
                {/* File sidebar */}
                <div className="w-[240px] shrink-0 border-r border-slate-200 dark:border-[#2d333b] bg-white dark:bg-[#0f1118] flex flex-col">
                  <div className="h-11 flex items-center justify-between px-4 border-b border-slate-200 dark:border-[#1c2128]">
                    <span className="text-[14px] font-bold text-slate-800 dark:text-white">Code</span>
                    <div className="flex items-center gap-0.5">
                      <button className="p-1.5 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-white/[0.06] rounded transition-colors" title="Activity">
                        <Activity className="w-3.5 h-3.5" />
                      </button>
                      <button className="p-1.5 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-white/[0.06] rounded transition-colors" title="Download">
                        <Download className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                  <div className="h-10 flex items-center justify-between px-3 border-b border-slate-100 dark:border-[#1c2128]">
                    <span className="text-[12px] font-bold text-slate-500 dark:text-slate-400">Code files</span>
                    <div className="flex items-center gap-0.5">
                      <button className="p-1.5 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded transition-colors" title="Search">
                        <Search className="w-3.5 h-3.5" />
                      </button>
                      <button className="p-1.5 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded transition-colors" title="Toggle">
                        <Code2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                  <div className="flex-1 overflow-hidden">
                    {(['connecting', 'idle', 'cloning', 'installing', 'starting', 'health_check', 'preparing'].includes(status)) && files.length === 0 && !isNewProject ? (
                      <div className="flex flex-col items-center justify-center h-full gap-3 px-4 py-8">
                        <Loader2 className="w-5 h-5 text-orange-400 animate-spin" />
                        <div className="text-center space-y-1">
                          <p className="text-[12px] font-medium text-slate-500 dark:text-slate-400">
                            {status === 'cloning' ? 'Cloning repository…'
                              : status === 'installing' ? 'Installing dependencies…'
                              : status === 'starting' ? 'Starting dev server…'
                              : status === 'health_check' ? 'Connecting preview…'
                              : 'Preparing workspace…'}
                          </p>
                          <p className="text-[11px] text-slate-400 dark:text-slate-500">Files will appear here shortly</p>
                        </div>
                        <div className="w-full mt-2 space-y-2 px-2 animate-pulse">
                          {[70, 55, 80, 45, 65, 50, 72].map((w, i) => (
                            <div key={i} className="flex items-center gap-2">
                              <div className="w-3.5 h-3.5 rounded bg-slate-200 dark:bg-slate-700 shrink-0" />
                              <div className="h-2.5 rounded bg-slate-200 dark:bg-slate-700" style={{ width: `${w}%` }} />
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : resolvingInfo?.path === 'new_project' && files.length === 0 ? (
                      <div className="flex flex-col items-center justify-center h-full gap-3 px-4 py-8 text-center">
                        <div className="w-10 h-10 rounded-xl bg-orange-50 dark:bg-orange-900/20 flex items-center justify-center">
                          <Wand2 className="w-5 h-5 text-orange-400" />
                        </div>
                        <div className="space-y-1">
                          <p className="text-[12px] font-semibold text-slate-600 dark:text-slate-300">Waiting for AI</p>
                          <p className="text-[11px] text-slate-400 dark:text-slate-500 leading-relaxed">
                            Files will appear here once the agent starts generating code
                          </p>
                        </div>
                      </div>
                    ) : (
                      <FileExplorer
                        files={files}
                        selectedFile={selectedFile}
                        onFileSelect={handleFileSelect}
                        projectName={conversation?.title || 'Project'}
                      />
                    )}
                  </div>
                </div>

                {/* Editor area */}
                <div className="flex-1 min-w-0 bg-white flex flex-col">
                  {selectedFile && (
                    <div className="shrink-0 px-4 py-2 bg-amber-50 dark:bg-amber-900/10 border-b border-amber-200/70 dark:border-amber-700/30 flex items-center gap-1">
                      <span className="text-[13px] text-amber-800 dark:text-amber-300">
                        Code editing is only available on paid plans.{' '}
                        <button
                          onClick={() => setShowExportModal(true)}
                          className="font-semibold text-orange-500 hover:text-orange-600 hover:underline transition-colors">
                          Upgrade your plan
                        </button>
                      </span>
                    </div>
                  )}
                  <div className="flex-1 min-h-0">
                    <FileViewer
                      path={selectedFile}
                      content={editedFileContent !== null ? editedFileContent : fileContent}
                      loading={fileLoading}
                      onContentChange={(val) => setEditedFileContent(val)}
                    />
                  </div>
                </div>
              </div>
            )}

            {/* TERMINAL tab */}
            {rightPanel === 'terminal' && (
              <div className="h-full flex flex-col bg-[#1e1e2e]">
                <div className="flex-1 overflow-y-auto p-4 font-mono text-xs leading-relaxed custom-scrollbar">
                  {terminalLogs.length === 0 ? (
                    <div className="h-full flex flex-col items-center justify-center text-slate-600 space-y-3">
                      <Terminal className="w-8 h-8 opacity-20" />
                      <p>Ready to execute commands...</p>
                      <span className="text-[10px] bg-slate-800/50 px-2 py-1 rounded text-slate-500">Waiting for agent</span>
                    </div>
                  ) : (
                    terminalLogs.filter((log) => log.type !== 'user').map((log) => (
                      <div key={log.id} className="mb-2 break-all group">
                        <span className={cn(
                          'whitespace-pre-wrap',
                          log.type === 'error' || log.content?.includes('[ERROR]') ? 'text-red-400'
                            : log.content?.startsWith('$') ? 'text-emerald-400 font-bold'
                            : log.type === 'file_write' ? 'text-amber-400'
                            : 'text-slate-300',
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
                        if (e.key === 'Enter' && e.currentTarget.value.trim()) {
                          sendMessage(e.currentTarget.value.trim());
                          e.currentTarget.value = '';
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
                <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-orange-400 to-orange-600 flex items-center justify-center mb-5 shadow-lg shadow-orange-500/20">
                  <Monitor className="w-7 h-7 text-white" />
                </div>
                <h3 className="text-[18px] font-bold text-slate-800 dark:text-white mb-2">Preview & Build</h3>
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
