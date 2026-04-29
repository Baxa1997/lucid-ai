# Yoga studio booking site — Variant A

===LAYOUT_BLUEPRINT===

hero_archetype: full-bleed-dark
reasoning: Evokes a serene, introspective mood suitable for a wellness domain, allowing the imagery to speak powerfully.

features_archetype: zigzag

card_language: Softly rounded corners (md-lg radius), subtle inner shadow for depth, and a small, bottom-right micro-element (e.g., a small leaf icon) on hover.

typography_pairing:
  Heading font: Playfair Display (Google Fonts). Weight: 700. Tracking: normal.
  Body font: Lato (Google Fonts). Weight: 300, 400. Tracking: normal. Italic: used for quotes/testimonials.

motion_language:
  Enter animation: Gentle fade-up (y-translate from 20px, opacity from 0).
  Hover state: Subtle lift (translate-y -2px) with a slight increase in shadow.
  Scroll-linked behavior: Parallax effect on background imagery in hero and section dividers.
  Emotional register: Calm, inviting, fluid.

decorative_pattern: Subtle, organic line pattern resembling flowing water or abstract brushstrokes.
  Application: Applied as a `background-image` with `opacity: 0.05` and `background-repeat: repeat` to the `body` element and specific section backgrounds (e.g., testimonial section).

border_radius_language: Philosophy: Softness and organic flow, avoiding sharp edges.
  Buttons: `rounded-lg`
  Cards: `rounded-xl`
  Images: `rounded-2xl`
  Inputs: `rounded-md`

color_application_strategy: photographic-neutral
  Sections: Hero uses a deep, desaturated photo with a dark overlay. Feature sections use light, neutral backgrounds (off-white, light grey) with pops of a single accent color (e.g., muted sage green) for CTAs and highlights. Booking calendar uses a clean white background.

hover_interaction_style:
  Primary cards: Lift and subtle glow (box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1), 0 4px 6px -2px rgba(0,0,0,0.05); transition: all 0.3s ease-in-out;).
  CTAs: Background color subtly darkens, text slightly scales up (scale-105).
  Image cards: Slight zoom-in effect on the image (scale-103) within the card boundaries.
  Nav links: Underline appears with a smooth slide-in animation from the center.

spacing_rhythm: airy-luxury

live_ui_recipe: The "Book a Class" button in the hero section triggers a smooth modal overlay for class selection, which slides up from the bottom with a gentle bounce. As users scroll through class listings, each class card subtly expands on hover, revealing instructor details and a "Book Now" button. The booking calendar itself features a satisfying "snap" animation when selecting dates, and a subtle shimmer effect on available time slots.

scroll_reveal_style:
  y-distance: 40px
  duration: 0.8
  easing: [0.25, 0.1, 0.25, 1] (ease-in-out)
  stagger interval: 0.15
  viewport amount trigger: 0.3

ambient_motion: grain-noise
  CSS keyframe: `@keyframes grain { 0%, 100% { transform: translate(0, 0); } 10% { transform: translate(-5%, -10%); } 20% { transform: translate(-15%, 10%); } ... (more random translations) }`
  JSX placement: A fixed, full-screen `div` with `pointer-events: none; opacity: 0.03; background-image: url('/grain.png'); animation: grain 8s steps(10) infinite;` placed as the lowest layer in the main layout component.

design_dna_summary: A tranquil, fluid digital space that guides users with soft transitions, organic forms, and a serene visual palette.
