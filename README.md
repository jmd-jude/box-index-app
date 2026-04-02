# Box Index Tool

A Next.js web app to generate formatted Excel document indexes from Box case folders.

## What it does

1. Authenticate with Box via OAuth.
2. Pick a case folder using the Box Content Picker.
3. The app traverses the folder, extracts file metadata, and generates an Excel index report.
4. Optionally, enable **AI enrichment** to extract a document date and brief description from each PDF using Box AI. Results populate the Document Date and Notes columns in the report. Multi-date compilations return a date range.
5. The report is automatically uploaded back into the selected Box folder.

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

# Optional — override the Box AI model used for enrichment (default: google__gemini_2_5_pro)
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

## Deployment (Railway)

1. Push the repo to GitHub
2. Create a new project on Railway and deploy from the GitHub repo
3. Set the four environment variables in Railway's dashboard (`BOX_CLIENT_ID`, `BOX_CLIENT_SECRET`, `BOX_REDIRECT_URI`, `SESSION_SECRET`)
4. Update `BOX_REDIRECT_URI` to your Railway app URL (e.g. `https://your-app.railway.app/api/auth/callback`)
5. Add the Railway URL to your Box app's registered redirect URIs and CORS Domains

## Project structure

```
python/
  manifest.py   # Box folder traversal and metadata extraction
  enrich.py     # AI enrichment — extracts document date and description via Box AI (extract_structured)
  report.py     # Excel report generation
src/
  app/
    api/
      auth/     # Box OAuth login + callback
      generate/ # Job creation and Python subprocess orchestration
      job/      # Job status polling endpoint
    page.tsx    # Main UI (auth → folder picker → job status)
  lib/
    box.ts      # Token refresh, Box API helpers
    jobs.ts     # In-memory job state
    session.ts  # iron-session config
```

## Environment variables

| Variable | Purpose |
|---|---|
| `BOX_CLIENT_ID` | Box Custom App client ID |
| `BOX_CLIENT_SECRET` | Box Custom App client secret |
| `BOX_REDIRECT_URI` | Must match redirect URI registered in Box developer console |
| `SESSION_SECRET` | 32+ character random string for session cookie encryption |
| `BOX_AI_MODEL` | Box AI model for enrichment (default: `google__gemini_2_5_pro`) — optional |
