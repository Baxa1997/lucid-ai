# AI dev tool for backend engineers — Variant A

===LAYOUT_BLUEPRINT===

hero_archetype:
  product-showcase
  reasoning: For a dev tool, direct visibility of the product in action, even if conceptual, builds immediate trust and understanding.

features_archetype:
  bento-mixed

card_language:
  Cards will feature a subtle `border-2 border-gray-800` with `rounded-xl` corners. A `shadow-lg shadow-primary-900/20` will provide depth, and a small `::before` pseudo-element with a `bg-gradient-to-br from-primary-500 to-accent-600` will appear on hover as a top-left indicator.

typography_pairing:
  Heading font: `IBM Plex Mono` (Weights: `700` for main headings, `600` for subheadings, `letter-spacing: -0.02em`). Body font: `Inter` (Weights: `400` for paragraphs, `500` for emphasis, `line-height: 1.6`, `tracking-normal`).

motion_language:
  Enter animation: Elements `fade-in-up` with `y: 20px`, `opacity: 0` to `y: 0`, `opacity: 1`. Hover state: `scale-103` with a `subtle glow` for interactive elements. Scroll-linked behavior: `parallax` for background elements, `sticky` for navigation. Emotional register: `precise`, `responsive`, `empowering`.

decorative_pattern:
  Subtle hexagonal grid pattern (`background-image: url('/hex-grid.svg')`) applied to `::before` pseudo-elements on section containers with `opacity: 0.03`, `background-size: 200px`, `background-repeat: repeat`.

border_radius_language:
  Philosophy: A balance of modern sharpness and approachable softness, reflecting precision and user-friendliness. Buttons: `rounded-md`. Cards: `rounded-xl`. Images: `rounded-lg`. Inputs: `rounded-md`.

color_application_strategy:
  inverted-dark. Primary background `bg-gray-950`, text `text-gray-100`. Accent colors (`primary-500` for interactive elements, `accent-600` for highlights) applied to CTAs, progress indicators, and key data visualizations. Section headers will use `text-primary-300`.

hover_interaction_style:
  Primary cards: `transform scale-[1.01] translate-y-[-2px] shadow-2xl shadow-primary-900/30 transition-all duration-300 ease-out`. CTAs: `bg-gradient-to-br from-primary-600 to-accent-700 transform translateY-[-1px]`. Image cards: `brightness-110 saturate-120`. Nav links: `text-primary-400 border-b-2 border-primary-400`.

spacing_rhythm:
  dense-information

live_ui_recipe:
  The signature motion moment is a "Code Stream Reveal" in the hero section. As the user scrolls down, a simulated code editor window animates into view, lines of code `type-out` rapidly, showcasing a key feature. Supporting micro-interactions include `tooltip` hovers on syntax elements, revealing contextual help, and `progress bar` animations for task completion indicators, reflecting the backend development workflow. Interactive code snippets will allow users to `toggle` between different language examples with a smooth `cross-fade` transition.

scroll_reveal_style:
  `y: 30`, `duration: 0.8`, `easing: [0.2, 0.6, 0.3, 0.9]`, `staggerChildren: 0.15`, `viewport: { once: true, amount: 0.4 }`.

ambient_motion:
  grain-noise. Applied as a `::before` pseudo-element on the main content wrapper: `content: ''; position: fixed; top: 0; left: 0; width: 100%; height: 100%; pointer-events: none; background-image: url('/noise.png'); opacity: 0.02; z-index: -1; animation: grain 8s steps(10) infinite;`. JSX placement: `div` wrapping main content.

design_dna_summary:
  A precise, dark-themed interface with subtle code-inspired patterns, responsive motion, and clear product showcases, empowering backend engineers.
