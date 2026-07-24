# Web UI

`apps/web/` is the static frontend for `MRI_Agent_v4`. It is framework-free —
plain HTML + CSS + JS, no build step.

```text
apps/web/
├── index.html    # workstation shell
├── styles.css
├── app.js
└── serve.sh      # standalone static preview
```

## How it is served

- The FastAPI backend (`apps/api/main.py`) mounts this whole directory at
  `/static`.
- `GET /` returns `apps/web/index.html`.
- The page loads `/static/styles.css` and `/static/app.js`.

## Backend routes the UI calls

- `/api/session`
- `/api/graph`
- `/api/events`
- `/api/chat`
- `/api/patch`
- `/api/proposals/apply-latest`
- `/api/execute/next`
- `/api/execute/until-done`
- `/api/reset`
- `/api/tools`
- `/api/domains`
- `/api/capabilities`
- `/api/tools/bridge/health`
- `/api/planner/health`
- `/artifacts/...`

The backend exposes more routes than this (see the top-level `README.md`); the
list above is only what the current UI actually fetches.

## Static preview (no backend)

Layout-only check — every API call will fail:

```bash
cd apps/web
./serve.sh          # http://127.0.0.1:8001
```

## Same-origin run (with backend)

From the repository root:

```bash
.venv/bin/python run_demo.py
```

Then open `http://127.0.0.1:8008/`.

Note that `index.html` pulls webfonts from `fonts.googleapis.com`, so the page
falls back to system fonts on an offline or air-gapped host.
