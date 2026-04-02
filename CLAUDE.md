# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## What This Is

FPAmed Box Index Tool — a Next.js 14 web app that lets FPAmed (forensic psychiatry expert witness firm) staff authenticate with Box, pick a case folder, and generate a formatted Excel document index. The Excel report is written back to the selected Box folder automatically.

Core document processing logic lives in `python/manifest.py` (Box folder traversal + metadata extraction), `python/enrich.py` (optional AI enrichment via Box AI `extract_structured`), and `python/report.py` (Excel generation). These are invoked as child processes from the Next.js API layer.

---

## Commands

```bash
npm run dev        # start dev server on localhost:3000
npm run build      # production build (also validates TypeScript)
npx tsc --noEmit   # type-check without building
```

**Python environment** — always use the local venv:
```bash
.venv/bin/python3 python/manifest.py --token <tok> --folder-id <id> --output-dir /tmp/test
.venv/bin/python3 python/enrich.py --manifest-file /tmp/test/slug_manifest.csv --token <tok>
.venv/bin/python3 python/report.py --input-file /tmp/test/slug_manifest.csv --output-file /tmp/test/out.xlsx
```

Install/update Python deps:
```bash
.venv/bin/pip install -r requirements.txt
```

No test suite exists yet.

---

## Architecture

### Auth flow
Box OAuth2 authorization code flow. `/api/auth/login` redirects to Box, `/api/auth/callback` exchanges the code for tokens and stores them in an encrypted HTTP-only cookie via `iron-session`. The session holds `accessToken`, `refreshToken`, `expiresAt`, `userName`, `userEmail`.

`src/lib/box.ts:getFreshToken()` handles silent token refresh (refreshes if within 5 minutes of expiry) and mutates the session in place — callers must `await session.save()` after calling it.

### Job system
`POST /api/generate` creates a job record and fires off `runJob()` as a floating promise (fire-and-forget), returning `{ jobId }` immediately. The frontend polls `GET /api/job/[jobId]` every second.

Job state is a module-level `Map` attached to `global.__jobs`. **This must stay on `global`** — Next.js compiles each route in its own module context in dev mode, so a plain module-level `Map` in `src/lib/jobs.ts` would be invisible to other routes.

Job pipeline: manifest.py → *(optional)* enrich.py → report.py → upload to Box → update job status to `complete` with `boxFileUrl`. The `enrich` flag is passed from the frontend checkbox via `POST /api/generate`.

### Python invocation
`src/app/api/generate/route.ts` auto-detects the venv python at `.venv/bin/python3`, falling back to system `python3`. stdout/stderr lines are captured and appended to `job.log[]` (capped at 20 lines) for display in the UI. The last non-empty output line is surfaced as the error message on non-zero exit.

Intermediate files (manifest CSVs, Excel) are written to a `os.tmpdir()` subdirectory keyed by job ID and cleaned up after the Box upload.

### Frontend
`src/app/page.tsx` is a single client component with three render states gated on `auth` and `job` state:
1. Unauthenticated — connect button
2. Authenticated, no active job — Box Content Picker fills the full viewport
3. Job running/complete/error — spinner + live log buffer, or result/error UI

The Box Content Picker (CDN: `window.Box.ContentPicker`) is initialized in a `useEffect` that depends on `[auth, pickerKey]`. `pickerKey` is incremented by `handleReset()` to force the picker to remount and reinitialize after "Generate another" is clicked — without this, the picker's DOM node is destroyed when State 3 renders and doesn't reconnect on return to State 2.

The `/api/auth/token` endpoint returns only the access token (never the refresh token) for client-side use by the picker.

---

## Environment Variables

Box variables are required. `BOX_AI_MODEL` is optional.

| Variable | Required | Purpose |
|---|---|---|
| `BOX_CLIENT_ID` | Yes | Box Custom App client ID |
| `BOX_CLIENT_SECRET` | Yes | Box Custom App client secret |
| `BOX_REDIRECT_URI` | Yes | Must match redirect URI registered in Box developer console |
| `SESSION_SECRET` | Yes | 32+ char random string for iron-session cookie encryption |
| `BOX_AI_MODEL` | No | Box AI model for enrichment (default: `google__gemini_2_5_pro`) |

For local dev, `BOX_REDIRECT_URI=http://localhost:3000/api/auth/callback`. The Box Custom App must also have `http://localhost:3000` added to its **CORS Domains** list in the Box developer console, or the Content Picker will show "A network error has occurred."

---

## Key Files

| File | Notes |
|---|---|
| `src/lib/jobs.ts` | Job type, `global.__jobs` Map, `createJob` / `updateJob` / `appendLog` |
| `src/lib/box.ts` | `getFreshToken`, `getBoxUser`, `uploadToBox` |
| `src/lib/session.ts` | `SessionData` interface and `iron-session` options |
| `src/app/api/generate/route.ts` | Job creation, Python subprocess orchestration, Box upload |
| `python/manifest.py` | Modified from root `manifest.py` — accepts `--token`, `--folder-id`, `--output-dir` |
| `python/enrich.py` | AI enrichment — calls Box AI `extract_structured` with each PDF's file ID (no download), writes `AI Date` and `AI Description` back to the manifest CSV; date field supports ranges for multi-date compilations |
| `python/report.py` | Modified from root `report.py` — accepts `--input-file`, `--output-file`; uses `AI Date` over filename/Box metadata date when present; populates Notes column with `AI Description` |
| `src/app/globals.css` | Contains Box Content Picker CSS overrides scoped to `#box-picker-container` |

The root `manifest.py` and `report.py` are the original scripts with hardcoded constants — do not invoke these from the app. The `python/` versions are the ones wired to the API.
