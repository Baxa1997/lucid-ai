# Skill: Forms (react-hook-form + zod, shadcn/ui)

ALL forms — admin panels, landing pages, consumer websites, contact/registration forms — use this exact pattern. Never use uncontrolled inputs.

## CRITICAL: NEVER USE NATIVE HTML `<select>`
Native `<select>` is **FORBIDDEN**. It is un-styled, looks broken compared to shadcn inputs, and produces inconsistent UI.
ALWAYS use the shadcn/ui `Select` component with `SelectTrigger`, `SelectContent`, `SelectItem`, `SelectValue`.
This rule applies to EVERY form on every page type — admin, landing, contact, registration, booking, filter, etc.

## Install (already in skeleton)
```
react-hook-form  zod  @hookform/resolvers
```

## Base Pattern
```jsx
'use client'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import { toast } from 'sonner'

// 1. Define schema
const schema = z.object({
  name:     z.string().min(2, 'Name must be at least 2 characters'),
  email:    z.string().email('Invalid email address'),
  status:   z.enum(['active', 'inactive', 'pending']),
  notes:    z.string().optional(),
  price:    z.coerce.number().min(0, 'Price must be positive'),
})

// 2. Form component
export default function EntityForm({ initialData, onSubmit: onSuccess }) {
  const {
    register,
    handleSubmit,
    setValue,
    watch,
    formState: { errors, isSubmitting },
  } = useForm({
    resolver: zodResolver(schema),
    defaultValues: initialData ?? {
      name: '', email: '', status: 'active', notes: '', price: 0,
    },
  })

  const onSubmit = async (data: FormValues) => {
    try {
      const url = initialData?.id ? `/api/entities/${initialData.id}` : '/api/entities'
      const method = initialData?.id ? 'PUT' : 'POST'
      const res = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      })
      if (!res.ok) throw new Error('Failed to save')
      toast.success(initialData?.id ? 'Updated successfully' : 'Created successfully')
      onSuccess?.()
    } catch (e) {
      toast.error('Something went wrong')
    }
  }

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="space-y-5">
      {/* Text field */}
      <div className="space-y-1.5">
        <Label htmlFor="name">Name <span className="text-destructive">*</span></Label>
        <Input id="name" placeholder="Enter name" {...register('name')} />
        {errors.name && <p className="text-xs text-destructive">{errors.name.message}</p>}
      </div>

      {/* Email field */}
      <div className="space-y-1.5">
        <Label htmlFor="email">Email <span className="text-destructive">*</span></Label>
        <Input id="email" type="email" placeholder="name@example.com" {...register('email')} />
        {errors.email && <p className="text-xs text-destructive">{errors.email.message}</p>}
      </div>

      {/* Select field — shadcn/ui Select is NOT a native select */}
      <div className="space-y-1.5">
        <Label>Status <span className="text-destructive">*</span></Label>
        <Select
          defaultValue={watch('status')}
          onValueChange={(val) => setValue('status', val, { shouldValidate: true })}
        >
          <SelectTrigger>
            <SelectValue placeholder="Select status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="active">Active</SelectItem>
            <SelectItem value="inactive">Inactive</SelectItem>
            <SelectItem value="pending">Pending</SelectItem>
          </SelectContent>
        </Select>
        {errors.status && <p className="text-xs text-destructive">{errors.status.message}</p>}
      </div>

      {/* Number field */}
      <div className="space-y-1.5">
        <Label htmlFor="price">Price</Label>
        <Input id="price" type="number" step="0.01" placeholder="0.00" {...register('price')} />
        {errors.price && <p className="text-xs text-destructive">{errors.price.message}</p>}
      </div>

      {/* Textarea */}
      <div className="space-y-1.5">
        <Label htmlFor="notes">Notes</Label>
        <Textarea id="notes" placeholder="Optional notes..." rows={4} {...register('notes')} />
      </div>

      {/* Submit */}
      <div className="flex gap-3 pt-2">
        <Button type="submit" disabled={isSubmitting} className="min-w-24">
          {isSubmitting ? 'Saving...' : initialData?.id ? 'Update' : 'Create'}
        </Button>
        <Button type="button" variant="outline" onClick={() => history.back()}>
          Cancel
        </Button>
      </div>
    </form>
  )
}
```

