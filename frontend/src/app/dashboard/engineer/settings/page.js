'use client';

import { useState, useEffect, useCallback, useRef, forwardRef, useImperativeHandle } from 'react';
import {
  Settings, ChevronDown, Check, Plus, Trash2, Eye, EyeOff,
  X, ExternalLink, Key, Server, Globe, Lock, Cpu, HardDrive,
  Bell, BellOff, BarChart3, GitBranch, Package, Languages,
  Zap, Shield, ChevronRight, Info, Loader2, AlertCircle
} from 'lucide-react';
import { cn } from '@/lib/utils';

/* ────────────────────────────────────────────────
   Tab configuration with descriptions
   ──────────────────────────────────────────────── */
const tabs = [
  {
    id: 'llm',
    label: 'LLM',
    description: 'AI model configuration',
    Icon: Cpu,
    color: 'blue',
  },
  {
    id: 'mcp',
    label: 'MCP',
    description: 'Server connections',
    Icon: HardDrive,
    color: 'violet',
  },
  {
    id: 'application',
    label: 'Application',
    description: 'General preferences',
    Icon: Settings,
    color: 'emerald',
  },
  {
    id: 'secrets',
    label: 'Secrets',
    description: 'Keys & credentials',
    Icon: Shield,
    color: 'amber',
  },
];

const colorMap = {
  blue:    { bg: 'bg-blue-50 dark:bg-blue-500/10',    border: 'border-blue-100 dark:border-blue-500/20',   text: 'text-blue-600 dark:text-blue-400',    icon: 'text-blue-500',   activeBg: 'bg-blue-600',    dot: 'bg-blue-500' },
  violet:  { bg: 'bg-violet-50 dark:bg-violet-500/10',  border: 'border-violet-100 dark:border-violet-500/20', text: 'text-violet-600 dark:text-violet-400',  icon: 'text-violet-500', activeBg: 'bg-violet-600',  dot: 'bg-violet-500' },
  emerald: { bg: 'bg-emerald-50 dark:bg-emerald-500/10', border: 'border-emerald-100 dark:border-emerald-500/20',text: 'text-emerald-600 dark:text-emerald-400', icon: 'text-emerald-500',activeBg: 'bg-emerald-600', dot: 'bg-emerald-500' },
  amber:   { bg: 'bg-amber-50 dark:bg-amber-500/10',   border: 'border-amber-100 dark:border-amber-500/20',  text: 'text-amber-600 dark:text-amber-400',   icon: 'text-amber-500',  activeBg: 'bg-amber-600',   dot: 'bg-amber-500' },
};

/* ────────────────────────────────────────────────
   Section Card Wrapper
   ──────────────────────────────────────────────── */
function SectionCard({ title, description, icon: IconComp, children, className }) {
  return (
    <div className={cn("bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl shadow-soft", className)}>
      {(title || description) && (
        <div className="px-6 py-5 border-b border-slate-100 dark:border-slate-800">
          <div className="flex items-center gap-3">
            {IconComp && (
              <div className="w-8 h-8 rounded-lg bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 flex items-center justify-center shrink-0">
                <IconComp className="w-4 h-4 text-slate-500 dark:text-slate-400" />
              </div>
            )}
            <div>
              {title && <h3 className="text-sm font-bold text-slate-900 dark:text-slate-100">{title}</h3>}
              {description && <p className="text-xs text-slate-400 dark:text-slate-500 mt-0.5">{description}</p>}
            </div>
          </div>
        </div>
      )}
      <div className="px-6 py-5">{children}</div>
    </div>
  );
}

/* ────────────────────────────────────────────────
   Field Row - label + description + input
   ──────────────────────────────────────────────── */
function FieldRow({ label, description, badge, children, className }) {
  return (
    <div className={cn("flex flex-col sm:flex-row sm:items-start gap-1 sm:gap-8", className)}>
      <div className="sm:w-[200px] shrink-0 pt-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-slate-700 dark:text-slate-200">{label}</span>
          {badge}
        </div>
        {description && <p className="text-xs text-slate-400 dark:text-slate-500 mt-0.5 leading-relaxed">{description}</p>}
      </div>
      <div className="flex-1 min-w-0">{children}</div>
    </div>
  );
}

/* ────────────────────────────────────────────────
   Toggle Switch Component
   ──────────────────────────────────────────────── */
function Toggle({ checked, onChange, label, description }) {
  return (
    <div className="flex items-center justify-between py-3 group">
      <div className="flex-1 min-w-0 mr-4">
        {label && <span className="text-sm font-medium text-slate-700 dark:text-slate-200 group-hover:text-slate-900 dark:group-hover:text-white transition-colors">{label}</span>}
        {description && <p className="text-xs text-slate-400 dark:text-slate-500 mt-0.5">{description}</p>}
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={cn(
          "relative w-11 h-6 rounded-full transition-colors duration-200 shrink-0",
          checked ? "bg-blue-600" : "bg-slate-200 dark:bg-slate-700"
        )}
      >
        <span
          className={cn(
            "absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full transition-transform duration-200 shadow-sm",
            checked && "translate-x-5"
          )}
        />
      </button>
    </div>
  );
}

