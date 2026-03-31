# Website & Landing Page Architecture Patterns

## Layout Variants

### Variant A — Marketing Website (Vercel/Stripe style)
Best for: SaaS landing, Product launches, Agency sites
```
┌──────────────────────────────────────────────┐
│  Logo        Nav Items              CTA Btn  │
├──────────────────────────────────────────────┤
│                                              │
│     HERO: Large headline, subtitle,          │
│     CTA button, optional image/graphic       │
│     gradient or dark background              │
│                                              │
├──────────────────────────────────────────────┤
│                                              │
│     SOCIAL PROOF: Logo bar or stats row      │
│     "Trusted by 1000+ companies"             │
│                                              │
├──────────────────────────────────────────────┤
│     FEATURES: 3-column card grid             │
│     Icon + Title + Description each          │
├──────────────────────────────────────────────┤
│     HOW IT WORKS: Step-by-step (1-2-3)       │
│     Numbered cards or timeline               │
├──────────────────────────────────────────────┤
│     TESTIMONIALS: Card carousel or grid      │
│     Avatar + Quote + Name + Role             │
├──────────────────────────────────────────────┤
│     PRICING: 3-tier comparison               │
│     Highlighted "Popular" plan               │
├──────────────────────────────────────────────┤
│     FAQ: Accordion sections                  │
├──────────────────────────────────────────────┤
│     CTA: Final call-to-action banner         │
│     Gradient bg + headline + button          │
├──────────────────────────────────────────────┤
│     FOOTER: Multi-column links               │
│     Logo + Desc | Product | Company | Social │
└──────────────────────────────────────────────┘
```

### Variant B — Portfolio/Creative Site
Best for: Designers, Photographers, Freelancers
```
Nav (minimal) → Hero (full-screen image/video) → Work Grid → About (split layout) → Contact
```

### Variant C — Blog/Content Site
Best for: News, Magazine, Personal blog
```
Nav → Featured Post (large) → Grid (3 cols) → Categories → Newsletter → Footer
```

---

## Section Component Patterns

### Hero Section
```jsx
'use client'
import { ArrowRight } from 'lucide-react';

export function HeroSection() {
  return (
    <section className="hero">
      <div className="hero-content">
        <div className="hero-badge">
          <span>✨ Now in public beta</span>
        </div>
        <h1 className="hero-title">
          Build something<br />
          <span className="gradient-text">extraordinary</span>
        </h1>
        <p className="hero-subtitle">
          The modern platform for teams who ship fast.
          Beautiful by default. Powerful when you need it.
        </p>
        <div className="hero-actions">
          <a href="/signup" className="btn-primary">
            Get Started Free <ArrowRight size={16} />
          </a>
          <a href="/demo" className="btn-secondary">
            Watch Demo
          </a>
        </div>
        <div className="hero-stats">
          <div className="stat"><strong>10K+</strong> Active Users</div>
          <div className="stat-divider" />
          <div className="stat"><strong>99.9%</strong> Uptime</div>
          <div className="stat-divider" />
          <div className="stat"><strong>4.9★</strong> Rating</div>
        </div>
      </div>
    </section>
  );
}
export default HeroSection;
```

**CSS:**
```css
.hero {
  padding: 120px 24px 80px;
  text-align: center;
  background: linear-gradient(180deg, var(--color-bg) 0%, var(--color-bg-secondary) 100%);
}
.hero-title {
  font-size: clamp(2.5rem, 6vw, 4rem);
  font-weight: 800;
  line-height: 1.1;
  letter-spacing: -0.02em;
  color: var(--color-text);
}
.gradient-text {
  background: linear-gradient(135deg, var(--color-primary), var(--color-accent));
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}
.hero-subtitle {
  font-size: 1.125rem;
  color: var(--color-text-secondary);
  max-width: 560px;
  margin: 24px auto;
  line-height: 1.6;
}
.hero-actions { display: flex; gap: 12px; justify-content: center; margin-top: 32px; }
.hero-stats {
  display: flex; justify-content: center; gap: 32px;
  margin-top: 48px; font-size: 14px; color: var(--color-text-secondary);
}
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
    <section className="features">
      <div className="container">
        <div className="section-header">
          <span className="section-label">Features</span>
          <h2>Everything you need to scale</h2>
          <p>Powerful features that grow with your business</p>
        </div>
        <div className="features-grid">
          {features.map((f, i) => (
            <div key={i} className="feature-card">
              <div className="feature-icon"><f.icon size={24} /></div>
              <h3>{f.title}</h3>
              <p>{f.desc}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
export default FeaturesSection;
```

