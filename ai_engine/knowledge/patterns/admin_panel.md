# Admin Panel Architecture Patterns (Tailwind + shadcn/ui)

## Layout Variants

### Variant A — Sidebar Admin (Linear/Notion style)
Best for: CRM, CMS, Project Management, Analytics dashboards
```
┌──────────────────────────────────────────────┐
│ ┌──────┐ ┌─────────────────────────────────┐ │
│ │      │ │ Topbar: Search | Notif | Avatar │ │
│ │ Side │ ├─────────────────────────────────┤ │
│ │ bar  │ │  Page Title + Subtitle          │ │
│ │      │ │  ┌─────┐ ┌─────┐ ┌─────┐      │ │
│ │ Nav  │ │  │Stat │ │Stat │ │Stat │      │ │
│ │ 240px│ │  └─────┘ └─────┘ └─────┘      │ │
│ │      │ │  ┌───────────────────────────┐  │ │
│ │      │ │  │  Data Table / Content     │  │ │
│ │      │ │  └───────────────────────────┘  │ │
│ └──────┘ └─────────────────────────────────┘ │
└──────────────────────────────────────────────┘
```

**Sidebar structure (data config):**
```jsx
const navigation = [
  {
    group: "Main",
    items: [
      { label: "Dashboard", icon: "LayoutDashboard", route: "/", badge: null },
      { label: "Analytics", icon: "BarChart3", route: "/analytics", badge: null },
    ]
  },
  {
    group: "Management",
    items: [
      { label: "Users", icon: "Users", route: "/users", badge: "24" },
      { label: "Products", icon: "Package", route: "/products", badge: null },
      { label: "Orders", icon: "ShoppingCart", route: "/orders", badge: "3" },
    ]
  },
];
```

**Sidebar with Tailwind:**
```jsx
<aside className="w-60 h-screen fixed left-0 top-0 bg-card border-r border-border flex flex-col z-50">
  {/* Logo */}
  <div className="h-14 flex items-center px-4 border-b border-border">
    <span className="font-bold text-foreground">AppName</span>
  </div>

  {/* Nav groups */}
  <nav className="flex-1 overflow-y-auto py-4">
    {navigation.map(group => (
      <div key={group.group}>
        <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground px-4 py-2 block">
          {group.group}
        </span>
        {group.items.map(item => (
          <a
            key={item.route}
            href={item.route}
            className={`flex items-center gap-3 mx-2 px-3 py-2 rounded-md text-sm transition-colors
              ${active ? 'bg-primary/10 text-primary font-medium' : 'text-muted-foreground hover:bg-muted hover:text-foreground'}`}
          >
            <Icon size={18} />
            <span>{item.label}</span>
            {item.badge && (
              <span className="ml-auto text-xs bg-primary/10 text-primary px-2 py-0.5 rounded-full">
                {item.badge}
              </span>
            )}
          </a>
        ))}
      </div>
    ))}
  </nav>
</aside>
```

---

## Dashboard Components (Tailwind)

### Stat Card
```jsx
<div className="bg-card border border-border rounded-xl p-5 flex items-center gap-4">
  <div className="w-10 h-10 rounded-lg bg-primary/10 flex items-center justify-center text-primary">
    <Users size={20} />
  </div>
  <div className="flex-1">
    <p className="text-2xl font-bold text-foreground">2,847</p>
    <p className="text-sm text-muted-foreground">Total Users</p>
  </div>
  <div className="flex items-center gap-1 text-xs text-emerald-500">
    <ArrowUpRight size={14} />
    <span>+12.5%</span>
  </div>
</div>
```

