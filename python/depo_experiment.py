"""
Deposition Summary Experiment — Box AI page-by-page extraction.
Tests whether Box AI extract_structured can produce a quality deposition
summary without leaving Box infrastructure.

Approach: Download the PDF once, extract page text via PyMuPDF, then call
Box AI extract_structured with the text in the `content` field of the file
item (page-range scoping is not supported by extract_structured; inline
text items without a file id are also not supported).

Usage:
  .venv/bin/python3 python/depo_experiment.py \
    --file-id YOUR_BOX_FILE_ID \
    --token YOUR_BOX_DEV_TOKEN \
    --output depo_experiment_output.csv

  # Test on first 50 pages only
  .venv/bin/python3 python/depo_experiment.py \
    --file-id YOUR_BOX_FILE_ID \
    --token YOUR_BOX_DEV_TOKEN \
    --page-end 50 \
    --output depo_test_50pages.csv
"""

import argparse
import csv
import threading
import concurrent.futures
import time
import requests
import fitz  # pymupdf
from boxsdk import OAuth2, Client

BOX_EXTRACT_URL = "https://api.box.com/2.0/ai/extract_structured"

FIELDS = [
    {
        "key": "has_new_topic",
        "type": "string",
        "description": (
            "Does a new substantive topic begin on the focal page of this transcript excerpt? "
            "Answer 'yes' or 'no' only."
        ),
        "prompt": (
            "Look at the focal page (marked [FOCAL PAGE]). "
            "Does a new substantive topic begin on this page — meaning a line of questioning "
            "that is meaningfully distinct from what immediately preceded it? "
            "Only answer 'yes' if the focal page is the FIRST page where this topic appears. "
            "If the topic was already underway on the previous page, answer 'no'. "
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
            "focal page number as a plain integer. If no new topic begins, return empty string."
        ),
    },
]


def build_page_window(doc, page_num, context_pages=1):
    """
    Extract text from a sliding window of pages around page_num (1-indexed).
    The focal page is labelled [FOCAL PAGE]. Returns the combined text string.
    """
    total = len(doc)
    start = max(0, page_num - 1 - context_pages)  # 0-indexed
    end = min(total - 1, page_num - 1 + context_pages)

    parts = []
    for i in range(start, end + 1):
        label = f"--- PAGE {i + 1}{' [FOCAL PAGE]' if i + 1 == page_num else ''} ---"
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
        },
    }
    response = requests.post(BOX_EXTRACT_URL, headers=headers, json=payload, timeout=120)
    response.raise_for_status()
    answer = response.json().get("answer", {})
    return {
        "has_new_topic": (answer.get("has_new_topic") or "").strip().lower(),
        "subject": (answer.get("subject") or "").strip(),
        "summary": (answer.get("summary") or "").strip(),
        "page_range_end": (answer.get("page_range_end") or "").strip(),
    }


def process_page(token: str, file_id: str, page_num: int, total_pages: int,
                 model: str, doc) -> dict:
    content = build_page_window(doc, page_num)
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
        "page_range_end": "",
        "_failed": True,
    }


def deduplicate_topics(rows):
    """
    Merge adjacent entries with nearly identical subjects (first 30 chars,
    case-insensitive). Keeps the first, extends page_range_end if later end
    is larger.
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
            try:
                prev_end = int(prev.get("page_range_end") or prev["page_num"])
                curr_end = int(row.get("page_range_end") or row["page_num"])
                if curr_end > prev_end:
                    prev["page_range_end"] = str(curr_end)
            except (ValueError, TypeError):
                pass
        else:
            merged.append(row)
    return merged


def format_page_range(row):
    start = row["page_num"]
    try:
        end = int(row.get("page_range_end") or start)
    except (ValueError, TypeError):
        end = start
    return f"{start}–{end}" if end > start else str(start)


def main():
    parser = argparse.ArgumentParser(description="Deposition Summary Experiment")
    parser.add_argument("--file-id", required=True, help="Box file ID of the deposition PDF")
    parser.add_argument("--token", required=True, help="Box access token")
    parser.add_argument(
        "--output", default="depo_experiment_output.csv", help="Output CSV path"
    )
    parser.add_argument(
        "--model", default="google__gemini_2_5_pro", help="Box AI model ID"
    )
    parser.add_argument("--workers", type=int, default=3, help="Parallel API workers")
    parser.add_argument(
        "--page-start", type=int, default=1, help="First page to process (1-indexed)"
    )
    parser.add_argument(
        "--page-end", type=int, default=None, help="Last page to process (default: all)"
    )
    args = parser.parse_args()

    # --- Auth & PDF download ---
    auth = OAuth2(client_id=None, client_secret=None, access_token=args.token)
    client = Client(auth)

    print(f"Downloading PDF for file ID: {args.file_id}", flush=True)
    pdf_bytes = client.file(args.file_id).content()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    total_pages = len(doc)
    print(f"Transcript: {total_pages} pages", flush=True)

    page_start = max(1, args.page_start)
    page_end = min(total_pages, args.page_end) if args.page_end else total_pages
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
        result = process_page(args.token, args.file_id, page_num, total_pages, args.model, doc)
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

    # --- Filter, sort, deduplicate ---
    topic_rows = [
        r for r in results
        if r.get("has_new_topic") == "yes" and r.get("subject")
    ]
    topic_rows.sort(key=lambda r: r["page_num"])
    topic_rows = deduplicate_topics(topic_rows)

    failed_count = sum(1 for r in results if r.get("_failed"))

    # --- Write CSV ---
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["Page", "Subject", "Summary"])
        writer.writeheader()
        for row in topic_rows:
            writer.writerow(
                {
                    "Page": format_page_range(row),
                    "Subject": row["subject"],
                    "Summary": row["summary"],
                }
            )

    # --- Preview ---
    if topic_rows:
        print("\n--- Preview (first 10 topics) ---", flush=True)
        for row in topic_rows[:10]:
            print(f"  p.{format_page_range(row):>6}  {row['subject']}", flush=True)
            if row["summary"]:
                preview = row["summary"][:120] + ("…" if len(row["summary"]) > 120 else "")
                print(f"           {preview}", flush=True)

    print(f"\nFound {len(topic_rows)} topics across {n_pages} pages processed.", flush=True)
    if failed_count:
        print(f"Warning: {failed_count} pages failed.", flush=True)
    print(f"Output → {args.output}", flush=True)


if __name__ == "__main__":
    main()
