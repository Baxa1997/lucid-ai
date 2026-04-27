# Skill: Landing Page Sections (Next.js + shadcn/ui + framer-motion)

## Every section MUST follow this structure
```jsx
'use client'
import { motion } from 'framer-motion'
import { ds } from '@/lib/design-system'

export default function SectionName() {
  return (
    <section className={ds.sectionSpacing || "py-20 md:py-28"}>
      <div className={ds.maxWidth || "container mx-auto px-4 md:px-6 max-w-7xl"}>
        {/* section content */}
      </div>
    </section>
  )
}
```

## Hero Section
```jsx
'use client'
import { Button } from '@/components/ui/Button'
import { motion } from 'framer-motion'
import { ArrowRight, Play } from 'lucide-react'

export default function HeroSection() {
  return (
    <section className="relative overflow-hidden py-24 md:py-32 lg:py-40">
      <div className="absolute inset-0 bg-gradient-to-br from-primary/5 via-transparent to-accent/5" />
      <div className="container relative mx-auto px-4 md:px-6 max-w-7xl">
        <motion.div
          initial={{ opacity: 0, y: 30 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6 }}
          className="mx-auto max-w-3xl text-center"
        >
          <div className="mb-6 inline-flex items-center gap-2 rounded-full border px-4 py-1.5 text-sm">
            <span className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
            Announcement text here
          </div>
          <h1 className="text-4xl font-bold tracking-tight sm:text-5xl md:text-6xl lg:text-7xl">
            Compelling <span className="text-primary">headline</span> here
          </h1>
          <p className="mt-6 text-lg text-muted-foreground md:text-xl max-w-2xl mx-auto">
            Value proposition subheadline — one or two sentences max.
          </p>
          <div className="mt-10 flex flex-col sm:flex-row items-center justify-center gap-4">
            <Button size="lg" className="gap-2 text-lg px-8">
              Get Started <ArrowRight className="h-5 w-5" />
            </Button>
            <Button variant="outline" size="lg" className="gap-2 text-lg px-8">
              <Play className="h-5 w-5" /> Watch Demo
            </Button>
          </div>
        </motion.div>
      </div>
    </section>
  )
}
```

## Features Grid Section
```jsx
'use client'
import { motion } from 'framer-motion'
import { Card, CardContent } from '@/components/ui/Card'

const features = [
  { title: "Feature 1", description: "Description", icon: IconComponent },
  // 6-8 features
]

const container = { hidden: {}, show: { transition: { staggerChildren: 0.1 } } }
const item = { hidden: { opacity: 0, y: 20 }, show: { opacity: 1, y: 0 } }

export default function FeaturesSection() {
  return (
    <section className="py-20 md:py-28">
      <div className="container mx-auto px-4 md:px-6 max-w-7xl">
        <div className="mx-auto max-w-2xl text-center mb-16">
          <h2 className="text-3xl font-bold sm:text-4xl">Section Headline</h2>
          <p className="mt-4 text-lg text-muted-foreground">Supporting text</p>
        </div>
        <motion.div variants={container} initial="hidden" whileInView="show" viewport={{ once: true }}
          className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6"
        >
          {features.map((f, i) => (
            <motion.div key={i} variants={item}>
              <Card className="h-full hover:shadow-lg transition-all duration-300 hover:-translate-y-1">
                <CardContent className="p-6">
                  <div className="mb-4 inline-flex h-12 w-12 items-center justify-center rounded-lg bg-primary/10">
                    <f.icon className="h-6 w-6 text-primary" />
                  </div>
                  <h3 className="text-lg font-semibold">{f.title}</h3>
                  <p className="mt-2 text-muted-foreground">{f.description}</p>
                </CardContent>
              </Card>
            </motion.div>
          ))}
        </motion.div>
      </div>
    </section>
  )
}
```

## Pricing Section
```jsx
'use client'
import { motion } from 'framer-motion'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Check } from 'lucide-react'
import { Badge } from '@/components/ui/Badge'

const tiers = [
  { name: 'Starter', price: '$29', period: '/mo', features: ['Feature 1', 'Feature 2'], cta: 'Start Free Trial' },
  { name: 'Pro', price: '$79', period: '/mo', popular: true, features: ['Everything in Starter', 'Feature 3'], cta: 'Start Free Trial' },
  { name: 'Enterprise', price: 'Custom', period: '', features: ['Everything in Pro', 'Feature 4'], cta: 'Contact Sales' },
]

export default function PricingSection() {
  return (
    <section className="py-20 md:py-28 bg-muted/30">
      <div className="container mx-auto px-4 md:px-6 max-w-7xl">
        <div className="mx-auto max-w-2xl text-center mb-16">
          <h2 className="text-3xl font-bold sm:text-4xl">Simple, Transparent Pricing</h2>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-8 max-w-5xl mx-auto">
          {tiers.map((tier, i) => (
            <motion.div key={i} initial={{ opacity: 0, y: 20 }} whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }} transition={{ delay: i * 0.1 }}
            >
              <Card className={`h-full relative ${tier.popular ? 'border-primary shadow-lg scale-105' : ''}`}>
                {tier.popular && <Badge className="absolute -top-3 left-1/2 -translate-x-1/2">Most Popular</Badge>}
                <CardHeader>
                  <CardTitle>{tier.name}</CardTitle>
                  <div className="mt-4">
                    <span className="text-4xl font-bold">{tier.price}</span>
                    <span className="text-muted-foreground">{tier.period}</span>
                  </div>
                </CardHeader>
                <CardContent>
                  <ul className="space-y-3 mb-8">
                    {tier.features.map((f, j) => (
                      <li key={j} className="flex items-center gap-2">
                        <Check className="h-4 w-4 text-primary" />
                        <span className="text-sm">{f}</span>
                      </li>
                    ))}
                  </ul>
                  <Button className="w-full" variant={tier.popular ? 'default' : 'outline'}>{tier.cta}</Button>
                </CardContent>
              </Card>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  )
}
```

