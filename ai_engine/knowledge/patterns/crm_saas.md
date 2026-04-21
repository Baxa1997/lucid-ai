# CRM & SaaS Dashboard Patterns (Tailwind + shadcn/ui)

Sidebar-nav workspaces for CRM, project management, HR tools, team dashboards.
Key UI patterns: pipeline kanban, activity feeds, stat cards, timeline, inline editing.

---

## Page Map (CRM)
```
/ (dashboard)    — pipeline summary, today's tasks, recent activity
/leads           — lead table with score, source, owner, status
/deals           — kanban pipeline OR table view toggle
/contacts        — searchable contact list with avatar + company
/contacts/:id    — contact profile: info, activity, deals, notes
/companies       — company list with logo, ARR, contacts count
/activities      — timeline of calls, emails, meetings, notes
/reports         — charts: conversion funnel, win rate, revenue forecast
/settings        — pipeline stages, custom fields, user management
```

## Page Map (SaaS/Project Management)
```
/ (dashboard)    — active projects, tasks due today, team activity
/projects        — project card grid with progress bar + status
/projects/:id    — kanban board: To Do / In Progress / Review / Done
/tasks           — flat task list across all projects, filterable
/team            — team member grid with role, workload indicator
/settings        — workspace, integrations, billing
```

---

## Kanban Board
```jsx
'use client'
import { useState } from 'react'
import { Plus, MoreHorizontal } from 'lucide-react'

const STAGES = ['To Do', 'In Progress', 'Review', 'Done']

export default function KanbanBoard({ tasks }) {
  const [items, setItems] = useState(tasks)

  const byStage = (stage) => items.filter(t => t.stage === stage)

  return (
    <div className="flex gap-4 overflow-x-auto pb-4 h-full">
      {STAGES.map(stage => (
        <div key={stage} className="flex-shrink-0 w-72">
          {/* Column header */}
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold text-foreground">{stage}</span>
              <span className="text-xs bg-muted text-muted-foreground px-2 py-0.5 rounded-full">
                {byStage(stage).length}
              </span>
            </div>
            <button className="text-muted-foreground hover:text-foreground transition-colors">
              <Plus size={16} />
            </button>
          </div>

          {/* Cards */}
          <div className="space-y-2">
            {byStage(stage).map(task => (
              <div
                key={task.id}
                className="rounded-lg border border-border bg-card p-3 cursor-grab hover:shadow-sm transition-shadow group"
              >
                {task.tag && (
                  <span className="text-xs bg-primary/10 text-primary px-2 py-0.5 rounded-full font-medium">
                    {task.tag}
                  </span>
                )}
                <p className="text-sm font-medium text-foreground mt-2 leading-snug">{task.title}</p>
                {task.description && (
                  <p className="text-xs text-muted-foreground mt-1 line-clamp-2">{task.description}</p>
                )}
                <div className="flex items-center justify-between mt-3">
                  <div className="flex -space-x-2">
                    {task.assignees?.slice(0, 3).map(a => (
                      <img key={a} src={`https://i.pravatar.cc/28?u=${a}`}
                           className="w-7 h-7 rounded-full border-2 border-card" alt="" />
                    ))}
                  </div>
                  {task.dueDate && (
                    <span className={`text-xs ${new Date(task.dueDate) < new Date() ? 'text-destructive' : 'text-muted-foreground'}`}>
                      {task.dueDate}
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>

          {/* Add card */}
          <button className="mt-2 w-full flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground py-2 px-3 rounded-lg hover:bg-muted transition-colors">
            <Plus size={14} /> Add card
          </button>
        </div>
      ))}
    </div>
  )
}
```

---

## Activity Feed (timeline)
```jsx
const ACTIVITY_ICONS = {
  call:    { Icon: Phone,    bg: 'bg-blue-100 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400' },
  email:   { Icon: Mail,     bg: 'bg-purple-100 text-purple-600 dark:bg-purple-900/30' },
  meeting: { Icon: Calendar, bg: 'bg-emerald-100 text-emerald-600 dark:bg-emerald-900/30' },
  note:    { Icon: FileText, bg: 'bg-amber-100 text-amber-600 dark:bg-amber-900/30' },
  deal:    { Icon: DollarSign, bg: 'bg-pink-100 text-pink-600 dark:bg-pink-900/30' },
}

