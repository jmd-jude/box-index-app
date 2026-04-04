"""
Deposition Summary Pipeline — Box AI page-by-page extraction.
Production version of depo_experiment.py.

Downloads the PDF once, extracts page text via PyMuPDF, then calls
Box AI extract_structured with a 3-page sliding window for each focal page.

Usage:
  .venv/bin/python3 python/depo_summary.py \
    --file-id YOUR_BOX_FILE_ID \
    --token YOUR_BOX_DEV_TOKEN \
    --output-dir /tmp/depo_test
"""

import argparse
import csv
import os
import re
import threading
import concurrent.futures
import time
import requests
import fitz  # pymupdf
from boxsdk import OAuth2, Client

BOX_EXTRACT_URL = "https://api.box.com/2.0/ai/extract_structured"

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


def detect_testimony_start(doc):
    """
    Scan pages 1–15 for deposition testimony markers. Returns 1-indexed page number.
    Falls back to page 7 if no match found.
    """
    patterns = [r'\bEXAMINATION\b', r'\bQ\.\s', r'\bBY MR\.', r'\bBY MS\.']
    for page_idx in range(min(15, len(doc))):
        text = doc[page_idx].get_text()
        for p in patterns:
            if re.search(p, text):
                return page_idx + 1  # 1-indexed
    return 7


def detect_testimony_end(doc):
    """
    Scan last 5 pages for certification/signature pages. Returns last page to include (1-indexed).
    Falls back to len(doc) if no match found.
    """
    total = len(doc)
    patterns = [r'\bCERTIFICATE\b', r'WITNESS SIGNATURE', r'I, the undersigned']
    for page_idx in range(total - 1, max(total - 6, -1), -1):
        text = doc[page_idx].get_text()
        for p in patterns:
            if re.search(p, text, re.IGNORECASE):
                # page_idx is 0-indexed; the page before it in 1-indexed = page_idx
                return page_idx
    return total


def build_page_window(doc, page_num, page_start, page_end):
    """
    Build a 3-page context window around page_num (1-indexed), clamped to [page_start, page_end].
    The focal page is labelled [FOCAL PAGE]. Returns the combined text string.
    """
    start = max(page_start - 1, page_num - 2)  # 0-indexed
    end = min(page_end - 1, page_num)           # 0-indexed, inclusive

    parts = []
    for i in range(start, end + 1):
        label = f"--- PAGE {i + 1}{' [FOCAL PAGE]' if i + 1 == page_num else ' [CONTEXT]'} ---"
        parts.append(f"{label}\n{doc[i].get_text()}")
    return "\n\n".join(parts)


def call_box_ai(token: str, file_id: str, content: str, model: str) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "items": [{"type": "file", "id": file_id, "content": content}],
        "fields": FIELDS,
        "ai_agent": {
            "type": "ai_agent_extract_structured",
            "long_text": {"model": model},
            "basic_text": {"system_message": SYSTEM_CONTEXT},
        },
    }
    response = requests.post(BOX_EXTRACT_URL, headers=headers, json=payload, timeout=120)
    response.raise_for_status()
    answer = response.json().get("answer", {})
    return {
        "has_new_topic": (answer.get("has_new_topic") or "").strip().lower(),
        "subject": (answer.get("subject") or "").strip(),
        "summary": (answer.get("summary") or "").strip(),
        "legal_significance": (answer.get("legal_significance") or "").strip(),
    }


def process_page(token: str, file_id: str, page_num: int, model: str,
                 doc, page_start: int, page_end: int) -> dict:
    content = build_page_window(doc, page_num, page_start, page_end)
    last_err = None
    for attempt in range(1, 4):
        try:
            result = call_box_ai(token, file_id, content, model)
            result["page_num"] = page_num
            return result
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else "?"
            last_err = f"HTTP {status}"
            if e.response is not None and e.response.status_code == 429:
                wait = 10 * attempt
                print(
                    f"  Rate limited — waiting {wait}s before retry {attempt}/3 "
                    f"for page {page_num}",
                    flush=True,
                )
                time.sleep(wait)
            else:
                break
        except requests.exceptions.Timeout:
            last_err = "timeout"
            wait = 5 * attempt
            print(
                f"  Timeout — waiting {wait}s before retry {attempt}/3 for page {page_num}",
                flush=True,
            )
            time.sleep(wait)
        except Exception as e:
            last_err = str(e)
            break

    print(f"  Warning: failed for page {page_num}: {last_err}", flush=True)
    return {
        "page_num": page_num,
        "has_new_topic": "",
        "subject": "",
        "summary": "",
        "legal_significance": "",
        "_failed": True,
    }


def deduplicate_topics(rows):
    """
    Merge adjacent entries with nearly identical subjects (first 30 chars, case-insensitive).
    Keeps the first occurrence.
    """
    if not rows:
        return rows
    merged = [rows[0]]
    for row in rows[1:]:
        prev = merged[-1]
        if (
            prev["subject"].lower()[:30]
            and prev["subject"].lower()[:30] == row["subject"].lower()[:30]
        ):
            pass  # skip duplicate
        else:
            merged.append(row)
    return merged


