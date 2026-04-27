# Website & Landing Page Architecture Patterns (Tailwind + shadcn/ui)

## Layout Variants

### Variant A — Marketing Website (Vercel/Stripe style)
Best for: SaaS landing, Product launches, Agency sites
```
┌──────────────────────────────────────────────┐
│  Logo        Nav Items              CTA Btn  │
├──────────────────────────────────────────────┤
│     HERO: Large headline, subtitle,          │
│     CTA button, gradient or dark bg          │
├──────────────────────────────────────────────┤
│     SOCIAL PROOF: Logo bar or stats row      │
├──────────────────────────────────────────────┤
│     FEATURES: 3-column card grid             │
├──────────────────────────────────────────────┤
│     HOW IT WORKS: Step-by-step (1-2-3)       │
├──────────────────────────────────────────────┤
│     TESTIMONIALS: Card carousel or grid      │
├──────────────────────────────────────────────┤
│     PRICING: 3-tier comparison               │
├──────────────────────────────────────────────┤
│     FAQ: Accordion sections                  │
├──────────────────────────────────────────────┤
│     CTA: Final call-to-action banner         │
├──────────────────────────────────────────────┤
│     FOOTER: Multi-column links               │
└──────────────────────────────────────────────┘
```

### Variant B — Portfolio/Creative Site
```
Nav (minimal) → Hero (full-screen) → Work Grid → About (split) → Contact
```

### Variant C — Blog/Content Site
```
Nav → Featured Post (large) → Grid (3 cols) → Categories → Newsletter → Footer
```

---

## Section Component Patterns (Tailwind)

### Hero Section
```jsx
'use client'
import { ArrowRight } from 'lucide-react';

export function HeroSection() {
  return (
    <section className="pt-32 pb-20 px-6 text-center bg-gradient-to-b from-background to-muted/50">
      <div className="max-w-4xl mx-auto">
        <span className="inline-flex items-center rounded-full bg-primary/10 px-4 py-1.5 text-sm font-medium text-primary mb-6">
          ✨ Now in public beta
        </span>
        <h1 className="text-4xl md:text-5xl lg:text-6xl font-extrabold tracking-tight text-foreground leading-tight">
          Build something{' '}
          <span className="bg-gradient-to-r from-primary to-primary/60 bg-clip-text text-transparent">
            extraordinary
          </span>
        </h1>
        <p className="mt-6 text-lg text-muted-foreground max-w-2xl mx-auto leading-relaxed">
          The modern platform for teams who ship fast.
          Beautiful by default. Powerful when you need it.
        </p>
        <div className="flex gap-3 justify-center mt-8">
          <a href="/signup" className="inline-flex items-center gap-2 bg-primary text-primary-foreground px-6 py-3 rounded-full font-medium hover:bg-primary/90 transition-all">
            Get Started Free <ArrowRight size={16} />
          </a>
          <a href="/demo" className="inline-flex items-center gap-2 border border-border text-foreground px-6 py-3 rounded-full font-medium hover:bg-muted transition-all">
            Watch Demo
          </a>
        </div>
        <div className="flex justify-center gap-8 mt-12 text-sm text-muted-foreground">
          <div><strong className="text-foreground">10K+</strong> Active Users</div>
          <div className="w-px bg-border" />
          <div><strong className="text-foreground">99.9%</strong> Uptime</div>
          <div className="w-px bg-border" />
          <div><strong className="text-foreground">4.9★</strong> Rating</div>
        </div>
      </div>
    </section>
  );
}
export default HeroSection;
```

