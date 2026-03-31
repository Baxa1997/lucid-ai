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

### 3. CSS Variables Only
- [ ] NEVER use hardcoded hex colors
- [ ] All colors reference var(--color-*)
- [ ] All border-radius reference var(--radius-*)
- [ ] Font families reference var(--font-family) or var(--font-heading)

### 4. Accessibility
- [ ] Interactive elements have `cursor: pointer`
- [ ] Buttons have clear labels (not just icons)
- [ ] Images have alt text
- [ ] Form inputs have labels
- [ ] Focus states visible

### 5. Performance
- [ ] No inline style objects recreated on every render
- [ ] Event handlers stable (useCallback if passed as props)
- [ ] Lists have proper `key` props
- [ ] Images use lazy loading where appropriate

---

## CSS Variable System

Every project MUST define these variables in its CSS file:

```css
:root {
  /* Primary palette */
  --color-primary: #...;
  --color-primary-light: #...;
  --color-primary-dark: #...;
  --color-primary-50: #...;  /* very subtle tint for backgrounds */

  /* Accent (complementary to primary) */
  --color-accent: #...;

  /* Backgrounds */
  --color-bg: #...;           /* main page background */
  --color-bg-secondary: #...; /* card/section alternative bg */
  --color-bg-tertiary: #...;  /* hover states, input backgrounds */
  --color-surface: #...;      /* cards, modals, dropdowns */

  /* Text */
  --color-text: #...;            /* primary text */
  --color-text-secondary: #...;  /* body text, paragraphs */
  --color-text-muted: #...;      /* labels, captions, placeholders */

  /* Border */
  --color-border: #...;

  /* Typography */
  --font-family: 'Inter', sans-serif;
  --font-heading: 'Inter', sans-serif;

  /* Radius */
  --radius-sm: 0.25rem;
  --radius-md: 0.375rem;
  --radius-lg: 0.5rem;
  --radius-xl: 0.75rem;
  --radius-full: 9999px;
}
```

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
❌ style={{ color: '#6366f1' }}                   // hardcoded hex
❌ className="text-blue-500"                      // Tailwind (unless configured)
❌ var(--color-primary: #6366f1)                   // wrong CSS syntax
```

```
✅ style={{ color: 'var(--color-primary)' }}      // CSS variable
✅ className="hero-title"                          // semantic class name
```

### Component Anti-Patterns
```
❌ 500+ lines in a single component               // break into sections
❌ No loading state                                // always have skeleton/spinner
❌ No empty state                                  // always handle zero items
❌ console.log left in production code             // remove all logs
❌ Inline styles for complex layouts               // use CSS file or CSS-in-JS
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