## Testimonials Section
```jsx
'use client'
import { motion } from 'framer-motion'
import { Card, CardContent } from '@/components/ui/Card'
import { Star } from 'lucide-react'

const testimonials = [
  { quote: "...", name: "Jane Cooper", title: "CEO, Acme Inc", avatar: "https://i.pravatar.cc/150?u=jane" },
  { quote: "...", name: "John Smith", title: "CTO, TechCorp", avatar: "https://i.pravatar.cc/150?u=john" },
  { quote: "...", name: "Sarah Wilson", title: "VP Product, StartupXY", avatar: "https://i.pravatar.cc/150?u=sarah" },
]
```

## FAQ Section (accordion)
```jsx
'use client'
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion'

const faqs = [
  { q: "Question?", a: "Answer." },
]

export default function FAQSection() {
  return (
    <section className="py-20 md:py-28">
      <div className="container mx-auto px-4 md:px-6 max-w-3xl">
        <h2 className="text-3xl font-bold text-center mb-12">Frequently Asked Questions</h2>
        <Accordion type="single" collapsible className="w-full">
          {faqs.map((faq, i) => (
            <AccordionItem key={i} value={`item-${i}`}>
              <AccordionTrigger>{faq.q}</AccordionTrigger>
              <AccordionContent>{faq.a}</AccordionContent>
            </AccordionItem>
          ))}
        </Accordion>
      </div>
    </section>
  )
}
```

## Registration / Contact Form Section (with background image)

This is the correct pattern for a form that sits over a background photo (registration,
contact, booking, lead-capture sections). Two critical requirements:
1. The `<section>` MUST have `min-h-[700px]` (or similar) so the photo fills the space.
2. Dropdowns MUST use shadcn `Select` — NEVER native `<select>`.

```jsx
'use client'
import { useState } from 'react'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'

export default function RegisterSection() {
  const [country, setCountry] = useState('')
  const [stage, setStage] = useState('')

  return (
    // MUST have min-h so the background photo fills the section
    <section className="relative min-h-[700px] flex items-center overflow-hidden">
      {/* Background photo layer */}
      <img
        src="https://images.unsplash.com/photo-1522071820081-009f0129c71c?auto=format&fit=crop&w=1600&q=80"
        alt=""
        className="absolute inset-0 w-full h-full object-cover"
      />
      {/* Dark scrim for readability */}
      <div className="absolute inset-0 bg-gradient-to-r from-foreground/80 via-foreground/50 to-foreground/20" />

      {/* Content */}
      <div className="relative z-10 container mx-auto px-4 md:px-6 max-w-7xl grid md:grid-cols-2 gap-16 items-center">
        {/* Left: copy */}
        <div className="text-primary-foreground">
          <p className="text-sm font-semibold uppercase tracking-widest text-accent mb-4">Ready to begin?</p>
          <h2 className="text-4xl md:text-5xl font-bold leading-tight mb-6">
            Register your interest in under 3 minutes.
          </h2>
          <ul className="space-y-3 text-primary-foreground/80">
            {['No commitment required', 'Personalised qualification roadmap', 'Information on exam exemptions', 'Guidance from an advisor'].map((item, i) => (
              <li key={i} className="flex items-center gap-3">
                <span className="w-2 h-2 rounded-full bg-accent shrink-0" />
                {item}
              </li>
            ))}
          </ul>
        </div>

        {/* Right: form card — OPAQUE bg-card, never glassmorphism */}
        <div className="bg-card text-card-foreground rounded-2xl shadow-2xl p-8 space-y-5">
          <div>
            <h3 className="text-xl font-bold">Register Your Interest</h3>
            <p className="text-sm text-muted-foreground mt-1">Free. No commitment. Takes 3 minutes.</p>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label htmlFor="firstName">First Name <span className="text-destructive">*</span></Label>
              <Input id="firstName" placeholder="Jane" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="lastName">Last Name <span className="text-destructive">*</span></Label>
              <Input id="lastName" placeholder="Smith" />
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="email">Email Address <span className="text-destructive">*</span></Label>
            <Input id="email" type="email" placeholder="jane.smith@email.com" />
          </div>

          {/* shadcn Select — NEVER use native <select> */}
          <div className="space-y-1.5">
            <Label>Country <span className="text-destructive">*</span></Label>
            <Select value={country} onValueChange={setCountry}>
              <SelectTrigger>
                <SelectValue placeholder="Select your country" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="us">United States</SelectItem>
                <SelectItem value="uk">United Kingdom</SelectItem>
                <SelectItem value="ca">Canada</SelectItem>
                <SelectItem value="au">Australia</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {/* Another shadcn Select — NEVER use native <select> */}
          <div className="space-y-1.5">
            <Label>Where are you in your journey?</Label>
            <Select value={stage} onValueChange={setStage}>
              <SelectTrigger>
                <SelectValue placeholder="Select your stage" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="exploring">Just exploring</SelectItem>
                <SelectItem value="ready">Ready to apply</SelectItem>
                <SelectItem value="student">Current student</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <Button className="w-full" size="lg">Apply Now →</Button>
          <p className="text-xs text-muted-foreground text-center">
            By submitting, you agree to our Privacy Policy. We&apos;ll never share your data.
          </p>
        </div>
      </div>
    </section>
  )
}
```