function ActivityTimeline({ activities }) {
  return (
    <div className="relative">
      <div className="absolute left-5 top-0 bottom-0 w-px bg-border" />
      <div className="space-y-4">
        {activities.map(a => {
          const { Icon, bg } = ACTIVITY_ICONS[a.type] || ACTIVITY_ICONS.note
          return (
            <div key={a.id} className="flex gap-4 relative">
              <div className={`w-10 h-10 rounded-full flex-shrink-0 flex items-center justify-center z-10 ${bg}`}>
                <Icon size={16} />
              </div>
              <div className="flex-1 pb-4">
                <div className="flex items-start justify-between gap-2">
                  <p className="text-sm text-foreground"><strong>{a.user}</strong> {a.action}</p>
                  <span className="text-xs text-muted-foreground flex-shrink-0">{a.time}</span>
                </div>
                {a.note && <p className="text-xs text-muted-foreground mt-1 bg-muted rounded-lg px-3 py-2">{a.note}</p>}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
```

---

## Contact / Deal Profile Header
```jsx
<div className="rounded-xl border border-border bg-card p-6">
  <div className="flex items-start gap-4">
    <img src={`https://i.pravatar.cc/64?u=${contact.email}`}
         className="w-16 h-16 rounded-full border-2 border-border" alt={contact.name} />
    <div className="flex-1 min-w-0">
      <h1 className="text-xl font-bold text-foreground">{contact.name}</h1>
      <p className="text-sm text-muted-foreground">{contact.title} at {contact.company}</p>
      <div className="flex flex-wrap gap-3 mt-3 text-xs text-muted-foreground">
        <span className="flex items-center gap-1"><Mail size={12} />{contact.email}</span>
        <span className="flex items-center gap-1"><Phone size={12} />{contact.phone}</span>
        <span className="flex items-center gap-1"><Globe size={12} />{contact.website}</span>
      </div>
    </div>
    <div className="flex gap-2">
      <button className="border border-border text-sm px-3 py-1.5 rounded-lg hover:bg-muted transition-colors">Edit</button>
      <button className="bg-primary text-primary-foreground text-sm px-3 py-1.5 rounded-lg hover:bg-primary/90 transition-colors">
        Log Activity
      </button>
    </div>
  </div>

  {/* Stats row */}
  <div className="grid grid-cols-3 gap-4 mt-6 pt-6 border-t border-border">
    <div className="text-center">
      <div className="text-lg font-bold text-foreground">{contact.deals}</div>
      <div className="text-xs text-muted-foreground">Open Deals</div>
    </div>
    <div className="text-center border-x border-border">
      <div className="text-lg font-bold text-foreground">${contact.value}K</div>
      <div className="text-xs text-muted-foreground">Deal Value</div>
    </div>
    <div className="text-center">
      <div className="text-lg font-bold text-foreground">{contact.lastContact}</div>
      <div className="text-xs text-muted-foreground">Last Contact</div>
    </div>
  </div>
</div>
```

---

## Pipeline Funnel (CRM dashboard)
```jsx
const stages = [
  { name: 'Lead',        count: 124, value: '$820K',  pct: 100 },
  { name: 'Qualified',   count: 67,  value: '$540K',  pct: 80  },
  { name: 'Proposal',    count: 38,  value: '$310K',  pct: 60  },
  { name: 'Negotiation', count: 21,  value: '$195K',  pct: 40  },
  { name: 'Won',         count: 14,  value: '$128K',  pct: 25  },
]

{stages.map(s => (
  <div key={s.name} className="flex items-center gap-3">
    <span className="text-xs text-muted-foreground w-24 text-right">{s.name}</span>
    <div className="flex-1 h-8 bg-muted rounded-lg overflow-hidden">
      <div className="h-full bg-primary/80 rounded-lg transition-all duration-500 flex items-center px-3"
           style={{ width: `${s.pct}%` }}>
        <span className="text-xs font-medium text-primary-foreground">{s.count}</span>
      </div>
    </div>
    <span className="text-xs font-medium text-foreground w-16">{s.value}</span>
  </div>
))}
```

---

## Workload / Team Member Card
```jsx
<div className="rounded-xl border border-border bg-card p-4">
  <div className="flex items-center gap-3 mb-4">
    <img src={`https://i.pravatar.cc/40?u=${member.email}`} className="w-10 h-10 rounded-full" alt={member.name} />
    <div>
      <div className="text-sm font-semibold text-foreground">{member.name}</div>
      <div className="text-xs text-muted-foreground">{member.role}</div>
    </div>
    <span className={`ml-auto text-xs px-2 py-0.5 rounded-full font-medium ${
      member.status === 'online' ? 'bg-emerald-100 text-emerald-700' : 'bg-muted text-muted-foreground'
    }`}>{member.status}</span>
  </div>
  <div>
    <div className="flex justify-between text-xs mb-1">
      <span className="text-muted-foreground">Workload</span>
      <span className="font-medium text-foreground">{member.tasks} tasks</span>
    </div>
    <div className="h-2 bg-muted rounded-full overflow-hidden">
      <div className={`h-full rounded-full transition-all ${
        member.load > 80 ? 'bg-destructive' : member.load > 60 ? 'bg-amber-500' : 'bg-primary'
      }`} style={{ width: `${member.load}%` }} />
    </div>
  </div>
</div>
```

---

## CRM-Specific Entity Fields

**Contact**: firstName, lastName, email, phone, company, title, source (enum), status (enum: lead/prospect/customer/churned), owner (string), score (number 0-100), lastContact (date), notes (textarea)

**Deal**: title, value (number), stage (enum: lead/qualified/proposal/negotiation/won/lost), contact (relation), company (relation), closeDate (date), probability (number 0-100), owner (string)

**Company**: name, domain, industry, size (enum), ARR (number), status, website, address

**Activity**: type (enum: call/email/meeting/note/task), contact (relation), date, duration, outcome, notes
