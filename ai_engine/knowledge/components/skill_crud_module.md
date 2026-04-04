# Skill: CRUD Feature Module (React Admin) — API-Ready Pattern

## Structure
```
src/features/<entity>/
├── services/<entity>.service.js
├── hooks/use<Entity>.js
├── pages/<Entity>ListPage.jsx
└── pages/<Entity>FormPage.jsx
```

## Service Pattern (API-Ready with fetch())

Services make REAL HTTP calls to the API server.
Mock data lives in db.json (served by json-server), NOT hardcoded in services.

```jsx
// src/features/orders/services/order.service.js

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:3001';

export const orderService = {
  getAll: async (params = {}) => {
    try {
      const query = new URLSearchParams(params).toString();
      const res = await fetch(`${API_URL}/orders${query ? `?${query}` : ''}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      // json-server returns array directly; wrap for consistency
      return { data: Array.isArray(data) ? data : data.data || [], total: Array.isArray(data) ? data.length : data.total || 0 };
    } catch (err) {
      console.warn('orders.getAll failed, returning empty:', err.message);
      return { data: [], total: 0 };
    }
  },

  getById: async (id) => {
    try {
      const res = await fetch(`${API_URL}/orders/${id}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (err) {
      console.warn('orders.getById failed:', err.message);
      return null;
    }
  },

  create: async (data) => {
    try {
      const res = await fetch(`${API_URL}/orders`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (err) {
      console.warn('orders.create failed:', err.message);
      return { ...data, id: Date.now().toString() };
    }
  },

  update: async (id, data) => {
    try {
      const res = await fetch(`${API_URL}/orders/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (err) {
      console.warn('orders.update failed:', err.message);
      return { ...data, id };
    }
  },

  delete: async (id) => {
    try {
      const res = await fetch(`${API_URL}/orders/${id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return { success: true };
    } catch (err) {
      console.warn('orders.delete failed:', err.message);
      return { success: false };
    }
  },
};
```

## React Query Hooks Pattern
```jsx
// src/features/orders/hooks/useOrders.js
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { orderService } from '../services/order.service'

export function useOrders(params) {
  return useQuery({
    queryKey: ['orders', params],
    queryFn: () => orderService.getAll(params),
  })
}

export function useOrder(id) {
  return useQuery({
    queryKey: ['orders', id],
    queryFn: () => orderService.getById(id),
    enabled: !!id,
  })
}

export function useCreateOrder() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: orderService.create,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['orders'] }),
  })
}

export function useUpdateOrder() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }) => orderService.update(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['orders'] }),
  })
}

export function useDeleteOrder() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: orderService.delete,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['orders'] }),
  })
}
```

## List Page Pattern (with Design System)
```jsx
// src/features/orders/pages/OrderListPage.jsx
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Plus, Download, Search } from 'lucide-react'
import { motion } from 'framer-motion'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { DataTable } from '@/components/ui/data-table'
import { Badge } from '@/components/ui/Badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { ds } from '@/lib/design-system'
import { useOrders, useDeleteOrder } from '../hooks/useOrders'

const statusBadge = (status) => {
  const variant = ds.badge?.[status] || ds.badge?.default || ''
  return <Badge className={variant}>{status}</Badge>
}

const columns = [
  { accessorKey: 'orderNumber', header: 'Order #' },
  { accessorKey: 'customer', header: 'Customer' },
  { accessorKey: 'status', header: 'Status', cell: ({ row }) => statusBadge(row.getValue('status')) },
  { accessorKey: 'total', header: 'Total', cell: ({ row }) => `$${row.getValue('total')?.toFixed(2)}` },
  { accessorKey: 'date', header: 'Date' },
]

export default function OrderListPage() {
  const [search, setSearch] = useState('')
  const navigate = useNavigate()
  const { data, isLoading, error } = useOrders({ q: search })
  const deleteOrder = useDeleteOrder()

  // Loading state
  if (isLoading) {
    return (
      <motion.div {...ds.pageAnimation} className="space-y-6">
        <div className="h-8 w-48 animate-pulse bg-muted rounded" />
        <div className="h-64 animate-pulse bg-muted rounded-xl" />
      </motion.div>
    )
  }

  // Error state
  if (error) {
    return (
      <motion.div {...ds.pageAnimation} className="flex flex-col items-center justify-center py-20">
        <p className="text-destructive mb-4">Something went wrong loading orders.</p>
        <Button variant="outline" onClick={() => location.reload()}>Retry</Button>
      </motion.div>
    )
  }

  return (
    <motion.div {...ds.pageAnimation} className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Orders</h1>
          <p className="text-muted-foreground">Manage customer orders</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm"><Download className="h-4 w-4 mr-2" />Export</Button>
          <Button onClick={() => navigate('/orders/new')}><Plus className="h-4 w-4 mr-2" />New Order</Button>
        </div>
      </div>

      <Card className={ds.card}>
        <CardHeader className="flex flex-row items-center justify-between pb-2">
          <CardTitle className="text-lg">All Orders</CardTitle>
          <div className="relative w-64">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Search orders..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-9"
            />
          </div>
        </CardHeader>
        <CardContent>
          <DataTable columns={columns} data={data?.data || []} />
        </CardContent>
      </Card>
    </motion.div>
  )
}
```

## Form Page Pattern (with Design System)
```jsx
// src/features/orders/pages/OrderFormPage.jsx
import { useNavigate, useParams } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { motion } from 'framer-motion'
import { ArrowLeft } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Input } from '@/components/ui/Input'
import { Label } from '@/components/ui/Label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { ds } from '@/lib/design-system'
import { useOrder, useCreateOrder, useUpdateOrder } from '../hooks/useOrders'

const orderSchema = z.object({
  customer: z.string().min(1, 'Customer name is required'),
  email: z.string().email('Invalid email'),
  total: z.coerce.number().min(0, 'Must be positive'),
  status: z.enum(['pending', 'processing', 'delivered', 'cancelled']),
  notes: z.string().optional(),
})

export default function OrderFormPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const isEditing = !!id
  const { data: order, isLoading } = useOrder(id)
  const createOrder = useCreateOrder()
  const updateOrder = useUpdateOrder()

  const { register, handleSubmit, formState: { errors, isSubmitting }, setValue } = useForm({
    resolver: zodResolver(orderSchema),
    values: isEditing ? order : undefined,
  })

  const onSubmit = async (data) => {
    if (isEditing) await updateOrder.mutateAsync({ id, data })
    else await createOrder.mutateAsync(data)
    navigate('/orders')
  }

  if (isLoading && isEditing) {
    return <div className="space-y-4 max-w-2xl mx-auto">
      <div className="h-8 w-32 animate-pulse bg-muted rounded" />
      <div className="h-96 animate-pulse bg-muted rounded-xl" />
    </div>
  }

  return (
    <motion.div {...ds.pageAnimation}>
      <Button variant="ghost" size="sm" className="mb-4" onClick={() => navigate('/orders')}>
        <ArrowLeft className="h-4 w-4 mr-2" />Back to Orders
      </Button>
      <Card className={`${ds.card} max-w-2xl mx-auto`}>
        <CardHeader>
          <CardTitle>{isEditing ? 'Edit Order' : 'New Order'}</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
            <div>
              <Label htmlFor="customer">Customer <span className="text-destructive">*</span></Label>
              <Input id="customer" {...register('customer')} placeholder="Enter customer name" />
              {errors.customer && <p className="text-sm text-destructive mt-1">{errors.customer.message}</p>}
            </div>
            <div>
              <Label htmlFor="email">Email <span className="text-destructive">*</span></Label>
              <Input id="email" type="email" {...register('email')} placeholder="customer@example.com" />
              {errors.email && <p className="text-sm text-destructive mt-1">{errors.email.message}</p>}
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label htmlFor="total">Total ($) <span className="text-destructive">*</span></Label>
                <Input id="total" type="number" step="0.01" {...register('total')} placeholder="0.00" />
                {errors.total && <p className="text-sm text-destructive mt-1">{errors.total.message}</p>}
              </div>
              <div>
                <Label htmlFor="status">Status <span className="text-destructive">*</span></Label>
                <select id="status" {...register('status')} className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm">
                  <option value="pending">Pending</option>
                  <option value="processing">Processing</option>
                  <option value="delivered">Delivered</option>
                  <option value="cancelled">Cancelled</option>
                </select>
                {errors.status && <p className="text-sm text-destructive mt-1">{errors.status.message}</p>}
              </div>
            </div>
            <div>
              <Label htmlFor="notes">Notes</Label>
              <textarea id="notes" {...register('notes')} placeholder="Additional notes..." rows={3}
                className="flex w-full rounded-md border border-input bg-background px-3 py-2 text-sm" />
            </div>
            <div className="flex gap-2 pt-4">
              <Button type="submit" disabled={isSubmitting}>
                {isSubmitting ? 'Saving...' : (isEditing ? 'Update' : 'Create')}
              </Button>
              <Button type="button" variant="outline" onClick={() => navigate('/orders')}>Cancel</Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </motion.div>
  )
}
```

## Design System Import (MANDATORY)
```jsx
// Every component must import and use the design system:
import { ds } from '@/lib/design-system'

// Usage in JSX:
<Card className={ds.card}>
<Badge className={ds.badge[status]}>
<motion.div {...ds.pageAnimation}>
```

## db.json Pattern (served by json-server at localhost:3001)
```json
{
  "orders": [
    { "id": "1", "orderNumber": "ORD-2024-001", "customer": "John Smith", "email": "john@acme.com", "status": "delivered", "total": 1249.99, "date": "2024-12-01", "notes": "Express shipping" },
    { "id": "2", "orderNumber": "ORD-2024-002", "customer": "Sarah Johnson", "email": "sarah@corp.io", "status": "processing", "total": 879.50, "date": "2024-12-02", "notes": "" }
  ]
}
```

## RULES
- Services use fetch() — NOT hardcoded arrays
- Mock data lives in db.json — NOT in service files
- Every component imports `ds` from '@/lib/design-system'
- Every list page has: loading skeleton, empty state, error state
- Every form has: validation, required markers, error messages, loading submit
- Use Tailwind CSS variable classes — NEVER hardcode hex/rgb