**Key rules for this pattern:**
- `<section>` always has `min-h-[600px]` or larger so the photo fills vertically.
- `<img>` is `absolute inset-0 w-full h-full object-cover` — NOT `bg-[url(...)]` on the section.
- The form card is always `bg-card` (opaque) — NEVER `backdrop-blur` / `bg-card/80`.
- Every dropdown uses shadcn `Select` with `SelectTrigger` + `SelectContent` + `SelectItem`.
- Native `<select>` is **BANNED** — it breaks visual consistency with the rest of the UI.

## RULES
- Every section: 'use client' at top
- Every section: responsive (py-20 md:py-28, grid-cols-1 md:grid-cols-2 lg:grid-cols-3)
- Every section: container mx-auto px-4 md:px-6 max-w-7xl
- Every section: motion animation (whileInView for scroll reveal)
- NEVER hardcode colors — use bg-primary, text-foreground, bg-muted, etc.
- Avatars: https://i.pravatar.cc/150?u=uniquestring

## CRITICAL: ICON SAFETY
**lucide-react does NOT export brand/social icons.** These DO NOT EXIST and will CRASH the build:
- ❌ Facebook, Instagram, Twitter, Linkedin, Youtube, Tiktok, Pinterest, Github (as brand icon)
- ❌ Any social media brand icon from lucide-react

**For social icons, ALWAYS use inline SVG:**
```jsx
// ✅ CORRECT — inline SVG for social icons
const FacebookIcon = ({ className }) => (
  <svg className={className} viewBox="0 0 24 24" fill="currentColor">
    <path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/>
  </svg>
)

const InstagramIcon = ({ className }) => (
  <svg className={className} viewBox="0 0 24 24" fill="currentColor">
    <path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zM12 0C8.741 0 8.333.014 7.053.072 2.695.272.273 2.69.073 7.052.014 8.333 0 8.741 0 12c0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98C8.333 23.986 8.741 24 12 24c3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98C15.668.014 15.259 0 12 0zm0 5.838a6.162 6.162 0 100 12.324 6.162 6.162 0 000-12.324zM12 16a4 4 0 110-8 4 4 0 010 8zm6.406-11.845a1.44 1.44 0 100 2.881 1.44 1.44 0 000-2.881z"/>
  </svg>
)
```

**Safe lucide-react icons for UI:** Mail, Phone, MapPin, Clock, Star, Heart, ArrowRight, Play, Check, ChevronDown, Menu, X, ExternalLink, Globe, Send

---

## Animated Stats / Counter Section (2025 standard)

Numbers that count up from 0 when scrolled into view. This is **mandatory** for any
section displaying metrics, client counts, years, ratings, or other numerical trust signals.

```jsx
'use client'
import { useEffect, useRef, useState } from 'react'
import { useInView } from 'framer-motion'
import { motion } from 'framer-motion'

function AnimatedCounter({ target, suffix = '', prefix = '', duration = 1800, decimals = 0 }) {
  const ref = useRef(null)
  const inView = useInView(ref, { once: true, margin: '-10% 0px' })
  const [display, setDisplay] = useState(0)

  useEffect(() => {
    if (!inView) return
    let startTime = null
    const step = (timestamp) => {
      if (!startTime) startTime = timestamp
      const progress = Math.min((timestamp - startTime) / duration, 1)
      const eased = 1 - Math.pow(1 - progress, 3) // ease-out cubic
      setDisplay(parseFloat((eased * target).toFixed(decimals)))
      if (progress < 1) requestAnimationFrame(step)
    }
    requestAnimationFrame(step)
  }, [inView, target, duration, decimals])

  return (
    <span ref={ref}>
      {prefix}{decimals > 0 ? display.toFixed(decimals) : display.toLocaleString()}{suffix}
    </span>
  )
}

const stats = [
  { value: 180,  suffix: '+',  label: 'Countries Recognise the Qualification', prefix: '' },
  { value: 500,  suffix: '+',  label: 'Prometric Exam Centres Worldwide',       prefix: '' },
  { value: 98,   suffix: '%',  label: 'Student Satisfaction Rate',               prefix: '' },
  { value: 4.9,  suffix: '★', label: 'Average Tutor Rating',                    prefix: '', decimals: 1 },
]

export default function StatsSection() {
  return (
    <section className="py-16 md:py-20 bg-muted/40 border-y border-border">
      <div className="container mx-auto px-4 md:px-6 max-w-6xl">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-8 text-center">
          {stats.map((stat, i) => (
            <motion.div
              key={i}
              initial={{ opacity: 0, y: 24 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, amount: 0.4 }}
              transition={{ delay: i * 0.1, duration: 0.5 }}
            >
              <div className="text-4xl md:text-5xl font-bold text-primary">
                <AnimatedCounter
                  target={stat.value}
                  suffix={stat.suffix}
                  prefix={stat.prefix}
                  decimals={stat.decimals ?? 0}
                />
              </div>
              <p className="mt-2 text-sm text-muted-foreground leading-snug max-w-[140px] mx-auto">
                {stat.label}
              </p>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  )
}
```

