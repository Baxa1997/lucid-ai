'use client';

// ─────────────────────────────────────────────────────────
//  Lucid AI — FileExplorer (Light Mode)
//  Collapsible tree-view for the left panel
// ─────────────────────────────────────────────────────────

import { useState, useMemo } from 'react';
import { cn } from '@/lib/utils';
import {
  ChevronRight, ChevronDown, FileText, Folder, FolderOpen,
  Search, Plus, RefreshCw, MoreHorizontal,
  FileCode, FileJson, Settings2, Image, FileType,
} from 'lucide-react';

// ── File-type icon mapping ─────────────────────────────────
function getFileIcon(name) {
  const ext = name.split('.').pop()?.toLowerCase();
  const map = {
    js:    { icon: FileCode,  color: 'text-amber-600' },
    jsx:   { icon: FileCode,  color: 'text-blue-500' },
    ts:    { icon: FileCode,  color: 'text-blue-600' },
    tsx:   { icon: FileCode,  color: 'text-blue-600' },
    py:    { icon: FileCode,  color: 'text-emerald-600' },
    json:  { icon: FileJson,  color: 'text-amber-500' },
    md:    { icon: FileText,  color: 'text-slate-500' },
    css:   { icon: FileType,  color: 'text-purple-500' },
    scss:  { icon: FileType,  color: 'text-pink-500' },
    html:  { icon: FileCode,  color: 'text-orange-500' },
    yml:   { icon: Settings2, color: 'text-rose-500' },
    yaml:  { icon: Settings2, color: 'text-rose-500' },
    png:   { icon: Image,     color: 'text-emerald-500' },
    jpg:   { icon: Image,     color: 'text-emerald-500' },
    svg:   { icon: Image,     color: 'text-amber-500' },
    env:   { icon: Settings2, color: 'text-slate-400' },
    gitignore: { icon: FileText, color: 'text-slate-400' },
  };
  return map[ext] || { icon: FileText, color: 'text-slate-400' };
}

// ── Default placeholder tree ───────────────────────────────
const DEFAULT_TREE = [
  {
    name: 'src', type: 'dir', children: [
      {
        name: 'app', type: 'dir', children: [
          { name: 'globals.css', type: 'file' },
          { name: 'layout.js', type: 'file' },
          { name: 'page.js', type: 'file' },
        ],
      },
      {
        name: 'components', type: 'dir', children: [
          { name: 'Footer.js', type: 'file' },
          { name: 'Header.js', type: 'file' },
        ],
      },
      {
        name: 'lib', type: 'dir', children: [
          { name: 'utils.js', type: 'file' },
        ],
      },
    ],
  },
  { name: 'package.json', type: 'file' },
  { name: 'README.md', type: 'file' },
  { name: '.gitignore', type: 'file' },
];

// ── TreeNode component ─────────────────────────────────────
function TreeNode({ node, depth = 0, selectedFile, onFileSelect, parentPath = '' }) {
  const [isOpen, setIsOpen] = useState(depth < 2);
  const filePath = parentPath ? `${parentPath}/${node.name}` : node.name;
  const isDir = node.type === 'dir' || node.type === 'folder';
  const isSelected = selectedFile === filePath;
  const { icon: FileIcon, color: fileColor } = getFileIcon(node.name);

  return (
    <div>
      <button
        onClick={() => {
          if (isDir) {
            setIsOpen(!isOpen);
          } else {
            onFileSelect?.(filePath);
          }
        }}
        className={cn(
          "w-full flex items-center gap-1.5 py-1.5 px-2 text-left text-[13px] rounded-md transition-all group",
          isSelected
            ? "bg-blue-50 text-blue-700 font-semibold border border-blue-200"
            : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
        )}
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
      >
        {/* Chevron or spacer */}
        {isDir ? (
          isOpen
            ? <ChevronDown className="w-3.5 h-3.5 text-slate-400 shrink-0" />
            : <ChevronRight className="w-3.5 h-3.5 text-slate-400 shrink-0" />
        ) : (
          <span className="w-3.5 shrink-0" />
        )}

        {/* Icon */}
        {isDir ? (
          isOpen
            ? <FolderOpen className="w-4 h-4 text-amber-500 shrink-0" />
            : <Folder className="w-4 h-4 text-amber-500 shrink-0" />
        ) : (
          <FileIcon className={cn("w-4 h-4 shrink-0", fileColor)} />
        )}

        {/* Name */}
        <span className="truncate">{node.name}</span>
      </button>

      {/* Children */}
      {isDir && isOpen && node.children && (
        <div>
          {node.children
            .sort((a, b) => {
              // Dirs first, then alphabetical
              const aDir = a.type === 'dir' || a.type === 'folder';
              const bDir = b.type === 'dir' || b.type === 'folder';
              if (aDir !== bDir) return aDir ? -1 : 1;
              return a.name.localeCompare(b.name);
            })
            .map((child) => (
              <TreeNode
                key={child.name}
                node={child}
                depth={depth + 1}
                selectedFile={selectedFile}
                onFileSelect={onFileSelect}
                parentPath={filePath}
              />
            ))}
        </div>
      )}
    </div>
  );
}