### Feature Cards Section
```jsx
'use client'
import { Zap, Shield, Globe, BarChart3, Users, Lock } from 'lucide-react';

const features = [
  { icon: Zap, title: 'Lightning Fast', desc: 'Built on modern infrastructure that delivers sub-100ms response times globally.' },
  { icon: Shield, title: 'Enterprise Security', desc: 'SOC 2 compliant with end-to-end encryption and role-based access control.' },
  { icon: Globe, title: 'Global Scale', desc: 'Deploy to 30+ regions worldwide with automatic failover and load balancing.' },
  { icon: BarChart3, title: 'Real-time Analytics', desc: 'Track every metric that matters with customizable dashboards and alerts.' },
  { icon: Users, title: 'Team Collaboration', desc: 'Built for teams with real-time editing, comments, and approval workflows.' },
  { icon: Lock, title: 'Data Privacy', desc: 'GDPR compliant with data residency options and automated compliance tools.' },
];

export function FeaturesSection() {
  return (
    <section className="py-20 px-6">
      <div className="max-w-6xl mx-auto">
        <div className="text-center mb-12">
          <span className="text-xs font-semibold uppercase tracking-widest text-primary">Features</span>
          <h2 className="text-3xl font-bold text-foreground mt-2">Everything you need to scale</h2>
          <p className="text-muted-foreground mt-3">Powerful features that grow with your business</p>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {features.map((f, i) => (
            <div key={i} className="bg-card border border-border rounded-xl p-6 hover:shadow-lg hover:-translate-y-1 transition-all duration-300">
              <div className="w-10 h-10 rounded-lg bg-primary/10 flex items-center justify-center text-primary mb-4">
                <f.icon size={20} />
              </div>
              <h3 className="text-lg font-semibold text-foreground">{f.title}</h3>
              <p className="text-muted-foreground mt-2 text-sm leading-relaxed">{f.desc}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
export default FeaturesSection;
```

### Testimonials Section (data pattern)
```jsx
const testimonials = [
  {
    quote: "This platform transformed how our team collaborates. We shipped 3x faster in the first month.",
    name: "Sarah Chen",
    role: "CTO at TechFlow",
  },
  {
    quote: "The best developer experience I've ever had. Clean APIs, great documentation, incredible support.",
    name: "Marcus Johnson",
    role: "Lead Engineer at ScaleUp",
  },
  {
    quote: "We migrated from our legacy system in two weeks. The ROI was immediate and measurable.",
    name: "Elena Rodriguez",
    role: "VP Engineering at DataCore",
  },
];
// Use className="bg-card border rounded-xl p-6" for cards
// Use className="text-muted-foreground" for quotes
// Use initials in a className="w-10 h-10 rounded-full bg-primary/10 text-primary" circle
```

### Pricing Section (data pattern)
```jsx
const plans = [
  { name: 'Starter', price: 0, features: ['Up to 3 projects', '1GB storage', 'Community support'], popular: false },
  { name: 'Professional', price: 29, features: ['Unlimited projects', '50GB storage', 'Priority support', 'Advanced analytics'], popular: true },
  { name: 'Enterprise', price: 99, features: ['Everything in Pro', 'Unlimited storage', 'Dedicated support', 'SSO & SAML'], popular: false },
];
// Popular card: className="border-primary bg-primary/5 shadow-lg scale-105"
// Normal card: className="bg-card border border-border rounded-xl p-6"
// Price: className="text-4xl font-bold text-foreground"
```

### FAQ Accordion Pattern
```jsx
'use client'
import { useState } from 'react';
import { ChevronDown } from 'lucide-react';

const faqs = [
  { q: 'How do I get started?', a: 'Sign up for a free account and follow our quick-start guide.' },
  { q: 'Can I cancel anytime?', a: 'Yes, cancel your subscription at any time. No hidden fees.' },
  { q: 'Do you offer a free trial?', a: 'All paid plans include a 14-day free trial with full access.' },
];

export function FAQSection() {
  const [openIndex, setOpenIndex] = useState(null);
  return (
    <section className="py-20 px-6">
      <div className="max-w-3xl mx-auto">
        <h2 className="text-3xl font-bold text-foreground text-center mb-12">Frequently Asked Questions</h2>
        <div className="space-y-3">
          {faqs.map((faq, i) => (
            <div key={i} className="border border-border rounded-lg overflow-hidden">
              <button
                onClick={() => setOpenIndex(openIndex === i ? null : i)}
                className="w-full flex justify-between items-center p-4 text-left text-foreground font-medium hover:bg-muted/50 transition-colors"
              >
                <span>{faq.q}</span>
                <ChevronDown size={20} className={`transition-transform duration-200 ${openIndex === i ? 'rotate-180' : ''}`} />
              </button>
              {openIndex === i && (
                <p className="px-4 pb-4 text-muted-foreground text-sm leading-relaxed">{faq.a}</p>
              )}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
export default FAQSection;
```

