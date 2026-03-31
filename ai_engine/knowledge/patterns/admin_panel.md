# Admin Panel Architecture Patterns

## Layout Variants

### Variant A — Sidebar Admin (Linear/Notion style)
Best for: CRM, CMS, Project Management, Analytics dashboards
```
┌──────────────────────────────────────────────┐
│ ┌──────┐ ┌─────────────────────────────────┐ │
│ │      │ │ Topbar: Search | Notif | Avatar │ │
│ │ Side │ ├─────────────────────────────────┤ │
│ │ bar  │ │                                 │ │
│ │      │ │  Page Title + Subtitle          │ │
│ │ Nav  │ │  ┌─────┐ ┌─────┐ ┌─────┐      │ │
│ │ 240px│ │  │Stat │ │Stat │ │Stat │      │ │
│ │      │ │  └─────┘ └─────┘ └─────┘      │ │
│ │      │ │                                 │ │
│ │      │ │  ┌───────────────────────────┐  │ │
│ │      │ │  │  Data Table / Content     │  │ │
│ │      │ │  └───────────────────────────┘  │ │
│ └──────┘ └─────────────────────────────────┘ │
└──────────────────────────────────────────────┘
```

**Sidebar structure:**
```jsx
// Navigation config pattern
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
  {
    group: "Settings",
    items: [
      { label: "General", icon: "Settings", route: "/settings", badge: null },
      { label: "Team", icon: "UserCog", route: "/team", badge: null },
    ]
  }
];
```

**Sidebar CSS pattern:**
```css
.sidebar {
  width: 240px;
  height: 100vh;
  position: fixed;
  left: 0;
  top: 0;
  background: var(--color-bg);
  border-right: 1px solid var(--color-border);
  display: flex;
  flex-direction: column;
  transition: width 0.2s ease;
  z-index: 50;
}
.sidebar.collapsed { width: 64px; }
.sidebar .nav-group-label {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-text-muted);
  padding: 8px 16px;
  margin-top: 16px;
}
.sidebar .nav-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 16px;
  font-size: 13px;
  color: var(--color-text-secondary);
  border-radius: 6px;
  margin: 2px 8px;
  transition: all 0.15s ease;
  cursor: pointer;
}
.sidebar .nav-item:hover {
  background: var(--color-bg-secondary);
  color: var(--color-text);
}
.sidebar .nav-item.active {
  background: var(--color-primary-50, rgba(99,102,241,0.1));
  color: var(--color-primary);
  font-weight: 500;
}
```

### Variant B — Top-Nav Admin (Vercel/GitHub style)
Best for: Developer tools, Settings panels, Simple dashboards
```
┌──────────────────────────────────────────────┐
│ Logo     Dashboard  Users  Settings    Avatar │
├──────────────────────────────────────────────┤
│                                              │
│  ┌─── Tab Bar ─────────────────────────────┐ │
│  │ Overview | Details | Activity | Settings │ │
│  └─────────────────────────────────────────┘ │
│                                              │
│  Content Area                                │
│                                              │
└──────────────────────────────────────────────┘
```

---

## Required Pages & Components

### Dashboard Page
**Components:** 4 stat cards → chart → recent table → quick actions

```jsx
// Stat card pattern
<div className="stat-card">
  <div className="stat-icon" style={{ background: 'var(--color-primary-50)' }}>
    <Users size={20} color="var(--color-primary)" />
  </div>
  <div className="stat-content">
    <span className="stat-value">2,847</span>
    <span className="stat-label">Total Users</span>
  </div>
  <div className="stat-trend positive">
    <ArrowUpRight size={14} />
    <span>+12.5%</span>
  </div>
</div>
```

**CSS pattern:**
```css
.stat-card {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 20px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
}
.stat-value { font-size: 24px; font-weight: 700; color: var(--color-text); }
.stat-label { font-size: 13px; color: var(--color-text-muted); }
.stat-trend { font-size: 12px; display: flex; align-items: center; gap: 2px; }
.stat-trend.positive { color: #10b981; }
.stat-trend.negative { color: #ef4444; }
```

### Data Table Page
**Components:** Search bar → filter row → sortable table → pagination → empty state

