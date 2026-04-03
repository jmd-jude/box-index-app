# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## What This Is

FPAmed Box Index Tool — a Next.js 14 web app that lets FPAmed (forensic psychiatry expert witness firm) staff authenticate with Box and run one of two pipelines:

**Document Index:** Pick a case folder → generates a formatted Excel document index (file metadata, page counts, AI-extracted dates and descriptions). Report is uploaded back to the selected folder.

**Deposition Summary:** Pick a deposition transcript PDF → processes it page by page using Box AI, detecting topic boundaries and extracting subject labels, summaries, and legal significance notes. Excel summary is uploaded to the same folder as the source transcript.

Core document processing logic lives in Python scripts invoked as child processes from the Next.js API layer:
- `python/manifest.py` — Box folder traversal + metadata extraction
- `python/enrich.py` — optional AI enrichment via Box AI `extract_structured`
- `python/report.py` — Excel generation for document index
- `python/depo_summary.py` — page-by-page deposition extraction via Box AI
- `python/depo_report.py` — Excel generation for deposition summary

---

## Commands

```bash
npm run dev        # start dev server on localhost:3000
npm run build      # production build (also validates TypeScript)
npx tsc --noEmit   # type-check without building
```

**Python environment** — always use the local venv:
```bash
# Document Index
.venv/bin/python3 python/manifest.py --token <tok> --folder-id <id> --output-dir /tmp/test
.venv/bin/python3 python/enrich.py --manifest-file /tmp/test/slug_manifest.csv --token <tok>
.venv/bin/python3 python/report.py --input-file /tmp/test/slug_manifest.csv --output-file /tmp/test/out.xlsx

# Deposition Summary
.venv/bin/python3 python/depo_summary.py --file-id <id> --token <tok> --output-dir /tmp/depo_test
.venv/bin/python3 python/depo_report.py --input-file /tmp/depo_test/slug_depo_topics.csv --output-file /tmp/depo_test/summary.xlsx

# Test on first 10 pages only (faster iteration)
.venv/bin/python3 python/depo_summary.py --file-id <id> --token <tok> --output-dir /tmp/depo_test --page-end 10
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
Both pipelines use the same job system. `POST /api/generate` or `POST /api/depo` creates a job record and fires off `runJob()` as a floating promise (fire-and-forget), returning `{ jobId }` immediately. The frontend polls `GET /api/job/[jobId]` every second.

Job state is a module-level `Map` attached to `global.__jobs`. **This must stay on `global`** — Next.js compiles each route in its own module context in dev mode, so a plain module-level `Map` in `src/lib/jobs.ts` would be invisible to other routes.

The `Job` interface includes a `pipeline` field (`'document_index' | 'deposition_summary'`) passed to `createJob()` at job creation time.

**Document Index pipeline:** manifest.py → *(optional)* enrich.py → report.py → upload to Box folder → `complete`

**Deposition Summary pipeline:** depo_summary.py → depo_report.py → upload to Box (parent folder of transcript) → `complete`

### Python invocation
Both route handlers auto-detect the venv python at `.venv/bin/python3`, falling back to system `python3`. stdout/stderr lines are captured and appended to `job.log[]` for display in the UI. The last non-empty output line is surfaced as the error message on non-zero exit.

Intermediate files (CSVs, Excel) are written to a `os.tmpdir()` subdirectory keyed by job ID and cleaned up after the Box upload.

The depo route makes one additional Box API call before starting the job: `GET /files/{file_id}?fields=parent` to resolve the parent folder ID for upload.

### Frontend
`src/app/page.tsx` is a single client component with four render states:
1. Unauthenticated — connect button
2. Authenticated, no pipeline selected — two-card pipeline selector (Document Index / Deposition Summary)
3. Authenticated, pipeline selected, no active job — Box Content Picker (folder mode for document index, file/PDF mode for deposition summary) + selected item bar
4. Job running/complete/error — spinner + live log buffer, or result/error UI

The Box Content Picker (CDN: `window.Box.ContentPicker`) is initialized in a `useEffect` that depends on `[auth, pipeline, pickerKey]`. `pickerKey` is incremented by `handleReset()` to force the picker to remount. `handleReset()` resets `pipeline` to null, returning the user to the pipeline selector (not logout).

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
| `BOX_AI_MODEL` | No | Box AI model for both pipelines (default: `google__gemini_2_5_pro`) |

For local dev, `BOX_REDIRECT_URI=http://localhost:3000/api/auth/callback`. The Box Custom App must also have `http://localhost:3000` added to its **CORS Domains** list in the Box developer console, or the Content Picker will show "A network error has occurred."

---

## Key Files

| File | Notes |
|---|---|
| `src/lib/jobs.ts` | Job type (includes `pipeline` field), `global.__jobs` Map, `createJob` / `updateJob` / `appendLog` |
| `src/lib/box.ts` | `getFreshToken`, `getBoxUser`, `uploadToBox` |
| `src/lib/session.ts` | `SessionData` interface and `iron-session` options |
| `src/app/api/generate/route.ts` | Document index job orchestration — manifest → enrich → report → upload |
| `src/app/api/depo/route.ts` | Deposition summary job orchestration — depo_summary → depo_report → upload to parent folder |
| `python/manifest.py` | Accepts `--token`, `--folder-id`, `--output-dir` |
| `python/enrich.py` | AI enrichment — calls Box AI `extract_structured` with each PDF's file ID; date field supports ranges |
| `python/report.py` | Accepts `--input-file`, `--output-file`; uses `AI Date` over filename/Box metadata date when present |
| `python/depo_summary.py` | Downloads PDF, auto-detects testimony start/end, processes pages with 3-page sliding window via Box AI; outputs `{slug}_depo_topics.csv` |
| `python/depo_report.py` | Reads topics CSV, produces formatted Excel with PAGE/SUBJECT/SUMMARY/SIGNIFICANCE columns; accent border on rows with legal significance |
| `python/depo_experiment.py` | Original proof-of-concept — do not invoke from app, do not modify |
| `src/app/globals.css` | Box Content Picker CSS overrides scoped to `#box-picker-container` |

The root `manifest.py` and `report.py` are the original scripts with hardcoded constants — do not invoke these from the app. The `python/` versions are the ones wired to the API.
