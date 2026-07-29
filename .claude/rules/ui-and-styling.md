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
- `shadow-neo-md`: `3px 3px 0px 0px var(--color-brand-text)` (hover quarter-press for standard buttons)
- `shadow-neo-sm`: `2px 2px 0px 0px var(--color-brand-text)` (small)
- `shadow-neo-xs`: `1px 1px 0px 0px var(--color-brand-text)` (hover press for `-sm` buttons)

**Font:** `--font-sans: "VT323", ui-sans-serif, system-ui, sans-serif`

## Component Patterns

**Card:**
```html
<div class="border-brand-text shadow-neo border-2 bg-white p-6">
```

**Button (all buttons follow this):**
```html
<button class="bg-brand-green shadow-neo border-brand-text border-2 px-4 py-3 font-bold
    transition-all duration-150 ease-in-out
    hover:translate-x-px hover:translate-y-px hover:shadow-neo-md
    active:translate-x-1 active:translate-y-1 active:shadow-none cursor-pointer">
```
- The shadow's outer edge is the button's fixed "contact point with the page" — it must never move
- Hover subtly depresses the button (translate by 1px while the shadow shrinks by 1px)
- Active presses it flat: translate by exactly the shadow offset while the shadow shrinks to none
- The button only ever moves DOWN toward the page — never lifts on the z-axis
- Pairings: `shadow-neo` (4px) → hover `translate-*-px` + `shadow-neo-md`, active `translate-*-1` + `shadow-none`; `shadow-neo-sm` (2px) → hover `translate-*-px` + `shadow-neo-xs`, active `translate-*-0.5` + `shadow-none`

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
- `.pixel-banner` + `.reader-banner--{color}`: per-reader-type banner texture. Combine both classes. The color variant (yellow/orange/pink/cyan/green/purple) sets `--banner-color`; `.pixel-banner` renders two offset checkerboards over it for a dithered pixel texture. Use the `reader_color` template filter to map a reader type name to the token: `{% load dna_extras %}` then `class="pixel-banner reader-banner--{{ dna.reader_type|reader_color }}"`. All six tokens are hand-written in `static/src/input.css` (not Tailwind-scanned). Unknown type names fall back to `purple` via the filter.

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
