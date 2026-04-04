"""
Deposition PDF Generator — prepends a clickable summary table to the transcript PDF.

Reads the _depo_topics.csv and the downloaded transcript PDF, generates a
styled summary table (with internal GoTo links in the Page column), and
prepends it to the transcript to create a single navigable PDF.

Usage:
  .venv/bin/python3 python/depo_pdf_generator.py \
    --transcript-path /tmp/depo_test/slug_transcript.pdf \
    --csv-path        /tmp/depo_test/slug_depo_topics.csv \
    --output-path     /tmp/depo_test/slug_Summarized.pdf \
    [--case-name "Smith v. Jones"]
"""

import argparse
import csv
import os
import re
from datetime import date

import fitz  # PyMuPDF


# ── Colour palette ─────────────────────────────────────────────────────────────
def _rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


C_GREEN   = _rgb('669966')
C_BLACK   = _rgb('212121')
C_DARK    = _rgb('37474F')
C_COL_HDR = _rgb('455A64')
C_ACCENT  = _rgb('1565C0')
C_LTGRAY  = _rgb('F5F5F5')
C_GRID    = _rgb('DDDDDD')
C_WHITE   = (1.0, 1.0, 1.0)
C_TEXT    = _rgb('212121')
C_MUTED   = _rgb('666666')

# ── Layout constants ───────────────────────────────────────────────────────────
PW, PH   = 612, 792           # US Letter in points
MARGIN   = 40
CW       = PW - 2 * MARGIN    # 532 pt content width

COL_PAGE    = 60
COL_SUBJECT = 140
COL_SUMMARY = CW - COL_PAGE - COL_SUBJECT   # 332

FSIZE  = 9
LINE_H = FSIZE + 3   # 12 pt
PAD    = 5           # vertical padding inside each cell

FONT_SERIF  = "Times-Roman"
FONT_BOLD   = "Times-Bold"
FONT_ITALIC = "Times-Italic"


# ── Helpers ────────────────────────────────────────────────────────────────────
def _inset(rect, d):
    return fitz.Rect(rect.x0 + d, rect.y0 + d, rect.x1 - d, rect.y1 - d)


def _fill(page, rect, color):
    page.draw_rect(rect, color=None, fill=color, width=0)


def _tb(page, rect, text, font, size, color=None, align=0, label=''):
    rc = page.insert_textbox(rect, text, fontname=font, fontsize=size,
                             color=color or C_TEXT, align=align)
    if rc < 0 and text:
        print(
            f"  [textbox overflow] {label} rc={rc:.1f} "
            f"rect_h={rect.height:.1f} text_len={len(text)} font={font} size={size}",
            flush=True,
        )


def _measure_lines(text: str, col_width: float, fontname: str, fontsize: float) -> int:
    """Exact line count using PyMuPDF font metrics — simulates word-wrap identically to insert_textbox."""
    if not text:
        return 1
    usable = col_width - 2 * PAD
    lines, cur_w = 1, 0.0
    for word in text.split():
        w = fitz.get_text_length(word + ' ', fontname=fontname, fontsize=fontsize)
        if cur_w > 0 and cur_w + w > usable:
            lines += 1
            cur_w = w
        else:
            cur_w += w
    return lines + 1  # +1 safety: PyMuPDF renders nothing if even 1pt overflows


def _row_height(topic: dict) -> float:
    subject_lines = _measure_lines(topic.get('subject', ''), COL_SUBJECT, FONT_BOLD, FSIZE)
    sig = topic.get('legal_significance', '').strip()
    if sig:
        subject_lines += _measure_lines(sig, COL_SUBJECT, FONT_ITALIC, FSIZE - 1)
    max_lines = max(
        1,
        _measure_lines(topic.get('summary', ''), COL_SUMMARY, FONT_SERIF, FSIZE),
        subject_lines,
    )
    return max(22.0, max_lines * LINE_H + 2 * PAD)


_UNICODE_MAP = str.maketrans({
    '\u2018': "'", '\u2019': "'",   # ' '
    '\u201c': '"', '\u201d': '"',   # " "
    '\u2013': '-', '\u2014': '--',  # en-dash, em-dash
    '\u2026': '...',                # ellipsis
    '\u00a0': ' ',                  # non-breaking space
    '\u2022': '*',                  # bullet
})


def _sanitize(text: str, label: str = '') -> str:
    """Replace Unicode typography with ASCII equivalents; drop anything non-ASCII."""
    if not text:
        return text
    result = text.translate(_UNICODE_MAP).encode('ascii', errors='replace').decode('ascii')
    replaced = sum(1 for a, b in zip(text, result) if a != b) + abs(len(text) - len(result))
    if replaced:
        print(f"  [sanitize] {label}: replaced {replaced} non-ASCII char(s)", flush=True)
    return result