---

## Horizontal Marquee Strip (logos / quotes / stats)

A continuously-scrolling strip that makes the page feel alive between sections.
Use for: partner logos, press mentions, client quotes, product categories.

```jsx
'use client'
import { useRef } from 'react'

const logos = [
  { name: 'Deloitte',   src: 'https://upload.wikimedia.org/wikipedia/commons/5/56/Deloitte.svg' },
  { name: 'PwC',        src: 'https://upload.wikimedia.org/wikipedia/commons/0/05/PricewaterhouseCoopers_Logo.svg' },
  { name: 'KPMG',       src: 'https://upload.wikimedia.org/wikipedia/commons/9/9d/KPMG_logo.svg' },
  { name: 'EY',         src: 'https://upload.wikimedia.org/wikipedia/commons/3/34/EY_logo_2019.svg' },
  { name: 'Accenture',  src: 'https://upload.wikimedia.org/wikipedia/commons/c/cd/Accenture.svg' },
  { name: 'McKinsey',   src: 'https://upload.wikimedia.org/wikipedia/commons/3/37/McKinsey_and_Company_Logo.svg' },
]

export default function LogoMarquee() {
  return (
    <section className="py-12 border-y border-border bg-background overflow-hidden">
      <div className="container mx-auto px-4 mb-6 text-center">
        <p className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">
          Trusted by professionals at
        </p>
      </div>

      {/* Fade edges */}
      <div className="relative [mask-image:linear-gradient(to_right,transparent,white_8%,white_92%,transparent)]">
        <div
          className="flex gap-12 w-max items-center"
          style={{ animation: 'marquee 32s linear infinite' }}
        >
          {[...logos, ...logos].map((logo, i) => (
            <div key={i} className="shrink-0 flex items-center justify-center h-10 w-28 grayscale opacity-60 hover:opacity-100 hover:grayscale-0 transition-all duration-300">
              <img src={logo.src} alt={logo.name} className="max-h-8 max-w-full object-contain" />
            </div>
          ))}
        </div>
      </div>

      <style>{`
        @keyframes marquee {
          from { transform: translateX(0) }
          to   { transform: translateX(-50%) }
        }
      `}</style>
    </section>
  )
}
```

For text/quote marquees (testimonial snippets):
```jsx
const quotes = [
  '"Best decision of my career"',
  '"Recognised in 180+ countries"',
  '"Passed first attempt"',
  '"Life-changing qualification"',
  '"World-class support"',
]

// Wrap each in a pill: className="px-5 py-2 rounded-full border border-border bg-card text-sm text-muted-foreground shrink-0"
```

---

## Bento Feature Grid (2025 editorial style)

Asymmetric grid where tiles have different spans — one hero tile, smaller supporting tiles.
This is the top 2025 pattern replacing the generic 3-column icon grid.

```jsx
'use client'
import { motion } from 'framer-motion'
import { Shield, Globe, Clock, Award, BookOpen, Users } from 'lucide-react'

const tiles = [
  {
    id: 'hero',
    colSpan: 'md:col-span-2',
    rowSpan: 'md:row-span-2',
    bg: 'bg-primary text-primary-foreground',
    icon: Award,
    title: 'Globally Recognised',
    body: 'The ACCA qualification is accepted by 7,500+ employers in 180+ countries — from Big Four to FTSE 100.',
    image: 'https://images.unsplash.com/photo-1454165804606-c3d57bc86b40?auto=format&fit=crop&w=800&q=80',
  },
  {
    id: 'a',
    colSpan: 'md:col-span-1',
    bg: 'bg-card border border-border',
    icon: Globe,
    title: '180+ Countries',
    body: 'Exam centres on every continent.',
  },
  {
    id: 'b',
    colSpan: 'md:col-span-1',
    bg: 'bg-accent/10',
    icon: Clock,
    title: 'Flexible Study',
    body: 'Self-paced or structured — fit around your life.',
  },
  {
    id: 'c',
    colSpan: 'md:col-span-1',
    bg: 'bg-card border border-border',
    icon: Shield,
    title: 'Exam Exemptions',
    body: 'Prior qualifications may reduce your exam count.',
  },
  {
    id: 'd',
    colSpan: 'md:col-span-1',
    bg: 'bg-muted',
    icon: Users,
    title: '247,000+ Members',
    body: 'Join the world\'s largest accountancy network.',
  },
]

const containerV = { hidden: {}, visible: { transition: { staggerChildren: 0.07 } } }
const tileV = { hidden: { opacity: 0, y: 28, scale: 0.97 }, visible: { opacity: 1, y: 0, scale: 1, transition: { duration: 0.5, ease: [0.25, 0.46, 0.45, 0.94] } } }

export default function BentoFeatures() {
  return (
    <section className="py-24 md:py-32">
      <div className="container mx-auto px-4 md:px-6 max-w-7xl">
        <div className="overflow-hidden mb-14">
          <motion.h2
            initial={{ y: '100%' }} whileInView={{ y: '0%' }}
            viewport={{ once: true }}
            transition={{ duration: 0.65, ease: [0.33, 1, 0.68, 1] }}
            className="text-3xl md:text-5xl font-bold text-center"
          >
            Why professionals choose ACCA
          </motion.h2>
        </div>

        <motion.div
          variants={containerV} initial="hidden" whileInView="visible"
          viewport={{ once: true, amount: 0.1 }}
          className="grid grid-cols-1 md:grid-cols-3 md:grid-rows-2 gap-4"
        >
          {tiles.map((tile) => (
            <motion.div
              key={tile.id}
              variants={tileV}
              className={`${tile.colSpan ?? ''} ${tile.rowSpan ?? ''} ${tile.bg} rounded-2xl p-6 md:p-8 flex flex-col justify-between min-h-[200px] relative overflow-hidden group`}
            >
              {tile.image && (
                <img
                  src={tile.image} alt=""
                  className="absolute inset-0 w-full h-full object-cover opacity-20 group-hover:opacity-30 transition-opacity duration-500"
                />
              )}
              <div className="relative z-10">
                <tile.icon className="w-8 h-8 mb-4 opacity-80" />
                <h3 className="text-xl font-bold mb-2">{tile.title}</h3>
                <p className="text-sm opacity-80 leading-relaxed">{tile.body}</p>
              </div>
            </motion.div>
          ))}
        </motion.div>
      </div>
    </section>
  )
}
```

