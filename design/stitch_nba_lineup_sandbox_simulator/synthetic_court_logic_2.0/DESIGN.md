---
name: Synthetic Court Logic 2.0
colors:
  surface: '#0c1324'
  surface-dim: '#0c1324'
  surface-bright: '#33394c'
  surface-container-lowest: '#070d1f'
  surface-container-low: '#151b2d'
  surface-container: '#191f31'
  surface-container-high: '#23293c'
  surface-container-highest: '#2e3447'
  on-surface: '#dce1fb'
  on-surface-variant: '#bcc9cd'
  inverse-surface: '#dce1fb'
  inverse-on-surface: '#2a3043'
  outline: '#869397'
  outline-variant: '#3d494c'
  surface-tint: '#4cd7f6'
  primary: '#4cd7f6'
  on-primary: '#003640'
  primary-container: '#06b6d4'
  on-primary-container: '#00424f'
  inverse-primary: '#00687a'
  secondary: '#a4d64c'
  on-secondary: '#233600'
  secondary-container: '#719e13'
  on-secondary-container: '#1e2f00'
  tertiary: '#ffb873'
  on-tertiary: '#4b2800'
  tertiary-container: '#e89337'
  on-tertiary-container: '#5b3200'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#acedff'
  primary-fixed-dim: '#4cd7f6'
  on-primary-fixed: '#001f26'
  on-primary-fixed-variant: '#004e5c'
  secondary-fixed: '#bff365'
  secondary-fixed-dim: '#a4d64c'
  on-secondary-fixed: '#131f00'
  on-secondary-fixed-variant: '#354e00'
  tertiary-fixed: '#ffdcbf'
  tertiary-fixed-dim: '#ffb873'
  on-tertiary-fixed: '#2d1600'
  on-tertiary-fixed-variant: '#6a3b00'
  background: '#0c1324'
  on-background: '#dce1fb'
  surface-variant: '#2e3447'
  obsidian-base: '#020617'
  charcoal-surface: '#0f172a'
  electric-cyan: '#06b6d4'
  neon-volt: '#bef264'
  data-gray: '#94a3b8'
typography:
  headline-xl:
    fontFamily: Hanken Grotesk
    fontSize: 48px
    fontWeight: '800'
    lineHeight: '1.1'
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Hanken Grotesk
    fontSize: 32px
    fontWeight: '700'
    lineHeight: '1.2'
    letterSpacing: -0.01em
  headline-md:
    fontFamily: Hanken Grotesk
    fontSize: 24px
    fontWeight: '600'
    lineHeight: '1.3'
  body-lg:
    fontFamily: Hanken Grotesk
    fontSize: 18px
    fontWeight: '400'
    lineHeight: '1.6'
  body-md:
    fontFamily: Hanken Grotesk
    fontSize: 16px
    fontWeight: '400'
    lineHeight: '1.5'
  label-mono-lg:
    fontFamily: IBM Plex Mono
    fontSize: 14px
    fontWeight: '600'
    lineHeight: '1.2'
    letterSpacing: 0.05em
  label-mono-sm:
    fontFamily: IBM Plex Mono
    fontSize: 12px
    fontWeight: '500'
    lineHeight: '1.2'
    letterSpacing: 0.1em
  headline-lg-mobile:
    fontFamily: Hanken Grotesk
    fontSize: 28px
    fontWeight: '700'
    lineHeight: '1.2'
spacing:
  grid-unit: 4px
  gutter: 16px
  margin-mobile: 16px
  margin-desktop: 32px
  container-max: 1440px
---

## Brand & Style

The design system is engineered for the "Basketball Lab"—a high-performance analytical environment where data is the primary athlete. It departs from traditional sports broadcasting aesthetics in favor of a **Dark Tech** aesthetic that feels like a specialized forensic software for court strategy. 

