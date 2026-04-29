# AI dev tool for backend engineers — Variant B

hero_archetype:
  cinematic-parallax
  reasoning: This provides a sophisticated, immersive entry point suitable for a complex developer tool, hinting at depth and power without being overly flashy.

features_archetype:
  bento-mixed
  reasoning: Allows for showcasing diverse features—code snippets, diagrams, UI elements—in an organized yet dynamic grid, reflecting the multi-faceted nature of a dev tool.

card_language:
  Cards feature a subtle dark border (border-white/10) and a soft inner glow, suggesting a premium, contained environment for information. They have a slightly rounded corner, indicating robustness.

typography_pairing:
  Heading font: `Syne` weight-700 clamp(48px, 6vw, 96px), tracking-tight. Body font: `Inter` regular, text-base, leading-relaxed. This pairs a strong, modern display font with a highly readable, professional sans-serif for content.

motion_language:
  Enter animation: `fadeUp` 0.6s ease-out stagger 0.1s. Hover state: subtle `scale-103` with a `translateY(-4px)` for interactive elements. Scroll-linked behavior: background elements (like grid lines) have a slow, subtle parallax effect. Emotional register: controlled, precise, and powerful.

decorative_pattern:
  80px grid with SVG noise (opacity 0.035) applied as a background overlay across all major content sections, providing a consistent, technical texture.

border_radius_language:
  Philosophy: Controlled, subtle rounding for a professional yet approachable feel. Buttons: rounded-md. Cards: rounded-lg. Images: rounded-sm. Inputs: rounded-md.

color_application_strategy:
  inverted-dark. Deep near-black background (#0a0d12) with vibrant, controlled accent colors (e.g., electric blue #007bff) for interactive elements and highlights. Code blocks will use a slightly lighter dark grey for contrast.

hover_interaction_style:
  Primary cards: `scale-103` with a `box-shadow` expansion. CTAs: `bg-accent` to `bg-accent-dark` with a subtle `glow-shadow` pulse. Image cards: `brightness-120` on hover. Nav links: `text-accent` on hover with an underline reveal.

spacing_rhythm:
  standard-modern
  reasoning: Provides ample breathing room for complex information without feeling sparse, ensuring readability and visual hierarchy.

live_ui_recipe:
  A signature motion moment involves a code editor component where lines of code `fadeUp` and `stagger` into view, simulating real-time compilation or execution. Hovering over specific code elements triggers a tooltip with contextual API documentation, using a `fade-in` and `slide-up` animation. Interactive diagrams animate their connections on scroll, highlighting data flow. Buttons emit a subtle `glow-pulse` on click, confirming interaction.

scroll_reveal_style:
  `y: 20`, `duration: 0.7`, `ease: [0.25, 0.1, 0.25, 1]`, `staggerChildren: 0.1`, `viewport: { once: true, amount: 0.4 }`.

ambient_motion:
  gradient-mesh + grain-noise. Subtle, slow-moving gradient orbs (e.g., `radial-gradient` of accent colors with `blur-[120px]`) layered behind content in the hero and key feature sections, combined with a global SVG grain noise (opacity 0.02) for texture.

design_dna_summary:
  A sophisticated, dark-themed interface for engineers, blending precise motion, structured layouts, and subtle technical textures for an empowering and intuitive experience.
