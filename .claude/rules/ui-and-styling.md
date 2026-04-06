---
paths:
  - "core/templates/**/*.html"
  - "static/**"
---

# UI & Styling Patterns

## Design System: Neobrutalist

Hard 2px borders, heavy offset shadows, bright saturated colors, VT323 retro monospace font, no rounded corners.

## Tailwind @theme Variables (`static/src/input.css`)

**Colors:**
- `brand-background` (#f5f5f5), `brand-text` (#1f2937)
- Accents: `brand-yellow`, `brand-orange`, `brand-pink`, `brand-cyan`, `brand-green`, `brand-purple`
- Badge scale: `badge-5` (strongest, green) → `badge-2` (weakest, light)

**Shadows:**
- `shadow-neo`: `4px 4px 0px 0px var(--color-brand-text)` (standard)
- `shadow-neo-sm`: `2px 2px 0px 0px var(--color-brand-text)` (small)

**Font:** `--font-sans: "VT323", ui-sans-serif, system-ui, sans-serif`

## Component Patterns

**Card:**
```html
<div class="border-brand-text shadow-neo border-2 bg-white p-6">
```

**Button (all buttons follow this):**
```html
<button class="bg-brand-green shadow-neo border-brand-text border-2 px-4 py-3 font-bold
    transition-all duration-150 ease-in-out hover:shadow-none
    active:translate-x-1 active:translate-y-1 cursor-pointer">
```
- Hover removes shadow (pressed effect)
- Active translates 0.5-1px (tactile feedback)

**Button partials** in `templates/core/partials/buttons/`: `primary_button`, `secondary_button` (multi-color/size), `nav_button`, `small_button`, `small_link_button`, `link_button`, `close_button`, `icon_button`. Include with context vars like `text`, `color`, `size`, `hover_color`.

**DNA card partials** in `templates/core/partials/dna/`: 16 components for dashboard display. Include via `{% include %}` with `dna`, `pronoun_pos`, `enrichment` context.

## Alpine.js Patterns (v3.x via CDN)

- `x-data` objects on container divs with methods and computed getters
- `[x-cloak]` hides elements until Alpine loads (prevents FOUC)
- `x-teleport="body"` for modals (z-index management)
- `@keydown.escape.window` to close modals
- `Alpine.store('enrichment', {...})` for cross-component state
- Polling: `setInterval` with `fetch()` + `clearInterval` on success

**Common patterns:**
- File upload drag-drop: `@dragover.prevent`, `@drop.prevent`
- Scroll-triggered animations: `IntersectionObserver` with `threshold: 0.3`
- Counter animations: `requestAnimationFrame` with cosine easing over 800ms
- Loading dots: `setInterval(() => dots = dots.length >= 3 ? '' : dots + '.', 500)`

## Chart.js

```javascript
Chart.defaults.font.family = "VT323";
Chart.defaults.font.size = 16;
Chart.defaults.color = "#1f2937";

const chartColors = [
    "#ffb4dd", "#40e7aa", "#ffa75e", "#8bbfff", "#FFE9CE",
    "#ff647c", "#ffe56c", "#A1CDF1", "#9af6d4", "#fe9393"
];
```

- Charts are **scroll-triggered** via `createChartOnScroll()` using IntersectionObserver
- Classes toggle from `.chart-await` (hidden) to `.chart-visible` (shown)
- Canvas drop-shadow: `4px 4px 0px #1f2937`
- Chart config in `templates/core/partials/dna/charts_scripts.html`

## CSS Custom Classes

- `.grid-background`: 2rem grid pattern background
- `.cover-crosshatch`: diagonal stripe pattern for book cover placeholders
- `.scroll-fade-left` / `.scroll-fade-up`: scroll-triggered fade animations (0.6s ease-out)
- `.enrichPulse`: opacity 1→0.5→1 infinite animation for enrichment banners

## Responsive

- Mobile-first with `md:` and `sm:` breakpoints
- Grid: `grid grid-cols-1 gap-6 md:grid-cols-2` or `md:grid-cols-3`
- Container: `container mx-auto max-w-4xl`
- Input focus: `focus:ring-2 focus:ring-brand-purple focus:outline-none`

## Template Inheritance

All pages extend `core/base.html`. Key blocks: `seo_title`, `seo_description`, `og_*`, `twitter_*`, `structured_data`, `extra_head_js`, `content`.

## JavaScript

- **All inline** in templates — no separate JS files
- Libraries via CDN: Alpine.js 3.15.8, Chart.js, html-to-image 1.11.11
- Vanilla ES6+: arrow functions, async/await, optional chaining, template literals
- Data serialization: `{{ dna|json_script:"dna-data" }}` + `JSON.parse()`