---

## Zigzag / Alternating Feature Rows

For detailed feature breakdowns where each row has image + copy.
Far more premium than a uniform card grid.

```jsx
'use client'
import { motion } from 'framer-motion'
import { CheckCircle } from 'lucide-react'

const rows = [
  {
    eyebrow: 'Career Progression',
    title: 'Open doors in 180 countries',
    body: 'The ACCA qualification is recognised by governments, regulators, and top firms globally. Whether you plan to work in London, Dubai, or Singapore — your qualification travels with you.',
    bullets: ['Big Four preferred', 'Government-recognised', 'MRA with 10+ bodies'],
    image: 'https://images.unsplash.com/photo-1497366216548-37526070297c?auto=format&fit=crop&w=1000&q=80',
    imageRight: true,
  },
  {
    eyebrow: 'Flexible Learning',
    title: 'Study on your own terms',
    body: 'ACCA fits around full-time work. Study online, in-person, or self-paced — choose what works for your schedule. Most students complete their qualification while employed.',
    bullets: ['Self-paced modules', '3 exam sittings per year', 'Online resources 24/7'],
    image: 'https://images.unsplash.com/photo-1516321318423-f06f85e504b3?auto=format&fit=crop&w=1000&q=80',
    imageRight: false,
  },
]

export default function ZigzagFeatures() {
  return (
    <section className="py-24 md:py-32 bg-muted/30">
      <div className="container mx-auto px-4 md:px-6 max-w-6xl space-y-24">
        {rows.map((row, i) => (
          <motion.div
            key={i}
            initial={{ opacity: 0, y: 40 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, amount: 0.2 }}
            transition={{ duration: 0.6, ease: [0.25, 0.46, 0.45, 0.94] }}
            className={`grid md:grid-cols-2 gap-12 md:gap-20 items-center ${row.imageRight ? '' : 'md:[&>*:first-child]:order-2'}`}
          >
            {/* Copy */}
            <div>
              <p className="text-xs font-semibold uppercase tracking-widest text-primary mb-3">{row.eyebrow}</p>
              <h3 className="text-3xl md:text-4xl font-bold mb-4 leading-tight">{row.title}</h3>
              <p className="text-muted-foreground leading-relaxed mb-6">{row.body}</p>
              <ul className="space-y-3">
                {row.bullets.map((b, j) => (
                  <li key={j} className="flex items-center gap-3 text-sm">
                    <CheckCircle className="w-5 h-5 text-primary shrink-0" />
                    {b}
                  </li>
                ))}
              </ul>
            </div>
            {/* Image */}
            <div className="relative rounded-2xl overflow-hidden aspect-[4/3] shadow-2xl">
              <img src={row.image} alt={row.title} className="w-full h-full object-cover" />
              <div className="absolute inset-0 bg-gradient-to-t from-foreground/20 to-transparent" />
            </div>
          </motion.div>
        ))}
      </div>
    </section>
  )
}
```

---

## Testimonials — Masonry / Staggered Cards (2025 style)

Instead of a boring 3-column uniform grid, use masonry-style with varying heights.

