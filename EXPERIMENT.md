# EXPERIMENT.md — Deposition Summary Proof of Concept

## Purpose

This experiment tests whether Box AI's `extract_structured` endpoint,
applied page-by-page to a deposition transcript PDF, can produce a
complete and accurate deposition summary that meets the quality bar
defined in the functional requirements — without leaving Box's
infrastructure and without requiring DocETL or direct Claude/OpenAI API
calls.

The output of this experiment is a CSV or Excel file containing the
three-column summary structure defined in the functional spec:
- **Subject** — short topic label (3–7 words)
- **Page/Line** — page number(s) covered
- **Summary** — concise third-person narrative (25–100 words)

If Box AI clears the quality bar on a real deposition transcript, the
deposition summary feature can be built on the same architecture as the
existing index tool — same stack, same infrastructure, same HIPAA/BAA
posture. If it doesn't, this experiment produces the evidence needed to
justify a different approach.

---

## What To Build

A single standalone Python script: `python/depo_experiment.py`

It takes a Box file ID (a deposition transcript PDF already stored in
Box) and a Box access token, processes it page by page using Box AI,
and outputs a formatted summary.

This script is NOT wired into the Next.js app. It runs from the CLI
only. The goal is to validate quality before building the full
integration.

---

## Context: How The Existing App Works

The existing index tool follows this pattern in `src/app/api/generate/route.ts`:

1. `manifest.py` — walks Box folder, extracts file metadata, outputs CSV
2. `enrich.py` — calls Box AI `extract_structured` on each PDF file,
   adds `AI Date` and `AI Description` columns to the manifest CSV
3. `report.py` — reads enriched manifest CSV, generates formatted Excel

The deposition experiment follows the same pattern as `enrich.py` but
operates on **pages within a single file** rather than files within a
folder.

The key call in `enrich.py` that this experiment extends:

```python
POST https://api.box.com/2.0/ai/extract_structured
{
  "items": [{"type": "file", "id": file_id}],
  "fields": [...],
  "ai_agent": {
    "type": "ai_agent_extract_structured",
    "long_text": {"model": "google__gemini_2_5_pro"}
  }
}
```

The experiment needs to call this endpoint once per page, not once per
file. Box AI supports passing a specific byte range or page range in the
`items` array — this is the key mechanism to use.

---

## Box AI Page-Level Extraction

Box AI's `extract_structured` endpoint supports scoping extraction to a
specific page range within a file using the `items` array:

```python
{
  "items": [
    {
      "type": "file",
      "id": file_id,
      "content": {
        "type": "pages",
        "pages": [
          {"start": page_num, "end": context_end}
        ]
      }
    }
  ],
  "fields": [...],
  "ai_agent": {
    "type": "ai_agent_extract_structured",
    "long_text": {"model": "google__gemini_2_5_pro"}
  }
}
```

Use a sliding window of 3 pages: for page N, send pages N-1 through
N+1 as context (clamped to document bounds). This gives the model
enough context to understand whether a topic is beginning, continuing,
or concluding — without overwhelming it with the full document.

If the Box AI API does not support the `content.pages` scoping in
`extract_structured` (verify this — it may be a `ask` endpoint feature
only), fall back to downloading the PDF bytes, extracting individual
page text using PyMuPDF (already a dependency: `import fitz`), and
sending the text directly in a `content` field or as an inline text
item.

---

## The Extraction Fields

For each page window, ask Box AI to extract:

```python
FIELDS = [
    {
        "key": "has_new_topic",
        "type": "string",
        "description": (
            "Does a new substantive topic begin on the focal page of this transcript excerpt? "
            "Answer 'yes' or 'no' only."
        ),
        "prompt": (
            "Look at the focal page (the middle page if three pages are provided, "
            "or the only page if one page is provided). "
            "Does a new substantive topic begin on this page — meaning a line of questioning "
            "that is meaningfully distinct from what immediately preceded it? "
            "Answer 'yes' or 'no' only."
        ),
    },
    {
        "key": "subject",
        "type": "string",
        "description": (
            "If a new topic begins on the focal page, provide a short subject label "
            "of 3–7 words describing the topic. "
            "If no new topic begins, return an empty string."
        ),
        "prompt": (
            "If a new topic begins on the focal page, write a 3–7 word noun phrase "
            "that labels the topic — for example: 'Prior psychiatric hospitalization history' "
            "or 'Relationship with treating physician'. "
            "If no new topic begins, return empty string."
        ),
    },
    {
        "key": "summary",
        "type": "string",
        "description": (
            "If a new topic begins on the focal page, provide a concise third-person "
            "narrative summary of the testimony on this topic as it appears on the focal page. "
            "25–100 words. Plain declarative prose. No verbatim quotation. "
            "If no new topic begins, return empty string."
        ),
        "prompt": (
            "If a new topic begins on the focal page, summarize the testimony in "
            "25–100 words of plain third-person declarative prose. "
            "Do not quote verbatim. Do not start with 'The witness' or 'The deponent'. "
            "If no new topic begins, return empty string."
        ),
    },
    {
        "key": "page_range_end",
        "type": "string",
        "description": (
            "If a new topic begins on the focal page, what is the last page number "
            "where this topic appears to continue before a new topic begins? "
            "If the topic continues beyond the current window, return the focal page number. "
            "If no new topic begins, return empty string."
        ),
        "prompt": (
            "If a new topic begins on the focal page, estimate the last page where "
            "this topic continues. If it extends beyond what you can see, return the "
            "focal page number. If no new topic begins, return empty string."
        ),
    },
]
```

