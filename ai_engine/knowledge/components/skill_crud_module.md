# Skill: CRUD Feature Module (React Admin)

## Structure
```
src/features/<entity>/
├── services/<entity>.service.js
├── hooks/use<Entity>.js
├── pages/<Entity>ListPage.jsx
└── pages/<Entity>FormPage.jsx
```

## Service Pattern (with mock data fallback)
```jsx
// src/features/orders/services/order.service.js
import apiClient from '@/api/client'

const MOCK_ORDERS = [
  { id: '1', orderNumber: 'ORD-2024-001', customer: 'John Smith', email: 'john@acme.com', status: 'delivered', total: 1249.99, items: 3, date: '2024-12-01', address: '123 Main St, New York' },
  { id: '2', orderNumber: 'ORD-2024-002', customer: 'Sarah Johnson', email: 'sarah@corp.io', status: 'processing', total: 879.50, items: 2, date: '2024-12-02', address: '456 Oak Ave, Chicago' },
  // ... 10-20 rows of realistic data
]

export const orderService = {
  getAll: async (params) => {
    try { return await apiClient.get('/orders', { params }) }
    catch { return { data: MOCK_ORDERS, total: MOCK_ORDERS.length } }
  },
  getById: async (id) => {
    try { return await apiClient.get(`/orders/${id}`) }
    catch { return MOCK_ORDERS.find(o => o.id === id) }
  },
  create: async (data) => {
    try { return await apiClient.post('/orders', data) }
    catch { return { ...data, id: Date.now().toString() } }
  },
  update: async (id, data) => {
    try { return await apiClient.put(`/orders/${id}`, data) }
    catch { return { ...data, id } }
  },
  delete: async (id) => {
    try { return await apiClient.delete(`/orders/${id}`) }
    catch { return { success: true } }
  },
}
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

## List Page Pattern
```jsx
// src/features/orders/pages/OrderListPage.jsx
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Plus, Download } from 'lucide-react'
import { motion } from 'framer-motion'
import { Button } from '@/components/ui/Button'
import { DataTable } from '@/components/ui/data-table'
import { Badge } from '@/components/ui/Badge'
import { useOrders, useDeleteOrder } from '../hooks/useOrders'

const statusVariants = { delivered: 'default', processing: 'warning', cancelled: 'destructive', pending: 'secondary' }

const columns = [/* column definitions using patterns from skill_datatable.md */]

export default function OrderListPage() {
  const navigate = useNavigate()
  const { data, isLoading } = useOrders()
  const deleteOrder = useDeleteOrder()
  
  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="space-y-6">
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
      <DataTable columns={columns} data={data?.data || []} searchKey="customer" />
    </motion.div>
  )
}
```

## Form Page Pattern
```jsx
// src/features/orders/pages/OrderFormPage.jsx
import { useNavigate, useParams } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Input } from '@/components/ui/Input'
import { Label } from '@/components/ui/Label'
import { useOrder, useCreateOrder, useUpdateOrder } from '../hooks/useOrders'

const orderSchema = z.object({
  customer: z.string().min(1, 'Customer name is required'),
  email: z.string().email('Invalid email'),
  total: z.number().min(0, 'Must be positive'),
  status: z.enum(['pending', 'processing', 'delivered', 'cancelled']),
})

export default function OrderFormPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const isEditing = !!id
  const { data: order } = useOrder(id)
  const createOrder = useCreateOrder()
  const updateOrder = useUpdateOrder()

  const { register, handleSubmit, formState: { errors } } = useForm({
    resolver: zodResolver(orderSchema),
    values: isEditing ? order : undefined,
  })

  const onSubmit = async (data) => {
    if (isEditing) await updateOrder.mutateAsync({ id, data })
    else await createOrder.mutateAsync(data)
    navigate('/orders')
  }

  return (
    <Card className="max-w-2xl mx-auto">
      <CardHeader>
        <CardTitle>{isEditing ? 'Edit Order' : 'New Order'}</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
          <div>
            <Label htmlFor="customer">Customer</Label>
            <Input id="customer" {...register('customer')} placeholder="Enter customer name" />
            {errors.customer && <p className="text-sm text-destructive mt-1">{errors.customer.message}</p>}
          </div>
          {/* more fields */}
          <div className="flex gap-2 pt-4">
            <Button type="submit">{isEditing ? 'Update' : 'Create'}</Button>
            <Button type="button" variant="outline" onClick={() => navigate('/orders')}>Cancel</Button>
          </div>
        </form>
      </CardContent>
    </Card>
  )
}
```
