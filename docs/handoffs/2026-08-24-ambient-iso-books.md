# Handoff: ambient 3D iso books + vibe fixes (feat/ambient-iso-books)

## Where things stand

Branch `feat/ambient-iso-books` is checked out in the **main checkout** (user-approved) and
is 7 commits ahead of `origin/main`. Not pushed, no PR yet. Latest commits:

- `9ba9ffd` piles of 2-3 only, edge/phase randomizer, pink dropped from vibe palette — **committed but NOT visually verified**
- `1408a7e` single-sentence wrapping vibe with persistent colour
- `dfc7215` uniform book size, centred tilt, per-load randomized layout
- `770f5e7` dropped the 8-bit SVG filter (user chose "no filter" from a live A/B)
- earlier: the closed six-face 3D book geometry itself

The feature: home-page ambient background of small flat 3D books drifting upward in
piles. Each book is a closed six-face preserve-3d box (`.ambient-book` in
`static/src/input.css`, markup in `core/templates/core/home.html` around line 110).
All books lie flat (`--flat`); piles = sibling books sharing `--x/--d/--delay/--r/--s`
(lockstep drift) with `--yo` vertical stagger and `--rz` tilt jitter. An inline
`<script>` in home.html shuffles pile positions/phases per load.

Key vars on `.ambient-book`: `--s` scale, `--tk` thickness factor, `--yo` pile lift,
`--rz` in-plane tilt (flat books only), `--t = 11px·s·tk` extrusion.
Pile math: projected thickness step u = 9.33·s·tk px; `--yo` of book i = running sum of
`u·(tk_below + tk_i)/2` (books are anchored at volume centre since the
`transform-origin: 50% 50% calc(var(--t)/-2)` change).

## How to run / verify

- Docker stack serves the main checkout on :8000 (`docker-compose -f docker-compose.local.yml up -d`).
  **Always hard-refresh** — Django dev serves output.css with heuristic caching and stale
  CSS makes the books render as broken grey rectangles.
- Rebuild CSS after editing `static/src/input.css`: `pnpm run build`.
- If the web container crash-loops with `OSError: [Errno 35] Resource deadlock avoided`
  on some file: recreate that file's inode (`cp x tmp && mv x old && mv tmp x && rm old`) — macOS bind-mount glitch.
- A static test harness with big zoomed books lives in the parked worktree:
  `.claude/worktrees/iso-books/.harness/inspect.html` (worktree is detached at `2931c7e`,
  kept only for the harness; the branch itself lives in the main checkout now).

## Remaining work (user-requested, in priority order)

Items 1–5 are DONE (2026-08-24 session): the `9ba9ffd` state was verified on :8000;
piles are now `.ambient-pile` wrapper divs owning `--x/--d/--delay/--r/--s`, the drift
animation (`pile-drift`), the `--r` pivot origin and a group `opacity: 0.7` (user asked
for more transparency than 0.9; books themselves back to opacity 1 so they occlude
correctly); each pile has its own `--s` (0.5–0.7) with `--yo` recomputed per pile and a
`--py` var (mean of the pile's `--yo`s) used in the pivot correction
`transform-origin: 50% calc(50% + 4.6px·--s − --py)`; the randomizer splits edges
evenly (odd pile → random side), places at most one middle pile on ~25% of loads, and
skips `display: none` piles so mobile always gets 2 left / 2 right.

6. **Bring back the 1px pixelate filter as a preview.** User wants to see the subtle
   variant again before final call. Re-add the SVG filter (cell 1: `feFlood x=0 y=0
   w=1 h=1; feComposite w=1 h=1; feTile; feComposite in=SourceGraphic operator=in;
   feMorphology dilate radius=0.5`) plus the temporary bottom-left toggle from git
   history (`git show 9ba9ffd~4:core/templates/core/home.html` has the toggle pattern —
   it was removed in `770f5e7`). Let the user click between none/1px live, then strip
   the toggle and keep the winner.

7. **Logo: 3-book stack + DNA strand, replacing the 🧬 emoji.** Emoji lives in
   `core/templates/core/base.html` line 97 (SVG favicon data-URI) and line 203 (navbar
   `🧬<span>Bibliotype</span>`). Generate candidates with the `gemini-imagegen` skill
   using the prompt below, iterate with the user, then wire the chosen asset in as an
   inline SVG/img in the navbar and regenerate the favicon. **Image-gen prompt (paste
   and vary):**

   > Flat isometric pixel-art logo mark for "Bibliotype", a reading-analytics web app
   > with a neobrutalist 8-bit aesthetic. Subject: a tidy stack of three hardcover
   > books in 3/4 isometric view, the top book slightly askew (~6 degrees). Bold
   > dark-navy (#1f2937) 2px outlines, flat saturated pastel fills using only this
   > palette: pink #ffb4dd, cyan #8bbfff, yellow #ffe56c, green #40e7aa, orange
   > #ffa75e. Covers carry a subtle two-tone checkerboard dither texture; fore-edge
   > pages drawn as thin parallel lines. A DNA double helix winds vertically through
   > the stack like a bookmark ribbon — two intertwined strands emerging from between
   > the pages of the top book and wrapping down the front corner, connected by short
   > rungs. Crisp hard pixel edges, no gradients, no soft shadows (one hard offset
   > shadow in dark navy is fine), transparent background, centered composition, must
   > stay legible at 32x32 px.

   Variants to explore: (a) helix as bookmark ribbon (above); (b) helix formed out of
   the books' own fore-edge page lines connecting across the three books; (c) helix as
   a vertical element standing behind/beside the stack; (d) minimal mark — two
   intertwined ribbons that only hint at book covers. VT323 wordmark stays separate;
   logo is the mark only.

## Git bookkeeping

- When the user is happy: push `feat/ambient-iso-books`, open PR to `main`. Merging
  auto-deploys to prod.
- Stray commit `c5c4af3` (.env-guard settings): DONE — cherry-picked onto
  `chore/env-guard-permissions`, merged as PR #159.
- `.testing-screenshots/` is untracked scratch; leave it.

## Vibe changes already shipped on this branch (context)

`core/services/llm_service.py` prompts Gemini for exactly ONE lowercase sentence
(`vibe_phrases` list of 1, service slices `[:1]`). Colour is minted server-side at
generation (`VIBE_COLORS`, no pink) and persisted as `dna_data["reading_vibe_color"]`
in `core/services/dna/__init__.py` (~line 829); cached vibes reuse their stored colour.
`partials/dna/vibe_display.html` renders one wrapping span (`box-decoration-clone`),
fallback colour `#8bbfff` for pre-change profiles. Vibe-adjacent tests pass (60).