```jsx
'use client'
import { motion } from 'framer-motion'
import { Star } from 'lucide-react'

const testimonials = [
  { quote: 'Passing ACCA opened the door to my role at Deloitte. The qualification speaks for itself globally.', name: 'Aisha Rahman', role: 'Senior Auditor, Deloitte', avatar: 'https://i.pravatar.cc/80?u=aisha', stars: 5 },
  { quote: 'The flexibility to study while working full-time made all the difference. I completed all papers in 3 years.', name: 'James Okafor', role: 'Finance Manager, Shell', avatar: 'https://i.pravatar.cc/80?u=james', stars: 5 },
  { quote: 'Recognised in UAE, UK, and Singapore — I have worked in three countries with one qualification.', name: 'Priya Nair', role: 'CFO, Gulf Holdings', avatar: 'https://i.pravatar.cc/80?u=priya', stars: 5 },
  { quote: 'The exam exemptions based on my degree saved me 4 papers. Great starting point.', name: 'Carlos Mendes', role: 'Tax Consultant, PwC', avatar: 'https://i.pravatar.cc/80?u=carlos', stars: 5 },
  { quote: 'ACCA gave me the technical edge I needed to move from accounting to CFO track in 5 years.', name: 'Sophie Laurent', role: 'Finance Director, L\'Oréal', avatar: 'https://i.pravatar.cc/80?u=sophie', stars: 5 },
]

const containerV = { hidden: {}, visible: { transition: { staggerChildren: 0.09 } } }
const cardV = { hidden: { opacity: 0, y: 36, scale: 0.96 }, visible: { opacity: 1, y: 0, scale: 1, transition: { duration: 0.55, ease: [0.25, 0.46, 0.45, 0.94] } } }

export default function TestimonialsSection() {
  return (
    <section className="py-24 md:py-32 bg-foreground text-background">
      <div className="container mx-auto px-4 md:px-6 max-w-7xl">
        <div className="overflow-hidden mb-12 text-center">
          <motion.h2
            initial={{ y: '100%' }} whileInView={{ y: '0%' }}
            viewport={{ once: true }}
            transition={{ duration: 0.6, ease: [0.33, 1, 0.68, 1] }}
            className="text-3xl md:text-5xl font-bold"
          >
            Professionals who made the leap
          </motion.h2>
        </div>

        <motion.div
          variants={containerV} initial="hidden" whileInView="visible"
          viewport={{ once: true, amount: 0.1 }}
          className="columns-1 sm:columns-2 lg:columns-3 gap-4 space-y-4"
        >
          {testimonials.map((t, i) => (
            <motion.div
              key={i} variants={cardV}
              className="break-inside-avoid bg-background/10 border border-background/20 rounded-2xl p-6 inline-block w-full"
            >
              <div className="flex gap-0.5 mb-3">
                {Array.from({ length: t.stars }).map((_, j) => (
                  <Star key={j} className="w-4 h-4 fill-primary text-primary" />
                ))}
              </div>
              <p className="text-sm leading-relaxed text-background/90 mb-4">&ldquo;{t.quote}&rdquo;</p>
              <div className="flex items-center gap-3">
                <img src={t.avatar} alt={t.name} className="w-10 h-10 rounded-full object-cover" />
                <div>
                  <p className="text-sm font-semibold text-background">{t.name}</p>
                  <p className="text-xs text-background/60">{t.role}</p>
                </div>
              </div>
            </motion.div>
          ))}
        </motion.div>
      </div>
    </section>
  )
}
```

---

## Sticky Scroll Story Section (2025 — "as you scroll, story unfolds")

One panel stays fixed while the copy scrolls through chapters on the right.
Used by Linear, Stripe, Apple — the highest-prestige UX pattern for feature storytelling.

```jsx
'use client'
import { useRef, useState, useEffect } from 'react'
import { motion, useScroll, useTransform } from 'framer-motion'

const chapters = [
  {
    step: '01',
    title: 'Tell us your background',
    body: 'Answer 3 quick questions about your education and current role. Our system calculates your exact exam exemptions in real time.',
    image: 'https://images.unsplash.com/photo-1586281380349-632531db7ed4?auto=format&fit=crop&w=800&q=80',
  },
  {
    step: '02',
    title: 'Receive your roadmap',
    body: 'Get a personalised study plan — number of papers, recommended order, and estimated completion timeline based on your pace preference.',
    image: 'https://images.unsplash.com/photo-1512314889357-e157c22f938d?auto=format&fit=crop&w=800&q=80',
  },
  {
    step: '03',
    title: 'Begin your first paper',
    body: 'Access study materials, mock exams, and tutor support from day one. Most students pass their first paper within 3 months.',
    image: 'https://images.unsplash.com/photo-1434030216411-0b793f4b4173?auto=format&fit=crop&w=800&q=80',
  },
]

export default function StickyScrollStory() {
  const [active, setActive] = useState(0)
  const sectionRef = useRef(null)

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => entries.forEach(e => {
        if (e.isIntersecting) setActive(Number(e.target.dataset.index))
      }),
      { rootMargin: '-45% 0px -45% 0px' }
    )
    const items = sectionRef.current?.querySelectorAll('[data-index]')
    items?.forEach(el => observer.observe(el))
    return () => observer.disconnect()
  }, [])

  return (
    <section ref={sectionRef} className="py-24 md:py-32">
      <div className="container mx-auto px-4 md:px-6 max-w-6xl">
        <div className="overflow-hidden mb-16 text-center">
          <motion.h2
            initial={{ y: '100%' }} whileInView={{ y: '0%' }}
            viewport={{ once: true }}
            transition={{ duration: 0.6, ease: [0.33, 1, 0.68, 1] }}
            className="text-3xl md:text-5xl font-bold"
          >
            How it works
          </motion.h2>
        </div>

        <div className="grid md:grid-cols-2 gap-12 md:gap-20 items-start">
          {/* Sticky image panel */}
          <div className="hidden md:block sticky top-28">
            <div className="relative aspect-[4/3] rounded-2xl overflow-hidden shadow-2xl">
              {chapters.map((ch, i) => (
                <motion.img
                  key={i} src={ch.image} alt={ch.title}
                  className="absolute inset-0 w-full h-full object-cover"
                  animate={{ opacity: active === i ? 1 : 0 }}
                  transition={{ duration: 0.5 }}
                />
              ))}
            </div>
          </div>

          {/* Scrolling steps */}
          <div className="space-y-24 md:space-y-32 py-8">
            {chapters.map((ch, i) => (
              <div key={i} data-index={i} className={`transition-opacity duration-300 ${active === i ? 'opacity-100' : 'opacity-30'}`}>
                <p className="text-5xl font-bold text-primary/20 mb-4">{ch.step}</p>
                <h3 className="text-2xl md:text-3xl font-bold mb-4">{ch.title}</h3>
                <p className="text-muted-foreground leading-relaxed text-lg">{ch.body}</p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}
```

