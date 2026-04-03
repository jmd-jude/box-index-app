# DEPO_SUMMARY.md — Deposition Summary Pipeline
## Build Spec for Claude Code

---

## Overview

This document specifies the additions required to extend the FPAmed Box
Index Tool (currently deployed on Railway) with a second pipeline:
**Deposition Summary**.

The existing app produces a formatted Excel document index from a Box
folder. The new pipeline produces a formatted deposition summary from a
single Box PDF file — a three-column structured table (Subject, Page,
Summary) covering every substantive page of the transcript.

The experiment proving this architecture works is complete. The Python
script `python/depo_experiment.py` exists and has been validated against
a real 303-page transcript. This build spec promotes that experiment
into a production pipeline integrated with the existing app.

---

## What Has Already Been Proven

- Box AI's `extract_structured` endpoint supports page-level scoping
- Processing 303 pages at 5 workers completes well under 10 minutes
- Topic count on a 303-page transcript: ~100 topics (target: 75–100)
- Subject labels are practitioner-meaningful at 3–7 words
- Summaries are coherent third-person prose at 25–100 words
- Zero API failures on a full run
- Gemini 2.5 Pro via Box AI performs well on this task

**Known gaps identified in evaluation vs. incumbent provider:**
- Misses frequency qualifiers ("more than a dozen times," "less than
  50% of the time") — addressed in updated extraction prompts below
- Captures what was asked rather than what was admitted — addressed
  in updated extraction prompts below
- Flat chronological output vs. thematic grouping — addressed in
  output format below
- Procedural pages (cover, appearances, certifications) fire as topics
  — addressed with page-start heuristic and preamble skip below

---

## Files To Create

```
python/depo_summary.py        # Production pipeline script
python/depo_report.py         # Formatted Excel/PDF output generator
src/app/api/depo/route.ts     # New API route for deposition jobs
```

## Files To Modify

```
src/app/page.tsx              # Add pipeline selector step
src/lib/jobs.ts               # Add pipeline field to Job interface
src/app/api/generate/route.ts # Minor: confirm no changes needed
```

---

## 1. Python Pipeline: `python/depo_summary.py`

Production version of `depo_experiment.py`. Follow the same patterns
as `enrich.py` (parallel workers, retry logic, progress printing,
CSV output).

### CLI Arguments

```
--file-id       Box file ID of the deposition transcript PDF (required)
--token         Box access token (required)
--output-dir    Directory for output files (required)
--model         Box AI model ID (default: google__gemini_2_5_pro)
--workers       Parallel API workers (default: 5)
--page-start    First page to process (default: auto-detect — see below)
--page-end      Last page to process (default: all pages)
```

### Preamble Skip Heuristic

Deposition transcripts have standardized cover pages, appearance
listings, and index pages that contain no substantive testimony.
These should not be processed.

Default behavior when `--page-start` is not specified:
- Download PDF, scan pages 1–15 for the phrase "EXAMINATION" or "Q."
  or "BY MR." or "BY MS." using PyMuPDF text extraction
- Set `page_start` to the first page where any of these patterns appear
- Print: `Auto-detected testimony start: page {N}`
- Fall back to page 7 if no pattern found within first 15 pages

Also skip the final pages:
- Scan the last 5 pages for "CERTIFICATE" or "WITNESS SIGNATURE" or
  "I, the undersigned"
- Set `page_end` to exclude those pages
- Print: `Auto-detected testimony end: page {N}`

### Extraction Fields (Updated from Experiment)

These prompts incorporate findings from the comparative evaluation.
Key improvements:
1. Explicitly request frequency qualifiers
2. Ask for what was admitted, not just what was discussed
3. Force "first page only" to reduce adjacent duplicates
4. Expert witness context in system framing

```python
SYSTEM_CONTEXT = (
    "You are a litigation support analyst processing deposition transcripts "
    "for use by forensic psychiatry expert witnesses in complex litigation. These summaries "
    "will be used by forensic psychiatry expert witnesses to rapidly locate and reference specific "
    "testimony. Accuracy, completeness, and precise attribution are paramount. "
    "The expert witnesses reviewing these summaries are professionals who will "
    "be testifying in court — they need summaries that capture not just what "
    "was discussed, but specifically what the witness admitted, denied, "
    "qualified, or quantified."
)

FIELDS = [
    {
        "key": "has_new_topic",
        "type": "string",
        "description": (
            "Does a new substantive topic of testimony BEGIN on the focal page "
            "of this transcript excerpt? Answer 'yes' or 'no' only. "
            "Answer 'yes' ONLY if this is the FIRST page where this topic appears. "
            "If the topic was already underway on the previous page, answer 'no'. "
            "Procedural matters (objections, administrative discussion, breaks) "
            "do not constitute new substantive topics."
        ),
        "prompt": (
            "Look only at the FOCAL PAGE (marked [FOCAL PAGE] in the excerpt). "
            "Does a new substantive topic of testimony BEGIN on this page — "
            "meaning a line of questioning meaningfully distinct from what "
            "immediately preceded it, AND this is the first page where it appears? "
            "Answer 'yes' or 'no' only."
        ),
    },
    {
        "key": "subject",
        "type": "string",
        "description": (
            "If a new topic begins on the focal page, a short noun phrase of "
            "3–7 words identifying the topic. Should be specific enough to be "
            "useful for issue-spotting in litigation. Examples: "
            "'Witness's prior relationship with decedent', "
            "'Capacity observations during estate planning meetings', "
            "'Defendant's financial pressure on plaintiff'. "
            "Return empty string if no new topic begins."
        ),
        "prompt": (
            "If a new topic begins on the focal page, write a 3–7 word noun "
            "phrase that labels the topic specifically enough for a litigator "
            "to identify it as legally relevant. Avoid generic labels like "
            "'Further examination' or 'Continued discussion'. "
            "Return empty string if no new topic begins."
        ),
    },
    {
        "key": "summary",
        "type": "string",
        "description": (
            "If a new topic begins on the focal page, a 30–120 word summary "
            "of the testimony. Must capture: (1) what the witness specifically "
            "admitted, denied, or conceded — not just what was asked; "
            "(2) any frequency or quantity qualifiers the witness used "
            "(e.g., 'more than a dozen times,' 'less than half the time,' "
            "'approximately three occasions'); "
            "(3) any limiting admissions or denials that qualify the testimony. "
            "Write in plain third-person declarative prose. "
            "Do not use verbatim quotation. "
            "Do not begin with 'The witness' or 'The deponent'. "
            "Return empty string if no new topic begins."
        ),
        "prompt": (
            "If a new topic begins on the focal page, summarize the testimony "
            "in 30–120 words of plain third-person prose. "
            "Focus on what the witness ADMITTED, DENIED, or CONCEDED — not "
            "just what was asked. Capture any specific numbers, frequencies, "
            "or qualifiers ('more than a dozen,' 'less than 50 percent,' "
            "'approximately'). Include any limiting admissions that qualify "
            "the testimony. Do not quote verbatim. "
            "Return empty string if no new topic begins."
        ),
    },
    {
        "key": "legal_significance",
        "type": "string",
        "description": (
            "If a new topic begins on the focal page, a brief note (10 words "
            "or fewer) on why this testimony may be significant for litigation — "
            "e.g., 'Establishes timeline', 'Key admission re: capacity', "
            "'Limiting denial', 'Financial motive evidence'. "
            "Return empty string if no new topic begins or if significance "
            "is not apparent."
        ),
        "prompt": (
            "If a new topic begins and the testimony appears legally significant, "
            "note why in 10 words or fewer. Examples: 'Key admission', "
            "'Limiting denial', 'Establishes timeline', 'Credibility issue'. "
            "Return empty string if no new topic or significance not apparent."
        ),
    },
]
```

### Page Window Logic

For each focal page N:
- Context window: pages max(page_start, N-1) through min(page_end, N+1)
- Mark the focal page clearly in the text sent to Box AI:
  `--- PAGE {N} [FOCAL PAGE] ---`
- Surrounding pages labeled: `--- PAGE {N-1} [CONTEXT] ---`

### Output

Write two files to `--output-dir`:

1. `{slug}_depo_topics.csv` — raw extracted topics
   Columns: page_start, page_end, subject, summary, legal_significance

2. Print progress: `[{n}/{total}] page {N} → {subject or 'no new topic'}`

3. Print final stats:
   `Found {topic_count} topics across {pages_processed} pages processed`
   `Output → {csv_path}`

### Retry and Rate Limit Logic

Follow `enrich.py` exactly:
- 3 attempts per page
- On 429: wait 10 × attempt seconds
- On timeout: wait 5 × attempt seconds
- On non-retriable error: log warning, continue
- Track and report failure count at end

---

## 2. Python Report: `python/depo_report.py`

Reads the topics CSV and produces a formatted Excel workbook.
Follow the same patterns as `report.py` (openpyxl, color scheme,
alternating rows, section headers).

### CLI Arguments

```
--input-file    Path to {slug}_depo_topics.csv (required)
--output-file   Path for output .xlsx (required)
--case-name     Override case name in header (optional)
```

### Output Structure

**Header block** (rows 1–3):
- Row 1: Case name (from `--case-name` or derived from filename), green fill
- Row 2: "DEPOSITION SUMMARY — {total_topics} topics across {page_count} pages", black fill
- Row 3: "Generated by FPAmed Box Index Tool — {date}", dark fill

**Column headers** (row 5):
```
PAGE  |  SUBJECT  |  SUMMARY  |  SIGNIFICANCE
```

**Data rows:**
- One row per topic, sorted by page_start
- Column widths:
  - PAGE: 10 (centered)
  - SUBJECT: 35 (left-aligned, wrap)
  - SUMMARY: 90 (left-aligned, wrap)
  - SIGNIFICANCE: 30 (left-aligned, wrap)
- Alternating row fill (LTGRAY / white) same as report.py
- Row height: auto based on summary length (minimum 30px)
- Page column: show as "p. {start}" or "p. {start}–{end}" if range

**Legal significance highlighting:**
- If legal_significance is non-empty, apply a subtle left border
  accent (color: #1565C0, 3px) to that row to make it visually
  scannable
- Do NOT use bold or colored text — keep it subtle

**Footer note** (2 rows after last data row):
```
NOTES ON METHODOLOGY: This summary was generated by processing the
transcript page by page using Box AI. Every page was individually
examined. Topic count and page citations reflect the automated
pipeline output and should be verified against the source transcript
for any testimony that will be cited in a filing or expert report.
```
Footer style: italic, 9pt, gray text, light gray fill, merged across
all columns.

**No separate sheets needed** for this output — single worksheet.

### Do NOT implement thematic grouping in this version.

Thematic grouping (clustering topics by legal issue) is a future
enhancement. The flat chronological structure is sufficient for the
initial production version and matches the page-by-page verification
workflow that the functional spec requires.

---

## 3. New API Route: `src/app/api/depo/route.ts`

Follow the exact same pattern as `src/app/api/generate/route.ts`.

### Differences from generate/route.ts:

- Accepts `fileId` and `fileName` instead of `folderId` and `folderName`
- Runs two Python steps instead of three:
  - Step 1: `depo_summary.py` → produces topics CSV
  - Step 2: `depo_report.py` → produces Excel
- Upload filename format: `{fileName}_summary_{dateStamp}.xlsx`
- Upload destination: parent folder of the selected file
  (requires one additional Box API call to get parent folder ID —
  use `GET /files/{file_id}?fields=parent` before starting the job)
- Progress messages:
  - "Detecting testimony pages..."
  - "Processing page {N} of {total}..."  (from stdout parsing)
  - "Generating summary report..."
  - "Uploading to Box..."

### Job creation:

Pass `pipeline: 'deposition_summary'` when calling `createJob()`.
This requires the `pipeline` field addition to `jobs.ts` (see below).

---

## 4. Modify `src/lib/jobs.ts`

Add `pipeline` field to the Job interface:

```typescript
export interface Job {
  id: string;
  status: 'queued' | 'running' | 'complete' | 'error';
  pipeline: 'document_index' | 'deposition_summary';  // ADD THIS
  folderId: string;
  folderName: string;
  createdAt: string;
  completedAt?: string;
  boxFileUrl?: string;
  error?: string;
  progress?: string;
  log?: string[];
}
```

Update `createJob()` to accept and store `pipeline`:

```typescript
export function createJob(
  id: string,
  folderId: string,
  folderName: string,
  pipeline: Job['pipeline']  // ADD THIS
): Job {
```

Update all `createJob()` call sites to pass the pipeline value:
- `src/app/api/generate/route.ts`: pass `'document_index'`
- `src/app/api/depo/route.ts`: pass `'deposition_summary'`

---

## 5. Modify `src/app/page.tsx`

### New state: pipeline selection

Add a pipeline selection step between authentication and the content
picker. This is a new UI state inserted into the existing state machine.

**New state variable:**
```typescript
type Pipeline = 'document_index' | 'deposition_summary' | null;
const [pipeline, setPipeline] = useState<Pipeline>(null);
```

**Updated state machine:**
```
unauthenticated
  → authenticated, no pipeline selected     ← NEW STATE
    → authenticated, pipeline selected, no item selected
      → job running / complete / error
```

**Reset behavior:** `handleReset()` should reset `pipeline` to null,
returning the user to pipeline selection (not all the way to logout).

### Pipeline selection UI (new State 2)

Shown after authentication, before the Box content picker renders.

Two cards side by side (or stacked on mobile):

**Card 1 — Document Index**
- Icon: folder/grid icon
- Title: "Document Index"
- Description: "Generate a formatted Excel index of all files in a
  Box folder, with page counts, document dates, and duplicate detection."
- Button: "Select a folder →"

**Card 2 — Deposition Summary**
- Icon: document/lines icon
- Title: "Deposition Summary"
- Description: "Generate a structured page-by-page summary of a
  deposition transcript, organized by topic with page citations."
- Button: "Select a transcript →"

Clicking a card sets `pipeline` and advances to the content picker.

Style: match existing color scheme (green #669966, slate, white).
Cards should feel like a simple professional tool selector, not a
marketing page.

**Back navigation:** Show a small "← Change" link above the content
picker that resets `pipeline` to null, allowing the user to return to
pipeline selection without logging out.

### Content picker configuration by pipeline

**Document Index** (existing behavior — no change):
```javascript
picker.show('0', auth.accessToken, {
  container: '#box-picker-container',
  type: 'folder',
  maxSelectable: 1,
  ...
});
```

**Deposition Summary** (new):
```javascript
picker.show('0', auth.accessToken, {
  container: '#box-picker-container',
  type: 'file',           // FILE not folder
  maxSelectable: 1,
  extensions: ['pdf'],    // PDF only
  ...
});
```

### Selected item display bar

The green bar that appears when a folder is selected needs to update
for the deposition pipeline:

- Label: "Selected transcript" instead of "Selected folder"
- Button text: "Generate Summary" instead of "Generate Index"
- Remove the "AI enrichment" checkbox (not applicable to deposition
  pipeline — AI is always used)
- POST to `/api/depo` instead of `/api/generate`
- Send `{ fileId, fileName }` instead of `{ folderId, folderName }`

### Job running/complete copy by pipeline

Update the running and complete states to show pipeline-appropriate copy:

**Running:**
- Document Index: "Generating index…" (existing)
- Deposition Summary: "Generating deposition summary…"

**Complete:**
- Document Index: "The Excel report has been saved to your Box folder." (existing)
- Deposition Summary: "The summary has been saved to the same Box
  folder as your transcript."

**Complete action buttons:**
- Document Index: "Open in Box" + "Generate another" (existing)
- Deposition Summary: "Open Summary" + "Summarize another"

---

## 6. Update Header Copy

The header currently reads "Document Index Generator".

Update to: "FPAmed Document Tools" or simply remove the subtitle
since the app now does more than one thing. Keep it generic.

---

## Definition of Done

- [ ] User can select "Document Index" or "Deposition Summary" after
      logging in
- [ ] Document Index pipeline works exactly as before (no regression)
- [ ] Deposition Summary pipeline accepts a single PDF file from Box
- [ ] Summary job runs `depo_summary.py` → `depo_report.py` in sequence
- [ ] Progress log shows page-by-page processing updates
- [ ] Completed Excel summary is uploaded to the same Box folder as
      the source transcript
- [ ] "Open Summary" button links directly to the uploaded file in Box
- [ ] "← Change" link returns user to pipeline selector without logout
- [ ] Header subtitle updated to reflect multi-pipeline scope
- [ ] No regressions in the Document Index pipeline

---

## Files To Reference

When building, read these existing files for patterns:

- `python/enrich.py` — Box AI API call pattern, retry logic, workers
- `python/manifest.py` — Box SDK auth, PyMuPDF usage
- `python/depo_experiment.py` — existing page-by-page extraction logic
  to promote into production (DO NOT modify this file — copy logic into
  depo_summary.py)
- `python/report.py` — Excel report generation pattern
- `src/app/api/generate/route.ts` — API route pattern to follow exactly
- `src/app/page.tsx` — existing state machine to extend
- `src/lib/jobs.ts` — job interface to extend

---

## What NOT To Build In This Version

- Thematic clustering / topic grouping (future enhancement)
- Multi-file batch processing (future enhancement)
- Side-by-side comparison with a reference summary
- Any changes to the Document Index pipeline behavior
- Mobile optimization
- User history or saved runs

---

## Running Locally To Test

```bash
# Test depo_summary.py directly before wiring into the app
source .venv/bin/activate

python python/depo_summary.py \
  --file-id YOUR_BOX_FILE_ID \
  --token YOUR_BOX_DEV_TOKEN \
  --output-dir /tmp/depo_test

python python/depo_report.py \
  --input-file /tmp/depo_test/{slug}_depo_topics.csv \
  --output-file /tmp/depo_test/summary.xlsx

# Verify the Excel output before testing the full web flow
```

---

## Deployment

No changes to Railway deployment configuration required.
The existing `Dockerfile` and Railway service handle Python + Node
together. New Python scripts are automatically included.

After deploying, test against a real transcript in the client's
Box account before announcing the feature.
