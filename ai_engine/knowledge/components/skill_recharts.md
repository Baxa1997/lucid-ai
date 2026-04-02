# Skill: Recharts (React charts)

## Import
```jsx
import {
  AreaChart, Area, BarChart, Bar, LineChart, Line,
  PieChart, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer,
} from 'recharts'
```

## Area Chart Pattern (KPI trend)
```jsx
const revenueData = [
  { month: 'Jan', revenue: 4200, orders: 120 },
  { month: 'Feb', revenue: 5100, orders: 145 },
  { month: 'Mar', revenue: 4800, orders: 132 },
  { month: 'Apr', revenue: 6200, orders: 168 },
  { month: 'May', revenue: 5900, orders: 155 },
  { month: 'Jun', revenue: 7100, orders: 189 },
  { month: 'Jul', revenue: 6800, orders: 178 },
  { month: 'Aug', revenue: 7500, orders: 195 },
  { month: 'Sep', revenue: 8200, orders: 210 },
  { month: 'Oct', revenue: 7900, orders: 205 },
  { month: 'Nov', revenue: 8800, orders: 228 },
  { month: 'Dec', revenue: 9500, orders: 245 },
]

<Card>
  <CardHeader>
    <CardTitle>Revenue Overview</CardTitle>
  </CardHeader>
  <CardContent>
    <ResponsiveContainer width="100%" height={350}>
      <AreaChart data={revenueData}>
        <defs>
          <linearGradient id="colorRevenue" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="hsl(var(--chart-1))" stopOpacity={0.3} />
            <stop offset="95%" stopColor="hsl(var(--chart-1))" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
        <XAxis dataKey="month" className="text-xs" />
        <YAxis className="text-xs" />
        <Tooltip
          contentStyle={{
            backgroundColor: 'hsl(var(--card))',
            border: '1px solid hsl(var(--border))',
            borderRadius: 'var(--radius)',
          }}
        />
        <Area type="monotone" dataKey="revenue" stroke="hsl(var(--chart-1))" fill="url(#colorRevenue)" />
      </AreaChart>
    </ResponsiveContainer>
  </CardContent>
</Card>
```

## Bar Chart Pattern (comparison)
```jsx
<ResponsiveContainer width="100%" height={350}>
  <BarChart data={data}>
    <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
    <XAxis dataKey="name" className="text-xs" />
    <YAxis className="text-xs" />
    <Tooltip contentStyle={{ backgroundColor: 'hsl(var(--card))', border: '1px solid hsl(var(--border))' }} />
    <Bar dataKey="value" fill="hsl(var(--chart-1))" radius={[4, 4, 0, 0]} />
    <Bar dataKey="target" fill="hsl(var(--chart-2))" radius={[4, 4, 0, 0]} />
  </BarChart>
</ResponsiveContainer>
```

## Pie/Donut Chart Pattern
```jsx
const COLORS = ['hsl(var(--chart-1))', 'hsl(var(--chart-2))', 'hsl(var(--chart-3))', 'hsl(var(--chart-4))', 'hsl(var(--chart-5))']

<ResponsiveContainer width="100%" height={300}>
  <PieChart>
    <Pie data={data} cx="50%" cy="50%" innerRadius={60} outerRadius={100} paddingAngle={5} dataKey="value">
      {data.map((entry, index) => (
        <Cell key={index} fill={COLORS[index % COLORS.length]} />
      ))}
    </Pie>
    <Tooltip />
    <Legend />
  </PieChart>
</ResponsiveContainer>
```

## KPI Stat Card Pattern
```jsx
import { TrendingUp, TrendingDown } from 'lucide-react'

function StatCard({ title, value, change, trend, icon: Icon }) {
  return (
    <Card>
      <CardContent className="p-6">
        <div className="flex items-center justify-between">
          <p className="text-sm font-medium text-muted-foreground">{title}</p>
          <Icon className="h-5 w-5 text-muted-foreground" />
        </div>
        <div className="mt-2">
          <p className="text-2xl font-bold">{value}</p>
          <div className={`flex items-center gap-1 text-sm mt-1 ${trend === 'up' ? 'text-emerald-600' : 'text-red-500'}`}>
            {trend === 'up' ? <TrendingUp className="h-4 w-4" /> : <TrendingDown className="h-4 w-4" />}
            <span>{change}</span>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
```

## IMPORTANT RULES
- Always use `hsl(var(--chart-N))` for colors — NEVER hex
- Always wrap in `<ResponsiveContainer width="100%" height={N}>`
- Always 12+ data points for trend charts
- Always include CartesianGrid + Tooltip
