"""
Condensed transcript detection — discovery/test script.

Downloads a PDF from Box and scans every page for transcript page-number labels
using PyMuPDF text extraction. Prints per-page findings and a condensed-format
verdict. Not invoked by the app.

Usage:
  .venv/bin/python3 python/test_condensed_detect.py \
    --file-id <box-file-id> \
    --token <box-access-token> \
    --output-dir /tmp/condensed_test
"""

import argparse
import json
import os
import re

import fitz
import requests
from boxsdk import OAuth2, Client


def find_page_labels(page, page_idx):
    """
    Scan a single PDF page for 'Page N' labels using word-level extraction.
    Returns list of dicts: {transcript_page, x, y, word_text}
    """
    words = page.get_text("words")  # [(x0, y0, x1, y1, word, block, line, word_idx), ...]
    labels = []

    # Pass 1: look for the two-word sequence "Page" <integer>
    for i, w in enumerate(words):
        if w[4].strip().lower() == "page" and i + 1 < len(words):
            nxt = words[i + 1]
            try:
                page_num = int(nxt[4].strip())
            except ValueError:
                continue
            # x, y = top-left of the "Page" word
            labels.append({
                "transcript_page": page_num,
                "x": w[0],
                "y": w[1],
                "text": f"Page {page_num}",
            })

    return labels


def build_page_map(doc):
    """
    Scan all pages and return a map: transcript_page -> {pdf_page, x, y}
    Also returns raw findings per PDF page for debugging.
    """
    page_map = {}
    raw = {}

    for page_idx in range(len(doc)):
        page = doc[page_idx]
        labels = find_page_labels(page, page_idx)
        raw[page_idx] = labels
        for lbl in labels:
            tp = lbl["transcript_page"]
            if tp not in page_map:
                page_map[tp] = {
                    "pdf_page": page_idx,
                    "x": lbl["x"],
                    "y": lbl["y"],
                }

    return page_map, raw


def main():
    parser = argparse.ArgumentParser(description="Condensed transcript detection test")
    parser.add_argument("--file-id", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    # Download PDF via Box (same pattern as depo_summary.py)
    auth = OAuth2(client_id=None, client_secret=None, access_token=args.token)
    client = Client(auth)

    print(f"Downloading PDF for file ID: {args.file_id}", flush=True)
    file_obj = client.file(args.file_id).get()
    file_name = file_obj.name
    pdf_bytes = client.file(args.file_id).content()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    total_pdf_pages = len(doc)
    print(f"File: {file_name}")
    print(f"PDF pages: {total_pdf_pages}\n")

    # Save PDF locally for manual inspection
    pdf_path = os.path.join(args.output_dir, file_name)
    with open(pdf_path, "wb") as f:
        f.write(pdf_bytes)
    print(f"PDF saved → {pdf_path}\n")

    # Scan for page labels
    page_map, raw = build_page_map(doc)
    doc.close()

    # Print per-PDF-page findings
    print("=== Per-PDF-page label findings ===")
    for page_idx in range(total_pdf_pages):
        labels = raw.get(page_idx, [])
        if labels:
            label_strs = [f"{l['text']} @ ({l['x']:.1f}, {l['y']:.1f})" for l in labels]
            print(f"  PDF page {page_idx + 1}: {', '.join(label_strs)}")
        else:
            print(f"  PDF page {page_idx + 1}: (no labels found)")

    # Condensed verdict
    n_transcript = len(page_map)
    ratio = n_transcript / total_pdf_pages if total_pdf_pages else 0
    condensed = ratio > 1.5
    print(f"\n=== Verdict ===")
    print(f"  Transcript page labels found: {n_transcript}")
    print(f"  PDF pages: {total_pdf_pages}")
    print(f"  Ratio: {ratio:.2f}")
    print(f"  Format: {'CONDENSED' if condensed else 'UNCONDENSED'}")

    if page_map:
        pages = sorted(page_map.keys())
        print(f"  Transcript page range detected: {min(pages)}–{max(pages)}")

    # Save map JSON for inspection
    map_path = os.path.join(args.output_dir, "page_map.json")
    with open(map_path, "w") as f:
        json.dump({str(k): v for k, v in sorted(page_map.items())}, f, indent=2)
    print(f"\nPage map saved → {map_path}")


if __name__ == "__main__":
    main()