---

## Tailwind Utility Patterns

### Buttons
```jsx
// Primary button
<button className="inline-flex items-center gap-2 bg-primary text-primary-foreground px-6 py-3 rounded-full font-medium hover:bg-primary/90 transition-all">
  Get Started <ArrowRight size={16} />
</button>

// Secondary/outline button
<button className="inline-flex items-center gap-2 border border-border text-foreground px-6 py-3 rounded-full font-medium hover:bg-muted transition-all">
  Learn More
</button>

// Ghost button
<button className="text-muted-foreground hover:text-foreground transition-colors">
  Skip for now
</button>
```

### Section Layout
```jsx
{/* Standard section wrapper — use design system tokens */}
import { ds } from '@/lib/design-system'

<section className={ds.sectionSpacing}>
  <div className={ds.maxWidth}>
    {/* Section header */}
    <div className="text-center mb-12">
      <span className="text-xs font-semibold uppercase tracking-widest text-primary">Label</span>
      <h2 className={ds.heading.h2 + " text-foreground mt-2"}>Section Title</h2>
      <p className="text-muted-foreground mt-3 max-w-2xl mx-auto">Description text</p>
    </div>
    {/* Content grid */}
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
      {/* Cards */}
    </div>
  </div>
</section>
```

---

## Design System Integration

ALL section components must import and use the shared design system:

```jsx
'use client'
import { motion } from 'framer-motion'
import { ds } from '@/lib/design-system'

export default function FeaturesSection() {
  return (
    <section className={ds.sectionSpacing}>
      <div className={ds.maxWidth}>
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          className="text-center mb-16"
        >
          <h2 className={ds.heading.h2}>Features</h2>
        </motion.div>
        <motion.div
          variants={ds.stagger.container}
          initial="initial"
          whileInView="animate"
          viewport={{ once: true }}
          className="grid grid-cols-1 md:grid-cols-3 gap-6"
        >
          {features.map((f, i) => (
            <motion.div key={i} variants={ds.stagger.child}>
              <div className={ds.card + " p-6 h-full"}>
                {/* feature content */}
              </div>
            </motion.div>
          ))}
        </motion.div>
      </div>
    </section>
  )
}
```

## Animation Patterns (framer-motion)

```jsx
// Page-level transition
<motion.div {...ds.pageAnimation}>

// Scroll reveal (single element)
<motion.div
  initial={{ opacity: 0, y: 20 }}
  whileInView={{ opacity: 1, y: 0 }}
  viewport={{ once: true }}
  transition={{ duration: 0.5 }}
>

// Staggered list
<motion.div variants={ds.stagger.container} initial="initial" whileInView="animate" viewport={{ once: true }}>
  {items.map((item, i) => (
    <motion.div key={i} variants={ds.stagger.child}>
  ))}
</motion.div>

// Card hover
<motion.div {...ds.cardHover}>
```

## CRITICAL RULES
1. **Every section** must have `'use client'` at the top
2. **Every section** must import `{ ds }` from `@/lib/design-system`
3. **Every section** must use `ds.sectionSpacing` and `ds.maxWidth`
4. **Every card** must use `ds.card` for consistent styling
5. **Every section** must have framer-motion scroll animations
6. **No hardcoded colors** — use Tailwind utility classes only
7. **Responsive** — every section must use sm: md: lg: breakpoints
8. **Avatars** must use `https://i.pravatar.cc/150?u=uniquestring`
9. **Social icons** — NEVER import from lucide-react, use inline SVG

---

## 2025 Live-Feel Patterns (mandatory for all landing pages)

### Pattern 1 — Cascading Hero Entry (replace static hero immediately)
```jsx
// Badge: delay 0.1s
<motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1, duration: 0.55 }}>
  <Badge>Announcement text</Badge>
</motion.div>

// H1: clip-path slide-up, delay 0.25s
<div className="overflow-hidden">
  <motion.h1 initial={{ y: '100%' }} animate={{ y: '0%' }} transition={{ delay: 0.25, duration: 0.75, ease: [0.33, 1, 0.68, 1] }}>
    Headline here
  </motion.h1>
</div>

// Subtitle: delay 0.5s
<motion.p initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.5, duration: 0.7 }}>

// CTAs: delay 0.65s
<motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.65, duration: 0.6 }}>
```

