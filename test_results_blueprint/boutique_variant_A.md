# Boutique wine bar in Brooklyn — Variant A

===LAYOUT_BLUEPRINT===

hero_archetype:
  editorial-offset
  reasoning: This evokes a sophisticated, curated feel, perfect for showcasing a premium wine selection and inviting atmosphere.

features_archetype:
  bento-mixed

card_language:
  Cards will feature a subtle `border-2 border-gray-200` with an `shadow-md` for depth. A small, elegant grape leaf icon will serve as a micro-element in the bottom right corner of relevant cards.

typography_pairing:
  Heading font: Playfair Display (weight: 700, tracking: normal, no italic). Body font: Merriweather Sans (weight: 400, tracking: tight, italic for quotes).

motion_language:
  Enter animation: `fade-in-up` (y: 20px, opacity: 0 to 1). Hover state: `scale-105` with a `shadow-lg` increase. Scroll-linked behavior: `parallax` subtle background shifts on hero and key imagery. Emotional register: understated elegance, inviting, sophisticated.

decorative_pattern:
  A subtle, organic "wine stain" texture applied as a `background-image` with `background-blend-mode: multiply` and `opacity: 0.03` to sections like the footer and "About Us" background.

border_radius_language:
  Philosophy: Softening edges for an inviting, artisanal feel without appearing overly playful. Buttons: `rounded-lg` (8px). Cards: `rounded-xl` (12px). Images: `rounded-2xl` (16px). Inputs: `rounded-md` (6px).

color_application_strategy:
  Mono-accent. Deep burgundy (`#6B2737`) as the primary accent for CTAs, headings, and key highlights. Neutral palette of warm grays (`#F5F5F5`, `#E0E0E0`, `#A0A0A0`) and off-white (`#FCFCFC`) for backgrounds and body text. Duotone-photos for menu item images.

hover_interaction_style:
  Primary cards: `transform scale-[1.02] transition-transform duration-200 ease-out`. CTAs: `background-color transition-colors duration-200 ease-in-out` to a slightly darker burgundy. Image cards: `opacity-90` overlay with a subtle `drop-shadow-lg`. Nav links: `underline-offset-4 underline-thickness-2 hover:underline`.

spacing_rhythm:
  airy-luxury

live_ui_recipe:
  Upon hovering over a wine bottle image in the "Our Wines" section, the label subtly "peels" back slightly (transform: rotateX(5deg) perspective(1000px)) revealing a brief tasting note tooltip. Clicking a menu item card expands it with a smooth `height` transition, revealing detailed descriptions and pairing suggestions. A small, animated "cork pop" icon appears briefly next to "Add to Cart" buttons on successful selection. The main navigation features a subtle `underline` animation that expands from the center when hovered.

scroll_reveal_style:
  y-distance: 30, duration: 0.8, easing: [0.6, -0.05, 0.01, 0.99], stagger interval: 0.15, viewport amount trigger: 0.3.

ambient_motion:
  grain-noise. Applied as a fixed `div` overlay with `background-image: url('noise.png')` (small, repeating texture), `opacity: 0.02`, and `animation: grain 8s steps(10) infinite;` defined in CSS keyframes. Placed at the root of the main layout component.

design_dna_summary:
  Sophisticated elegance meets artisanal warmth, crafting an inviting digital experience reflecting Brooklyn's boutique wine culture with subtle, refined interactions.