### Data Table
```jsx
<div className="bg-card border border-border rounded-xl overflow-hidden">
  {/* Toolbar */}
  <div className="flex items-center justify-between p-4 border-b border-border">
    <div className="relative">
      <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
      <input
        className="pl-9 pr-4 py-2 bg-muted rounded-md text-sm text-foreground placeholder:text-muted-foreground border-0 focus:ring-2 focus:ring-primary/20"
        placeholder="Search..."
      />
    </div>
    <div className="flex gap-2">
      <button className="inline-flex items-center gap-2 px-3 py-2 border border-border rounded-md text-sm text-foreground hover:bg-muted transition-colors">
        <Filter size={16} /> Filters
      </button>
      <button className="inline-flex items-center gap-2 px-3 py-2 bg-primary text-primary-foreground rounded-md text-sm hover:bg-primary/90 transition-colors">
        <Plus size={16} /> Add New
      </button>
    </div>
  </div>

  {/* Table */}
  <table className="w-full">
    <thead>
      <tr className="border-b border-border">
        <th className="text-left text-xs font-medium text-muted-foreground uppercase tracking-wider px-4 py-3">Name</th>
        <th className="text-left text-xs font-medium text-muted-foreground uppercase tracking-wider px-4 py-3">Status</th>
        <th className="text-left text-xs font-medium text-muted-foreground uppercase tracking-wider px-4 py-3">Date</th>
        <th className="text-right text-xs font-medium text-muted-foreground uppercase tracking-wider px-4 py-3">Actions</th>
      </tr>
    </thead>
    <tbody>
      {items.map(item => (
        <tr key={item.id} className="border-b border-border last:border-0 hover:bg-muted/50 transition-colors">
          <td className="px-4 py-3 text-sm text-foreground font-medium">{item.name}</td>
          <td className="px-4 py-3"><StatusBadge status={item.status} /></td>
          <td className="px-4 py-3 text-sm text-muted-foreground">{item.date}</td>
          <td className="px-4 py-3 text-right">
            <button className="p-1.5 hover:bg-muted rounded text-muted-foreground hover:text-foreground transition-colors"><Eye size={14} /></button>
            <button className="p-1.5 hover:bg-muted rounded text-muted-foreground hover:text-foreground transition-colors"><Edit size={14} /></button>
            <button className="p-1.5 hover:bg-muted rounded text-muted-foreground hover:text-red-500 transition-colors"><Trash2 size={14} /></button>
          </td>
        </tr>
      ))}
    </tbody>
  </table>
</div>
```

### Status Badges (Tailwind)
```jsx
const statusStyles = {
  active:     'bg-emerald-500/10 text-emerald-600',
  pending:    'bg-yellow-500/10 text-yellow-600',
  inactive:   'bg-red-500/10 text-red-600',
  draft:      'bg-blue-500/10 text-blue-600',
  processing: 'bg-indigo-500/10 text-indigo-600',
};

function StatusBadge({ status }) {
  return (
    <span className={`inline-flex px-2.5 py-0.5 rounded-full text-xs font-medium ${statusStyles[status] || statusStyles.draft}`}>
      {status.charAt(0).toUpperCase() + status.slice(1)}
    </span>
  );
}
```

### Mock Data (served from db.json via json-server)
```json
// db.json — at project root, served by json-server on port 3001
{
  "users": [
    { "id": "1", "name": "Sarah Mitchell", "email": "sarah.m@company.co", "role": "admin", "status": "active", "lastLogin": "2024-03-15T10:23:00" },
    { "id": "2", "name": "Marcus Chen", "email": "marcus.c@company.co", "role": "editor", "status": "active", "lastLogin": "2024-03-14T08:45:00" },
    { "id": "3", "name": "Alex Rivera", "email": "alex.r@company.co", "role": "viewer", "status": "pending", "lastLogin": null },
    { "id": "4", "name": "Priya Sharma", "email": "priya.s@company.co", "role": "admin", "status": "active", "lastLogin": "2024-03-15T09:12:00" },
    { "id": "5", "name": "Jordan Williams", "email": "jordan.w@company.co", "role": "editor", "status": "inactive", "lastLogin": "2024-02-28T16:30:00" }
  ]
}
```