---

## Script Structure: `python/depo_experiment.py`

```
CLI args:
  --file-id       Box file ID of the deposition transcript PDF
  --token         Box access token
  --output        Output file path (default: depo_experiment_output.csv)
  --model         Box AI model (default: google__gemini_2_5_pro)
  --workers       Parallel API workers (default: 3)
  --page-start    First page to process (default: 1, for testing subsets)
  --page-end      Last page to process (default: all pages)

Steps:
  1. Get total page count
     - Download PDF bytes from Box using the SDK (same pattern as
       get_page_count_from_pdf in manifest.py)
     - Use PyMuPDF: doc = fitz.open(stream=pdf_bytes, filetype="pdf")
     - total_pages = len(doc)
     - Print: "Transcript: {total_pages} pages"

  2. Process pages in parallel (ThreadPoolExecutor, same pattern as enrich.py)
     - For each page N (1-indexed):
       - Build context window: max(1, N-1) to min(total_pages, N+1)
       - Call Box AI extract_structured with page window
       - Parse response: has_new_topic, subject, summary, page_range_end
       - Print: "[N/total] page {N} → {subject or 'no new topic'}"
     - Collect all results

  3. Filter and assemble
     - Keep only rows where has_new_topic == 'yes' and subject is non-empty
     - Sort by page number
     - Deduplicate: if two adjacent entries have nearly identical subjects,
       keep the first and merge page ranges
     - Build final rows: page_num, subject, summary

  4. Output
     - Write CSV with columns: Page, Subject, Summary
     - Also print a simple text preview of the first 10 entries to stdout
     - Print stats: "Found {N} topics across {total_pages} pages"
     - Print: "Output → {output_file}"
```

---

## Fallback: PyMuPDF Text Extraction

If Box AI's `extract_structured` does not support page-level scoping,
implement this fallback:

```python
def get_page_text(doc, page_num, context_pages=1):
    """
    Extract text from a page window using PyMuPDF.
    Returns dict: {focal_page_num: text, context: [surrounding texts]}
    """
    total = len(doc)
    start = max(0, page_num - 1 - context_pages)  # 0-indexed
    end = min(total - 1, page_num - 1 + context_pages)
    
    texts = []
    for i in range(start, end + 1):
        page_text = doc[i].get_text()
        label = f"--- PAGE {i+1} ---"
        if i + 1 == page_num:
            label = f"--- PAGE {i+1} [FOCAL PAGE] ---"
        texts.append(f"{label}\n{page_text}")
    
    return "\n\n".join(texts)
```

In this fallback, send the extracted text to Box AI using a text
content item rather than a file reference. Check Box AI docs for the
correct payload structure for inline text.

If Box AI does not support inline text either, use the Anthropic API
directly as a last resort — but flag this clearly in output since it
changes the HIPAA/BAA posture.

---

## Success Criteria for the Experiment

Run the script against a real deposition transcript already in the
client's Box account (use the test transcript linked in the functional
requirements doc if available).

The experiment passes if:

- [ ] Every substantive page produces at least a topic detection decision
- [ ] Topic count is in the range of 75-100 entries for a 250–300 page
      transcript
- [ ] Subject labels are 3–7 words and meaningful to a practitioner
- [ ] Summaries are 25–100 words of coherent third-person prose
- [ ] No obvious hallucinations (fabricated names, dates, medications)
- [ ] Runtime is acceptable (target: under 10 minutes for 300 pages
      at 3 workers — adjust workers if rate limited)

If the experiment passes, the next step is building `depo_summary.py`
as a proper pipeline script following the same pattern as `enrich.py`,
and wiring it into the Next.js app in `generate/route.ts` as an
optional Step 2 (alongside or replacing the existing enrich step for
deposition files).

If the experiment fails on quality, document specifically where it
fails (topic detection accuracy, summary quality, hallucinations,
coverage gaps) — this evidence will inform whether DocETL + Anthropic
API is needed as an alternative path.

---

## Files To Reference

When building this script, read these existing files for patterns to follow:

- `python/enrich.py` — parallel Box AI API calls, retry logic, progress
  printing, CSV read/write pattern
- `python/manifest.py` — Box SDK auth pattern, PyMuPDF page count
  extraction (`get_page_count_from_pdf` function)

Do not modify either of those files. The experiment script is standalone.

---

## Running The Experiment

```bash
# Activate venv
source .venv/bin/activate

# Run against a specific Box file
python python/depo_experiment.py \
  --file-id YOUR_BOX_FILE_ID \
  --token YOUR_BOX_DEV_TOKEN \
  --output depo_test_output.csv

# Test on first 50 pages only (faster iteration)
python python/depo_experiment.py \
  --file-id YOUR_BOX_FILE_ID \
  --token YOUR_BOX_DEV_TOKEN \
  --page-end 50 \
  --output depo_test_50pages.csv
```

Get a fresh developer token from:
https://app.box.com/developers/console → your app → Configuration →
Developer Token (valid 60 minutes)

---

## Dependencies

Already in `requirements.txt`:
- `boxsdk` — Box API client
- `pymupdf` (fitz) — PDF parsing

No new dependencies needed.
