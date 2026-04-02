# Skill: DataTable (shadcn/ui + @tanstack/react-table)

## Import
```jsx
import { DataTable } from '@/components/ui/data-table'
```

## Column Definition Pattern
```jsx
const columns = [
  {
    accessorKey: "id",
    header: "ID",
  },
  {
    accessorKey: "name",
    header: "Name",
    cell: ({ row }) => (
      <div className="flex items-center gap-3">
        <img src={`https://i.pravatar.cc/32?u=${row.original.id}`} className="h-8 w-8 rounded-full" />
        <div>
          <p className="font-medium">{row.getValue("name")}</p>
          <p className="text-sm text-muted-foreground">{row.original.email}</p>
        </div>
      </div>
    ),
  },
  {
    accessorKey: "status",
    header: "Status",
    cell: ({ row }) => {
      const status = row.getValue("status")
      const variants = { active: "default", pending: "warning", inactive: "destructive" }
      return <Badge variant={variants[status] || "secondary"}>{status}</Badge>
    },
  },
  {
    accessorKey: "amount",
    header: () => <div className="text-right">Amount</div>,
    cell: ({ row }) => <div className="text-right font-medium">${parseFloat(row.getValue("amount")).toFixed(2)}</div>,
  },
  {
    accessorKey: "date",
    header: "Date",
    cell: ({ row }) => new Date(row.getValue("date")).toLocaleDateString(),
  },
  {
    id: "actions",
    cell: ({ row }) => (
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon"><MoreHorizontal className="h-4 w-4" /></Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem onClick={() => navigate(`/edit/${row.original.id}`)}>Edit</DropdownMenuItem>
          <DropdownMenuItem className="text-destructive">Delete</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    ),
  },
]
```

## Usage
```jsx
<DataTable columns={columns} data={data} searchKey="name" />
```

## Required Imports for Column Cells
```jsx
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { MoreHorizontal } from 'lucide-react'
```