### API-Ready Service Pattern
```jsx
// src/features/users/services/user.service.js
const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:3001';

export const userService = {
  getAll: async (params = {}) => {
    try {
      const query = new URLSearchParams(params).toString();
      const res = await fetch(`${API_URL}/users${query ? `?${query}` : ''}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      return { data: Array.isArray(data) ? data : [], total: Array.isArray(data) ? data.length : 0 };
    } catch (err) {
      console.warn('users.getAll failed:', err.message);
      return { data: [], total: 0 };
    }
  },
  getById: async (id) => {
    try {
      const res = await fetch(`${API_URL}/users/${id}`);
      return res.ok ? await res.json() : null;
    } catch { return null; }
  },
  create: async (data) => {
    try {
      const res = await fetch(`${API_URL}/users`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
      return res.ok ? await res.json() : { ...data, id: Date.now().toString() };
    } catch { return { ...data, id: Date.now().toString() }; }
  },
  update: async (id, data) => {
    try {
      const res = await fetch(`${API_URL}/users/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
      return res.ok ? await res.json() : { ...data, id };
    } catch { return { ...data, id }; }
  },
  delete: async (id) => {
    try {
      await fetch(`${API_URL}/users/${id}`, { method: 'DELETE' });
      return { success: true };
    } catch { return { success: false }; }
  },
};
```

### Forms (Tailwind)
```jsx
<form onSubmit={handleSubmit} className="max-w-2xl mx-auto">
  <div className="mb-6">
    <h2 className="text-xl font-bold text-foreground">Create User</h2>
    <p className="text-sm text-muted-foreground mt-1">Fill in the details below</p>
  </div>

  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
    <div>
      <label className="block text-sm font-medium text-foreground mb-1.5">
        Full Name <span className="text-red-500">*</span>
      </label>
      <input
        type="text"
        className="w-full px-3 py-2 bg-background border border-border rounded-md text-sm text-foreground placeholder:text-muted-foreground focus:ring-2 focus:ring-primary/20 focus:border-primary"
        placeholder="Enter name"
      />
    </div>
    <div>
      <label className="block text-sm font-medium text-foreground mb-1.5">Email</label>
      <input
        type="email"
        className="w-full px-3 py-2 bg-background border border-border rounded-md text-sm text-foreground placeholder:text-muted-foreground focus:ring-2 focus:ring-primary/20 focus:border-primary"
        placeholder="user@company.com"
      />
    </div>
    <div>
      <label className="block text-sm font-medium text-foreground mb-1.5">Role</label>
      <select className="w-full px-3 py-2 bg-background border border-border rounded-md text-sm text-foreground focus:ring-2 focus:ring-primary/20">
        <option value="viewer">Viewer</option>
        <option value="editor">Editor</option>
        <option value="admin">Admin</option>
      </select>
    </div>
  </div>

  <div className="flex justify-end gap-3 mt-6 pt-4 border-t border-border">
    <button type="button" className="px-4 py-2 border border-border rounded-md text-sm text-foreground hover:bg-muted transition-colors">
      Cancel
    </button>
    <button type="submit" className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm hover:bg-primary/90 transition-colors">
      Save
    </button>
  </div>
</form>
```

---

## Design System Integration

ALL components must import and use the shared design system:

```jsx
import { ds } from '@/lib/design-system'

// Use ds.card instead of ad-hoc card classes
<Card className={ds.card}>
<Badge className={ds.badge[status]}>
<motion.div {...ds.pageAnimation}>
```

## CRITICAL RULES
1. **Services use fetch()** — never hardcode mock data inside service files
2. **Mock data in db.json** — served by json-server, NOT inline constants
3. **Design system imports** — all components import `ds` from `@/lib/design-system`
4. **Loading/Empty/Error states** — every data component handles all three
5. **No hardcoded colors** — use Tailwind utility classes only