/* ────────────────────────────────────────────────
   Custom Dropdown Component
   ──────────────────────────────────────────────── */
function Dropdown({ value, options, onChange, placeholder }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-4 py-2.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 hover:border-blue-300 dark:hover:border-blue-500/50 focus:border-blue-300 focus:ring-2 focus:ring-blue-500/10 transition-all outline-none"
      >
        <span className={value ? 'text-slate-700 dark:text-slate-200' : 'text-slate-400 dark:text-slate-500'}>{value || placeholder}</span>
        <ChevronDown className={cn("w-4 h-4 text-slate-400 transition-transform", open && "rotate-180")} />
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute top-full left-0 right-0 mt-1 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl overflow-hidden z-50 shadow-lg">
            <div className="max-h-48 overflow-y-auto">
              {options.map((opt) => (
                <button
                  key={opt}
                  onClick={() => { onChange(opt); setOpen(false); }}
                  className={cn(
                    "w-full flex items-center justify-between px-4 py-2.5 text-sm text-left hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors",
                    value === opt ? "text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-500/10" : "text-slate-600 dark:text-slate-300"
                  )}
                >
                  {opt}
                  {value === opt && <Check className="w-3.5 h-3.5 text-blue-600 dark:text-blue-400" />}
                </button>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

/* ════════════════════════════════════════════════
   LLM TAB — Supabase-backed
   ════════════════════════════════════════════════ */

// ── Provider / model catalogue ───────────────────
const PROVIDER_CONFIG = {
  anthropic: {
    label: 'Anthropic Claude',
    docsUrl: 'https://console.anthropic.com/settings/keys',
    models: [
      { id: 'anthropic/claude-3-5-sonnet-20241022', label: 'Claude Sonnet 3.5' },
      { id: 'anthropic/claude-3-5-opus-20241022',   label: 'Claude Opus 3.5' },
      { id: 'anthropic/claude-sonnet-4-6',          label: 'Claude Sonnet 4.6' },
      { id: 'anthropic/claude-opus-4-6',            label: 'Claude Opus 4.6' },
    ],
  },
};

const PROVIDER_KEYS = Object.keys(PROVIDER_CONFIG); // ['google', 'anthropic']

const LLMTab = forwardRef(function LLMTab(_, ref) {
  // ── local state ──────────────────────────────────
  const [provider,   setProvider]   = useState('anthropic');
  const [model,      setModel]      = useState('anthropic/claude-3-5-sonnet-20241022');
  const [apiKey,     setApiKey]     = useState('');
  const [showKey,    setShowKey]    = useState(false);
  const [hasKey,     setHasKey]     = useState(false);  // true if Supabase has an encrypted key
  const [advanced,   setAdvanced]   = useState(false);
  const [loading,    setLoading]    = useState(true);
  const [saving,     setSaving]     = useState(false);
  const [saveStatus, setSaveStatus] = useState(null); // 'success' | 'error' | null
  const [error,      setError]      = useState(null);

  // ── helpers ───────────────────────────────────────
  const providerModels = PROVIDER_CONFIG[provider]?.models ?? [];
  const currentModelLabel = providerModels.find(m => m.id === model)?.label ?? model;
  const providerLabel = PROVIDER_CONFIG[provider]?.label ?? provider;
  const docsUrl = PROVIDER_CONFIG[provider]?.docsUrl ?? '#';

  const switchProvider = useCallback((newProvider) => {
    setProvider(newProvider);
    const firstModel = PROVIDER_CONFIG[newProvider]?.models?.[0]?.id ?? '';
    setModel(firstModel);
    setApiKey('');   // clear the typed key when switching provider
    setHasKey(false);
    setError(null);
  }, []);

  // ── load settings from Supabase on mount ─────────
  useEffect(() => {
    let cancelled = false;
    async function loadSettings() {
      try {
        const res = await fetch('/api/settings');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (cancelled) return;

        // Validate loaded provider/model against our catalogue
        const loadedProvider = PROVIDER_KEYS.includes(data.llm_provider)
          ? data.llm_provider : 'anthropic';
        const loadedModels   = PROVIDER_CONFIG[loadedProvider].models.map(m => m.id);
        const loadedModel    = loadedModels.includes(data.llm_model)
          ? data.llm_model : loadedModels[0];

        setProvider(loadedProvider);
        setModel(loadedModel);
        setHasKey(!!data.has_api_key);
      } catch (err) {
        if (!cancelled) setError('Could not load settings. Using defaults.');
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    loadSettings();
    return () => { cancelled = true; };
  }, []);

  // ── save handler ──────────────────────────────────
  const handleSave = useCallback(async () => {
    setSaving(true);
    setSaveStatus(null);
    setError(null);
    try {
      const body = { llm_provider: provider, llm_model: model };
      if (apiKey.trim()) body.api_key = apiKey.trim();

      const res = await fetch('/api/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.error ?? `HTTP ${res.status}`);
      }

      setSaveStatus('success');
      if (apiKey.trim()) {
        setHasKey(true);
        setApiKey('');   // clear field after save — key is now stored
      }
      setTimeout(() => setSaveStatus(null), 3500);
    } catch (err) {
      setError(err.message);
      setSaveStatus('error');
    } finally {
      setSaving(false);
    }
  }, [provider, model, apiKey]);

  // ── expose save() to parent via ref ─────────────
  useImperativeHandle(ref, () => ({ save: handleSave }), [handleSave]);

  // ── render ────────────────────────────────────────
  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="w-6 h-6 text-blue-500 animate-spin" />
        <span className="ml-3 text-sm text-slate-400">Loading settings…</span>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Error banner */}
      {error && (
        <div className="flex items-center gap-3 px-4 py-3 bg-red-50 dark:bg-red-500/10 border border-red-100 dark:border-red-500/20 rounded-xl">
          <AlertCircle className="w-4 h-4 text-red-500 shrink-0" />
          <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
        </div>
      )}

      {/* Provider & Model */}
      <SectionCard
        title="Model Configuration"
        description="Select your AI provider and model for code generation"
        icon={Cpu}
      >
        <div className="space-y-5">
          {/* Provider */}
          <FieldRow label="Provider" description="AI service that powers the agent">
            <div className="flex flex-col gap-2">
              {PROVIDER_KEYS.map((pk) => {
                const cfg = PROVIDER_CONFIG[pk];
                const isSelected = provider === pk;
                return (
                  <button
                    key={pk}
                    type="button"
                    onClick={() => switchProvider(pk)}
                    className={cn(
                      "flex items-center gap-3 px-4 py-3 rounded-xl border text-sm font-medium transition-all text-left",
                      isSelected
                        ? "border-blue-400 dark:border-blue-500 bg-blue-50 dark:bg-blue-500/10 text-blue-700 dark:text-blue-300 shadow-sm"
                        : "border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 text-slate-600 dark:text-slate-400 hover:border-blue-300 dark:hover:border-blue-500/60 hover:bg-white dark:hover:bg-slate-700"
                    )}
                  >
                    <span className={cn(
                      "w-2 h-2 rounded-full shrink-0",
                      isSelected ? "bg-blue-500" : "bg-slate-300 dark:bg-slate-600"
                    )} />
                    {cfg.label}
                    {isSelected && <Check className="w-4 h-4 ml-auto text-blue-500" />}
                  </button>
                );
              })}
            </div>
          </FieldRow>

          <div className="border-t border-slate-100 dark:border-slate-800" />

          {/* Model */}
          <FieldRow label="Model" description="Specific model variant to use">
            <Dropdown
              value={currentModelLabel}
              options={providerModels.map(m => m.label)}
              onChange={(label) => {
                const found = providerModels.find(m => m.label === label);
                if (found) setModel(found.id);
              }}
              placeholder="Select model…"
            />
          </FieldRow>
        </div>
      </SectionCard>

      {/* API Key */}
      <SectionCard
        title="Authentication"
        description="Your API key is stored securely and never shared"
        icon={Key}
      >
        <FieldRow
          label="API Key"
          description={`Required for accessing ${providerLabel}`}
          badge={
            hasKey && !apiKey ? (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-emerald-50 dark:bg-emerald-500/10 border border-emerald-100 dark:border-emerald-500/20 rounded-full">
                <Check className="w-3 h-3 text-emerald-600 dark:text-emerald-400" />
                <span className="text-[10px] font-bold text-emerald-600 dark:text-emerald-400 uppercase">Saved</span>
              </span>
            ) : null
          }
        >
          <div className="relative">
            <input
              id="llm-api-key"
              type={showKey ? 'text' : 'password'}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={hasKey ? '••••••••••••••••••••  (key saved — paste to replace)' : `Paste your ${providerLabel} API key…`}
              className="w-full px-4 py-2.5 pr-10 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500 focus:border-blue-300 dark:focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 outline-none transition-all"
            />
            <button
              type="button"
              onClick={() => setShowKey(!showKey)}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 transition-colors"
            >
              {showKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
            </button>
          </div>
          <p className="mt-2 text-xs text-slate-400 dark:text-slate-500">
            Don&apos;t have an API key?{' '}
            <a
              href={docsUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-600 dark:text-blue-400 hover:text-blue-500 underline underline-offset-2 transition-colors"
            >
              Get one here ↗
            </a>
          </p>
        </FieldRow>
      </SectionCard>

      {/* Advanced Settings */}
      <SectionCard
        title="Advanced Settings"
        description="Custom endpoints and model overrides"
        icon={Zap}
      >
        <Toggle
          checked={advanced}
          onChange={setAdvanced}
          label="Enable advanced configuration"
          description="Override default API endpoints and model identifiers"
        />
        {advanced && (
          <div className="space-y-5 pt-4 border-t border-slate-100 dark:border-slate-800 mt-4 animate-slide-down">
            <FieldRow label="Base URL" description="Custom API endpoint">
              <input
                type="text"
                placeholder={'https://api.anthropic.com'}
                className="w-full px-4 py-2.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500 focus:border-blue-300 dark:focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 outline-none transition-all"
              />
            </FieldRow>
            <div className="border-t border-slate-100 dark:border-slate-800" />
            <FieldRow label="Custom Model ID" description="Override the model identifier (LiteLLM format)">
              <input
                type="text"
                placeholder="e.g. gemini/gemini-3-flash-preview"
                className="w-full px-4 py-2.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500 focus:border-blue-300 dark:focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 outline-none transition-all"
              />
            </FieldRow>
          </div>
        )}
      </SectionCard>

      {/* Save row */}
      <div className="flex items-center justify-end gap-4">
        {saveStatus === 'success' && (
          <span className="flex items-center gap-1.5 text-sm text-emerald-600 dark:text-emerald-400 animate-fade-in">
            <Check className="w-4 h-4" /> Settings saved!
          </span>
        )}
        {saveStatus === 'error' && (
          <span className="flex items-center gap-1.5 text-sm text-red-500 dark:text-red-400 animate-fade-in">
            <AlertCircle className="w-4 h-4" /> Save failed.
          </span>
        )}
        <button
          onClick={handleSave}
          disabled={saving}
          className={cn(
            "flex items-center gap-2 px-6 py-2.5 rounded-xl text-sm font-bold transition-all shadow-sm active:scale-[0.98]",
            saving
              ? "bg-blue-400 text-white cursor-not-allowed"
              : "bg-blue-600 text-white hover:bg-blue-700 shadow-blue-600/15"
          )}
        >
          {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
          {saving ? 'Saving…' : 'Save Changes'}
        </button>
      </div>
    </div>
  );
});

/* ════════════════════════════════════════════════
   MCP TAB
   ════════════════════════════════════════════════ */
function MCPTab() {
  const [servers, setServers] = useState([]);
  const [showAddModal, setShowAddModal] = useState(false);
  const [newServerName, setNewServerName] = useState('');
  const [newServerUrl, setNewServerUrl] = useState('');

  const handleAddServer = () => {
    if (!newServerName.trim()) return;
    setServers([...servers, {
      id: Date.now(),
      name: newServerName,
      url: newServerUrl,
      status: 'active',
    }]);
    setNewServerName('');
    setNewServerUrl('');
    setShowAddModal(false);
  };

  const handleRemove = (id) => {
    setServers(servers.filter(s => s.id !== id));
  };

  return (
    <div className="space-y-6">
      <SectionCard
        title="MCP Servers"
        description="Configure Model Context Protocol servers for tool access"
        icon={HardDrive}
      >
        {/* Add Server Button */}
        <div className="mb-5">
          <button
            onClick={() => setShowAddModal(true)}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-xl text-sm font-bold hover:bg-blue-700 transition-all shadow-sm shadow-blue-600/15 active:scale-[0.98]"
          >
            <Plus className="w-4 h-4" />
            Add Server
          </button>
        </div>

        {/* Server List */}
        {servers.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 border border-dashed border-slate-200 dark:border-slate-700 rounded-xl bg-slate-50/50 dark:bg-slate-800/50">
            <div className="w-12 h-12 rounded-xl bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 flex items-center justify-center mb-3">
              <HardDrive className="w-5 h-5 text-slate-300 dark:text-slate-600" />
            </div>
            <p className="text-sm font-medium text-slate-400 dark:text-slate-500">No servers configured</p>
            <p className="text-xs text-slate-300 dark:text-slate-600 mt-1">Add an MCP server to get started</p>
          </div>
        ) : (
          <div className="border border-slate-200 dark:border-slate-700 rounded-xl overflow-hidden">
            <div className="divide-y divide-slate-100 dark:divide-slate-800">
              {servers.map((server) => (
                <div key={server.id} className="flex items-center justify-between px-5 py-4 hover:bg-slate-50/50 dark:hover:bg-slate-800/50 transition-colors">
                  <div className="flex items-center gap-3">
                    <div className="w-9 h-9 rounded-lg bg-violet-50 dark:bg-violet-500/10 border border-violet-100 dark:border-violet-500/20 flex items-center justify-center">
                      <Server className="w-4 h-4 text-violet-600 dark:text-violet-400" />
                    </div>
                    <div>
                      <p className="text-sm font-semibold text-slate-800 dark:text-slate-200">{server.name}</p>
                      {server.url && <p className="text-xs text-slate-400 dark:text-slate-500 mt-0.5">{server.url}</p>}
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="flex items-center gap-1.5 px-2.5 py-1 bg-emerald-50 dark:bg-emerald-500/10 border border-emerald-100 dark:border-emerald-500/20 rounded-full">
                      <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                      <span className="text-[10px] font-bold text-emerald-600 dark:text-emerald-400 uppercase">Active</span>
                    </span>
                    <button
                      onClick={() => handleRemove(server.id)}
                      className="p-1.5 rounded-lg text-slate-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-500/10 transition-all"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </SectionCard>

      {/* Add Server Modal */}
      {showAddModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm animate-fade-in">
          <div className="w-full max-w-md bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl p-6 shadow-2xl animate-slide-up">
            <div className="flex items-center justify-between mb-6">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg bg-violet-50 dark:bg-violet-500/10 border border-violet-100 dark:border-violet-500/20 flex items-center justify-center">
                  <HardDrive className="w-4 h-4 text-violet-600 dark:text-violet-400" />
                </div>
                <h3 className="text-lg font-bold text-slate-900 dark:text-slate-100">Add MCP Server</h3>
              </div>
              <button
                onClick={() => setShowAddModal(false)}
                className="p-1.5 rounded-lg text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800 transition-all"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 dark:text-slate-200 mb-2">Server Name</label>
                <input
                  type="text"
                  value={newServerName}
                  onChange={(e) => setNewServerName(e.target.value)}
                  placeholder="e.g. My MCP Server"
                  className="w-full px-4 py-2.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500 focus:border-blue-300 dark:focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 outline-none transition-all"
                  autoFocus
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 dark:text-slate-200 mb-2">Server URL</label>
                <input
                  type="text"
                  value={newServerUrl}
                  onChange={(e) => setNewServerUrl(e.target.value)}
                  placeholder="e.g. http://localhost:3001"
                  className="w-full px-4 py-2.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500 focus:border-blue-300 dark:focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 outline-none transition-all"
                />
              </div>
            </div>
            <div className="flex justify-end gap-3 mt-6">
              <button
                onClick={() => setShowAddModal(false)}
                className="px-4 py-2 text-sm font-medium text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleAddServer}
                disabled={!newServerName.trim()}
                className={cn(
                  "px-5 py-2 rounded-xl text-sm font-bold transition-all",
                  newServerName.trim()
                    ? "bg-blue-600 text-white hover:bg-blue-700 shadow-sm shadow-blue-600/15"
                    : "bg-slate-100 dark:bg-slate-800 text-slate-400 dark:text-slate-500 cursor-not-allowed"
                )}
              >
                Add Server
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ════════════════════════════════════════════════
   APPLICATION TAB
   ════════════════════════════════════════════════ */
function ApplicationTab() {
  const [language, setLanguage] = useState('English');
  const [anonymousUsage, setAnonymousUsage] = useState(false);
  const [soundNotifications, setSoundNotifications] = useState(true);
  const [gitUsername, setGitUsername] = useState('openhands');
  const [gitEmail, setGitEmail] = useState('openhands@all-hands.dev');
  const [packageManager, setPackageManager] = useState('yarn');

  const languages = ['English', 'Spanish', 'French', 'German', 'Chinese', 'Japanese', 'Korean', 'Russian'];
  const packageManagers = ['yarn', 'npm', 'pnpm', 'bun'];

  return (
    <div className="space-y-6">
      {/* General Section */}
      <SectionCard
        title="General"
        description="Language and notification preferences"
        icon={Languages}
      >
        <div className="space-y-5">
          <FieldRow label="Language" description="Display language for the interface">
            <Dropdown
              value={language}
              options={languages}
              onChange={setLanguage}
              placeholder="Select language..."
            />
          </FieldRow>

          <div className="border-t border-slate-100 dark:border-slate-800" />

          <div className="space-y-0">
            <Toggle
              checked={soundNotifications}
              onChange={setSoundNotifications}
              label="Sound Notifications"
              description="Play sounds for important events"
            />
            <div className="border-t border-slate-100 dark:border-slate-800" />
            <Toggle
              checked={anonymousUsage}
              onChange={setAnonymousUsage}
              label="Anonymous Usage Data"
              description="Help us improve by sharing anonymous usage statistics"
            />
          </div>
        </div>
      </SectionCard>

      {/* Git Settings */}
      <SectionCard
        title="Git Configuration"
        description="Username and email for commit authorship"
        icon={GitBranch}
      >
        <div className="space-y-5">
          <FieldRow label="Username" description="Your Git display name">
            <input
              type="text"
              value={gitUsername}
              onChange={(e) => setGitUsername(e.target.value)}
              className="w-full px-4 py-2.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 focus:border-blue-300 dark:focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 outline-none transition-all"
            />
          </FieldRow>

          <div className="border-t border-slate-100 dark:border-slate-800" />

          <FieldRow label="Email" description="Linked to your Git commits">
            <input
              type="text"
              value={gitEmail}
              onChange={(e) => setGitEmail(e.target.value)}
              className="w-full px-4 py-2.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 focus:border-blue-300 dark:focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 outline-none transition-all"
            />
          </FieldRow>
        </div>
      </SectionCard>

      {/* Package Manager */}
      <SectionCard
        title="Package Manager"
        description="Default tool for managing project dependencies"
        icon={Package}
      >
        <FieldRow label="Default Manager" description="Used for new projects">
          <Dropdown
            value={packageManager}
            options={packageManagers}
            onChange={setPackageManager}
            placeholder="Select package manager..."
          />
        </FieldRow>
        <div className="mt-4 flex items-start gap-2 p-3 bg-blue-50 dark:bg-blue-500/10 border border-blue-100 dark:border-blue-500/20 rounded-xl">
          <Info className="w-4 h-4 text-blue-500 dark:text-blue-400 shrink-0 mt-0.5" />
          <p className="text-xs text-blue-600 dark:text-blue-400 leading-relaxed">
            For existing projects, the agent will automatically detect the package manager from lock files (yarn.lock, package-lock.json, or pnpm-lock.yaml).
          </p>
        </div>
      </SectionCard>

      {/* Save */}
      <div className="flex justify-end">
        <button className="px-6 py-2.5 bg-blue-600 text-white rounded-xl text-sm font-bold hover:bg-blue-700 transition-all shadow-sm shadow-blue-600/15 active:scale-[0.98]">
          Save Changes
        </button>
      </div>
    </div>
  );
}

/* ════════════════════════════════════════════════
   SECRETS TAB
   ════════════════════════════════════════════════ */
function SecretsTab() {
  const [secrets, setSecrets] = useState([]);
  const [showAddForm, setShowAddForm] = useState(false);
  const [newName, setNewName] = useState('');
  const [newValue, setNewValue] = useState('');
  const [newDescription, setNewDescription] = useState('');

  const handleAddSecret = () => {
    if (!newName.trim() || !newValue.trim()) return;
    setSecrets([...secrets, {
      id: Date.now(),
      name: newName,
      description: newDescription,
    }]);
    setNewName('');
    setNewValue('');
    setNewDescription('');
    setShowAddForm(false);
  };

  const handleDelete = (id) => {
    setSecrets(secrets.filter(s => s.id !== id));
  };

  return (
    <div className="space-y-6">
      <SectionCard
        title="Secret Management"
        description="Store sensitive keys and tokens securely"
        icon={Shield}
      >
        {/* Add Secret Form */}
        {showAddForm ? (
          <div className="space-y-5 animate-fade-in">
            <div className="p-5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl space-y-5">
              <h4 className="text-sm font-bold text-slate-900 dark:text-slate-100">New Secret</h4>

              <FieldRow label="Name" description="Identifier for the secret">
                <input
                  type="text"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  placeholder="e.g. OpenAI_API_Key"
                  className="w-full px-4 py-2.5 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500 focus:border-blue-300 dark:focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 outline-none transition-all"
                  autoFocus
                />
              </FieldRow>

              <div className="border-t border-slate-200 dark:border-slate-700" />

              <FieldRow label="Value" description="The secret content">
                <textarea
                  value={newValue}
                  onChange={(e) => setNewValue(e.target.value)}
                  rows={4}
                  className="w-full px-4 py-3 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500 focus:border-blue-300 dark:focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 outline-none transition-all resize-none font-mono"
                />
              </FieldRow>

              <div className="border-t border-slate-200 dark:border-slate-700" />

              <FieldRow label="Description" description="Optional note about this secret">
                <input
                  type="text"
                  value={newDescription}
                  onChange={(e) => setNewDescription(e.target.value)}
                  placeholder="What is this secret used for?"
                  className="w-full px-4 py-2.5 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500 focus:border-blue-300 dark:focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 outline-none transition-all"
                />
              </FieldRow>

              <div className="flex items-center gap-3 pt-2">
                <button
                  onClick={handleAddSecret}
                  disabled={!newName.trim() || !newValue.trim()}
                  className={cn(
                    "px-5 py-2 rounded-xl text-sm font-bold transition-all",
                    newName.trim() && newValue.trim()
                      ? "bg-blue-600 text-white hover:bg-blue-700 shadow-sm shadow-blue-600/15"
                      : "bg-slate-100 dark:bg-slate-700 text-slate-400 dark:text-slate-500 cursor-not-allowed"
                  )}
                >
                  Add Secret
                </button>
                <button
                  onClick={() => { setShowAddForm(false); setNewName(''); setNewValue(''); setNewDescription(''); }}
                  className="px-4 py-2 text-sm font-medium text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 transition-colors"
                >
                  Cancel
                </button>
              </div>
            </div>
          </div>
        ) : (
          <div className="mb-5">
            <button
              onClick={() => setShowAddForm(true)}
              className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-xl text-sm font-bold hover:bg-blue-700 transition-all shadow-sm shadow-blue-600/15 active:scale-[0.98]"
            >
              <Plus className="w-4 h-4" />
              Add a new secret
            </button>
          </div>
        )}

        {/* Secrets Table */}
        {secrets.length === 0 && !showAddForm ? (
          <div className="flex flex-col items-center justify-center py-12 border border-dashed border-slate-200 dark:border-slate-700 rounded-xl bg-slate-50/50 dark:bg-slate-800/50">
            <div className="w-12 h-12 rounded-xl bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 flex items-center justify-center mb-3">
              <Shield className="w-5 h-5 text-slate-300 dark:text-slate-600" />
            </div>
            <p className="text-sm font-medium text-slate-400 dark:text-slate-500">No secrets configured</p>
            <p className="text-xs text-slate-300 dark:text-slate-600 mt-1">Add secrets to use in your workflows</p>
          </div>
        ) : secrets.length > 0 && (
          <div className="border border-slate-200 dark:border-slate-700 rounded-xl overflow-hidden mt-5">
            {/* Header */}
            <div className="grid grid-cols-[1fr_1fr_80px] px-5 py-3 border-b border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800">
              <span className="text-[11px] font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-wider">Name</span>
              <span className="text-[11px] font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-wider">Description</span>
              <span className="text-[11px] font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-wider text-right">Actions</span>
            </div>

            {/* Body */}
            <div className="divide-y divide-slate-100 dark:divide-slate-800">
              {secrets.map((secret) => (
                <div key={secret.id} className="grid grid-cols-[1fr_1fr_80px] items-center px-5 py-3.5 hover:bg-slate-50/50 dark:hover:bg-slate-800/50 transition-colors">
                  <div className="flex items-center gap-2.5">
                    <div className="w-7 h-7 rounded-lg bg-amber-50 dark:bg-amber-500/10 border border-amber-100 dark:border-amber-500/20 flex items-center justify-center">
                      <Lock className="w-3.5 h-3.5 text-amber-500 dark:text-amber-400" />
                    </div>
                    <span className="text-sm font-medium text-slate-700 dark:text-slate-200">{secret.name}</span>
                  </div>
                  <span className="text-sm text-slate-400 dark:text-slate-500">{secret.description || '—'}</span>
                  <div className="flex justify-end">
                    <button
                      onClick={() => handleDelete(secret.id)}
                      className="p-1.5 rounded-lg text-slate-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-500/10 transition-all"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </SectionCard>
    </div>
  );
}

/* ════════════════════════════════════════════════
   MAIN SETTINGS PAGE
   ════════════════════════════════════════════════ */
export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState('llm');
  const llmTabRef = useRef(null);
  const [headerSaving, setHeaderSaving] = useState(false);
  const [headerSaveStatus, setHeaderSaveStatus] = useState(null); // 'success' | 'error' | null

  const handleHeaderSave = useCallback(async () => {
    if (activeTab === 'llm' && llmTabRef.current?.save) {
      setHeaderSaving(true);
      setHeaderSaveStatus(null);
      try {
        await llmTabRef.current.save();
        setHeaderSaveStatus('success');
        setTimeout(() => setHeaderSaveStatus(null), 3000);
      } catch {
        setHeaderSaveStatus('error');
      } finally {
        setHeaderSaving(false);
      }
    }
  }, [activeTab]);

  const renderTabContent = () => {
    switch (activeTab) {
      case 'llm': return <LLMTab ref={llmTabRef} />;
      case 'mcp': return <MCPTab />;
      case 'application': return <ApplicationTab />;
      case 'secrets': return <SecretsTab />;
      default: return null;
    }
  };

  const activeTabData = tabs.find(t => t.id === activeTab);
  const activeColor = colorMap[activeTabData?.color || 'blue'];

  return (
    <div className="min-h-full bg-[#f0f4f9] dark:bg-[#0d1117] transition-colors duration-200">
      <div className="max-w-6xl mx-auto px-8 py-10">

        {/* Page Header */}
        <div className="flex items-center justify-between mb-8">
          <div className="flex items-center gap-4">
            <div className="w-12 h-12 rounded-2xl bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 flex items-center justify-center shadow-soft">
              <Settings className="w-6 h-6 text-slate-500 dark:text-slate-400" />
            </div>
            <div>
              <h1 className="text-2xl font-extrabold text-slate-900 dark:text-slate-100 tracking-tight">Settings</h1>
              <p className="text-sm text-slate-400 dark:text-slate-500 font-medium mt-0.5">Configure your AI development environment</p>
            </div>
          </div>
          
          <button
            onClick={handleHeaderSave}
            disabled={headerSaving || activeTab !== 'llm'}
            className={cn(
              "flex items-center gap-2 px-6 py-2.5 rounded-xl text-sm font-bold transition-all shadow-lg active:scale-[0.98]",
              headerSaving
                ? "bg-blue-400 text-white cursor-not-allowed shadow-blue-400/20"
                : headerSaveStatus === 'success'
                  ? "bg-emerald-600 text-white shadow-emerald-600/20"
                  : activeTab !== 'llm'
                    ? "bg-slate-200 dark:bg-slate-800 text-slate-400 dark:text-slate-500 cursor-not-allowed shadow-none"
                    : "bg-blue-600 text-white hover:bg-blue-700 shadow-blue-600/20"
            )}
          >
            {headerSaving
              ? <Loader2 className="w-4 h-4 animate-spin" />
              : headerSaveStatus === 'success'
                ? <Check className="w-4 h-4" />
                : <Check className="w-4 h-4" />}
            {headerSaving ? 'Saving…' : headerSaveStatus === 'success' ? 'Saved!' : 'Save Changes'}
          </button>
        </div>

        {/* Content Area */}
        <div className="grid grid-cols-1 lg:grid-cols-[260px_1fr] gap-8 relative z-10">

          {/* ── Tab Sidebar ── */}
          <div className="lg:sticky lg:top-32 self-start space-y-6">
            <nav className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl p-2 shadow-soft space-y-1">
              {tabs.map((tab) => {
                const isActive = activeTab === tab.id;
                const colors = colorMap[tab.color]; 
                const activeBg = colors?.bg || 'bg-slate-50';
                const activeBorder = colors?.border || 'border-slate-200';
                const activeIconBg = colors?.activeBg || 'bg-slate-800';
                
                return (
                  <button
                    key={tab.id}
                    onClick={() => setActiveTab(tab.id)}
                    className={cn(
                      "w-full flex items-center gap-3 px-3.5 py-3 rounded-xl text-left transition-all duration-200 group relative overflow-hidden",
                      isActive
                        ? `${activeBg} border ${activeBorder}`
                        : "hover:bg-slate-50 dark:hover:bg-slate-800 border border-transparent"
                    )}
                  >
                    {/* Active Indicator Bar */}
                    {isActive && (
                      <div className={cn("absolute left-0 top-0 bottom-0 w-1", colors?.activeBg || 'bg-blue-600')} />
                    )}

                    <div className={cn(
                      "w-9 h-9 rounded-lg flex items-center justify-center shrink-0 transition-colors shadow-sm",
                      isActive
                        ? `${activeIconBg} text-white`
                        : "bg-slate-100 dark:bg-slate-800 text-slate-400 dark:text-slate-500 group-hover:bg-white dark:group-hover:bg-slate-700 group-hover:text-slate-600 dark:group-hover:text-slate-300 group-hover:shadow-sm"
                    )}>
                      <tab.Icon className="w-4.5 h-4.5" />
                    </div>
                    
                    <div className="flex-1 min-w-0">
                      <span className={cn(
                        "block text-sm font-bold transition-colors",
                        isActive ? "text-slate-900 dark:text-slate-100" : "text-slate-500 dark:text-slate-400 group-hover:text-slate-700 dark:group-hover:text-slate-200"
                      )}>
                        {tab.label}
                      </span>
                      <span className={cn(
                        "block text-[11px] font-medium transition-colors truncate",
                        isActive ? "text-slate-500 dark:text-slate-400" : "text-slate-400 dark:text-slate-500 group-hover:text-slate-500 dark:group-hover:text-slate-400"
                      )}>
                        {tab.description}
                      </span>
                    </div>
                    
                    {isActive && (
                      <ChevronRight className="w-4 h-4 text-slate-400 dark:text-slate-500" />
                    )}
                  </button>
                );
              })}
            </nav>
            
            <div className="p-5 bg-gradient-to-br from-blue-600 to-indigo-700 rounded-2xl text-white text-center shadow-lg shadow-blue-900/20">
              <h3 className="font-bold text-sm mb-1">Need Help?</h3>
              <p className="text-xs text-blue-100 mb-3 leading-relaxed">Check our documentation for advanced configuration guides.</p>
              <button className="px-4 py-2 bg-white/10 hover:bg-white/20 border border-white/20 rounded-lg text-xs font-bold transition-all w-full backdrop-blur-sm">
                View Docs
              </button>
            </div>
          </div>

          {/* ── Tab Content ── */}
          <div className="min-w-0">
             {renderTabContent()}
          </div>

        </div>
      </div>
    </div>
  );
}