### Testimonials Section
```jsx
const testimonials = [
  {
    quote: "This platform transformed how our team collaborates. We shipped 3x faster in the first month.",
    name: "Sarah Chen",
    role: "CTO at TechFlow",
    avatar: null, // use initials fallback
  },
  {
    quote: "The best developer experience I've ever had. Clean APIs, great documentation, and incredible support.",
    name: "Marcus Johnson",
    role: "Lead Engineer at ScaleUp",
    avatar: null,
  },
  {
    quote: "We migrated from our legacy system in two weeks. The ROI was immediate and measurable.",
    name: "Elena Rodriguez",
    role: "VP Engineering at DataCore",
    avatar: null,
  },
];
```

### Pricing Section Pattern
```jsx
const plans = [
  {
    name: 'Starter',
    price: 0,
    period: '/month',
    description: 'Perfect for side projects',
    features: ['Up to 3 projects', '1GB storage', 'Community support', 'Basic analytics'],
    cta: 'Start Free',
    popular: false,
  },
  {
    name: 'Professional',
    price: 29,
    period: '/month',
    description: 'For growing teams',
    features: ['Unlimited projects', '50GB storage', 'Priority support', 'Advanced analytics', 'Custom domains', 'Team collaboration'],
    cta: 'Start Trial',
    popular: true,
  },
  {
    name: 'Enterprise',
    price: 99,
    period: '/month',
    description: 'For large organizations',
    features: ['Everything in Pro', 'Unlimited storage', 'Dedicated support', 'Custom integrations', 'SLA guarantee', 'SSO & SAML', 'Audit logs'],
    cta: 'Contact Sales',
    popular: false,
  },
];
```

### FAQ Accordion Pattern
```jsx
'use client'
import { useState } from 'react';
import { ChevronDown } from 'lucide-react';

const faqs = [
  { q: 'How do I get started?', a: 'Sign up for a free account and follow our quick-start guide. You can be up and running in under 5 minutes.' },
  { q: 'Can I cancel anytime?', a: 'Yes, you can cancel your subscription at any time. No contracts, no hidden fees.' },
  { q: 'Do you offer a free trial?', a: 'Yes! All paid plans include a 14-day free trial with full access to all features.' },
  { q: 'What payment methods do you accept?', a: 'We accept all major credit cards, PayPal, and bank transfers for annual plans.' },
];

export function FAQSection() {
  const [openIndex, setOpenIndex] = useState(null);
  return (
    <section className="faq">
      <div className="container">
        <h2>Frequently Asked Questions</h2>
        <div className="faq-list">
          {faqs.map((faq, i) => (
            <div key={i} className={`faq-item ${openIndex === i ? 'open' : ''}`}>
              <button onClick={() => setOpenIndex(openIndex === i ? null : i)}>
                <span>{faq.q}</span>
                <ChevronDown size={20} style={{ transform: openIndex === i ? 'rotate(180deg)' : 'none', transition: '0.2s' }} />
              </button>
              {openIndex === i && <p>{faq.a}</p>}
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

## CSS Utility Patterns

### Buttons
```css
.btn-primary {
  display: inline-flex; align-items: center; gap: 8px;
  padding: 10px 20px;
  background: var(--color-primary); color: #fff;
  border-radius: var(--radius-md);
  font-size: 14px; font-weight: 500;
  border: none; cursor: pointer;
  transition: all 0.2s ease;
}
.btn-primary:hover { opacity: 0.9; transform: translateY(-1px); }
.btn-secondary {
  display: inline-flex; align-items: center; gap: 8px;
  padding: 10px 20px;
  background: transparent; color: var(--color-text);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  font-size: 14px; font-weight: 500;
  cursor: pointer; transition: all 0.2s ease;
}
.btn-secondary:hover { border-color: var(--color-text-secondary); }
```

### Section Layout
```css
.container { max-width: 1200px; margin: 0 auto; padding: 0 24px; }
.section-header { text-align: center; margin-bottom: 48px; }
.section-header h2 { font-size: 2rem; font-weight: 700; color: var(--color-text); }
.section-header p { font-size: 1rem; color: var(--color-text-secondary); margin-top: 12px; }
.section-label {
  display: inline-block;
  font-size: 13px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.05em; color: var(--color-primary); margin-bottom: 12px;
}
```
