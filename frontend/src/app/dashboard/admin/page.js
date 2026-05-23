'use client';

import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import {
  Users, FolderGit2, DollarSign, TrendingUp, Loader2,
  Crown, Zap, Building2, Activity, ArrowLeft, RefreshCw,
} from 'lucide-react';
import { cn } from '@/lib/utils';

function StatCard({ icon: Icon, label, value, sub, color }) {
  const colors = {
    blue:   'bg-blue-50 dark:bg-blue-500/10 text-blue-600 dark:text-blue-400',
    orange: 'bg-orange-50 dark:bg-orange-500/10 text-[#dc5426] dark:text-orange-400',
    green:  'bg-emerald-50 dark:bg-emerald-500/10 text-emerald-600 dark:text-emerald-400',
    purple: 'bg-violet-50 dark:bg-violet-500/10 text-violet-600 dark:text-violet-400',
  };
  return (
    <div className="bg-white dark:bg-[#161b22] rounded-2xl border border-slate-200/80 dark:border-[#2d333b] p-5">
      <div className={cn('w-10 h-10 rounded-xl flex items-center justify-center mb-3', colors[color])}>
        <Icon className="w-5 h-5" />
      </div>
      <p className="text-2xl font-bold text-slate-900 dark:text-white">{value}</p>
      <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">{label}</p>
      {sub && <p className="text-[11px] text-slate-400 dark:text-slate-500 mt-1">{sub}</p>}
    </div>
  );
}

function PlanBadge({ plan }) {
  const styles = {
    free:       'bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400',
    pro:        'bg-orange-50 dark:bg-orange-500/10 text-[#dc5426] dark:text-orange-400',
    enterprise: 'bg-amber-50 dark:bg-amber-500/10 text-amber-600 dark:text-amber-400',
  };
  return (
    <span className={cn('px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider', styles[plan] ?? styles.free)}>
      {plan}
    </span>
  );
}

