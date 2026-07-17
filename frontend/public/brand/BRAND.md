# ECO — Brand Identity

**Enterprise Change Orchestrator**

One mark, three letters. The open ring is the **O**; its sweep reads as a **C**; three rounded bars inside form the **E**. The middle bar extends through the ring's opening and terminates in a node — with the two arc tips, the mark is a three-node **dependency graph**: enterprise systems, connected, with data flowing outward. That is the product: change enters the enterprise, ECO shows where it lands.

---

## Assets

| File | Use |
|---|---|
| `eco-logo-dark.svg` | Full lockup on dark surfaces (`#09090B`) |
| `eco-logo-light.svg` | Full lockup on light surfaces |
| `eco-mark-mono.svg` | Standalone mark, inherits `currentColor` — nav bars, embedded UI |
| `../favicon.svg` | Browser favicon (dark tile, legible at 16 px) |
| `../app-icon-512.svg` | App icon 512×512 (export to PNG for stores/OS) |

React components: `src/components/Logo.tsx` exports `<Logo />` (lockup) and `<LogoMark />` (mark only) with `variant="blue" | "white" | "mono"`.

**Rules**

- Never place gradients *inside* the mark. Solid strokes only. (A tile wash behind it, as in the app icon, is fine.)
- Clear space: half the mark's height on all sides.
- Minimum size: 16 px (mark), 96 px wide (lockup).
- Don't rotate, outline, shadow, or re-color the nodes separately from the strokes except as specified below.

---

## Color palette

| Token | Hex | Role |
|---|---|---|
| `eco.blue` | `#0F62FE` | Primary — the mark, actions, focus (IBM Blue 60) |
| `eco.blue.deep` | `#0043CE` | Hover / pressed, nodes on light surfaces (IBM Blue 70) |
| `eco.accent` | `#4DA3FF` | Graph nodes on dark, data-flow highlights |
| `eco.ink` | `#09090B` | Dark canvas |
| `eco.surface` | `#121619` | Raised dark surfaces, light-mode text (Cool Gray 100) |
| `eco.paper` | `#FFFFFF` | Light canvas, dark-mode text |
| `eco.gray` | `#697077` | Secondary text (Cool Gray 60) |
| `eco.positive` | `#42BE65` | Success / checkmarks (IBM Green 40) |

On dark: text at `#FFFFFF`, secondary at 46–62 % white. On light: text `#121619`, secondary `#697077`.

---

## Typography

| Role | Face | Weight | Notes |
|---|---|---|---|
| Display / wordmark | **IBM Plex Sans** | 600 | Wordmark tracks at `0.30–0.42em`; tighten display headings to `-0.02em` |
| Body / UI | IBM Plex Sans | 400–500 | 15 px base, 1.6 line height |
| Data / terminal | **IBM Plex Mono** | 400–500 | Boot logs, asset IDs, code, metrics |

The wordmark is always **ECO** set in Plex Sans 600, wide-tracked, with the subline `ENTERPRISE CHANGE ORCHESTRATOR` in 400 uppercase at ~34 % tracking. Never a different face.

---

## Design tokens

```json
{
  "color": {
    "brand":   { "primary": "#0F62FE", "deep": "#0043CE", "accent": "#4DA3FF" },
    "surface": { "ink": "#09090B", "raised": "#121619", "paper": "#FFFFFF" },
    "text":    { "onDark": "#FFFFFF", "onDarkMuted": "rgba(255,255,255,0.55)",
                 "onLight": "#121619", "onLightMuted": "#697077" },
    "status":  { "positive": "#42BE65", "warning": "#F1C21B",
                 "danger": "#DA1E28", "elevated": "#FF832B" }
  },
  "font": {
    "sans": "\"IBM Plex Sans\", system-ui, sans-serif",
    "mono": "\"IBM Plex Mono\", ui-monospace, monospace"
  },
  "radius": { "control": 2, "tile": 14, "appIcon": 112 },
  "motion": {
    "ease":  { "enter": [0.16, 1, 0.3, 1], "exit": "easeInOut", "ambient": "easeInOut" },
    "duration": { "micro": 0.26, "enter": 0.7, "exit": 0.65, "ambient": 26 }
  }
}
```

---

## Motion guidelines

The brand moves like the product thinks: **deliberate, quiet, precise.**

1. **One ease for arrivals** — `cubic-bezier(0.16, 1, 0.3, 1)` (a long-tail ease-out). Exits use `easeInOut`. Nothing bounces except graph nodes, which may land with a tight spring (`stiffness ≥ 450, damping ≥ 16`).
2. **Ambient motion is glacial** — background drift cycles run 20–35 s. If you can *watch* it move, it's too fast.
3. **Draw, don't pop** — strokes (mark, checkmarks, edges) animate `pathLength 0 → 1`. Nodes scale in after their edge arrives: structure first, then endpoints.
4. **One glow per sequence** — a single soft radial pulse is the ceiling. No persistent glows, no neon.
5. **Stagger in the 45–120 ms range** — siblings enter as a cascade, never simultaneously, never slower than 120 ms apart.
6. **Respect `prefers-reduced-motion`** — every sequence has a static or single-fade equivalent. Canvas animation pauses entirely.
7. **60 fps or cut it** — animate only `transform`, `opacity`, and SVG stroke properties. Particle fields render to one canvas.

Splash choreography (the canonical example): dark → particles (0.3 s) → connections (0.8 s) → morph (1.3–1.8 s) → mark settles +2° (2.4 s) → single pulse (2.8 s) → wordmark (3.0 s) → boot log (3.5 s) → exit by ~5.2 s. Total stays inside 4–6 s.