---

## Date Picker Field
```jsx
import { CalendarIcon } from 'lucide-react'
import { format } from 'date-fns'
import { Calendar } from '@/components/ui/calendar'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'

// Inside form, not registered via register() — use Controller instead:
import { Controller } from 'react-hook-form'

<Controller
  control={control}
  name="dueDate"
  render={({ field }) => (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" className="w-full justify-start text-left font-normal">
          <CalendarIcon className="mr-2 h-4 w-4" />
          {field.value ? format(field.value, 'PPP') : <span className="text-muted-foreground">Pick a date</span>}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-auto p-0" align="start">
        <Calendar
          mode="single"
          selected={field.value}
          onSelect={field.onChange}
          initialFocus
        />
      </PopoverContent>
    </Popover>
  )}
/>
```

---

## Multi-select Tags Input (simple)
```jsx
'use client'
import { useState } from 'react'
import { X } from 'lucide-react'

export function TagInput({ value = [], onChange }) {
  const [input, setInput] = useState('')

  const add = (e) => {
    if ((e.key === 'Enter' || e.key === ',') && input.trim()) {
      e.preventDefault()
      const tag = input.trim().replace(/,$/, '')
      if (tag && !value.includes(tag)) onChange([...value, tag])
      setInput('')
    }
  }

  return (
    <div className="flex flex-wrap gap-1.5 rounded-lg border border-border bg-background px-3 py-2 min-h-10 focus-within:ring-2 focus-within:ring-primary/50">
      {value.map(tag => (
        <span key={tag} className="inline-flex items-center gap-1 bg-primary/10 text-primary text-xs px-2 py-0.5 rounded-full">
          {tag}
          <button type="button" onClick={() => onChange(value.filter(t => t !== tag))} className="hover:text-destructive">
            <X size={10} />
          </button>
        </span>
      ))}
      <input
        value={input}
        onChange={e => setInput(e.target.value)}
        onKeyDown={add}
        placeholder={value.length === 0 ? 'Type and press Enter...' : ''}
        className="flex-1 min-w-20 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
      />
    </div>
  )
}
```

---

## Form Layout Variants

**Full-page form** (new record page):
```jsx
<div className="max-w-2xl mx-auto py-8 px-6">
  <div className="mb-6">
    <h1 className="text-2xl font-bold text-foreground">Create New [Entity]</h1>
    <p className="text-sm text-muted-foreground mt-1">Fill in the details below.</p>
  </div>
  <div className="rounded-xl border border-border bg-card p-6">
    <EntityForm />
  </div>
</div>
```

**Slide-over / drawer form** (inline edit without page nav):
```jsx
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet'

<Sheet open={open} onOpenChange={setOpen}>
  <SheetContent className="w-full sm:max-w-lg overflow-y-auto">
    <SheetHeader className="mb-6">
      <SheetTitle>Edit {entity.name}</SheetTitle>
    </SheetHeader>
    <EntityForm initialData={entity} onSubmit={() => setOpen(false)} />
  </SheetContent>
</Sheet>
```

---

## CRITICAL RULES for Forms
- shadcn/ui `Select` does NOT support `...register()` — use `Controller` or `setValue` + `onValueChange`
- `z.coerce.number()` for number inputs (HTML returns strings)
- Always show field-level errors immediately below the input
- Disable submit button while `isSubmitting` to prevent double-submit
- Use `toast.success` / `toast.error` from `sonner` for feedback
- `Textarea` comes from `@/components/ui/textarea`, NOT a plain `<textarea>`
