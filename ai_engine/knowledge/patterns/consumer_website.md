# Consumer Website Architecture Patterns (Tailwind + shadcn/ui)

Multi-page public sites for restaurants, clinics, law firms, agencies, real estate, etc.
Top-header nav, no auth, no sidebar. Each page is a standalone component.

---

## Standard Page Structure

```
┌──────────────────────────────────────────┐
│  Logo     About  Services  Contact  CTA  │  ← sticky top header
├──────────────────────────────────────────┤
│  Hero (full-width, domain-specific bg)   │
├──────────────────────────────────────────┤
│  Page-specific sections (3-6 sections)   │
├──────────────────────────────────────────┤
│  Footer: logo, nav columns, social, copy │
└──────────────────────────────────────────┘
```

## Sticky Header (all pages share this)
```jsx
'use client'
import { useState, useEffect } from 'react'
import { Menu, X } from 'lucide-react'
import { Button } from '@/components/ui/button'

export default function Header() {
  const [scrolled, setScrolled] = useState(false)
  const [mobileOpen, setMobileOpen] = useState(false)

  useEffect(() => {
    const fn = () => setScrolled(window.scrollY > 10)
    window.addEventListener('scroll', fn)
    return () => window.removeEventListener('scroll', fn)
  }, [])

  const links = [
    { label: 'About', href: '/about' },
    { label: 'Services', href: '/services' },
    { label: 'Contact', href: '/contact' },
  ]

  return (
    <header className={`fixed top-0 inset-x-0 z-50 transition-all duration-300 ${
      scrolled ? 'bg-background/95 backdrop-blur border-b border-border shadow-sm' : 'bg-transparent'
    }`}>
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
        <a href="/" className="font-bold text-xl text-foreground">BrandName</a>
        <nav className="hidden md:flex items-center gap-6">
          {links.map(l => (
            <a key={l.href} href={l.href}
               className="text-sm text-muted-foreground hover:text-foreground transition-colors">
              {l.label}
            </a>
          ))}
          <Button size="sm">Get in Touch</Button>
        </nav>
        <button className="md:hidden" onClick={() => setMobileOpen(!mobileOpen)}>
          {mobileOpen ? <X size={22} /> : <Menu size={22} />}
        </button>
      </div>
      {mobileOpen && (
        <div className="md:hidden bg-background border-b border-border px-4 py-4 flex flex-col gap-3">
          {links.map(l => <a key={l.href} href={l.href} className="text-sm py-1">{l.label}</a>)}
        </div>
      )}
    </header>
  )
}
```

---

## Homepage Structure (6 sections minimum)

### 1. Hero — domain-specific headline + CTA
```jsx
<section className="pt-32 pb-20 bg-gradient-to-b from-background to-muted/30">
  <div className="max-w-7xl mx-auto px-6 grid md:grid-cols-2 gap-12 items-center">
    <div>
      <span className="text-sm font-semibold text-primary uppercase tracking-widest">
        Domain tagline
      </span>
      <h1 className="mt-3 text-4xl md:text-5xl font-extrabold tracking-tight text-foreground leading-tight">
        Bold domain-specific <br className="hidden md:block" />
        <span className="text-primary">headline here</span>
      </h1>
      <p className="mt-5 text-lg text-muted-foreground leading-relaxed max-w-lg">
        One compelling sentence about the value proposition.
      </p>
      <div className="mt-8 flex gap-3 flex-wrap">
        <a href="/contact" className="inline-flex items-center gap-2 bg-primary text-primary-foreground px-6 py-3 rounded-lg font-semibold hover:bg-primary/90 transition-colors">
          Primary CTA
        </a>
        <a href="/services" className="inline-flex items-center gap-2 border border-border text-foreground px-6 py-3 rounded-lg font-semibold hover:bg-muted transition-colors">
          Learn More
        </a>
      </div>
    </div>
    <div className="rounded-2xl overflow-hidden shadow-2xl">
      <img src="https://picsum.photos/seed/hero/800/600" alt="Hero" className="w-full h-full object-cover" />
    </div>
  </div>
</section>
```

