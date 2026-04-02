# Senior Engineer Quality Standards

## Code Quality Checklist

Every file you write MUST pass this checklist:

### 1. Import Safety
- [ ] Every import points to a file that EXISTS in the workspace
- [ ] No circular imports
- [ ] No unused imports
- [ ] Default imports match the component export name
- [ ] Icons imported individually: `import { Icon } from 'lucide-react'`

### 2. Component Structure
- [ ] Named export AND default export
- [ ] Props destructured with defaults
- [ ] Loading state rendered (skeleton or spinner)
- [ ] Empty state rendered (icon + message + CTA)
- [ ] Error state handled (try/catch + error message)
- [ ] Responsive design (works on mobile 375px → desktop 1440px)

### 3. Tailwind + shadcn/ui Classes Only
- [ ] NEVER use hardcoded hex colors (#fff, #000, #333)
- [ ] NEVER use inline style={{}} for colors, spacing, or layout
- [ ] NEVER use var(--color-primary) or var(--color-bg) — those don't exist
- [ ] Use Tailwind semantic classes: bg-primary, text-foreground, bg-muted, bg-card, border-border
- [ ] Use Tailwind spacing: p-4, px-6, py-3, gap-4, space-y-4
- [ ] Use Tailwind typography: text-sm, text-lg, font-bold, tracking-tight

### 4. Accessibility
- [ ] Interactive elements have `cursor: pointer`
- [ ] Buttons have clear labels (not just icons)
- [ ] Images have alt text
- [ ] Form inputs have labels
- [ ] Focus states visible

### 5. Performance
- [ ] No inline style={{}} objects — use className with Tailwind
- [ ] Event handlers stable (useCallback if passed as props)
- [ ] Lists have proper `key` props
- [ ] Images use lazy loading where appropriate

---

## Tailwind Design Token System (shadcn/ui)

Every project uses HSL-based CSS variables consumed by Tailwind:

```css
:root {
  /* These are HSL values (no hsl() wrapper) — Tailwind adds hsl() automatically */
  --background: 0 0% 100%;
  --foreground: 222.2 84% 4.9%;
  --card: 0 0% 100%;
  --card-foreground: 222.2 84% 4.9%;
  --primary: 221.2 83.2% 53.3%;
  --primary-foreground: 210 40% 98%;
  --secondary: 210 40% 96.1%;
  --secondary-foreground: 222.2 47.4% 11.2%;
  --muted: 210 40% 96.1%;
  --muted-foreground: 215.4 16.3% 46.9%;
  --accent: 210 40% 96.1%;
  --accent-foreground: 222.2 47.4% 11.2%;
  --destructive: 0 84.2% 60.2%;
  --border: 214.3 31.8% 91.4%;
  --ring: 221.2 83.2% 53.3%;
  --radius: 0.5rem;
}
```

Use these via Tailwind classes:
- `bg-primary` / `text-primary-foreground` — for primary buttons
- `bg-background` / `text-foreground` — for page background
- `bg-card` / `border-border` — for cards
- `bg-muted` / `text-muted-foreground` — for subtle backgrounds and secondary text
- `bg-destructive` — for error/delete actions

---

## Anti-Patterns (NEVER DO)

### Import Anti-Patterns
```
❌ import Navbar from '@/components/Navbar'     // @ alias may not exist
❌ import { Button } from './ui/button'          // file may not exist
❌ import axios from 'axios'                     // package may not be installed
```

```
✅ import Navbar from '../components/Navbar'     // relative path
✅ import Navbar from './Navbar'                  // same directory
```

### Layout Anti-Patterns
```
❌ Creating src/App.jsx in a Next.js project     // doesn't exist in Next.js
❌ Using BrowserRouter in Next.js                 // Next.js has built-in routing
❌ Creating (marketing)/page.js route groups      // keep routes flat
❌ Multiple layout.js files                       // only root layout.js
❌ 'use client' on page.js files                  // pages are server components
```

### CSS Anti-Patterns
```
❌ style={{ color: '#6366f1' }}                   // hardcoded hex inline
❌ style={{ color: 'var(--color-primary)' }}       // var(--color-*) DON'T EXIST
❌ var(--color-primary: #6366f1)                   // wrong CSS syntax
❌ className="stat-card" with custom CSS            // use Tailwind utilities
```

```
✅ className="bg-primary text-primary-foreground"  // Tailwind semantic
✅ className="text-foreground bg-card border"      // Tailwind + shadcn tokens
✅ className="hover:bg-muted transition-colors"    // Tailwind hover state
```

### Component Anti-Patterns
```
❌ 500+ lines in a single component               // break into sections
❌ No loading state                                // always have skeleton/spinner
❌ No empty state                                  // always handle zero items
❌ console.log left in production code             // remove all logs
❌ Inline style={{}} for layouts/colors             // use className with Tailwind
```

---

## File Naming Conventions

| Type | React/Next.js | Vue |
|------|--------------|-----|
| Page component | `Home.jsx` or `page.js` | `HomeView.vue` |
| Section component | `HeroSection.jsx` | `HeroSection.vue` |
| Shared component | `Button.jsx` | `BaseButton.vue` |
| Layout | `Layout.jsx` | `AppLayout.vue` |
| Hook | `useAuth.js` | `useAuth.ts` |
| Store | `authStore.js` | `auth.ts` |
| Utility | `formatDate.js` | `formatDate.ts` |
| Constants | `constants.js` | `constants.ts` |
| Types | `types.ts` | `types.ts` |