### Pattern 2 — Stagger Grid Reveal (replace all static card grids)
```jsx
const C = { hidden: {}, visible: { transition: { staggerChildren: 0.08, delayChildren: 0.1 } } }
const I = { hidden: { opacity: 0, y: 32, scale: 0.97 }, visible: { opacity: 1, y: 0, scale: 1, transition: { duration: 0.5, ease: [0.25, 0.46, 0.45, 0.94] } } }

<motion.div variants={C} initial="hidden" whileInView="visible" viewport={{ once: true, amount: 0.15 }} className="grid ...">
  {items.map((item, i) => <motion.div key={i} variants={I}>...</motion.div>)}
</motion.div>
```

### Pattern 3 — H2 Clip Reveal (use on 2-3 key section headings)
```jsx
<div className="overflow-hidden">
  <motion.h2 initial={{ y: '100%' }} whileInView={{ y: '0%' }} viewport={{ once: true }}
    transition={{ duration: 0.65, ease: [0.33, 1, 0.68, 1] }} className="text-4xl font-bold">
    Section Headline
  </motion.h2>
</div>
```

### Pattern 4 — Ambient Orbs (drop inside any hero section)
```jsx
<div className="absolute inset-0 overflow-hidden pointer-events-none" aria-hidden>
  <div className="absolute top-1/4 -left-20 w-96 h-96 bg-primary/20 rounded-full blur-3xl"
       style={{ animation: 'orb1 14s ease-in-out infinite alternate' }} />
  <div className="absolute bottom-0 right-0 w-80 h-80 bg-accent/15 rounded-full blur-3xl"
       style={{ animation: 'orb2 18s ease-in-out infinite alternate-reverse' }} />
  <style>{`
    @keyframes orb1{from{transform:translate(0,0)scale(1)}to{transform:translate(50px,30px)scale(1.12)}}
    @keyframes orb2{from{transform:translate(0,0)scale(1)}to{transform:translate(-40px,-25px)scale(1.08)}}
  `}</style>
</div>
```

### Pattern 5 — Animated Counter (for any stat number)
```jsx
function Counter({ target, suffix='', duration=1800 }) {
  const ref = useRef(null)
  const inView = useInView(ref, { once: true, margin: '-10% 0px' })
  const [v, setV] = useState(0)
  useEffect(() => {
    if (!inView) return
    let s = null, n = 0
    const step = (ts) => {
      if (!s) s = ts
      const p = Math.min((ts - s) / duration, 1)
      setV(Math.round((1 - Math.pow(1 - p, 3)) * target))
      if (p < 1) n = requestAnimationFrame(step)
    }
    n = requestAnimationFrame(step)
    return () => cancelAnimationFrame(n)
  }, [inView])
  return <span ref={ref}>{v.toLocaleString()}{suffix}</span>
}
```

### Pattern 6 — Marquee Logo Strip (for trust/partner rows)
```jsx
<div className="overflow-hidden [mask-image:linear-gradient(to_right,transparent,white_8%,white_92%,transparent)]">
  <div className="flex gap-10 w-max" style={{ animation: 'marquee 28s linear infinite' }}>
    {[...logos, ...logos].map((l, i) => <img key={i} src={l} className="h-8 opacity-50 hover:opacity-100 grayscale hover:grayscale-0 transition-all shrink-0" />)}
  </div>
  <style>{`@keyframes marquee{from{transform:translateX(0)}to{transform:translateX(-50%)}}`}</style>
</div>
```

### Pattern 7 — Scroll Transition Header
```jsx
const [scrolled, setScrolled] = useState(false)
useEffect(() => {
  const fn = () => setScrolled(window.scrollY > 20)
  window.addEventListener('scroll', fn, { passive: true })
  return () => window.removeEventListener('scroll', fn)
}, [])

<header className={`fixed inset-x-0 top-0 z-50 transition-all duration-500 ${
  scrolled ? 'bg-background/90 backdrop-blur-md border-b border-border shadow-sm' : 'bg-transparent'
}`}>
```