---

## Final CTA — Full-Bleed with Ambient Motion

The closing CTA section must feel premium: dark or brand-colored, with ambient movement.

```jsx
'use client'
import { motion } from 'framer-motion'
import { ArrowRight } from 'lucide-react'
import { Button } from '@/components/ui/Button'

export default function CTASection() {
  return (
    <section className="relative py-32 md:py-40 bg-foreground text-background overflow-hidden">
      {/* Ambient orbs */}
      <div className="absolute inset-0 pointer-events-none overflow-hidden">
        <div
          className="absolute -top-32 -left-32 w-96 h-96 bg-primary/30 rounded-full blur-[100px]"
          style={{ animation: 'ctaOrb1 14s ease-in-out infinite alternate' }}
        />
        <div
          className="absolute -bottom-32 -right-32 w-80 h-80 bg-accent/20 rounded-full blur-[80px]"
          style={{ animation: 'ctaOrb2 18s ease-in-out infinite alternate-reverse' }}
        />
      </div>

      <div className="relative z-10 container mx-auto px-4 md:px-6 max-w-4xl text-center">
        <motion.div
          initial={{ opacity: 0, y: 40 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, amount: 0.3 }}
          transition={{ duration: 0.7, ease: [0.25, 0.46, 0.45, 0.94] }}
        >
          <p className="text-xs font-semibold uppercase tracking-widest text-background/50 mb-4">
            Ready to begin?
          </p>
          <h2 className="text-4xl md:text-6xl font-bold mb-6 leading-tight">
            Your accounting career<br className="hidden md:block" /> starts here.
          </h2>
          <p className="text-lg text-background/70 mb-10 max-w-xl mx-auto">
            Register your interest today. Free, no commitment, takes 3 minutes.
          </p>
          <div className="flex flex-col sm:flex-row gap-4 justify-center">
            <Button size="lg" className="bg-primary text-primary-foreground px-10 py-4 text-base font-semibold gap-2 group">
              Register Your Interest
              <ArrowRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
            </Button>
            <Button size="lg" variant="outline" className="border-background/30 text-background hover:bg-background/10 px-10 py-4 text-base">
              Download the Guide
            </Button>
          </div>
        </motion.div>
      </div>

      <style>{`
        @keyframes ctaOrb1 { from { transform: translate(0,0) scale(1) } to { transform: translate(60px,40px) scale(1.15) } }
        @keyframes ctaOrb2 { from { transform: translate(0,0) scale(1) } to { transform: translate(-50px,-30px) scale(1.1) } }
      `}</style>
    </section>
  )
}
```

---

## Hero — Full-Bleed Dark with Entry Sequence

The most dramatic hero variant. Text cascades in as the page loads, never all at once.

