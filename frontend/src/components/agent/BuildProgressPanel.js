'use client';

// ─────────────────────────────────────────────────────────
//  BuildProgressPanel — Right-panel build progress view
//  Shows animated batch progress, live file list, mini tree
//  during project generation. Base44-inspired design.
// ─────────────────────────────────────────────────────────

import { useState, useEffect, useMemo } from 'react';
import { Layers, FileCode2, FolderOpen } from 'lucide-react';
import { BATCH_ORDER } from './build/fileUtils';
import { buildTreeFromPaths } from './build/FileTree';
import ProgressHeader from './build/ProgressHeader';
import PhasesList from './build/PhasesList';
import FilesList from './build/FilesList';
import FileTree from './build/FileTree';
import TabBar from '@/components/ui/TabBar';

const SECTION_TABS = [
  { id: 'progress', label: 'Phases',  icon: Layers },
  { id: 'files',    label: 'Files',   icon: FileCode2 },
  { id: 'tree',     label: 'Tree',    icon: FolderOpen },
];

/**
 * BuildProgressPanel — Right panel during generation.
 *
 * Displays:
 *  1. Overall progress bar with percentage + time estimate
 *  2. Batch-by-batch progress with file counts
 *  3. Live file list with "just created" animations
 *  4. Mini file tree growing as files are generated
 */
export default function BuildProgressPanel({ isVisible = false, onComplete }) {
  const [percentage, setPercentage] = useState(0);
  const [currentMessage, setCurrentMessage] = useState('Preparing workspace...');
  const [batches, setBatches] = useState([]);
  const [allFiles, setAllFiles] = useState([]);
  const [recentFiles, setRecentFiles] = useState([]);
  const [isComplete, setIsComplete] = useState(false);
  const [totalFiles, setTotalFiles] = useState(0);
  const [startTime] = useState(Date.now());
  const [activeSection, setActiveSection] = useState('progress');

  // Initialize batches
  useEffect(() => {
    if (isVisible && batches.length === 0) {
      setBatches(BATCH_ORDER.map((name) => ({
        name,
        status: 'pending',
        files: [],
        filesExpected: 0,
      })));
    }
  }, [isVisible]);

  // Listen for WebSocket-dispatched window events
  useEffect(() => {
    if (!isVisible) return;

    const handleProgress = (e) => {
      const { batch, message, files, percentage: pct } = e.detail;
      setPercentage(pct || 0);
      setCurrentMessage(message || '');

      setBatches((prev) => prev.map((b) =>
        b.name === batch
          ? { ...b, status: 'active', files: [...b.files, ...(files || [])] }
          : b,
      ));

      if (files?.length) {
        setAllFiles((prev) => [...prev, ...files.filter((f) => !prev.includes(f))]);
        setRecentFiles(files);
        setTimeout(() => setRecentFiles([]), 3000);
      }
    };

    const handleBatchComplete = (e) => {
      const { batch, files_created, files_expected } = e.detail;
      setBatches((prev) => prev.map((b) =>
        b.name === batch
          ? { ...b, status: 'done', files: files_created || b.files, filesExpected: files_expected || b.filesExpected }
          : b,
      ));
      if (files_created?.length) {
        setAllFiles((prev) => [...prev, ...files_created.filter((f) => !prev.includes(f))]);
      }
    };

    const handleComplete = (e) => {
      const { total_files, message } = e.detail;
      setIsComplete(true);
      setPercentage(100);
      setTotalFiles(total_files || allFiles.length);
      setCurrentMessage(message || '✅ Project generated!');
      setBatches((prev) => prev.map((b) => ({ ...b, status: 'done' })));
      onComplete?.();
    };

    window.addEventListener('generation_progress', handleProgress);
    window.addEventListener('batch_complete', handleBatchComplete);
    window.addEventListener('generation_complete', handleComplete);
    return () => {
      window.removeEventListener('generation_progress', handleProgress);
      window.removeEventListener('batch_complete', handleBatchComplete);
      window.removeEventListener('generation_complete', handleComplete);
    };
  }, [isVisible, allFiles, onComplete]);

  // Time estimate
  const elapsed = (Date.now() - startTime) / 1000;
  const estimatedTotal = percentage > 5 ? (elapsed / percentage) * 100 : 0;
  const remaining = Math.max(0, estimatedTotal - elapsed);
  const remainingMin = Math.floor(remaining / 60);
  const remainingSec = Math.floor(remaining % 60);

  // Build file tree
  const fileTree = useMemo(() => buildTreeFromPaths(allFiles), [allFiles]);

  // Dynamic file count label in tabs
  const sectionTabs = SECTION_TABS.map((t) =>
    t.id === 'files' ? { ...t, label: `Files (${allFiles.length})` } : t,
  );

  if (!isVisible) return null;

  return (
    <div className="h-full flex flex-col bg-white dark:bg-[#0f1118] overflow-hidden">

      {/* ── Header ─────────────────────────────────────── */}
      <ProgressHeader
        isComplete={isComplete}
        percentage={percentage}
        currentMessage={currentMessage}
        allFilesCount={allFiles.length}
        remainingMin={remainingMin}
        remainingSec={remainingSec}
      />

      {/* ── Section Tabs ────────────────────────────────── */}
      <div className="shrink-0 flex items-center gap-1 px-4 py-2 border-b border-slate-100 dark:border-white/[0.05] bg-slate-50/50 dark:bg-white/[0.02]">
        <TabBar
          tabs={sectionTabs}
          activeTab={activeSection}
          onTabChange={setActiveSection}
          activeColor="slate"
        />
      </div>

      {/* ── Content ─────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto custom-scrollbar">
        {activeSection === 'progress' && (
          <PhasesList
            batches={batches}
            currentMessage={currentMessage}
            isComplete={isComplete}
            allFilesCount={totalFiles || allFiles.length}
            elapsed={elapsed}
          />
        )}
        {activeSection === 'files' && (
          <FilesList allFiles={allFiles} recentFiles={recentFiles} activeSection={activeSection} />
        )}
        {activeSection === 'tree' && (
          <FileTree fileTree={fileTree} recentFiles={recentFiles} />
        )}
      </div>
    </div>
  );
}