### 2. Stats bar (trust signals)
```jsx
<section className="py-12 bg-muted/50 border-y border-border">
  <div className="max-w-7xl mx-auto px-6">
    <div className="grid grid-cols-2 md:grid-cols-4 gap-8 text-center">
      {[
        { value: '15+', label: 'Years Experience' },
        { value: '500+', label: 'Clients Served' },
        { value: '98%', label: 'Satisfaction Rate' },
        { value: '24/7', label: 'Support' },
      ].map(stat => (
        <div key={stat.label}>
          <div className="text-3xl font-bold text-primary">{stat.value}</div>
          <div className="text-sm text-muted-foreground mt-1">{stat.label}</div>
        </div>
      ))}
    </div>
  </div>
</section>
```

### 3. Services grid (3 columns)
```jsx
<section className="py-20 px-6">
  <div className="max-w-7xl mx-auto">
    <div className="text-center mb-12">
      <h2 className="text-3xl font-bold text-foreground">Our Services</h2>
      <p className="mt-3 text-muted-foreground max-w-xl mx-auto">What we do best.</p>
    </div>
    <div className="grid md:grid-cols-3 gap-6">
      {services.map(s => (
        <div key={s.title} className="rounded-xl border border-border bg-card p-6 hover:shadow-md transition-shadow">
          <div className="w-12 h-12 rounded-lg bg-primary/10 flex items-center justify-center mb-4">
            <s.Icon className="h-6 w-6 text-primary" />
          </div>
          <h3 className="font-semibold text-foreground mb-2">{s.title}</h3>
          <p className="text-sm text-muted-foreground leading-relaxed">{s.description}</p>
        </div>
      ))}
    </div>
  </div>
</section>
```

### 4. Testimonials (3-card row)
```jsx
<section className="py-20 px-6 bg-muted/30">
  <div className="max-w-7xl mx-auto">
    <h2 className="text-3xl font-bold text-center text-foreground mb-12">What Clients Say</h2>
    <div className="grid md:grid-cols-3 gap-6">
      {testimonials.map(t => (
        <div key={t.name} className="rounded-xl border border-border bg-card p-6">
          <div className="flex gap-1 mb-4">
            {[...Array(5)].map((_, i) => <span key={i} className="text-amber-400">★</span>)}
          </div>
          <p className="text-sm text-muted-foreground italic leading-relaxed">"{t.quote}"</p>
          <div className="flex items-center gap-3 mt-4">
            <img src={`https://i.pravatar.cc/40?u=${t.name}`} className="w-10 h-10 rounded-full" alt={t.name} />
            <div>
              <div className="text-sm font-semibold text-foreground">{t.name}</div>
              <div className="text-xs text-muted-foreground">{t.role}</div>
            </div>
          </div>
        </div>
      ))}
    </div>
  </div>
</section>
```

---

## Contact Page (always include)
```jsx
<section className="py-20 px-6">
  <div className="max-w-2xl mx-auto">
    <h1 className="text-4xl font-bold text-foreground mb-2">Get in Touch</h1>
    <p className="text-muted-foreground mb-8">We'll respond within 24 hours.</p>
    <form className="space-y-5">
      <div className="grid sm:grid-cols-2 gap-4">
        <div>
          <label className="text-sm font-medium text-foreground block mb-1.5">First Name</label>
          <input className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50" placeholder="John" />
        </div>
        <div>
          <label className="text-sm font-medium text-foreground block mb-1.5">Last Name</label>
          <input className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50" placeholder="Smith" />
        </div>
      </div>
      <div>
        <label className="text-sm font-medium text-foreground block mb-1.5">Email</label>
        <input type="email" className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50" placeholder="john@example.com" />
      </div>
      <div>
        <label className="text-sm font-medium text-foreground block mb-1.5">Message</label>
        <textarea rows={5} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50 resize-none" placeholder="How can we help?" />
      </div>
      <button type="submit" className="w-full bg-primary text-primary-foreground py-2.5 rounded-lg font-semibold hover:bg-primary/90 transition-colors">
        Send Message
      </button>
    </form>
  </div>