def compute_page_ranges(topic_rows, page_end):
    """
    Assign page_end to each topic: the page before the next topic starts,
    or page_end for the last topic.
    """
    for i, row in enumerate(topic_rows):
        if i + 1 < len(topic_rows):
            row["page_end"] = topic_rows[i + 1]["page_num"] - 1
        else:
            row["page_end"] = page_end
    return topic_rows


def make_slug(file_name: str) -> str:
    """Derive a filesystem-safe slug from the file name."""
    stem = os.path.splitext(file_name)[0] if file_name else "depo"
    slug = re.sub(r'[^\w\s-]', '', stem).strip()
    slug = re.sub(r'[\s-]+', '_', slug)
    return slug[:60] or "depo"


def main():
    parser = argparse.ArgumentParser(description="Deposition Summary Pipeline")
    parser.add_argument("--file-id", required=True, help="Box file ID of the deposition PDF")
    parser.add_argument("--token", required=True, help="Box access token")
    parser.add_argument("--output-dir", required=True, help="Directory for output files")
    parser.add_argument(
        "--model", default="google__gemini_2_5_pro", help="Box AI model ID"
    )
    parser.add_argument("--workers", type=int, default=5, help="Parallel API workers")
    parser.add_argument(
        "--page-start", type=int, default=None,
        help="First page to process (default: auto-detect)"
    )
    parser.add_argument(
        "--page-end", type=int, default=None,
        help="Last page to process (default: auto-detect)"
    )
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    # --- Auth & PDF download ---
    auth = OAuth2(client_id=None, client_secret=None, access_token=args.token)
    client = Client(auth)

    print(f"Downloading PDF for file ID: {args.file_id}", flush=True)
    file_obj = client.file(args.file_id).get()
    file_name = file_obj.name
    pdf_bytes = client.file(args.file_id).content()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    total_pages = len(doc)
    print(f"Transcript: {total_pages} pages ({file_name})", flush=True)

    # Save transcript PDF for later use by depo_pdf_generator.py
    slug = make_slug(file_name)
    transcript_path = os.path.join(args.output_dir, f"{slug}_transcript.pdf")
    with open(transcript_path, 'wb') as _f:
        _f.write(pdf_bytes)
    print(f"Transcript PDF saved → {transcript_path}", flush=True)

    # --- Preamble skip ---
    if args.page_start is not None:
        page_start = max(1, args.page_start)
    else:
        page_start = detect_testimony_start(doc)
        print(f"Auto-detected testimony start: page {page_start}", flush=True)

    if args.page_end is not None:
        page_end = min(total_pages, args.page_end)
    else:
        page_end = detect_testimony_end(doc)
        print(f"Auto-detected testimony end: page {page_end}", flush=True)

    pages_to_process = list(range(page_start, page_end + 1))
    n_pages = len(pages_to_process)

    print(
        f"Processing pages {page_start}–{page_end} ({n_pages} pages) "
        f"with {args.workers} workers (model: {args.model})",
        flush=True,
    )

    counter = [0]
    lock = threading.Lock()
    results = []

    def process(page_num):
        result = process_page(
            args.token, args.file_id, page_num, args.model,
            doc, page_start, page_end
        )
        with lock:
            counter[0] += 1
            n = counter[0]
        label = result.get("subject") or "no new topic"
        if result.get("_failed"):
            label = "FAILED"
        print(f"  [{n}/{n_pages}] page {page_num} → {label}", flush=True)
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(process, p): p for p in pages_to_process}
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    doc.close()

    # --- Filter, sort, deduplicate, compute ranges ---
    topic_rows = [
        r for r in results
        if r.get("has_new_topic") == "yes" and r.get("subject")
    ]
    topic_rows.sort(key=lambda r: r["page_num"])
    topic_rows = deduplicate_topics(topic_rows)
    topic_rows = compute_page_ranges(topic_rows, page_end)

    failed_count = sum(1 for r in results if r.get("_failed"))

    # --- Write CSV ---
    csv_path = os.path.join(args.output_dir, f"{slug}_depo_topics.csv")

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["page_start", "page_end", "subject", "summary", "legal_significance"]
        )
        writer.writeheader()
        for row in topic_rows:
            writer.writerow({
                "page_start": row["page_num"],
                "page_end": row["page_end"],
                "subject": row["subject"],
                "summary": row["summary"],
                "legal_significance": row["legal_significance"],
            })

    print(
        f"\nFound {len(topic_rows)} topics across {n_pages} pages processed.",
        flush=True,
    )
    if failed_count:
        print(f"Warning: {failed_count} pages failed.", flush=True)
    print(f"Output → {csv_path}", flush=True)


if __name__ == "__main__":
    main()
