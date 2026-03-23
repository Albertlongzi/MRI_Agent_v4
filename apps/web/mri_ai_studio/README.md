# MRI AI STUDIO — Frontend

> Developed by **ZIYANG** · Connects to `MRI_Agent_v4` backend

A framework-free clinical radiology workstation UI — pure HTML + CSS + JS, no build step required. Siemens Healthineers-inspired dark theme (Healthy Orange · Petrol · Black).

---

## Quick Start

### 1. Local preview (no backend)

```bash
cd apps/web
python3 -m http.server 8001
# → open http://127.0.0.1:8001
```

The UI loads a built-in fallback snapshot (prostate demo case) so you can inspect the layout and Reflector error states without any backend.

---

### 2. Connect to HPC backend

The backend (`MRI_Agent_v4/apps/api`) must be reachable from your browser. Two patterns:

#### Option A — SSH tunnel (recommended)

On your **local machine**:

```bash
# Forward HPC port 18008 → local 18008
ssh -N -L 18008:localhost:18008 <hpc-user>@<hpc-host>
```

Then open `apps/web/index.html` and add one line **before** `<link rel="stylesheet" …>`:

```html
<script>window.API_BASE_URL = "http://127.0.0.1:18008";</script>
```

#### Option B — Serve frontend from FastAPI (same origin)

Copy `apps/web/` into the backend's static directory and mount it:

```python
# In apps/api/main.py
from fastapi.staticfiles import StaticFiles
app.mount("/static", StaticFiles(directory="apps/web"), name="static")
```

Then visit `http://<hpc-node>:18008/static/index.html`.  
No `API_BASE_URL` needed — same origin, no CORS.

#### Option C — Separate dev server (Vite / any)

```bash
cd apps/web
# serve with any static server
npx serve .        # or
python3 -m http.server 8001
```

Set in `index.html`:
```html
<script>window.API_BASE_URL = "http://127.0.0.1:18008";</script>
```

---

## File Structure

```
mri_ai_studio/
├── README.md
├── apps/
│   └── web/
│       ├── index.html      # Shell HTML — references styles.css + app.js
│       ├── styles.css      # All styling (CSS variables, layout, themes)
│       ├── app.js          # All UI logic (state, render, API calls)
│       └── serve.sh        # Quick local preview helper
└── workstation.html        # Self-contained single-file version (all inlined)
```

> **`workstation.html`** is a single-file build (CSS + JS inlined) — open directly in any browser for instant preview or sharing. The `apps/web/` split version is for HPC deployment.

---

## API Surface

The frontend calls these endpoints on `API_BASE_URL` (default: same origin):

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/health` | System health |
| `GET` | `/api/planner/health` | Brain LLM status |
| `GET` | `/api/session` | Full session snapshot |
| `GET` | `/api/graph` | Current ActionGraph |
| `GET` | `/api/events` | Event timeline |
| `POST` | `/api/chat` | Send message to Brain |
| `POST` | `/api/patch` | Insert review checkpoint |
| `POST` | `/api/proposals/apply-latest` | Apply latest proposal |
| `POST` | `/api/execute/next` | Step pipeline forward |
| `POST` | `/api/reset` | Reset session |
| `GET` | `/api/tools` | Tool catalog |
| `GET` | `/api/domains` | Domain catalog |
| `GET` | `/api/capabilities` | Capability summary |
| `GET` | `/api/tools/bridge/health` | Tool bridge status |
| `GET` | `/artifacts/…` | Artifact file serving |

The backend must allow CORS from your frontend origin. `MRI_Agent_v4` already sets:
```python
allow_origins=["*"]
allow_methods=["*"]
allow_headers=["*"]
```

---

## UI Layout

```
┌─────────────────────────────────────────────────────────────────┐
│  MRI AI STUDIO    [Case] [Domain] [Status]    [●connected] [src] │  ← Topbar
├──────────┬──────────────────────────────────────┬───────────────┤
│          │  Pipeline ▷ Execute  Apply  Reset     │  Inspector    │
│  Chat    ├──────────────────────────────────────┤  ┌──────────┐ │
│          │  ⬡──⬡──⬡──⬡──⬡──⬡  (drag nodes)     │  │Node      │ │
│  [msg]   ├──────────────────────────────────────┤  │Events    │ │
│  [msg]   │  Viewer                              │  │Tools     │ │
│          │  [artifact preview]  [artifact rail] │  └──────────┘ │
│  [input] │                                      │               │
└──────────┴──────────────────────────────────────┴───────────────┘
         ↕ drag handles between all panels
```

All panel borders are draggable — left/right column widths and graph/viewer height split are all resizable at runtime.

---

## Reflector (Error Recovery) UX

When a node fails, the Reflector mechanism is displayed across three areas simultaneously:

### 1. Graph Canvas
- Failed node: **red pulsing border** + `✕` icon + truncated error message on the card
- Reflecting node: **orange breathing glow** (Brain LLM analyzing)
- Retrying node: **petrol/teal glow** (re-running with fixed params)
- Pipeline strip dots change color to match state

### 2. Reflector Banner (top of Pipeline panel)
- Orange gradient bar with spinner
- Shows current phase: `Brain LLM analyzing…` → `Retrying…` → `Resolved`
- **Retry Now** button — triggers `/api/execute/next`
- **Dismiss** button — hides banner without acting

### 3. Inspector → Node tab
When a failed node is selected:
- **⚠ Error Details** — full error message + timestamp
- **🧠 Brain LLM Analysis** — Reflector's reasoning for the fix
- **Retry History** — table of all attempts with status and error

### 4. Chat Panel
Reflector messages appear as a distinct `⟳ Brain Reflector` role with orange left-border, showing error → analysis → proposed fix in natural language.

---

## Configuration

### `window.API_BASE_URL`
Set in `index.html` before loading `app.js`:
```html
<script>window.API_BASE_URL = "http://127.0.0.1:18008";</script>
```
Defaults to `""` (same origin).

### Fallback demo data
If the backend is unreachable, the UI loads a built-in prostate case demo:
- 6-node pipeline (Case Intake → Identify Sequences → Register ADC → Segment → Package Evidence → Report)
- `register_adc` pre-set to **failed** state with full Reflector demo
- Chat history pre-populated with Reflector message

---

## Browser Support

Modern browsers only (Chrome 90+, Firefox 88+, Safari 14+, Edge 90+).  
Uses: CSS Grid, CSS custom properties, `pointer-events`, `ResizeObserver` (optional).

No npm, no build step, no framework dependencies.

---

## Deployment Checklist

```
□  Backend running on HPC node (port 18008 or similar)
□  SSH tunnel open if accessing from local browser
□  window.API_BASE_URL set in index.html (if not same-origin)
□  /artifacts/ route served by backend (for artifact file preview)
□  CORS headers set on backend (already done in MRI_Agent_v4)
□  Open index.html in browser — status badge turns green when connected
```
