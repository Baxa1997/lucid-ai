'use client';

import { 
  Github, ChevronDown, ChevronUp, ExternalLink, Check, 
  AlertCircle, Settings2, User, Mail, Link2, X, Plus,
  Trash2, Pencil
} from 'lucide-react';
import { useState } from 'react';
import { cn } from '@/lib/utils';

/* GitLab SVG icon */
function GitLabIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor">
      <path d="M22.65 14.39L12 22.13 1.35 14.39a.84.84 0 01-.3-.94l1.22-3.78 2.44-7.51a.42.42 0 01.82 0l2.44 7.51h8.06l2.44-7.51a.42.42 0 01.82 0l2.44 7.51 1.22 3.78a.84.84 0 01-.3.94z" />
    </svg>
  );
}

/* Notion SVG icon */
function NotionIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor">
      <path d="M4.459 4.208c.746.606 1.026.56 2.428.466l13.215-.793c.28 0 .047-.28-.046-.326L18.19 2.2c-.42-.28-.98-.606-2.052-.513l-12.8.93c-.466.047-.56.28-.373.466l1.494 1.125zm.793 3.08v13.904c0 .747.373 1.026 1.214.98l14.523-.84c.84-.046.933-.56.933-1.166V6.368c0-.606-.233-.886-.747-.84l-15.177.84c-.56.047-.746.28-.746.92zm14.337.745c.093.42 0 .84-.42.888l-.7.14v10.264c-.607.327-1.167.514-1.634.514-.746 0-.933-.234-1.493-.933l-4.571-7.178v6.952l1.447.327s0 .84-1.167.84l-3.219.187c-.093-.187 0-.653.327-.747l.84-.213V8.957l-1.166-.093c-.094-.42.14-1.026.793-1.073l3.453-.233 4.759 7.272V8.49l-1.213-.14c-.094-.514.28-.886.747-.933l3.22-.187z"/>
    </svg>
  );
}

/* ════════════════════════════════════════════════
   GITHUB CARD
   ════════════════════════════════════════════════ */