// ── Main FileExplorer ──────────────────────────────────────
export default function FileExplorer({
  files = [],
  selectedFile,
  onFileSelect,
  projectName = 'Project',
}) {
  const [search, setSearch] = useState('');

  // Use agent-provided tree or default
  const tree = useMemo(() => {
    if (Array.isArray(files) && files.length > 0) {
      // If files is an array of strings, convert to tree structure
      if (typeof files[0] === 'string') {
        return buildTreeFromPaths(files);
      }
      return files;
    }
    return DEFAULT_TREE;
  }, [files]);

  // Filter tree by search
  const filteredTree = useMemo(() => {
    if (!search.trim()) return tree;
    return filterTree(tree, search.toLowerCase());
  }, [tree, search]);

  return (
    <div className="h-full flex flex-col bg-white border-r border-slate-200">
      {/* Header */}
      <div className="shrink-0 flex items-center justify-between px-3 py-2.5 border-b border-slate-200 bg-slate-50/80">
        <span className="text-[11px] font-bold text-slate-500 uppercase tracking-wider truncate">
          {projectName}
        </span>
        <div className="flex items-center gap-0.5">
          <button className="p-1 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded transition-colors" title="New file">
            <Plus className="w-3.5 h-3.5" />
          </button>
          <button className="p-1 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded transition-colors" title="Refresh">
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
          <button className="p-1 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded transition-colors" title="More">
            <MoreHorizontal className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Search */}
      <div className="shrink-0 px-2 py-2">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-8 pr-3 py-2 text-xs bg-slate-50 border border-slate-200 rounded-lg text-slate-800 placeholder:text-slate-400 outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/20 transition-all"
            placeholder="Search files..."
          />
        </div>
      </div>

      {/* Tree */}
      <div className="flex-1 overflow-y-auto px-1.5 py-1 custom-scrollbar">
        {filteredTree.map((node) => (
          <TreeNode
            key={node.name}
            node={node}
            selectedFile={selectedFile}
            onFileSelect={onFileSelect}
          />
        ))}

        {filteredTree.length === 0 && (
          <div className="flex flex-col items-center justify-center py-8 text-center px-4">
            <FileText className="w-8 h-8 text-slate-300 mb-2" />
            <p className="text-xs text-slate-400">No files found</p>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Helpers ────────────────────────────────────────────────

function buildTreeFromPaths(paths) {
  const root = [];
  paths.forEach((p) => {
    const parts = p.split('/');
    let current = root;
    parts.forEach((part, i) => {
      const existing = current.find((n) => n.name === part);
      if (existing) {
        current = existing.children || [];
      } else {
        const isFile = i === parts.length - 1;
        const node = {
          name: part,
          type: isFile ? 'file' : 'dir',
          ...(isFile ? {} : { children: [] }),
        };
        current.push(node);
        if (!isFile) current = node.children;
      }
    });
  });
  return root;
}

function filterTree(tree, query) {
  return tree
    .map((node) => {
      if (node.type === 'file') {
        return node.name.toLowerCase().includes(query) ? node : null;
      }
      const filteredChildren = filterTree(node.children || [], query);
      if (filteredChildren.length > 0 || node.name.toLowerCase().includes(query)) {
        return { ...node, children: filteredChildren };
      }
      return null;
    })
    .filter(Boolean);
}