```jsx
// Table structure pattern
<div className="table-container">
  <div className="table-toolbar">
    <div className="search-wrapper">
      <Search size={16} />
      <input placeholder="Search..." value={search} onChange={...} />
    </div>
    <div className="table-actions">
      <button className="btn-filter"><Filter size={16} /> Filters</button>
      <button className="btn-primary"><Plus size={16} /> Add New</button>
    </div>
  </div>
  <table className="data-table">
    <thead>
      <tr>
        <th onClick={() => sort('name')}>
          Name <ChevronDown size={14} />
        </th>
        <th>Status</th>
        <th>Date</th>
        <th>Actions</th>
      </tr>
    </thead>
    <tbody>
      {items.map(item => (
        <tr key={item.id}>
          <td>{item.name}</td>
          <td><StatusBadge status={item.status} /></td>
          <td>{formatDate(item.date)}</td>
          <td>
            <button><Eye size={14} /></button>
            <button><Edit size={14} /></button>
            <button><Trash2 size={14} /></button>
          </td>
        </tr>
      ))}
    </tbody>
  </table>
  {items.length === 0 && (
    <div className="empty-state">
      <Inbox size={48} />
      <h3>No items found</h3>
      <p>Get started by creating your first item</p>
      <button className="btn-primary">Create Item</button>
    </div>
  )}
  <div className="pagination">
    <span>Showing 1-10 of 156</span>
    <div className="pagination-buttons">
      <button disabled><ChevronLeft size={16} /></button>
      <button className="active">1</button>
      <button>2</button>
      <button>3</button>
      <button><ChevronRight size={16} /></button>
    </div>
  </div>
</div>
```

### Status Badges
```jsx
const statusColors = {
  active:     { bg: '#dcfce7', color: '#16a34a', label: 'Active' },
  pending:    { bg: '#fef9c3', color: '#ca8a04', label: 'Pending' },
  inactive:   { bg: '#fee2e2', color: '#dc2626', label: 'Inactive' },
  draft:      { bg: '#dbeafe', color: '#2563eb', label: 'Draft' },
  processing: { bg: '#e0e7ff', color: '#4f46e5', label: 'Processing' },
};

function StatusBadge({ status }) {
  const s = statusColors[status] || statusColors.draft;
  return (
    <span style={{
      background: s.bg, color: s.color,
      padding: '2px 8px', borderRadius: '9999px',
      fontSize: '12px', fontWeight: 500,
    }}>
      {s.label}
    </span>
  );
}
```

### Mock Data Pattern
```jsx
const mockUsers = [
  { id: 1, name: 'Sarah Mitchell', email: 'sarah.m@company.co', role: 'Admin', status: 'active', lastLogin: '2024-03-15T10:23:00' },
  { id: 2, name: 'Marcus Chen', email: 'marcus.c@company.co', role: 'Editor', status: 'active', lastLogin: '2024-03-14T08:45:00' },
  { id: 3, name: 'Alex Rivera', email: 'alex.r@company.co', role: 'Viewer', status: 'pending', lastLogin: null },
  { id: 4, name: 'Priya Sharma', email: 'priya.s@company.co', role: 'Admin', status: 'active', lastLogin: '2024-03-15T09:12:00' },
  { id: 5, name: 'Jordan Williams', email: 'jordan.w@company.co', role: 'Editor', status: 'inactive', lastLogin: '2024-02-28T16:30:00' },
];

const mockOrders = [
  { id: 'ORD-2401', customer: 'Acme Corp', amount: 2450.00, status: 'processing', date: '2024-03-15' },
  { id: 'ORD-2402', customer: 'TechStart Inc', amount: 890.00, status: 'active', date: '2024-03-14' },
  { id: 'ORD-2403', customer: 'GlobalTrade Ltd', amount: 12500.00, status: 'pending', date: '2024-03-14' },
  { id: 'ORD-2404', customer: 'DesignHub Co', amount: 3200.00, status: 'active', date: '2024-03-13' },
  { id: 'ORD-2405', customer: 'CloudNine SaaS', amount: 780.00, status: 'draft', date: '2024-03-12' },
];
```

### Forms Pattern
```jsx
// Create/Edit form pattern
<form className="form-container" onSubmit={handleSubmit}>
  <div className="form-header">
    <h2>Create User</h2>
    <p className="form-subtitle">Fill in the details below</p>
  </div>

  <div className="form-grid">
    <div className="form-field">
      <label>Full Name <span className="required">*</span></label>
      <input type="text" value={name} onChange={e => setName(e.target.value)} placeholder="Enter name" />
      {errors.name && <span className="field-error">{errors.name}</span>}
    </div>
    <div className="form-field">
      <label>Email</label>
      <input type="email" value={email} onChange={e => setEmail(e.target.value)} placeholder="user@company.com" />
    </div>
    <div className="form-field">
      <label>Role</label>
      <select value={role} onChange={e => setRole(e.target.value)}>
        <option value="viewer">Viewer</option>
        <option value="editor">Editor</option>
        <option value="admin">Admin</option>
      </select>
    </div>
  </div>

  <div className="form-actions">
    <button type="button" className="btn-secondary" onClick={onCancel}>Cancel</button>
    <button type="submit" className="btn-primary" disabled={loading}>
      {loading ? 'Saving...' : 'Save'}
    </button>
  </div>
</form>
```
