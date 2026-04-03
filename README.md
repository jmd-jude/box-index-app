# FPAmed Box Index Tool

A Next.js web app for FPAmed staff to generate formatted Excel reports from Box — document indexes for case folders and deposition summaries for transcript PDFs.

## Pipelines

### Document Index

1. Authenticate with Box via OAuth.
2. Pick a case folder using the Box Content Picker.
3. The app traverses the folder, extracts file metadata, and generates an Excel index report.
4. Optionally enable **AI enrichment** to extract a document date and brief description from each PDF via Box AI. Results populate the Document Date and Notes columns. Multi-date compilations return a date range.
5. The report is automatically uploaded back into the selected Box folder.

### Deposition Summary

1. Authenticate with Box via OAuth.
2. Pick a deposition transcript PDF using the Box Content Picker.
3. The app processes the transcript page by page using Box AI, detecting topic boundaries and extracting subject labels, summaries, and legal significance notes.
4. An Excel summary (PAGE / SUBJECT / SUMMARY / SIGNIFICANCE) is automatically uploaded to the same Box folder as the source transcript.

Preamble and certification pages are automatically detected and skipped. Processing a 300-page transcript takes approximately 10–15 minutes at 5 parallel workers.

## Local development

### Prerequisites

- Node.js 18+
- Python 3.x

### Setup

```bash
npm install
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Create a `.env.local` file in the project root:

```
BOX_CLIENT_ID=your_box_client_id
BOX_CLIENT_SECRET=your_box_client_secret
BOX_REDIRECT_URI=http://localhost:3000/api/auth/callback
SESSION_SECRET=a-random-string-at-least-32-characters-long

# Optional — override the Box AI model (default: google__gemini_2_5_pro)
BOX_AI_MODEL=google__gemini_2_5_pro
```

### Run

```bash
npm run dev
```

App runs at `http://localhost:3000`.

### Box app configuration

In the Box developer console, your Custom App must have:
- `http://localhost:3000/api/auth/callback` registered as a redirect URI
- `http://localhost:3000` added to CORS Domains

### Test Python scripts directly

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

## Deployment (Railway)

1. Push the repo to GitHub
2. Create a new project on Railway and deploy from the GitHub repo
3. Set environment variables in Railway's dashboard (`BOX_CLIENT_ID`, `BOX_CLIENT_SECRET`, `BOX_REDIRECT_URI`, `SESSION_SECRET`)
4. Update `BOX_REDIRECT_URI` to your Railway app URL (e.g. `https://your-app.railway.app/api/auth/callback`)
5. Add the Railway URL to your Box app's registered redirect URIs and CORS Domains

No additional deployment configuration is needed for the deposition pipeline — new Python scripts are automatically included.

## Project structure

```
python/
  manifest.py        # Box folder traversal and metadata extraction
  enrich.py          # AI enrichment — document date and description via Box AI
  report.py          # Excel report generation for document index
  depo_summary.py    # Page-by-page deposition extraction via Box AI
  depo_report.py     # Excel report generation for deposition summary
  depo_experiment.py # Original proof-of-concept script (do not invoke from app)
src/
  app/
    api/
      auth/          # Box OAuth login + callback
      generate/      # Document index job orchestration
      depo/          # Deposition summary job orchestration
      job/           # Job status polling endpoint
    page.tsx         # Main UI (auth → pipeline selector → picker → job status)
  lib/
    box.ts           # Token refresh, Box API helpers
    jobs.ts          # In-memory job state (pipeline-aware)
    session.ts       # iron-session config
```

## Environment variables

| Variable | Purpose |
|---|---|
| `BOX_CLIENT_ID` | Box Custom App client ID |
| `BOX_CLIENT_SECRET` | Box Custom App client secret |
| `BOX_REDIRECT_URI` | Must match redirect URI registered in Box developer console |
| `SESSION_SECRET` | 32+ character random string for session cookie encryption |
| `BOX_AI_MODEL` | Box AI model for both pipelines (default: `google__gemini_2_5_pro`) — optional |
