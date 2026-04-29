# Yoga studio booking site — Variant B

hero_archetype: typographic-hero
reasoning: A clean, centered typographic hero emphasizes tranquility and focus, aligning with the mindful nature of yoga.

features_archetype: zigzag
reasoning: A gentle zigzag layout provides visual interest without being overly dynamic, guiding the user through offerings in a calm, flowing manner.

card_language: Cards will feature a soft, pillowy aesthetic with rounded corners and a subtle, diffused shadow. They will have a thin, barely-there border in a light, complementary tone.

typography_pairing:
  Heading font: Fraunces italic clamp(48px, 6vw, 96px), centered, letter-spacing normal.
  Body font: Inter, font-light, text-base, line-height-relaxed.

motion_language:
  Enter animation: floatIn 0.9s ease-out, with a gentle upward drift.
  Hover state: translateY(-4px) with a subtle scale(1.01) on cards and buttons, duration 0.3s ease-in-out.
  Scroll-linked behavior: Soft parallax on background elements, creating a sense of depth and calm.
  Emotional register: Serene, fluid, and inviting.

decorative_pattern:
  Organic blob clip-paths: Applied as low-opacity SVG background elements in hero, feature sections, and testimonials, creating a soft, natural aesthetic.
  CSS: `background-image: url('/path/to/organic-blob.svg'); background-size: cover; opacity: 0.08;`

border_radius_language:
  Philosophy: Soft, organic curves for a welcoming, non-intrusive feel.
  Buttons: rounded-full
  Cards: rounded-3xl
  Images: rounded-2xl
  Inputs: rounded-lg

color_application_strategy:
  Mono-accent: A single, calming accent color (e.g., a muted sage green or soft lavender) used sparingly for buttons, icons, and highlight text. Backgrounds will be warm off-whites and light neutrals.
  Sections: Hero text and CTA, feature icons, booking button, selected testimonial highlights.

hover_interaction_style:
  Primary cards: Lift and subtle scale (translateY(-4px) scale(1.01)) with a soft shadow expansion.
  CTAs: Gentle background color shift to a slightly darker shade of the accent color, with a subtle text glow.
  Image cards: Slight desaturation on hover, revealing a soft overlay with a booking prompt.
  Nav links: Underline appears with a smooth, 0.2s ease-in-out transition.

spacing_rhythm: airy-luxury
reasoning: Ample whitespace and generous padding create a sense of calm and spaciousness, allowing content to breathe and feel uncluttered.

live_ui_recipe:
  The booking calendar will feature a "day-at-a-glance" view where hovering over a specific class slot gently expands the card to reveal instructor, duration, and a "Book Now" button. Clicking "Book Now" triggers a subtle modal slide-in from the bottom, with a soft blur applied to the background. Throughout the site, small, circular progress indicators will subtly pulse when loading, maintaining the calm aesthetic. Testimonial cards will gently rotate on hover, revealing a full quote.

scroll_reveal_style:
  y-distance: 20px
  duration: 0.8s
  easing: ease-out
  stagger interval: 0.15s
  viewport amount trigger: 0.3

ambient_motion:
  Organic blob shapes only: Applied as low-opacity, slow-moving SVG elements in the background of key sections (hero, philosophy, testimonials).
  CSS keyframe: `@keyframes float { 0% { transform: translateY(0px); } 50% { transform: translateY(-10px); } 100% { transform: translateY(0px); } }`
  JSX placement: `<motion.div animate={{ y: ["0%", "-5%", "0%"] }} transition={{ duration: 8, repeat: Infinity, ease: "easeInOut" }} className="absolute -z-10 opacity-10" style={{ clipPath: 'url(#organicBlob1)' }} />`

design_dna_summary: A serene, fluid, and inviting digital sanctuary, blending soft organic forms with airy typography and gentle motion for a truly calming user journey.
