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
{/* Standard section wrapper */}
<section className="py-20 px-6">
  <div className="max-w-6xl mx-auto">
    {/* Section header */}
    <div className="text-center mb-12">
      <span className="text-xs font-semibold uppercase tracking-widest text-primary">Label</span>
      <h2 className="text-3xl font-bold text-foreground mt-2">Section Title</h2>
      <p className="text-muted-foreground mt-3 max-w-2xl mx-auto">Description text</p>
    </div>
    {/* Content grid */}
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
      {/* Cards */}
    </div>
  </div>
</section>
```