```jsx
'use client'
import { motion } from 'framer-motion'
import { ArrowRight, Play } from 'lucide-react'
import { Button } from '@/components/ui/Button'

export default function HeroDark() {
  return (
    <section className="relative min-h-screen flex items-center overflow-hidden bg-foreground text-background">
      {/* Background photo + scrim */}
      <img
        src="https://images.unsplash.com/photo-1454165804606-c3d57bc86b40?auto=format&fit=crop&w=1600&q=80"
        alt=""
        className="absolute inset-0 w-full h-full object-cover opacity-25"
      />
      <div className="absolute inset-0 bg-gradient-to-br from-foreground/90 via-foreground/70 to-foreground/40" />

      {/* Ambient orbs */}
      <div className="absolute inset-0 pointer-events-none overflow-hidden">
        <div className="absolute top-1/4 right-1/4 w-96 h-96 bg-primary/20 rounded-full blur-[120px]"
             style={{ animation: 'heroOrb 16s ease-in-out infinite alternate' }} />
      </div>

      {/* Content */}
      <div className="relative z-10 container mx-auto px-4 md:px-6 max-w-7xl pt-24">
        <div className="max-w-3xl">
          {/* Eyebrow — first to appear */}
          <motion.div
            initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.1, duration: 0.6 }}
            className="inline-flex items-center gap-2 rounded-full border border-background/20 bg-background/10 px-4 py-1.5 text-sm text-background/80 mb-6"
          >
            <span className="w-2 h-2 rounded-full bg-primary animate-pulse" />
            180,000+ members in 100+ countries
          </motion.div>

          {/* H1 — clip-path reveal */}
          <div className="overflow-hidden mb-6">
            <motion.h1
              initial={{ y: '100%' }} animate={{ y: '0%' }}
              transition={{ delay: 0.25, duration: 0.75, ease: [0.33, 1, 0.68, 1] }}
              className="text-5xl md:text-7xl font-bold leading-[1.05] tracking-tight"
            >
              Your accounting<br />
              <em className="not-italic text-primary">career</em> starts here.
            </motion.h1>
          </div>

          {/* Subtitle */}
          <motion.p
            initial={{ opacity: 0 }} animate={{ opacity: 1 }}
            transition={{ delay: 0.5, duration: 0.7 }}
            className="text-lg md:text-xl text-background/70 mb-10 max-w-xl leading-relaxed"
          >
            ACCA is the world&apos;s most forward-thinking professional accountancy body.
            Earn a qualification recognised by employers in 180+ countries.
          </motion.p>

          {/* CTAs */}
          <motion.div
            initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.65, duration: 0.6 }}
            className="flex flex-col sm:flex-row gap-4"
          >
            <Button size="lg" className="px-8 py-4 text-base font-semibold gap-2 group">
              Apply Now
              <ArrowRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
            </Button>
            <Button size="lg" variant="ghost" className="text-background/80 hover:text-background hover:bg-background/10 px-8 py-4 gap-2">
              <Play className="w-4 h-4" /> Watch 2-min Overview
            </Button>
          </motion.div>

          {/* Floating stat cards */}
          <motion.div
            initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.9, duration: 0.6 }}
            className="flex flex-wrap gap-4 mt-12"
          >
            {[
              { value: '180+', label: 'Countries' },
              { value: '500+', label: 'Exam Centres' },
              { value: '98%', label: 'Satisfaction' },
            ].map((s, i) => (
              <div key={i} className="flex items-center gap-3 bg-background/10 border border-background/20 rounded-xl px-4 py-3">
                <span className="text-2xl font-bold text-primary">{s.value}</span>
                <span className="text-sm text-background/60">{s.label}</span>
              </div>
            ))}
          </motion.div>
        </div>
      </div>

      <style>{`
        @keyframes heroOrb { from { transform: translate(0,0) scale(1) } to { transform: translate(-40px,60px) scale(1.2) } }
      `}</style>
    </section>
  )
}
```

---

## Floating Ambient Hero Background (gradient orbs)

Drop this inside any hero `<section className="relative ...">` to add organic depth:

```jsx
{/* Ambient gradient orbs — slow, organic motion */}
<div className="absolute inset-0 overflow-hidden pointer-events-none" aria-hidden>
  <div
    className="absolute top-1/4 -left-24 w-[500px] h-[500px] rounded-full bg-primary/15 blur-[120px]"
    style={{ animation: 'orb1 14s ease-in-out infinite alternate' }}
  />
  <div
    className="absolute bottom-0 right-0 w-[400px] h-[400px] rounded-full bg-accent/10 blur-[100px]"
    style={{ animation: 'orb2 18s ease-in-out infinite alternate-reverse' }}
  />
  <div
    className="absolute top-1/2 left-1/2 w-[300px] h-[300px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-secondary/10 blur-[80px]"
    style={{ animation: 'orb3 22s ease-in-out infinite alternate' }}
  />
</div>
<style>{`
  @keyframes orb1 { from{transform:translate(0,0)scale(1)} to{transform:translate(60px,40px)scale(1.15)} }
  @keyframes orb2 { from{transform:translate(0,0)scale(1)} to{transform:translate(-50px,-30px)scale(1.1)} }
  @keyframes orb3 { from{transform:translate(-50%,-50%)scale(1)} to{transform:translate(-50%,-50%)scale(1.2)} }
`}</style>
```

---

## Dot Grid Background Decoration

Subtle structural depth — use on `bg-background` sections (not on colored/photo sections):

```jsx
{/* Dot grid — structural depth */}
<div
  className="absolute inset-0 pointer-events-none opacity-[0.06]"
  style={{
    backgroundImage: 'radial-gradient(circle, hsl(var(--foreground)) 1px, transparent 1px)',
    backgroundSize: '24px 24px',
  }}
/>
```

---

## TiltCard Component (3D hover depth)

Wrap any card to give it interactive 3D depth on hover:

```jsx
'use client'
import { useRef } from 'react'

export function TiltCard({ children, className = '', intensity = 8 }) {
  const ref = useRef(null)

  const onMove = (e) => {
    const rect = ref.current.getBoundingClientRect()
    const x = (e.clientX - rect.left) / rect.width  - 0.5
    const y = (e.clientY - rect.top)  / rect.height - 0.5
    ref.current.style.transform =
      `perspective(700px) rotateY(${x * intensity}deg) rotateX(${-y * intensity}deg) scale3d(1.02,1.02,1.02)`
  }
  const onLeave = () => {
    ref.current.style.transform = 'perspective(700px) rotateY(0deg) rotateX(0deg) scale3d(1,1,1)'
  }

  return (
    <div
      ref={ref}
      onMouseMove={onMove}
      onMouseLeave={onLeave}
      className={`transition-transform duration-200 ease-out will-change-transform ${className}`}
    >
      {children}
    </div>
  )
}
```

Usage: `<TiltCard className="rounded-2xl bg-card p-6 border border-border">...</TiltCard>`
Use on feature cards, pricing cards, service cards. Intensity 6-10 for cards, 3-4 for large panels.