The visual narrative is defined by:
- **Precision Engineering:** Sharp edges, technical monospaced accents, and structural grid overlays.
- **The "Glow" State:** Vital data points and active metrics emit a subtle luminescence, simulating a high-tech HUD or laboratory monitor.
- **Tactical Neutrality:** Using a deep obsidian backdrop to ensure that offensive (cyan) and defensive (volt) metrics command equal visual attention without chromatic bias.
- **Movement:** Subtle grid patterns and scan-line textures evoke a sense of real-time processing and spatial awareness.

## Colors

The palette is anchored in a deep **Obsidian Base** to eliminate visual noise and provide maximum contrast for analytical overlays. 

- **Primary (Electric Cyan):** Reserved for offensive metrics, shot-clock data, and "active" possession states.
- **Secondary (Neon Volt):** Designated for defensive pressure ratings, steals, blocks, and "suppression" zones.
- **Neutral:** A range of deep charcoals and slate grays used for structural elements like containers and inactive grid lines.
- **Hierarchy:** Both Primary and Secondary colors carry equal saturation and brightness to ensure neither phase of the game (offense or defense) is visually subordinated.

## Typography

Typography focuses on high-density information display. 

- **Hanken Grotesk** is used for primary UI elements and large headings to provide a modern, clean, and highly legible foundation.
- **IBM Plex Mono** is used for all "Data Units"—numbers, percentages, timestamps, and technical labels. This creates a clear mental distinction between descriptive text and analytical data.
- **Styling:** Headings should be set in tight leading to feel compact and "heavy." All mono labels should be uppercase to enhance the "system readout" feel.

## Layout & Spacing

This design system utilizes a **Technical Grid** model. All spacing is derived from a 4px base unit to ensure alignment with the visual grid patterns in the background.

- **The Laboratory Grid:** A subtle, low-opacity 32px square grid should be visible in the background of main dashboard views.
- **Structure:** Use a 12-column fluid grid for desktop with 16px gutters. For mobile, shift to a 4-column layout with consistent 16px margins.
- **Data Density:** Layouts should favor high information density. Components are packed tightly, using thin borders rather than large gaps to define boundaries.

## Elevation & Depth

Depth is conveyed through **chromatic layering and luminescence** rather than realistic shadows.

- **Surface Tiers:** The background is the lowest level (#020617). Card surfaces sit one tier above (#0f172a) with 1px solid borders (#1e293b).
- **Glow Borders:** High-priority cards (active plays) use a `box-shadow` with 0px blur and a 1px spread of the primary or secondary color to simulate a neon-lit edge.
- **Inner Depth:** Use "Inert" vs "Active" states. Active elements use a subtle inner glow (0px blur, 2px spread, 10% opacity) of the brand color to appear energized.
- **Glassmorphism:** Use only for temporary overlays (modals/tooltips) with a heavy backdrop blur (12px) and a semi-transparent dark tint.

## Shapes

The shape language is strictly **Sharp (0px)**. 

- All buttons, cards, input fields, and containers must have 90-degree corners. This reinforces the "engineered" laboratory aesthetic.
- **Exceptions:** Small status indicators (dots) or circular player avatars are permitted to maintain standard recognition patterns, but they should be housed within square framing elements where possible.
- **Notches:** Use 45-degree clipped corners (chamfers) for high-level tactical tabs or "action" buttons to add a futuristic, military-grade interface feel.

## Components

- **Buttons:** Sharp-edged with a 1px border. Primary buttons use a solid fill of Cyan or Volt with black text. Secondary buttons are transparent with colored borders and text.
- **Data Cards:** Dark charcoal backgrounds with a 1px slate border. Top corners may feature a small "tag" in IBM Plex Mono indicating whether the data is Offensive or Defensive.
- **Input Fields:** Darker than the card surface. On focus, the border glows with the Primary color and the grid pattern within the field becomes more prominent.
- **Chips / Tags:** Small, rectangular containers with monospaced text. Use Cyan for "Offense/Scoring" and Volt for "Defense/Stops."
- **Progress Bars / Gauges:** Use "Segmented" bars (vertical line dividers every 5%) to visualize percentages, evoking a hardware diagnostic feel.
- **Radar Charts:** Central to the "Lab" theme, these should use thin, high-contrast lines with Cyan and Volt overlays to compare offensive and defensive efficiency.