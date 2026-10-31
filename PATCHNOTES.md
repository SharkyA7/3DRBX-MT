# UV Editor — Patch Notes
**Version 2.0.0** — Saturday, October 31, 2026

---

## 🎉 Major Release: UV Checker → UV Editor

Complete transformation from a UV inspection tool into a full-featured **UV Editor** with device-adaptive UI, live 3D viewport, and professional UV editing tools ported from the Mio3 UV Blender addon.

---

## 🖥️ Device-Adaptive UI System

| Device | Layout |
|--------|--------|
| **Desktop / PC** | Full sidebars (Tools left, Options right), top bar with camera toggle |
| **Laptop** | Medium sidebars, optimized for trackpad |
| **Tablet / iPad** | Sidebars → bottom sheets (slide+fade), touch-first |
| **Mobile / iPhone** | Compact full-height sheets, fullscreen option |

- **First-visit device picker modal** with localStorage persistence
- **Loading screen** with progress bar for device-specific asset initialization
- **Settings modal** (top-right gear icon):
  - Layout width: Small / Medium / Wide / Custom slider
  - Color pickers: Background, Stroke, Accent
  - **Boost Mode** ⚡ — performance mode with resolution warning

---

## 🎮 Live 3D Viewport

- **Orbit / Pan / Zoom** with device-appropriate controls
- **Camera modes**: Perspective (3D) ↔ Orthographic (2D) — top bar toggle
- **Real-time checker texture** with configurable grid (8–64), patterns (Rainbow/Numbered/BW), glyphs
- **WebGL context loss recovery** with auto-reinit
- **Brave Shields detection** with helpful local-first messaging

---

## 🔍 UV Analysis Features (4 Modes)

| Mode | Description |
|------|-------------|
| **Problem Finder** | Flipped (red), Zero-area (amber), Outside 0–1 (cyan) — per-triangle |
| **Texel Density** | Area-weighted median, heatmap (blue→red), global/per-mesh normalize |
| **Overlap Detection** | 1024²/2048² rasterization, top-left fill rule, tile (0,0) only |
| **2D UV Map** | Full-screen modal with overlays, background picker, PNG export |

All analysis runs **chunked via `requestAnimationFrame`** — handles 200k+ triangle meshes without blocking.

---

## ✂️ UV Editing Tools (6 — Ported from Mio3 UV)

| Tool | Shortcut | Description |
|------|----------|-------------|
| **Straighten** | — | Align selected edges to nearest horizontal/vertical axis |
| **Align** | — | Snap selected vertices to 0.125 grid |
| **Relax** | — | Laplacian smoothing (5 iterations) |
| **Unwrap** | — | Planar projection from dominant axis |
| **Pack** | — | Grid bin-packing with padding |
| **Organize** | — | Uniform scale + grid layout for all islands |

> **Workflow**: Select islands in 2D UV Map → Click tool in left sidebar → Click **Apply** in right sidebar

---

## 🎯 Selection & Transform System

### Selection Modes (keys **1–4**)
- **1** Vertex • **2** Edge • **3** Face • **4** Island

### Transform Tools (keys **V/G/R/S**)
- **V** Select • **G** Move • **R** Rotate • **S** Scale

### Gizmo (3D Viewport)
- Click-drag **axis handles** (X=red, Y=green, Z=blue) to transform
- Modes: Translate / Rotate / Scale — switched via **G/R/S** during transform
- OrbitControls auto-disabled during gizmo drag

### 2D UV Map Selection
- **Click** to select (mode-dependent)
- **Shift+drag** for box selection
- **Ctrl+I** invert • **Ctrl+Shift+I** select connected • **Escape** clear

### Selection Highlights
- Vertices: cyan dots • Edges: amber lines • Faces: cyan fill • Islands: green outlines
- Status in bottom bar: "Selected: 42 verts, 3 islands"

---

## 📤 Export

| Format | Features |
|--------|----------|
| **OBJ** | Custom exporter — vertices, UVs (`vt`), normals (`vn`), proper face indices |
| **PNG** | 2D UV Map with background (black/white/transparent/dark-grid) + active overlay |

---

## ⌨️ Keyboard Shortcuts Reference

| Key | Action |
|-----|--------|
| **1 / 2 / 3 / 4** | Vertex / Edge / Face / Island selection mode |
| **V / G / R / S** | Select / Move / Rotate / Scale tool |
| **G / R / S** (in transform) | Switch gizmo mode |
| **Shift + drag** | Box select (2D UV Map) |
| **Ctrl + I** | Invert selection |
| **Ctrl + Shift + I** | Select connected |
| **Escape** | Clear selection |
| **Delete / Backspace** | Clear selection |

---

## 🛠️ Technical Highlights

- **Zero dependencies** beyond Three.js r160 (importmap) + JSZip (CDN)
- **CSP compliant** — only `cdn.jsdelivr.net` + `cdnjs.cloudflare.com`
- **100% local** — no trackers, no server calls, no analytics
- **Testable architecture** — analysis math isolated from DOM/Three.js
- **Defensive coding** — WebGL error recovery, context loss handling, Brave detection
- **Mobile-first CSS** — 360px min, 44px touch targets, CSS custom properties
- **Slide+fade animations** — `cubic-bezier(0.25,0.46,0.45,0.94)` easing

---

## 🐛 Fixes & Improvements

- Fixed base UV canvas transparency (was baked dark)
- Fixed PNG download not applying background color
- Fixed `totalTris` counting bug in chunked analysis
- Fixed "Analyzing UVs" hanging — added 1s elapsed timer
- Fixed Brave Shields false positives — softer messaging
- Fixed WebGL crashes on large textures (>10MB warning)
- Fixed `undefined target` error on file input

---

## 📁 Files Changed

- `uv-checker.html` → **uv-checker.html** (4559 lines, ~140KB)
  - Complete rewrite: CSS layout system, HTML modals/sheets, JS module
  - All features in single file for zero-build deployment

---

## 🙏 Credits

- **Mio3 UV** (Blender addon) — reference implementation for UV tools
- **Three.js** — WebGL rendering
- **JSZip** — ZIP import support

---

**Run locally:** Open `uv-checker.html` in any modern browser (Chrome, Firefox, Safari, Edge). For Brave: disable Shields if WebGL fails.