function formatTime(dateStr) {
  if (!dateStr) return '—';
  const d = new Date(dateStr);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

export default function AdminDashboard() {
  const router = useRouter();
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [activeTab, setActiveTab] = useState('overview');

  const load = () => {
    setLoading(true);
    setError(null);
    fetch('/api/admin/stats')
      .then((r) => {
        if (r.status === 403) throw new Error('Access denied — admin only.');
        if (!r.ok) throw new Error('Failed to load stats.');
        return r.json();
      })
      .then((data) => { setStats(data); setLoading(false); })
      .catch((e) => { setError(e.message); setLoading(false); });
  };

  useEffect(() => { load(); }, []);

  const tabs = ['overview', 'users', 'activity'];

  return (
    <div className="min-h-screen bg-[#fefcfa] dark:bg-[#0d1117]">
      {/* Top bar */}
      <div className="sticky top-0 z-10 bg-white/80 dark:bg-[#0d1117]/80 backdrop-blur border-b border-slate-200 dark:border-[#2d333b] px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button
            onClick={() => router.push('/dashboard')}
            className="p-1.5 rounded-lg text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 hover:bg-slate-100 dark:hover:bg-white/[0.06]"
          >
            <ArrowLeft className="w-4 h-4" />
          </button>
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 rounded-lg bg-gradient-to-br from-[#dc5426] to-orange-500 flex items-center justify-center">
              <Activity className="w-3.5 h-3.5 text-white" />
            </div>
            <span className="text-sm font-bold text-slate-900 dark:text-white">Admin Dashboard</span>
          </div>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-white/[0.06] disabled:opacity-50"
        >
          <RefreshCw className={cn('w-3.5 h-3.5', loading && 'animate-spin')} />
          Refresh
        </button>
      </div>

      <div className="max-w-6xl mx-auto px-6 py-8">
        {loading && !stats ? (
          <div className="flex items-center justify-center py-32">
            <Loader2 className="w-6 h-6 animate-spin text-slate-400" />
          </div>
        ) : error ? (
          <div className="flex flex-col items-center justify-center py-32 text-center">
            <p className="text-lg font-semibold text-slate-900 dark:text-white mb-2">{error}</p>
            <button onClick={load} className="text-sm text-[#dc5426] hover:underline">Try again</button>
          </div>
        ) : (
          <>
            {/* Tabs */}
            <div className="flex gap-1 mb-8 bg-slate-100 dark:bg-[#161b22] border border-slate-200 dark:border-[#2d333b] p-1 rounded-xl w-fit">
              {tabs.map((t) => (
                <button
                  key={t}
                  onClick={() => setActiveTab(t)}
                  className={cn(
                    'px-4 py-1.5 rounded-lg text-sm font-medium capitalize transition-all',
                    activeTab === t
                      ? 'bg-white dark:bg-[#0d1117] text-slate-900 dark:text-white shadow-sm'
                      : 'text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200'
                  )}
                >
                  {t}
                </button>
              ))}
            </div>

            {/* Overview tab */}
            {activeTab === 'overview' && (
              <div className="space-y-8">
                {/* Stat cards */}
                <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                  <StatCard icon={Users}        label="Total users"       value={stats.totalUsers}           color="blue" />
                  <StatCard icon={FolderGit2}   label="Total projects"    value={stats.totalProjects}         color="orange" />
                  <StatCard icon={Crown}        label="Paid subscribers"  value={stats.activePaidCount}       color="purple" sub={`Pro: ${stats.planCounts.pro} · Ent: ${stats.planCounts.enterprise}`} />
                  <StatCard icon={DollarSign}   label="Monthly revenue"   value={`$${stats.monthlyRevenue}`} color="green" sub="Estimated MRR" />
                </div>

                {/* Plan breakdown */}
                <div className="bg-white dark:bg-[#161b22] rounded-2xl border border-slate-200/80 dark:border-[#2d333b] p-6">
                  <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-200 mb-4">Plan distribution</h2>
                  <div className="space-y-3">
                    {[
                      { key: 'free',       label: 'Free',       icon: Zap,       color: 'bg-slate-300 dark:bg-slate-600' },
                      { key: 'pro',        label: 'Pro',        icon: TrendingUp, color: 'bg-[#dc5426]' },
                      { key: 'enterprise', label: 'Enterprise', icon: Building2,  color: 'bg-amber-500' },
                    ].map(({ key, label, icon: Icon, color }) => {
                      const count = stats.planCounts[key] || 0;
                      const total = stats.totalUsers || 1;
                      const pct = Math.round((count / total) * 100);
                      return (
                        <div key={key}>
                          <div className="flex items-center justify-between mb-1">
                            <div className="flex items-center gap-2">
                              <Icon className="w-3.5 h-3.5 text-slate-400" />
                              <span className="text-sm text-slate-700 dark:text-slate-300">{label}</span>
                            </div>
                            <span className="text-sm font-semibold text-slate-900 dark:text-white">{count} <span className="text-slate-400 font-normal text-xs">({pct}%)</span></span>
                          </div>
                          <div className="h-1.5 bg-slate-100 dark:bg-slate-800 rounded-full overflow-hidden">
                            <div className={cn('h-full rounded-full transition-all', color)} style={{ width: `${pct}%` }} />
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>

                {/* Signups chart */}
                <div className="bg-white dark:bg-[#161b22] rounded-2xl border border-slate-200/80 dark:border-[#2d333b] p-6">
                  <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-200 mb-4">Signups — last 14 days</h2>
                  {stats.signupsByDay && (
                    <div className="flex items-end gap-1.5 h-24">
                      {Object.entries(stats.signupsByDay).map(([day, count]) => {
                        const max = Math.max(...Object.values(stats.signupsByDay), 1);
                        const pct = (count / max) * 100;
                        return (
                          <div key={day} className="flex-1 flex flex-col items-center gap-1 group relative">
                            <div
                              className="w-full rounded-t-sm bg-[#dc5426]/60 dark:bg-orange-500/50 hover:bg-[#dc5426] dark:hover:bg-orange-500 transition-colors cursor-default"
                              style={{ height: `${Math.max(pct, 4)}%` }}
                            />
                            <div className="absolute -top-6 left-1/2 -translate-x-1/2 bg-slate-800 text-white text-[9px] px-1.5 py-0.5 rounded opacity-0 group-hover:opacity-100 whitespace-nowrap pointer-events-none">
                              {count} · {day.slice(5)}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Users tab */}
            {activeTab === 'users' && (
              <div className="bg-white dark:bg-[#161b22] rounded-2xl border border-slate-200/80 dark:border-[#2d333b] overflow-hidden">
                <div className="px-6 py-4 border-b border-slate-200 dark:border-[#2d333b]">
                  <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-200">Recent users</h2>
                </div>
                <div className="divide-y divide-slate-100 dark:divide-[#2d333b]">
                  {stats.recentUsers.map((u) => (
                    <div key={u.id} className="flex items-center gap-3 px-6 py-3.5">
                      {u.avatar_url ? (
                        <img src={u.avatar_url} alt="" className="w-8 h-8 rounded-full object-cover shrink-0" />
                      ) : (
                        <div className="w-8 h-8 rounded-full bg-slate-200 dark:bg-slate-700 flex items-center justify-center shrink-0">
                          <Users className="w-4 h-4 text-slate-400" />
                        </div>
                      )}
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-slate-900 dark:text-white truncate">{u.name || u.email || 'Unknown'}</p>
                        <p className="text-xs text-slate-400 truncate">{u.email}</p>
                      </div>
                      <div className="flex items-center gap-3 shrink-0">
                        <PlanBadge plan="free" />
                        <span className="text-xs text-slate-400">{formatTime(u.created_at)}</span>
                      </div>
                    </div>
                  ))}
                  {stats.recentUsers.length === 0 && (
                    <p className="px-6 py-8 text-sm text-slate-400 text-center">No users yet.</p>
                  )}
                </div>
              </div>
            )}

            {/* Activity tab */}
            {activeTab === 'activity' && (
              <div className="bg-white dark:bg-[#161b22] rounded-2xl border border-slate-200/80 dark:border-[#2d333b] overflow-hidden">
                <div className="px-6 py-4 border-b border-slate-200 dark:border-[#2d333b]">
                  <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-200">Recent sessions</h2>
                </div>
                <div className="divide-y divide-slate-100 dark:divide-[#2d333b]">
                  {stats.recentSessions.map((s, i) => (
                    <div key={i} className="flex items-center gap-3 px-6 py-3.5">
                      <div className="w-8 h-8 rounded-xl bg-orange-50 dark:bg-orange-500/10 flex items-center justify-center shrink-0">
                        <FolderGit2 className="w-4 h-4 text-[#dc5426]" />
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-slate-900 dark:text-white truncate">{s.title || 'Untitled'}</p>
                        <p className="text-xs text-slate-400 font-mono truncate">{s.user_id?.slice(0, 8)}…</p>
                      </div>
                      <span className="text-xs text-slate-400 shrink-0">{formatTime(s.created_at)}</span>
                    </div>
                  ))}
                  {stats.recentSessions.length === 0 && (
                    <p className="px-6 py-8 text-sm text-slate-400 text-center">No sessions yet.</p>
                  )}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
