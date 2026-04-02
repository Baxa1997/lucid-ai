# Skill: Landing Page Sections (Next.js + shadcn/ui + framer-motion)

## Every section MUST follow this structure
```jsx
'use client'
import { motion } from 'framer-motion'

export default function SectionName() {
  return (
    <section className="py-20 md:py-28">
      <div className="container mx-auto px-4 md:px-6 max-w-7xl">
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

## RULES
- Every section: 'use client' at top
- Every section: responsive (py-20 md:py-28, grid-cols-1 md:grid-cols-2 lg:grid-cols-3)
- Every section: container mx-auto px-4 md:px-6 max-w-7xl
- Every section: motion animation (whileInView for scroll reveal)
- NEVER hardcode colors — use bg-primary, text-foreground, bg-muted, etc.
- Avatars: https://i.pravatar.cc/150?u=uniquestring