def format_page(start, end):
    try:
        s, e = int(start), int(end)
    except (ValueError, TypeError):
        return str(start) if start else ''
    return f"p.{s}-{e}" if e > s else f"p.{s}"


def load_topics(path: str):
    rows = []
    with open(path, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows


def derive_case_name(csv_path: str) -> str:
    stem = os.path.splitext(os.path.basename(csv_path))[0]
    stem = re.sub(r'_depo_topics$', '', stem, flags=re.IGNORECASE)
    return stem.replace('_', ' ').title()


# ── PDF builder ────────────────────────────────────────────────────────────────
def build_summary_pdf(topics: list, case_name: str):
    """
    Build the summary table PDF (without links yet — links are injected after
    the page count is known and the transcript is appended).

    Returns:
      (fitz.Document, link_records)
      link_records: list of (page_idx_0based, fitz.Rect, phys_page_1based_in_transcript)
    """
    doc = fitz.open()
    link_records = []

    x_page    = float(MARGIN)
    x_subject = x_page + COL_PAGE
    x_summary = x_subject + COL_SUBJECT

    def new_page():
        return doc.new_page(width=PW, height=PH)

    page = new_page()
    y = float(MARGIN)

    # ── Masthead ──────────────────────────────────────────────────────────────
    H1, H2, H3 = 24.0, 20.0, 16.0

    r = fitz.Rect(MARGIN, y, MARGIN + CW, y + H1)
    _fill(page, r, C_GREEN)
    _tb(page, _inset(r, 2), _sanitize(case_name.upper()), FONT_BOLD, 11, C_WHITE, align=1)
    y += H1

    pages_set = sorted({int(t['page_start']) for t in topics if t.get('page_start')})
    span = (max(pages_set) - min(pages_set) + 1) if pages_set else 0

    r = fitz.Rect(MARGIN, y, MARGIN + CW, y + H2)
    _fill(page, r, C_BLACK)
    _tb(page, _inset(r, 2),
        f"DEPOSITION SUMMARY — {len(topics)} topics across {span} pages",
        FONT_BOLD, 10, C_WHITE, align=1)
    y += H2

    r = fitz.Rect(MARGIN, y, MARGIN + CW, y + H3)
    _fill(page, r, C_DARK)
    _tb(page, _inset(r, 2),
        f"Generated by FPAmed Box Index Tool — {date.today().strftime('%B %d, %Y')}",
        FONT_SERIF, 8, C_WHITE, align=1)
    y += H3 + 8  # spacer

    # ── Column headers ────────────────────────────────────────────────────────
    HDR_H = 18.0
    r_full = fitz.Rect(MARGIN, y, MARGIN + CW, y + HDR_H)
    _fill(page, r_full, C_COL_HDR)
    for label, x, w in [
        ('PAGE', x_page, COL_PAGE),
        ('SUBJECT', x_subject, COL_SUBJECT),
        ('SUMMARY', x_summary, COL_SUMMARY),
    ]:
        _tb(page, _inset(fitz.Rect(x, y, x + w, y + HDR_H), 2),
            label, FONT_BOLD, 9, C_WHITE, align=1)
    y += HDR_H

    # ── Data rows ─────────────────────────────────────────────────────────────
    FOOTER_RESERVE = 36.0  # space to reserve at page bottom for footer

    for i, topic in enumerate(topics):
        rh = _row_height(topic)

        if y + rh > PH - MARGIN - FOOTER_RESERVE:
            page = new_page()
            y = float(MARGIN)

        has_sig = bool(topic.get('legal_significance', '').strip())
        bg = C_LTGRAY if i % 2 == 0 else None

        r_row = fitz.Rect(MARGIN, y, MARGIN + CW, y + rh)
        if bg:
            _fill(page, r_row, bg)

        # Blue accent bar for legally significant rows
        if has_sig:
            _fill(page, fitz.Rect(MARGIN, y, MARGIN + 3, y + rh), C_ACCENT)

        # Grid outline
        page.draw_rect(r_row, color=C_GRID, width=0.5)

        # Page number cell
        r_pg = fitz.Rect(x_page, y, x_page + COL_PAGE, y + rh)
        page_label = format_page(topic.get('page_start', ''), topic.get('page_end', ''))
        _tb(page, _inset(r_pg, PAD), page_label, FONT_BOLD, FSIZE, align=1)

        # Record link target (resolved after transcript page count is known)
        try:
            ps = int(topic.get('page_start', 0))
        except (ValueError, TypeError):
            ps = 0
        if ps > 0:
            link_records.append((len(doc) - 1, r_pg, ps))

        # Subject cell
        if has_sig:
            subject_lines = _measure_lines(topic.get('subject', ''), COL_SUBJECT, FONT_BOLD, FSIZE)
            split_y = y + PAD + subject_lines * LINE_H
            r_sub_top = fitz.Rect(x_subject + PAD, y + PAD, x_subject + COL_SUBJECT - PAD, split_y)
            r_sub_bot = fitz.Rect(x_subject + PAD, split_y, x_subject + COL_SUBJECT - PAD, y + rh - PAD)
            _tb(page, r_sub_top,
                _sanitize(topic.get('subject', ''), f"p{page_label} subject"),
                FONT_BOLD, FSIZE,
                label=f"p{page_label} subject")
            _tb(page, r_sub_bot,
                _sanitize(topic.get('legal_significance', ''), f"p{page_label} significance"),
                FONT_ITALIC, FSIZE - 1, color=C_ACCENT,
                label=f"p{page_label} significance")
        else:
            r_sub = fitz.Rect(x_subject, y, x_subject + COL_SUBJECT, y + rh)
            _tb(page, _inset(r_sub, PAD),
                _sanitize(topic.get('subject', ''), f"p{page_label} subject"),
                FONT_SERIF, FSIZE,
                label=f"p{page_label} subject")

        # Summary cell
        r_sum = fitz.Rect(x_summary, y, x_summary + COL_SUMMARY, y + rh)
        _tb(page, _inset(r_sum, PAD),
            _sanitize(topic.get('summary', ''), f"p{page_label} summary"),
            FONT_SERIF, FSIZE,
            label=f"p{page_label} summary")

        y += rh

    # ── Methodology footer (last page) ────────────────────────────────────────
    FOOTER_H = 28.0
    fy = PH - MARGIN - FOOTER_H
    r_f = fitz.Rect(MARGIN, fy, MARGIN + CW, fy + FOOTER_H)
    _fill(page, r_f, C_LTGRAY)
    _tb(page, _inset(r_f, 3),
        "NOTES ON METHODOLOGY: This summary was generated by processing the transcript page "
        "by page using Box AI. Topic count and page citations should be verified against the "
        "source transcript before citing in any filing or expert report.",
        FONT_ITALIC, 8, C_MUTED, align=0)

    return doc, link_records


def main():
    p = argparse.ArgumentParser(description='Deposition PDF Generator')
    p.add_argument('--transcript-path', required=True, help='Path to downloaded transcript PDF')
    p.add_argument('--csv-path',        required=True, help='Path to _depo_topics.csv')
    p.add_argument('--output-path',     required=True, help='Output path for the Summarized PDF')
    p.add_argument('--case-name',       default=None,  help='Override case name in header')
    args = p.parse_args()

    if not os.path.exists(args.transcript_path):
        print(f'ERROR: transcript not found: {args.transcript_path}', flush=True)
        raise SystemExit(1)
    if not os.path.exists(args.csv_path):
        print(f'ERROR: CSV not found: {args.csv_path}', flush=True)
        raise SystemExit(1)

    topics = load_topics(args.csv_path)
    if not topics:
        print('ERROR: No topics in CSV.', flush=True)
        raise SystemExit(1)

    case_name = args.case_name or derive_case_name(args.csv_path)
    print(f'Building summary table for: {case_name} ({len(topics)} topics)', flush=True)

    summary_doc, link_records = build_summary_pdf(topics, case_name)
    n_summary = len(summary_doc)
    print(f'Summary table: {n_summary} page(s)', flush=True)

    transcript_doc = fitz.open(args.transcript_path)
    n_transcript = len(transcript_doc)
    print(f'Transcript: {n_transcript} page(s)', flush=True)

    # Append transcript pages to summary doc (summary pages now at indices 0..n_summary-1)
    summary_doc.insert_pdf(transcript_doc)
    transcript_doc.close()

    # Inject GoTo link annotations into the summary pages
    # Target page index in merged doc = (1-based physical page - 1) + n_summary
    for page_idx, rect, phys_page in link_records:
        dest = (phys_page - 1) + n_summary
        if dest < len(summary_doc):
            summary_doc[page_idx].insert_link({
                'kind': fitz.LINK_GOTO,
                'from': rect,
                'page': dest,
                'to': fitz.Point(0, 0),
            })

    summary_doc.save(args.output_path, garbage=4, deflate=True)
    summary_doc.close()

    print(f'PDF saved → {args.output_path}', flush=True)
    print(f'  Summary pages:    {n_summary}', flush=True)
    print(f'  Transcript pages: {n_transcript}', flush=True)
    print(f'  Total pages:      {n_summary + n_transcript}', flush=True)


if __name__ == '__main__':
    main()
