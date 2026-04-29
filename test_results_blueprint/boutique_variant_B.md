# Boutique wine bar in Brooklyn — Variant B

hero_archetype: full-bleed-dark
reasoning: This evokes an immediate sense of sophistication and intimacy, perfect for a high-end wine bar.

features_archetype: numbered-editorial
reasoning: This provides a structured yet elegant way to present unique offerings, akin to a curated menu.

card_language: Cards are minimal, featuring a subtle `border-t border-gold/20` only, with no shadow. They maintain an airy feel, allowing content to breathe, and incorporate a small, bottom-aligned gold line on hover.

typography_pairing:
  Heading: `Cormorant Garamond italic clamp(48px,6vw,96px) font-light tracking-wide`
  Body: `Lora regular text-base leading-relaxed`

motion_language:
  Enter animation: `opacity-only fade 0.8s ease-in-out` (no translate).
  Hover state: `gold shimmer` on CTA buttons, `subtle scale-103` on image cards with `duration-300`.
  Scroll-linked behavior: `slow parallax` on hero background image.
  Emotional register: `calm, luxurious, unhurried`.

decorative_pattern:
  Repeating diagonal gold lines at `opacity: 0.03` applied as a `background-image` on key sections like "Our Philosophy" and "Wine List".
  `background-image: repeating-linear-gradient(45deg, #FFD700 0px, #FFD700 1px, transparent 1px, transparent 20px); background-size: 20px 20px;`

border_radius_language:
  Philosophy: `Minimal and refined, favoring sharp edges or very subtle rounding to maintain elegance.`
  Buttons: `rounded-none`
  Cards: `rounded-sm`
  Images: `rounded-sm`
  Inputs: `rounded-sm`

color_application_strategy: photographic-neutral
  Hero: full-bleed dark photography with a deep `rgba(0,0,0,0.6)` scrim.
  Content sections: `warm off-white (#fafaf8)` background with `gold (#FFD700)` as the primary accent for text highlights and borders.
  CTAs: `border border-gold/40 text-gold uppercase`.

hover_interaction_style:
  Primary cards: `border-b-2 border-gold/60` on hover, `transition-all duration-300`.
  CTAs: `text-white bg-gold/80` on hover, `transition-colors duration-300`.
  Image cards: `scale-103` with `subtle blur-sm` on hover, `transition-all duration-300`.
  Nav links: `text-gold underline-offset-4 underline-gold` on hover.

spacing_rhythm: airy-luxury

live_ui_recipe:
  The hero section features a full-bleed, dimly lit photograph of the bar interior. As the user scrolls, the background image subtly `parallax` scrolls, revealing more of the opulent space. CTA buttons within the hero section have a `gold shimmer` effect on hover, drawing attention without being aggressive. As the user navigates the "Wine List" section, individual wine cards, initially border-t only, gain a `border-b-2 border-gold/60` on hover, subtly highlighting the selection. Navigation links elegantly `underline` in gold on hover, reinforcing the brand's premium feel.

scroll_reveal_style:
  y-distance: `20px`
  duration: `0.9s`
  easing: `ease-out`
  stagger interval: `0.12s`
  viewport amount trigger: `0.4`

ambient_motion: none
  The design relies on high-quality photography and subtle motion to create atmosphere, avoiding distracting ambient elements.

design_dna_summary: An elegant and unhurried experience, blending dark photography with subtle gold accents and refined typography for a luxurious Brooklyn wine bar.