</section>
```

---

## Footer (shared across all pages)
```jsx
<footer className="bg-card border-t border-border py-12 px-6">
  <div className="max-w-7xl mx-auto grid md:grid-cols-4 gap-8">
    <div>
      <div className="font-bold text-lg text-foreground mb-2">BrandName</div>
      <p className="text-sm text-muted-foreground leading-relaxed">Short tagline or description in 1-2 sentences.</p>
    </div>
    {footerLinks.map(col => (
      <div key={col.title}>
        <div className="text-sm font-semibold text-foreground mb-3">{col.title}</div>
        <ul className="space-y-2">
          {col.links.map(l => (
            <li key={l.label}>
              <a href={l.href} className="text-sm text-muted-foreground hover:text-foreground transition-colors">{l.label}</a>
            </li>
          ))}
        </ul>
      </div>
    ))}
  </div>
  <div className="max-w-7xl mx-auto mt-8 pt-6 border-t border-border flex flex-col sm:flex-row justify-between items-center gap-2">
    <p className="text-sm text-muted-foreground">© 2025 BrandName. All rights reserved.</p>
    <div className="flex gap-4">
      <a href="#" className="text-sm text-muted-foreground hover:text-foreground">Privacy</a>
      <a href="#" className="text-sm text-muted-foreground hover:text-foreground">Terms</a>
    </div>
  </div>
</footer>
```

---

## Domain-Specific Rules

**Food / Restaurant**: Use warm amber/orange primary. Include menu page with categories + item cards. Add reservation form. Hero has food photography. Include opening hours in footer.

**Healthcare / Clinic**: Use clean blue/teal primary. Radius 0.5rem (trustworthy). Include services list, doctor/team grid, appointment booking form. No playful animations — subtle fade-in only.

**Law / Finance / Accounting (ACCA, CPA, etc.)**: Navy or deep blue primary. Serif heading font (Playfair Display or Merriweather). Include practice areas, attorney/advisor profiles, case results. Conservative layout, minimal animations.

**Real Estate**: Large property image cards with price badges. Map embed placeholder. Agent profile section. Radius 0.5rem. Property listing grid with filter bar.

**Fitness / Gym**: Bold orange or red primary. Full-screen hero with dark overlay on gym photo. Class schedule table. Trainer profiles. Pricing tiers.

**Events / Conference**: Dark hero with gradient overlay. Schedule/agenda timeline. Speaker grid. Countdown timer component. Ticket CTA prominent.

---

## 2025 Live-Feel Requirements (all consumer websites)

Every consumer website MUST implement these — they are the difference between a static
template and a premium $50K+ agency build:

### Mandatory animated elements
1. **Sticky header scroll transition** — transparent on load, frosted-glass (`bg-background/90 backdrop-blur-md border-b`) after 20px scroll.
2. **Hero entry sequence** — badge → H1 → subtitle → CTAs animate in cascading with `motion.div initial/animate` (not whileInView), delays 0.1s / 0.25s / 0.5s / 0.65s.
3. **Animated stat counters** — any numeric stat section uses `AnimatedCounter` component (count from 0 on viewport entry using `useInView`).
4. **Stagger card reveals** — every card grid / service list uses `containerVariants` + `itemVariants` with `staggerChildren: 0.08` on `whileInView`.
5. **Logo / trust strip marquee** — if there's a partner or press logo row, use CSS infinite marquee (`@keyframes marquee`, `translateX(-50%)`).
6. **Ambient hero motion** — hero background has at least ONE slow-moving element: floating orb `(blur-[120px] animated 12-18s)`, dot-grid overlay, or grain noise.
7. **H2 clip-path reveal** — at least 2 section headings use `overflow-hidden` + inner element `initial:{y:'100%'} whileInView:{y:'0%'}`.
8. **CTA hover shimmer** — primary button has a CSS shimmer sweep on hover (pseudo-element `translateX(-100% → 100%)` on hover).

### Section background must alternate
Never two consecutive `bg-background` sections. Rotate: `bg-background` → `bg-muted/40` → `bg-foreground text-background` (for testimonials/CTA) → `bg-background`.

### Typography must be expressive
- H1: `text-5xl md:text-7xl font-bold` minimum
- Section H2: `text-3xl md:text-5xl font-bold`
- Eyebrow labels: `text-xs font-semibold uppercase tracking-widest text-primary`
- Import at least one expressive heading font (Fraunces, Playfair, DM Serif Display, Space Grotesk)

### Image treatment
- Hero image always has a gradient scrim over it — NEVER raw photo with light overlay
- All content images use `object-cover` inside a fixed `aspect-[...]` container
- No `picsum.photos` — use real Unsplash URLs with `?auto=format&fit=crop&w=1200&q=80`