function GitHubCard() {
  const [expanded, setExpanded] = useState(false);
  const [token, setToken] = useState('ghp_xxxxxxxxxxxxxxxxxxxx');
  const [host, setHost] = useState('github.com');
  const [showToken, setShowToken] = useState(false);
  const [tokenValid, setTokenValid] = useState(true);
  const [connected, setConnected] = useState(true);

  return (
    <div className={cn(
      "bg-white dark:bg-slate-900 rounded-2xl border overflow-hidden transition-all duration-300",
      connected ? "border-emerald-200 dark:border-emerald-500/20" : "border-slate-200 dark:border-slate-800",
      expanded && "shadow-card"
    )}>
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-4 px-5 py-4 text-left hover:bg-slate-50/50 dark:hover:bg-slate-800/50 transition-all"
      >
        <div className="w-10 h-10 rounded-xl bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 flex items-center justify-center shrink-0">
          <Github className="w-6 h-6 text-slate-900 dark:text-slate-100" />
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-bold text-slate-900 dark:text-slate-100">GitHub</h3>
          <div className="flex items-center gap-1.5 mt-0.5">
            <div className={cn("w-1.5 h-1.5 rounded-full", connected ? "bg-emerald-500" : "bg-slate-300 dark:bg-slate-600")} />
            <span className={cn("text-xs font-medium", connected ? "text-emerald-600 dark:text-emerald-400" : "text-slate-400 dark:text-slate-500")}>
              {connected ? 'Connected' : 'Not connected'}
            </span>
          </div>
        </div>
        <div className={cn(
          "w-8 h-8 rounded-lg flex items-center justify-center bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 shrink-0 transition-transform",
          expanded && "rotate-180"
        )}>
          <ChevronDown className="w-4 h-4 text-slate-400" />
        </div>
      </button>

      {/* Expanded Content */}
      <div className={cn(
        "overflow-hidden transition-all duration-300 ease-in-out",
        expanded ? "max-h-[500px] opacity-100" : "max-h-0 opacity-0"
      )}>
        <div className="px-5 pb-5 pt-1 border-t border-slate-100 dark:border-slate-800 space-y-5">
          {/* Token */}
          <div>
            <label className="flex items-center gap-2 text-sm font-medium text-slate-700 dark:text-slate-200 mb-2">
              {tokenValid && (
                <span className="w-5 h-5 rounded-full bg-emerald-100 dark:bg-emerald-500/10 flex items-center justify-center">
                  <Check className="w-3 h-3 text-emerald-600 dark:text-emerald-400" />
                </span>
              )}
              GitHub Token
            </label>
            <input
              type={showToken ? 'text' : 'password'}
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="<hidden>"
              className="w-full px-4 py-2.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500 focus:border-blue-300 dark:focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 outline-none transition-all"
            />
          </div>

          {/* Host */}
          <div>
            <label className="text-sm font-medium text-slate-700 dark:text-slate-200 mb-2 block">
              GitHub Host <span className="text-slate-400 dark:text-slate-500 font-normal">(optional)</span>
            </label>
            <input
              type="text"
              value={host}
              onChange={(e) => setHost(e.target.value)}
              placeholder="github.com"
              className="w-full px-4 py-2.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500 focus:border-blue-300 dark:focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 outline-none transition-all"
            />
          </div>

          {/* Help */}
          <p className="text-xs text-slate-400 dark:text-slate-500">
            Get your{' '}
            <a href="#" className="text-blue-600 dark:text-blue-400 hover:text-blue-500 underline underline-offset-2">GitHub token</a>
            {' '}or{' '}
            <a href="#" className="text-blue-600 dark:text-blue-400 hover:text-blue-500 underline underline-offset-2">click here for instructions</a>
          </p>

          {/* Actions */}
          <div className="flex items-center justify-between pt-2">
            <button
              onClick={() => setConnected(false)}
              className="px-4 py-2 bg-red-500 text-white rounded-xl text-sm font-bold hover:bg-red-600 transition-colors"
            >
              Disconnect
            </button>
            <button className="px-5 py-2 bg-blue-600 text-white rounded-xl text-sm font-bold hover:bg-blue-700 transition-colors shadow-sm shadow-blue-600/15">
              Save Changes
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ════════════════════════════════════════════════
   GITLAB CARD
   ════════════════════════════════════════════════ */
function GitLabCard() {
  const [expanded, setExpanded] = useState(false);
  const [token, setToken] = useState('glpat-xxxxxxxxxxxxxxxxxxxx');
  const [host, setHost] = useState('https://gitlab.udevs.io/');
  const [showToken, setShowToken] = useState(false);
  const [tokenValid, setTokenValid] = useState(true);
  const [hostValid, setHostValid] = useState(true);
  const [connected, setConnected] = useState(true);

  return (
    <div className={cn(
      "bg-white dark:bg-slate-900 rounded-2xl border overflow-hidden transition-all duration-300",
      connected ? "border-emerald-200 dark:border-emerald-500/20" : "border-slate-200 dark:border-slate-800",
      expanded && "shadow-card"
    )}>
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-4 px-5 py-4 text-left hover:bg-slate-50/50 dark:hover:bg-slate-800/50 transition-all"
      >
        <div className="w-10 h-10 rounded-xl bg-orange-50 dark:bg-orange-500/10 border border-orange-100 dark:border-orange-500/20 flex items-center justify-center shrink-0">
          <GitLabIcon className="w-6 h-6 text-orange-500" />
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-bold text-slate-900 dark:text-slate-100">GitLab</h3>
          <div className="flex items-center gap-1.5 mt-0.5">
            <div className={cn("w-1.5 h-1.5 rounded-full", connected ? "bg-emerald-500" : "bg-slate-300 dark:bg-slate-600")} />
            <span className={cn("text-xs font-medium", connected ? "text-emerald-600 dark:text-emerald-400" : "text-slate-400 dark:text-slate-500")}>
              {connected ? 'Connected' : 'Not connected'}
            </span>
          </div>
        </div>
        <div className={cn(
          "w-8 h-8 rounded-lg flex items-center justify-center bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 shrink-0 transition-transform",
          expanded && "rotate-180"
        )}>
          <ChevronDown className="w-4 h-4 text-slate-400" />
        </div>
      </button>

      {/* Expanded Content */}
      <div className={cn(
        "overflow-hidden transition-all duration-300 ease-in-out",
        expanded ? "max-h-[500px] opacity-100" : "max-h-0 opacity-0"
      )}>
        <div className="px-5 pb-5 pt-1 border-t border-slate-100 dark:border-slate-800 space-y-5">
          {/* Token */}
          <div>
            <label className="flex items-center gap-2 text-sm font-medium text-slate-700 dark:text-slate-200 mb-2">
              {tokenValid && (
                <span className="w-5 h-5 rounded-full bg-emerald-100 dark:bg-emerald-500/10 flex items-center justify-center">
                  <Check className="w-3 h-3 text-emerald-600 dark:text-emerald-400" />
                </span>
              )}
              GitLab Token
            </label>
            <input
              type={showToken ? 'text' : 'password'}
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="<hidden>"
              className="w-full px-4 py-2.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500 focus:border-blue-300 dark:focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 outline-none transition-all"
            />
          </div>

          {/* Host */}
          <div>
            <label className="flex items-center gap-2 text-sm font-medium text-slate-700 dark:text-slate-200 mb-2">
              {hostValid && (
                <span className="w-5 h-5 rounded-full bg-emerald-100 dark:bg-emerald-500/10 flex items-center justify-center">
                  <Check className="w-3 h-3 text-emerald-600 dark:text-emerald-400" />
                </span>
              )}
              GitLab Host <span className="text-slate-400 dark:text-slate-500 font-normal">(optional)</span>
            </label>
            <input
              type="text"
              value={host}
              onChange={(e) => setHost(e.target.value)}
              placeholder="https://gitlab.com"
              className="w-full px-4 py-2.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500 focus:border-blue-300 dark:focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 outline-none transition-all"
            />
          </div>

          {/* Help */}
          <p className="text-xs text-slate-400 dark:text-slate-500">
            Get your{' '}
            <a href="#" className="text-blue-600 dark:text-blue-400 hover:text-blue-500 underline underline-offset-2">GitLab token</a>
            {' '}or{' '}
            <a href="#" className="text-blue-600 dark:text-blue-400 hover:text-blue-500 underline underline-offset-2">click here for instructions</a>
          </p>

          {/* Actions */}
          <div className="flex items-center justify-between pt-2">
            <button
              onClick={() => setConnected(false)}
              className="px-4 py-2 bg-red-500 text-white rounded-xl text-sm font-bold hover:bg-red-600 transition-colors"
            >
              Disconnect
            </button>
            <button className="px-5 py-2 bg-blue-600 text-white rounded-xl text-sm font-bold hover:bg-blue-700 transition-colors shadow-sm shadow-blue-600/15">
              Save Changes
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ════════════════════════════════════════════════
   NOTION CARD (Multi-project)
   ════════════════════════════════════════════════ */
function NotionCard() {
  const [expanded, setExpanded] = useState(false);
  const [connected, setConnected] = useState(true);
  const [showNewForm, setShowNewForm] = useState(false);

  const [projects, setProjects] = useState([
    { id: 1, name: 'Lucid AI', databaseId: '21593460...', active: true },
    { id: 2, name: 'Lodify', databaseId: '2c505496...', active: false },
  ]);

  // New project form
  const [newName, setNewName] = useState('');
  const [newToken, setNewToken] = useState('');
  const [newDbId, setNewDbId] = useState('');

  // Edit mode
  const [editingId, setEditingId] = useState(null);
  const [editName, setEditName] = useState('');
  const [editToken, setEditToken] = useState('');
  const [editDbId, setEditDbId] = useState('');

  const handleAddProject = () => {
    if (!newName.trim() || !newToken.trim() || !newDbId.trim()) return;
    setProjects([...projects, {
      id: Date.now(),
      name: newName,
      databaseId: newDbId,
      active: false,
    }]);
    setNewName('');
    setNewToken('');
    setNewDbId('');
    setShowNewForm(false);
  };

  const handleDelete = (id) => {
    setProjects(projects.filter(p => p.id !== id));
  };

  const handleSetActive = (id) => {
    setProjects(projects.map(p => ({
      ...p,
      active: p.id === id,
    })));
  };

  const startEdit = (project) => {
    setEditingId(project.id);
    setEditName(project.name);
    setEditDbId(project.databaseId);
    setEditToken('secret_...');
  };

  const handleSaveEdit = () => {
    if (!editName.trim()) return;
    setProjects(projects.map(p => p.id === editingId ? {
      ...p,
      name: editName,
      databaseId: editDbId,
    } : p));
    setEditingId(null);
  };

  const projectCount = projects.length;

  return (
    <div className={cn(
      "bg-white dark:bg-slate-900 rounded-2xl border overflow-hidden transition-all duration-300",
      connected ? "border-emerald-200 dark:border-emerald-500/20" : "border-slate-200 dark:border-slate-800",
      expanded && "shadow-card"
    )}>
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-4 px-5 py-4 text-left hover:bg-slate-50/50 dark:hover:bg-slate-800/50 transition-all"
      >
        <div className="w-10 h-10 rounded-xl bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 flex items-center justify-center shrink-0">
          <NotionIcon className="w-6 h-6 text-slate-800 dark:text-slate-200" />
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-bold text-slate-900 dark:text-slate-100">Notion</h3>
          <div className="flex items-center gap-1.5 mt-0.5">
            <div className={cn("w-1.5 h-1.5 rounded-full", connected ? "bg-emerald-500" : "bg-slate-300 dark:bg-slate-600")} />
            <span className={cn("text-xs font-medium", connected ? "text-emerald-600 dark:text-emerald-400" : "text-slate-400 dark:text-slate-500")}>
              {projectCount} Projects
            </span>
          </div>
        </div>
        <div className={cn(
          "w-8 h-8 rounded-lg flex items-center justify-center bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 shrink-0 transition-transform",
          expanded && "rotate-180"
        )}>
          <ChevronDown className="w-4 h-4 text-slate-400" />
        </div>
      </button>

      {/* Expanded Content */}
      <div className={cn(
        "overflow-hidden transition-all duration-300 ease-in-out",
        expanded ? "max-h-[1200px] opacity-100" : "max-h-0 opacity-0"
      )}>
        <div className="px-5 pb-5 pt-3 border-t border-slate-100 dark:border-slate-800 space-y-4">
          {/* Section Header */}
          <div className="flex items-center justify-between">
            <h4 className="text-base font-bold text-slate-900 dark:text-slate-100">Notion Projects</h4>
            <button
              onClick={() => { setShowNewForm(true); setEditingId(null); }}
              className="flex items-center gap-1.5 px-4 py-2 bg-blue-600 text-white rounded-xl text-sm font-bold hover:bg-blue-700 transition-colors shadow-sm shadow-blue-600/15"
            >
              <Plus className="w-4 h-4" />
              New Project
            </button>
          </div>

          {/* New Project Form */}
          {showNewForm && (
            <div className="bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl p-5 space-y-4 animate-slide-down">
              <h5 className="text-sm font-bold text-slate-900 dark:text-slate-100">New Project</h5>
              <div>
                <label className="text-xs font-medium text-slate-500 dark:text-slate-400 mb-1.5 block">Project Name</label>
                <input
                  type="text"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  placeholder="e.g. My Startup"
                  className="w-full px-4 py-2.5 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500 focus:border-blue-300 dark:focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 outline-none transition-all"
                  autoFocus
                />
              </div>
              <div>
                <label className="text-xs font-medium text-slate-500 dark:text-slate-400 mb-1.5 block">Notion Integration Token</label>
                <input
                  type="text"
                  value={newToken}
                  onChange={(e) => setNewToken(e.target.value)}
                  placeholder="secret_..."
                  className="w-full px-4 py-2.5 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500 focus:border-blue-300 dark:focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 outline-none transition-all"
                />
              </div>
              <div>
                <label className="text-xs font-medium text-slate-500 dark:text-slate-400 mb-1.5 block">Notion Database ID</label>
                <input
                  type="text"
                  value={newDbId}
                  onChange={(e) => setNewDbId(e.target.value)}
                  placeholder="32-char ID from URL"
                  className="w-full px-4 py-2.5 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500 focus:border-blue-300 dark:focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 outline-none transition-all"
                />
              </div>
              <div className="flex items-center gap-3 pt-1">
                <button
                  onClick={handleAddProject}
                  disabled={!newName.trim() || !newToken.trim() || !newDbId.trim()}
                  className={cn(
                    "px-5 py-2 rounded-xl text-sm font-bold transition-all",
                    newName.trim() && newToken.trim() && newDbId.trim()
                      ? "bg-blue-600 text-white hover:bg-blue-700"
                      : "bg-slate-100 dark:bg-slate-700 text-slate-400 dark:text-slate-500 cursor-not-allowed"
                  )}
                >
                  Save Project
                </button>
                <button
                  onClick={() => { setShowNewForm(false); setNewName(''); setNewToken(''); setNewDbId(''); }}
                  className="px-4 py-2 text-sm font-medium text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 transition-colors"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}

          {/* Project List */}
          <div className="space-y-2">
            {projects.map((project) => (
              <div key={project.id}>
                {editingId === project.id ? (
                  /* Edit Mode */
                  <div className="bg-slate-50 dark:bg-slate-800 border border-blue-200 dark:border-blue-500/30 rounded-xl p-5 space-y-4 animate-fade-in">
                    <h5 className="text-sm font-bold text-slate-900 dark:text-slate-100">Edit Project</h5>
                    <div>
                      <label className="text-xs font-medium text-slate-500 dark:text-slate-400 mb-1.5 block">Project Name</label>
                      <input
                        type="text"
                        value={editName}
                        onChange={(e) => setEditName(e.target.value)}
                        className="w-full px-4 py-2.5 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500 focus:border-blue-300 dark:focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 outline-none transition-all"
                        autoFocus
                      />
                    </div>
                    <div>
                      <label className="text-xs font-medium text-slate-500 dark:text-slate-400 mb-1.5 block">Notion Integration Token</label>
                      <input
                        type="text"
                        value={editToken}
                        onChange={(e) => setEditToken(e.target.value)}
                        placeholder="secret_..."
                        className="w-full px-4 py-2.5 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500 focus:border-blue-300 dark:focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 outline-none transition-all"
                      />
                    </div>
                    <div>
                      <label className="text-xs font-medium text-slate-500 dark:text-slate-400 mb-1.5 block">Notion Database ID</label>
                      <input
                        type="text"
                        value={editDbId}
                        onChange={(e) => setEditDbId(e.target.value)}
                        className="w-full px-4 py-2.5 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500 focus:border-blue-300 dark:focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 outline-none transition-all"
                      />
                    </div>
                    <div className="flex items-center gap-3 pt-1">
                      <button
                        onClick={handleSaveEdit}
                        className="px-5 py-2 bg-blue-600 text-white rounded-xl text-sm font-bold hover:bg-blue-700 transition-colors"
                      >
                        Save Changes
                      </button>
                      <button
                        onClick={() => setEditingId(null)}
                        className="px-4 py-2 text-sm font-medium text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 transition-colors"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : (
                  /* Display Mode */
                  <div className={cn(
                    "flex items-center gap-4 px-4 py-3.5 rounded-xl border transition-all",
                    project.active
                      ? "bg-blue-50 dark:bg-blue-500/10 border-blue-200 dark:border-blue-500/20"
                      : "bg-slate-50 dark:bg-slate-800 border-slate-200 dark:border-slate-700 hover:border-slate-300 dark:hover:border-slate-600"
                  )}>
                    <div className="w-9 h-9 rounded-lg bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 flex items-center justify-center shrink-0">
                      <NotionIcon className="w-5 h-5 text-blue-600 dark:text-blue-400" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-semibold text-slate-900 dark:text-slate-100">{project.name}</span>
                        {project.active && (
                          <span className="px-2 py-0.5 bg-emerald-100 dark:bg-emerald-500/10 border border-emerald-200 dark:border-emerald-500/20 rounded text-[10px] font-bold text-emerald-700 dark:text-emerald-400 uppercase tracking-wider">
                            Active
                          </span>
                        )}
                      </div>
                      <p className="text-xs text-slate-400 dark:text-slate-500 mt-0.5">Database ID: {project.databaseId}</p>
                    </div>
                    <div className="flex items-center gap-1.5">
                      {!project.active && (
                        <button
                          onClick={() => handleSetActive(project.id)}
                          className="p-2 rounded-lg text-slate-400 hover:text-emerald-600 dark:hover:text-emerald-400 hover:bg-emerald-50 dark:hover:bg-emerald-500/10 transition-all"
                          title="Set as active"
                        >
                          <Check className="w-4 h-4" />
                        </button>
                      )}
                      <button
                        onClick={() => startEdit(project)}
                        className="p-2 rounded-lg text-slate-400 hover:text-blue-600 dark:hover:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-500/10 transition-all"
                        title="Edit"
                      >
                        <Pencil className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => handleDelete(project.id)}
                        className="p-2 rounded-lg text-slate-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-500/10 transition-all"
                        title="Delete"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ════════════════════════════════════════════════
   MAIN INTEGRATIONS PAGE
   ════════════════════════════════════════════════ */
export default function IntegrationsPage() {
  const [gitUsername, setGitUsername] = useState('AI Engineer');
  const [gitEmail, setGitEmail] = useState('ai@lucid.ai');

  return (
    <div className="min-h-full bg-[#f5f7fa] dark:bg-slate-950 transition-colors duration-200">
      <div className="px-6 py-8">
        
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-2xl font-extrabold text-slate-900 dark:text-slate-100 tracking-tight mb-1">
            Integrations
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Configure the username and email that OpenHands uses to commit changes.
          </p>
        </div>

        {/* Integration Cards */}
        <div className="space-y-3 mb-8">
          <GitHubCard />
          <GitLabCard />
          <NotionCard />
        </div>

        {/* Git Settings */}
        <div className="bg-white dark:bg-slate-900 rounded-2xl border border-slate-200 dark:border-slate-800 p-6 shadow-soft">
          <h3 className="text-sm font-bold text-slate-900 dark:text-slate-100 mb-1">Git Settings</h3>
          <p className="text-xs text-slate-500 dark:text-slate-400 mb-5">Configure the username and email that OpenHands uses to commit changes.</p>
          
          <div className="space-y-4">
            <div>
              <label className="text-[11px] font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-wider mb-1.5 block">Username</label>
              <div className="relative">
                <User className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                <input
                  value={gitUsername}
                  onChange={(e) => setGitUsername(e.target.value)}
                  className="w-full pl-10 pr-4 py-2.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 focus:border-blue-300 dark:focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 outline-none transition-all"
                />
              </div>
            </div>
            <div>
              <label className="text-[11px] font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-wider mb-1.5 block">Email</label>
              <div className="relative">
                <Mail className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                <input
                  value={gitEmail}
                  onChange={(e) => setGitEmail(e.target.value)}
                  className="w-full pl-10 pr-4 py-2.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 focus:border-blue-300 dark:focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 outline-none transition-all"
                />
              </div>
            </div>
            <button className="px-5 py-2 bg-blue-600 text-white rounded-xl text-xs font-bold hover:bg-blue-700 transition-colors shadow-sm shadow-blue-600/15">
              Save Changes
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
