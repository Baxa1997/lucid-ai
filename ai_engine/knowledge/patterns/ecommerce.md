# E-Commerce Architecture Patterns (Tailwind + shadcn/ui)

Sidebar-nav admin with product management, orders, customers, analytics.
Stack: Next.js App Router + shadcn/ui + recharts + json-server.

---

## Page Map

```
/ (dashboard)          — KPIs: revenue, orders, customers, conversion rate
/products              — product grid + filter bar + search
/products/new          — create product form
/products/:id          — edit product form
/orders                — order table with status badges + pagination
/orders/:id            — order detail: items, timeline, customer info
/customers             — customer table with LTV, order count
/customers/:id         — customer profile: orders history, info
/analytics             — revenue chart, top products, traffic sources
/settings              — store settings, payment config, shipping rules
```

---

## Product Card (grid view)
```jsx
<div className="rounded-xl border border-border bg-card overflow-hidden group hover:shadow-md transition-all">
  <div className="relative aspect-square overflow-hidden bg-muted">
    <img
      src={product.image || `https://picsum.photos/seed/${product.id}/400/400`}
      alt={product.name}
      className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
    />
    {product.stock === 0 && (
      <div className="absolute inset-0 bg-background/70 flex items-center justify-center">
        <span className="text-sm font-medium text-muted-foreground">Out of Stock</span>
      </div>
    )}
    <div className="absolute top-2 right-2 flex flex-col gap-1">
      {product.isNew && <span className="bg-primary text-primary-foreground text-xs px-2 py-0.5 rounded-full font-medium">New</span>}
      {product.discount > 0 && <span className="bg-destructive text-destructive-foreground text-xs px-2 py-0.5 rounded-full font-medium">-{product.discount}%</span>}
    </div>
  </div>
  <div className="p-4">
    <div className="text-xs text-muted-foreground mb-1">{product.category}</div>
    <h3 className="font-semibold text-foreground text-sm leading-tight line-clamp-2">{product.name}</h3>
    <div className="flex items-center justify-between mt-3">
      <div>
        {product.discount > 0 ? (
          <div className="flex items-center gap-2">
            <span className="font-bold text-foreground">${(product.price * (1 - product.discount/100)).toFixed(2)}</span>
            <span className="text-xs text-muted-foreground line-through">${product.price}</span>
          </div>
        ) : (
          <span className="font-bold text-foreground">${product.price}</span>
        )}
      </div>
      <span className="text-xs text-muted-foreground">{product.stock} in stock</span>
    </div>
  </div>
  <div className="px-4 pb-4 flex gap-2">
    <a href={`/products/${product.id}`} className="flex-1 text-center text-xs bg-primary text-primary-foreground py-1.5 rounded-md font-medium hover:bg-primary/90 transition-colors">
      Edit
    </a>
    <button className="text-xs border border-border text-foreground px-3 py-1.5 rounded-md hover:bg-muted transition-colors">
      View
    </button>
  </div>
</div>
```

---

## Order Status Badges
```jsx
const ORDER_STATUS = {
  pending:    'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300',
  processing: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300',
  shipped:    'bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-300',
  delivered:  'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300',
  cancelled:  'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300',
  refunded:   'bg-gray-100 text-gray-800 dark:bg-gray-900/30 dark:text-gray-300',
}

<span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${ORDER_STATUS[order.status]}`}>
  {order.status}
</span>
```

---

## Order Detail Page Layout
```jsx
<div className="max-w-5xl mx-auto space-y-6">
  {/* Header */}
  <div className="flex items-start justify-between">
    <div>
      <h1 className="text-2xl font-bold text-foreground">Order #{order.number}</h1>
      <p className="text-sm text-muted-foreground mt-1">Placed {order.date}</p>
    </div>
    <div className="flex gap-2">
      <button className="border border-border text-sm px-4 py-2 rounded-lg hover:bg-muted transition-colors">Print</button>
      <select className="border border-border text-sm px-4 py-2 rounded-lg bg-background">
        <option>Update Status</option>
        {Object.keys(ORDER_STATUS).map(s => <option key={s}>{s}</option>)}
      </select>
    </div>
  </div>

  <div className="grid lg:grid-cols-3 gap-6">
    {/* Items — 2/3 width */}
    <div className="lg:col-span-2 space-y-4">
      <div className="rounded-xl border border-border bg-card">
        <div className="p-4 border-b border-border font-semibold text-foreground">Items ({order.items.length})</div>
        <div className="divide-y divide-border">
          {order.items.map(item => (
            <div key={item.id} className="flex items-center gap-4 p-4">
              <img src={item.image} className="w-14 h-14 rounded-lg object-cover bg-muted" alt={item.name} />
              <div className="flex-1 min-w-0">
                <div className="font-medium text-foreground text-sm truncate">{item.name}</div>
                <div className="text-xs text-muted-foreground">{item.variant}</div>
              </div>
              <div className="text-right">
                <div className="text-sm font-medium text-foreground">${item.price}</div>
                <div className="text-xs text-muted-foreground">×{item.qty}</div>
              </div>
            </div>
          ))}
        </div>
        <div className="p-4 border-t border-border space-y-1">
          <div className="flex justify-between text-sm"><span className="text-muted-foreground">Subtotal</span><span>${order.subtotal}</span></div>
          <div className="flex justify-between text-sm"><span className="text-muted-foreground">Shipping</span><span>${order.shipping}</span></div>
          <div className="flex justify-between text-sm font-bold border-t border-border pt-2 mt-2"><span>Total</span><span>${order.total}</span></div>
        </div>
      </div>
    </div>

    {/* Sidebar — 1/3 */}
    <div className="space-y-4">
      <div className="rounded-xl border border-border bg-card p-4">
        <div className="font-semibold text-foreground mb-3">Customer</div>
        <img src={`https://i.pravatar.cc/40?u=${order.customer.email}`} className="w-10 h-10 rounded-full mb-2" alt="" />
        <div className="text-sm font-medium text-foreground">{order.customer.name}</div>
        <div className="text-xs text-muted-foreground">{order.customer.email}</div>
        <div className="text-xs text-muted-foreground mt-1">{order.customer.totalOrders} orders</div>
      </div>
      <div className="rounded-xl border border-border bg-card p-4">
        <div className="font-semibold text-foreground mb-3">Shipping Address</div>
        <address className="text-sm text-muted-foreground not-italic leading-relaxed">
          {order.address.line1}<br />
          {order.address.city}, {order.address.state} {order.address.zip}
        </address>
      </div>
    </div>
  </div>
</div>
```

---

## Analytics Dashboard KPIs
```jsx
const kpis = [
  { label: 'Total Revenue', value: '$84,291', change: '+12.5%', trend: 'up', icon: DollarSign, color: 'text-emerald-600' },
  { label: 'Total Orders', value: '2,847', change: '+8.2%', trend: 'up', icon: ShoppingCart, color: 'text-blue-600' },
  { label: 'Avg. Order Value', value: '$29.60', change: '+3.1%', trend: 'up', icon: TrendingUp, color: 'text-purple-600' },
  { label: 'Conversion Rate', value: '3.4%', change: '-0.2%', trend: 'down', icon: Target, color: 'text-amber-600' },
]
```

## Product Form Fields
```
name (string, required) | sku (string) | category (select) | price (number, required)
comparePrice (number) | stock (number) | weight (number) | status (enum: active/draft/archived)
description (textarea) | images (file[]) | tags (string[])
```
