'use client';

import { useState, useEffect, useCallback, useRef, forwardRef, useImperativeHandle } from 'react';
import {
  Settings, ChevronDown, Check, Plus, Trash2, Eye, EyeOff,
  X, ExternalLink, Key, Server, Globe, Lock, Cpu, HardDrive,
  Bell, BellOff, BarChart3, GitBranch, Package, Languages,
  Zap, Shield, ChevronRight, Info, Loader2, AlertCircle, Rocket,
  Database, Cloud, Container, Upload
} from 'lucide-react';
import ExportCodeModal from '@/components/ExportCodeModal';
import AuthProvidersTab from '@/components/AuthProvidersTab';
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
    color: 'emerald',
  },
  {
    id: 'mcp',
    label: 'MCP',
    description: 'Server connections',
    Icon: HardDrive,
    color: 'emerald',
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
  {
    id: 'deployment',
    label: 'Deployment',
    description: 'CI/CD & infrastructure',
    Icon: Rocket,
    color: 'rose',
  },
  {
    id: 'auth',
    label: 'Auth Providers',
    description: 'OAuth configuration',
    Icon: Database,
    color: 'teal',
  },
];

const colorMap = {
  blue:    { bg: 'bg-emerald-50 dark:bg-emerald-500/10', border: 'border-emerald-100 dark:border-emerald-500/20', text: 'text-emerald-600 dark:text-emerald-400', icon: 'text-emerald-500', activeBg: 'bg-emerald-600', dot: 'bg-emerald-500' },
  violet:  { bg: 'bg-teal-50 dark:bg-teal-500/10', border: 'border-teal-100 dark:border-teal-500/20', text: 'text-teal-600 dark:text-teal-400', icon: 'text-teal-500', activeBg: 'bg-teal-600', dot: 'bg-teal-500' },
  emerald: { bg: 'bg-emerald-50 dark:bg-emerald-500/10', border: 'border-emerald-100 dark:border-emerald-500/20',text: 'text-emerald-600 dark:text-emerald-400', icon: 'text-emerald-500',activeBg: 'bg-emerald-600', dot: 'bg-emerald-500' },
  amber:   { bg: 'bg-amber-50 dark:bg-amber-500/10',   border: 'border-amber-100 dark:border-amber-500/20',  text: 'text-amber-600 dark:text-amber-400',   icon: 'text-amber-500',  activeBg: 'bg-amber-600',   dot: 'bg-amber-500' },
  rose:    { bg: 'bg-rose-50 dark:bg-rose-500/10',     border: 'border-rose-100 dark:border-rose-500/20',    text: 'text-rose-600 dark:text-rose-400',     icon: 'text-rose-500',   activeBg: 'bg-rose-600',    dot: 'bg-rose-500' },
  teal:    { bg: 'bg-teal-50 dark:bg-teal-500/10',     border: 'border-teal-100 dark:border-teal-500/20',    text: 'text-teal-600 dark:text-teal-400',     icon: 'text-teal-500',   activeBg: 'bg-teal-600',    dot: 'bg-teal-500' },
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
          checked ? "bg-emerald-600" : "bg-slate-200 dark:bg-slate-700"
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
        className="w-full flex items-center justify-between px-4 py-2.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 hover:border-emerald-300 dark:hover:border-emerald-500/50 focus:border-emerald-300 focus:ring-2 focus:ring-emerald-500/10 transition-all outline-none"
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
                    value === opt ? "text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-500/10" : "text-slate-600 dark:text-slate-300"
                  )}
                >
                  {opt}
                  {value === opt && <Check className="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-400" />}
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
  // No loading gate — show UI immediately, data populates when ready

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
                        ? "border-emerald-400 dark:border-emerald-500 bg-emerald-50 dark:bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 shadow-sm"
                        : "border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 text-slate-600 dark:text-slate-400 hover:border-emerald-300 dark:hover:border-emerald-500/60 hover:bg-white dark:hover:bg-slate-700"
                    )}
                  >
                    <span className={cn(
                      "w-2 h-2 rounded-full shrink-0",
                      isSelected ? "bg-emerald-500" : "bg-slate-300 dark:bg-slate-600"
                    )} />
                    {cfg.label}
                    {isSelected && <Check className="w-4 h-4 ml-auto text-emerald-500" />}
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
              className="w-full px-4 py-2.5 pr-10 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500 focus:border-emerald-300 dark:focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/10 outline-none transition-all"
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
              className="text-emerald-600 dark:text-emerald-400 hover:text-emerald-500 underline underline-offset-2 transition-colors"
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
                className="w-full px-4 py-2.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500 focus:border-emerald-300 dark:focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/10 outline-none transition-all"
              />
            </FieldRow>
            <div className="border-t border-slate-100 dark:border-slate-800" />
            <FieldRow label="Custom Model ID" description="Override the model identifier (LiteLLM format)">
              <input
                type="text"
                placeholder="e.g. gemini/gemini-3-flash-preview"
                className="w-full px-4 py-2.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500 focus:border-emerald-300 dark:focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/10 outline-none transition-all"
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
              ? "bg-emerald-400 text-white cursor-not-allowed"
              : "bg-emerald-600 text-white hover:bg-emerald-700 shadow-emerald-600/15"
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
            className="flex items-center gap-2 px-4 py-2 bg-emerald-600 text-white rounded-xl text-sm font-bold hover:bg-emerald-700 transition-all shadow-sm shadow-emerald-600/15 active:scale-[0.98]"
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
                    <div className="w-9 h-9 rounded-lg bg-emerald-50 dark:bg-emerald-500/10 border border-emerald-100 dark:border-emerald-500/20 flex items-center justify-center">
                      <Server className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
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
                <div className="w-8 h-8 rounded-lg bg-emerald-50 dark:bg-emerald-500/10 border border-emerald-100 dark:border-emerald-500/20 flex items-center justify-center">
                  <HardDrive className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
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
                  className="w-full px-4 py-2.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500 focus:border-emerald-300 dark:focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/10 outline-none transition-all"
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
                  className="w-full px-4 py-2.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500 focus:border-emerald-300 dark:focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/10 outline-none transition-all"
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
                    ? "bg-emerald-600 text-white hover:bg-emerald-700 shadow-sm shadow-emerald-600/15"
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
  const [packageManager, setPackageManager] = useState('npm');
  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState(null);

  const languages = ['English', 'Spanish', 'French', 'German', 'Chinese', 'Japanese', 'Korean', 'Russian'];
  const packageManagers = ['npm', 'yarn', 'pnpm', 'bun'];

  // Load saved package manager from settings API
  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const res = await fetch('/api/settings');
        if (!res.ok) return;
        const data = await res.json();
        if (cancelled) return;
        if (data.package_manager) setPackageManager(data.package_manager);
      } catch { /* ignore */ }
    }
    load();
    return () => { cancelled = true; };
  }, []);

  const handleSave = useCallback(async () => {
    setSaving(true); setSaveStatus(null);
    try {
      const res = await fetch('/api/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ package_manager: packageManager }),
      });
      if (!res.ok) throw new Error((await res.json()).error || `HTTP ${res.status}`);
      setSaveStatus('success');
      setTimeout(() => setSaveStatus(null), 3500);
    } catch {
      setSaveStatus('error');
    } finally { setSaving(false); }
  }, [packageManager]);

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
              className="w-full px-4 py-2.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 focus:border-emerald-300 dark:focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/10 outline-none transition-all"
            />
          </FieldRow>

          <div className="border-t border-slate-100 dark:border-slate-800" />

          <FieldRow label="Email" description="Linked to your Git commits">
            <input
              type="text"
              value={gitEmail}
              onChange={(e) => setGitEmail(e.target.value)}
              className="w-full px-4 py-2.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 focus:border-emerald-300 dark:focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/10 outline-none transition-all"
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
        <FieldRow label="Default Manager" description="Used for new projects when no lock file exists">
          <Dropdown
            value={packageManager}
            options={packageManagers}
            onChange={setPackageManager}
            placeholder="Select package manager..."
          />
        </FieldRow>
        <div className="mt-4 flex items-start gap-2 p-3 bg-emerald-50 dark:bg-emerald-500/10 border border-emerald-100 dark:border-emerald-500/20 rounded-xl">
          <Info className="w-4 h-4 text-emerald-500 dark:text-emerald-400 shrink-0 mt-0.5" />
          <p className="text-xs text-emerald-600 dark:text-emerald-400 leading-relaxed">
            For existing projects, the agent will automatically detect the package manager from lock files (yarn.lock, package-lock.json, pnpm-lock.yaml, or bun.lockb).
          </p>
        </div>
      </SectionCard>

      {/* Save */}
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
              ? "bg-emerald-400 text-white cursor-not-allowed"
              : "bg-emerald-600 text-white hover:bg-emerald-700 shadow-emerald-600/15"
          )}
        >
          {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
          {saving ? 'Saving…' : 'Save Changes'}
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
                  className="w-full px-4 py-2.5 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500 focus:border-emerald-300 dark:focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/10 outline-none transition-all"
                  autoFocus
                />
              </FieldRow>

              <div className="border-t border-slate-200 dark:border-slate-700" />

              <FieldRow label="Value" description="The secret content">
                <textarea
                  value={newValue}
                  onChange={(e) => setNewValue(e.target.value)}
                  rows={4}
                  className="w-full px-4 py-3 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500 focus:border-emerald-300 dark:focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/10 outline-none transition-all resize-none font-mono"
                />
              </FieldRow>

              <div className="border-t border-slate-200 dark:border-slate-700" />

              <FieldRow label="Description" description="Optional note about this secret">
                <input
                  type="text"
                  value={newDescription}
                  onChange={(e) => setNewDescription(e.target.value)}
                  placeholder="What is this secret used for?"
                  className="w-full px-4 py-2.5 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500 focus:border-emerald-300 dark:focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/10 outline-none transition-all"
                />
              </FieldRow>

              <div className="flex items-center gap-3 pt-2">
                <button
                  onClick={handleAddSecret}
                  disabled={!newName.trim() || !newValue.trim()}
                  className={cn(
                    "px-5 py-2 rounded-xl text-sm font-bold transition-all",
                    newName.trim() && newValue.trim()
                      ? "bg-emerald-600 text-white hover:bg-emerald-700 shadow-sm shadow-emerald-600/15"
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
              className="flex items-center gap-2 px-4 py-2 bg-emerald-600 text-white rounded-xl text-sm font-bold hover:bg-emerald-700 transition-all shadow-sm shadow-emerald-600/15 active:scale-[0.98]"
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
   DEPLOYMENT TAB
   ════════════════════════════════════════════════ */
function DeploymentTab() {
  const [gitlabHost, setGitlabHost] = useState('');
  const [gitlabGroup, setGitlabGroup] = useState('');
  const [gitlabToken, setGitlabToken] = useState('');
  const [hasGitlabToken, setHasGitlabToken] = useState(false);
  const [showGitlabToken, setShowGitlabToken] = useState(false);
  const [opsRepoUrl, setOpsRepoUrl] = useState('');
  const [opsRepoBranch, setOpsRepoBranch] = useState('main');
  const [vercelToken, setVercelToken] = useState('');
  const [hasVercelToken, setHasVercelToken] = useState(false);
  const [showVercelToken, setShowVercelToken] = useState(false);
  const [vercelTeamId, setVercelTeamId] = useState('');
  const [k8sNamespace, setK8sNamespace] = useState('frontend-prod');
  const [k8sDomain, setK8sDomain] = useState('*.udevs.io');
  const [k8sTlsSecret, setK8sTlsSecret] = useState('');
  const [registryUrl, setRegistryUrl] = useState('');
  const [gitlabInviteUsername, setGitlabInviteUsername] = useState('udevs');
  
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const res = await fetch('/api/settings');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (cancelled) return;
        setGitlabHost(data.gitlab_host || '');
        setGitlabGroup(data.gitlab_group || '');
        setHasGitlabToken(!!data.has_gitlab_token);
        setOpsRepoUrl(data.ops_repo_url || '');
        setOpsRepoBranch(data.ops_repo_branch || 'main');
        setHasVercelToken(!!data.has_vercel_token);
        setVercelTeamId(data.vercel_team_id || '');
        setK8sNamespace(data.k8s_namespace || 'frontend-prod');
        setK8sDomain(data.k8s_domain || '*.udevs.io');
        setK8sTlsSecret(data.k8s_tls_secret || '');
        setRegistryUrl(data.registry_url || '');
        setGitlabInviteUsername(data.gitlab_invite_username || 'udevs');
      } catch (err) {
        if (!cancelled) setError('Could not load deployment settings.');
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, []);

  const handleSave = useCallback(async () => {
    setSaving(true); setSaveStatus(null); setError(null);
    try {
      const body = {
        gitlab_host: gitlabHost,
        gitlab_group: gitlabGroup,
        ops_repo_url: opsRepoUrl,
        ops_repo_branch: opsRepoBranch,
        vercel_team_id: vercelTeamId,
        k8s_namespace: k8sNamespace,
        k8s_domain: k8sDomain,
        k8s_tls_secret: k8sTlsSecret,
        registry_url: registryUrl,
        gitlab_invite_username: gitlabInviteUsername,
      };
      if (gitlabToken.trim()) body.gitlab_token = gitlabToken.trim();
      if (vercelToken.trim()) body.vercel_token = vercelToken.trim();

      const res = await fetch('/api/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error((await res.json()).error || `HTTP ${res.status}`);

      setSaveStatus('success');
      if (gitlabToken.trim()) { setHasGitlabToken(true); setGitlabToken(''); }
      if (vercelToken.trim()) { setHasVercelToken(true); setVercelToken(''); }
      setTimeout(() => setSaveStatus(null), 3500);
    } catch (err) {
      setError(err.message); setSaveStatus('error');
    } finally { setSaving(false); }
  }, [
    gitlabHost, gitlabGroup, gitlabToken, opsRepoUrl, opsRepoBranch,
    vercelToken, vercelTeamId, k8sNamespace, k8sDomain, k8sTlsSecret, registryUrl,
    gitlabInviteUsername
  ]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="w-6 h-6 text-rose-500 animate-spin" />
        <span className="ml-3 text-sm text-slate-400">Loading deployment settings…</span>
      </div>
    );
  }

  const inputCls = "w-full px-4 py-2.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm text-slate-700 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500 focus:border-rose-300 dark:focus:border-rose-500 focus:ring-2 focus:ring-rose-500/10 outline-none transition-all";

  return (
    <div className="space-y-6">
      {error && (
        <div className="flex items-center gap-3 px-4 py-3 bg-red-50 dark:bg-red-500/10 border border-red-100 dark:border-red-500/20 rounded-xl">
          <AlertCircle className="w-4 h-4 text-red-500 shrink-0" />
          <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
        </div>
      )}

      {/* GitLab */}
      <SectionCard title="GitLab" description="Repository hosting and CI/CD" icon={GitBranch}>
        <div className="space-y-5">
          <FieldRow label="Host URL" description="Your GitLab instance">
            <input type="text" value={gitlabHost} onChange={(e) => setGitlabHost(e.target.value)} placeholder="https://gitlab.udevs.io" className={inputCls} />
          </FieldRow>
          <div className="border-t border-slate-100 dark:border-slate-800" />
          <FieldRow label="Group / Namespace" description="Group ID or path for new repos">
            <input type="text" value={gitlabGroup} onChange={(e) => setGitlabGroup(e.target.value)} placeholder="e.g. frontend or 42" className={inputCls} />
          </FieldRow>
          <div className="border-t border-slate-100 dark:border-slate-800" />
          <FieldRow label="Access Token" description="GitLab personal access token"
            badge={hasGitlabToken && !gitlabToken ? (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-emerald-50 dark:bg-emerald-500/10 border border-emerald-100 dark:border-emerald-500/20 rounded-full">
                <Check className="w-3 h-3 text-emerald-600 dark:text-emerald-400" />
                <span className="text-[10px] font-bold text-emerald-600 dark:text-emerald-400 uppercase">Saved</span>
              </span>
            ) : null}
          >
            <div className="relative">
              <input type={showGitlabToken ? 'text' : 'password'} value={gitlabToken} onChange={(e) => setGitlabToken(e.target.value)} placeholder={hasGitlabToken ? '••••••••  (saved — paste to replace)' : 'glpat-xxxxxxxxxxxx'} className={inputCls + ' pr-10'} />
              <button type="button" onClick={() => setShowGitlabToken(!showGitlabToken)} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 transition-colors">
                {showGitlabToken ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
          </FieldRow>
          <div className="border-t border-slate-100 dark:border-slate-800" />
          <FieldRow label="Invite Username" description="GitLab user invited to new repos automatically">
            <input type="text" value={gitlabInviteUsername} onChange={(e) => setGitlabInviteUsername(e.target.value)} placeholder="e.g. udevs" className={inputCls} />
          </FieldRow>
        </div>
      </SectionCard>

      {/* Ops Repo */}
      <SectionCard title="Ops Repository" description="K8s deployment configs (values.yaml, config.json)" icon={Container}>
        <div className="space-y-5">
          <FieldRow label="Ops Repo URL" description="Where Helm values are stored">
            <input type="text" value={opsRepoUrl} onChange={(e) => setOpsRepoUrl(e.target.value)} placeholder="https://gitlab.udevs.io/ops/deployments" className={inputCls} />
          </FieldRow>
          <div className="border-t border-slate-100 dark:border-slate-800" />
          <FieldRow label="Branch" description="Default branch for ops commits">
            <input type="text" value={opsRepoBranch} onChange={(e) => setOpsRepoBranch(e.target.value)} placeholder="main" className={inputCls} />
          </FieldRow>
        </div>
      </SectionCard>

      {/* Vercel */}
      <SectionCard title="Vercel" description="Deployment for Next.js projects" icon={Cloud}>
        <div className="space-y-5">
          <FieldRow label="API Token" description="Vercel personal access token"
            badge={hasVercelToken && !vercelToken ? (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-emerald-50 dark:bg-emerald-500/10 border border-emerald-100 dark:border-emerald-500/20 rounded-full">
                <Check className="w-3 h-3 text-emerald-600 dark:text-emerald-400" />
                <span className="text-[10px] font-bold text-emerald-600 dark:text-emerald-400 uppercase">Saved</span>
              </span>
            ) : null}
          >
            <div className="relative">
              <input type={showVercelToken ? 'text' : 'password'} value={vercelToken} onChange={(e) => setVercelToken(e.target.value)} placeholder={hasVercelToken ? '••••••••  (saved — paste to replace)' : 'Vercel API token'} className={inputCls + ' pr-10'} />
              <button type="button" onClick={() => setShowVercelToken(!showVercelToken)} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 transition-colors">
                {showVercelToken ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
          </FieldRow>
          <div className="border-t border-slate-100 dark:border-slate-800" />
          <FieldRow label="Team ID" description="Optional — for team deployments">
            <input type="text" value={vercelTeamId} onChange={(e) => setVercelTeamId(e.target.value)} placeholder="team_xxxxxxxx" className={inputCls} />
          </FieldRow>
        </div>
      </SectionCard>

      {/* Kubernetes */}
      <SectionCard title="Kubernetes" description="K8s namespace, domain, and TLS" icon={Database}>
        <div className="space-y-5">
          <FieldRow label="Namespace" description="K8s namespace for deployments">
            <input type="text" value={k8sNamespace} onChange={(e) => setK8sNamespace(e.target.value)} placeholder="frontend-prod" className={inputCls} />
          </FieldRow>
          <div className="border-t border-slate-100 dark:border-slate-800" />
          <FieldRow label="Domain" description="Wildcard domain for ingress">
            <input type="text" value={k8sDomain} onChange={(e) => setK8sDomain(e.target.value)} placeholder="*.udevs.io" className={inputCls} />
          </FieldRow>
          <div className="border-t border-slate-100 dark:border-slate-800" />
          <FieldRow label="TLS Secret" description="TLS secret name for HTTPS">
            <input type="text" value={k8sTlsSecret} onChange={(e) => setK8sTlsSecret(e.target.value)} placeholder="e.g. app-tls" className={inputCls} />
          </FieldRow>
          <div className="border-t border-slate-100 dark:border-slate-800" />
          <FieldRow label="Registry URL" description="Docker registry for images">
            <input type="text" value={registryUrl} onChange={(e) => setRegistryUrl(e.target.value)} placeholder="registry.gitlab.udevs.io" className={inputCls} />
          </FieldRow>
        </div>
      </SectionCard>

      {/* Self-Host / Export */}
      <SectionCard title="Export Code" description="Push your generated code to your own repo" icon={Upload}>
        <div className="space-y-4">
          <div className="flex items-start gap-3 px-4 py-3 bg-indigo-50 dark:bg-indigo-500/10 border border-indigo-100 dark:border-indigo-500/20 rounded-xl">
            <Info className="w-4 h-4 text-indigo-500 dark:text-indigo-400 shrink-0 mt-0.5" />
            <p className="text-xs text-indigo-700 dark:text-indigo-300 leading-relaxed">
              Export your project code to GitHub, GitLab, or Bitbucket. This creates a clone — your platform copy stays untouched. Re-export anytime.
            </p>
          </div>
          <ExportCodeModal isOpen={false} onClose={() => {}} />
          {/* The actual export is project-specific — this button shows the modal for any saved project */}
        </div>
      </SectionCard>

      {/* Save */}
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
        <button onClick={handleSave} disabled={saving}
          className={cn(
            "flex items-center gap-2 px-6 py-2.5 rounded-xl text-sm font-bold transition-all shadow-sm active:scale-[0.98]",
            saving
              ? "bg-rose-400 text-white cursor-not-allowed"
              : "bg-rose-600 text-white hover:bg-rose-700 shadow-rose-600/15"
          )}
        >
          {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
          {saving ? 'Saving…' : 'Save Changes'}
        </button>
      </div>
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
  const [headerSaveStatus, setHeaderSaveStatus] = useState(null);

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
      case 'deployment': return <DeploymentTab />;
      case 'auth': return <AuthProvidersTab />;
      default: return null;
    }
  };

  const activeTabData = tabs.find(t => t.id === activeTab);
  const activeColor = colorMap[activeTabData?.color || 'blue'];

  return (
    <div className="h-full flex flex-col bg-[#f0f4f9] dark:bg-[#0d1117]">

      {/* ── Unified Card: Sidebar + Content ── */}
      <div className="flex-1 flex overflow-hidden bg-white dark:bg-slate-900 border-t border-slate-200/80 dark:border-slate-800/60">

        {/* ── Sidebar Panel (never scrolls) ── */}
        <div className="hidden lg:flex shrink-0 w-[230px] flex-col border-r border-slate-100 dark:border-slate-800/80 bg-slate-50/70 dark:bg-slate-900/50">
          {/* Settings heading inside sidebar */}
          <div className="px-5 pt-6 pb-4">
            <h1 className="text-[16px] font-bold text-slate-900 dark:text-slate-100 tracking-tight">Settings</h1>
            <p className="text-[11px] text-slate-400 dark:text-slate-500 mt-0.5">Manage your workspace</p>
          </div>

          {/* Tab navigation */}
          <nav className="flex-1 px-3 space-y-0.5">
            {tabs.map((tab) => {
              const isActive = activeTab === tab.id;
              const colors = colorMap[tab.color]; 
              
              return (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  className={cn(
                    "w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-left transition-all duration-150 group",
                    isActive
                      ? "bg-white dark:bg-slate-800 shadow-sm border border-slate-200/80 dark:border-slate-700/80"
                      : "hover:bg-white/80 dark:hover:bg-slate-800/50 border border-transparent"
                  )}
                >
                  <div className={cn(
                    "w-7 h-7 rounded-md flex items-center justify-center shrink-0 transition-colors",
                    isActive
                      ? `${colors?.activeBg || 'bg-emerald-600'} text-white`
                      : "bg-slate-200/60 dark:bg-slate-700/60 text-slate-400 dark:text-slate-500 group-hover:text-slate-500 dark:group-hover:text-slate-400"
                  )}>
                    <tab.Icon className="w-3.5 h-3.5" />
                  </div>
                  
                  <span className={cn(
                    "text-[13px] font-medium transition-colors",
                    isActive ? "text-slate-900 dark:text-slate-100" : "text-slate-500 dark:text-slate-400 group-hover:text-slate-700 dark:group-hover:text-slate-200"
                  )}>
                    {tab.label}
                  </span>
                </button>
              );
            })}
          </nav>

          {/* Footer */}
          <div className="px-5 py-4 border-t border-slate-100 dark:border-slate-800/60">
            <p className="text-[10px] text-slate-400 dark:text-slate-600 font-medium">Lucid AI v1.0</p>
          </div>
        </div>

        {/* ── Content Panel (scrollable) ── */}
        <div className="flex-1 flex flex-col overflow-hidden">
          {/* Content header bar */}
          <div className="shrink-0 flex items-center justify-between px-8 py-4 border-b border-slate-100 dark:border-slate-800/60 bg-white dark:bg-slate-900">
            <div>
              <h2 className="text-[15px] font-bold text-slate-900 dark:text-slate-100">{activeTabData?.label}</h2>
              <p className="text-[11px] text-slate-400 dark:text-slate-500 mt-0.5">{activeTabData?.description}</p>
            </div>
            <button
              onClick={handleHeaderSave}
              disabled={headerSaving || activeTab !== 'llm'}
              className={cn(
                "flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-[12px] font-semibold transition-all active:scale-[0.98]",
                headerSaving
                  ? "bg-emerald-400 text-white cursor-not-allowed"
                  : headerSaveStatus === 'success'
                    ? "bg-emerald-600 text-white"
                    : activeTab !== 'llm'
                      ? "bg-slate-100 dark:bg-slate-800 text-slate-400 dark:text-slate-500 cursor-not-allowed"
                      : "bg-emerald-600 text-white hover:bg-emerald-700 shadow-sm shadow-emerald-600/20"
              )}
            >
              {headerSaving
                ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                : headerSaveStatus === 'success'
                  ? <Check className="w-3.5 h-3.5" />
                  : <Check className="w-3.5 h-3.5" />}
              {headerSaving ? 'Saving…' : headerSaveStatus === 'success' ? 'Saved!' : 'Save'}
            </button>
          </div>

          {/* Scrollable content */}
          <div className="flex-1 overflow-y-auto bg-[#f8f9fc] dark:bg-[#0d1117]">
            <div className="max-w-[680px] mx-auto px-8 py-6 pb-16">
              {renderTabContent()}
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}
