# Skill: framer-motion (React animations)

## Import
```jsx
import { motion, AnimatePresence } from 'framer-motion'
```

## Fade-up on scroll (sections)
```jsx
<motion.div
  initial={{ opacity: 0, y: 40 }}
  whileInView={{ opacity: 1, y: 0 }}
  viewport={{ once: true }}
  transition={{ duration: 0.6 }}
>
  {/* section content */}
</motion.div>
```

## Staggered children (feature cards, grid items)
```jsx
const container = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { staggerChildren: 0.1 } },
}
const item = {
  hidden: { opacity: 0, y: 20 },
  show: { opacity: 1, y: 0 },
}

<motion.div variants={container} initial="hidden" whileInView="show" viewport={{ once: true }}
  className="grid grid-cols-1 md:grid-cols-3 gap-6"
>
  {features.map((feature, i) => (
    <motion.div key={i} variants={item}>
      <Card className="hover:shadow-lg transition-shadow">...</Card>
    </motion.div>
  ))}
</motion.div>
```

## Hover scale (cards, buttons)
```jsx
<motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }} transition={{ type: "spring", stiffness: 300 }}>
  <Card>...</Card>
</motion.div>
```

## Page transition
```jsx
<motion.div
  initial={{ opacity: 0, x: -20 }}
  animate={{ opacity: 1, x: 0 }}
  exit={{ opacity: 0, x: 20 }}
  transition={{ duration: 0.3 }}
>
  {/* page content */}
</motion.div>
```

## Counter animation (KPI numbers)
```jsx
import { useMotionValue, useTransform, animate } from 'framer-motion'
import { useEffect, useState } from 'react'

function AnimatedCounter({ value }) {
  const [display, setDisplay] = useState(0)
  useEffect(() => {
    const controls = animate(0, value, {
      duration: 1.5,
      onUpdate: (v) => setDisplay(Math.round(v)),
    })
    return controls.stop
  }, [value])
  return <span>{display.toLocaleString()}</span>
}
```

## IMPORTANT RULES
- Always use `viewport={{ once: true }}` for scroll animations (prevent re-triggering)
- Use `whileInView` for sections, `whileHover` for interactive elements
- Keep durations between 0.3-0.8s (fast enough to not feel sluggish)
- Use `type: "spring"` for natural-feeling interactions
- Wrap page transitions in `<AnimatePresence>` at the router level
