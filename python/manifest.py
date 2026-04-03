"""
Box Folder Manifest Generator — CLI wrapper for Next.js integration.
Accepts --token, --folder-id, --output-dir as arguments.
"""

import argparse
import csv
import json
import re
import time
import collections
import os
import io
import fitz  # pymupdf
from boxsdk import OAuth2, Client

REQUEST_DELAY = 0.03

NON_PAGE_EXTENSIONS = {
    '.wav', '.mp3', '.mp4', '.avi', '.mov', '.mkv',
    '.flac', '.aac', '.ogg', '.wma', '.m4a', '.m4v',
    '.wmv', '.mpg', '.mpeg', '.webm', '.m4b',
    '.zip', '.gz', '.tar', '.rar', '.7z', 'html', '.json', '.wgva', '.xml'
}


def infer_page_count_from_bates(filename):
    stem = os.path.splitext(filename)[0]

    parts = stem.rsplit('-', 1)
    if len(parts) == 2:
        left, right = parts
        lm = re.search(r'(\d+)$', left)
        rm = re.search(r'(\d+)$', right)
        if lm and rm:
            l_str, r_str = lm.group(1), rm.group(1)
            if len(l_str) >= 4 and len(r_str) >= 4:
                start, end = int(l_str), int(r_str)
                if 0 <= end - start <= 9999:
                    return end - start + 1

    for m in re.finditer(r'(?<!\d)(\d+)\s*-\s*(\d+)(?!\d)', stem):
        start, end = int(m.group(1)), int(m.group(2))
        if 1900 <= start <= 2099 and 1 <= end <= 31:
            continue
        if 1 <= start <= 31 and 1 <= end <= 31 and end - start <= 30:
            continue
        after = stem[m.end():]
        if re.match(r'_\d', after):
            continue
        if end >= start and 1 <= end - start + 1 <= 50000:
            return end - start + 1

    return None


def get_page_count_from_pdf(client, file_id):
    try:
        pdf_bytes = client.file(file_id).content()
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        count = len(doc)
        doc.close()
        return count
    except Exception:
        return None


def walk_box_folder(client, folder_id, path="", on_file=None, skipped=None):
    if skipped is None:
        skipped = {}
    manifest = []

    try:
        folder = client.folder(folder_id=folder_id).get()
        folder_path = f"{path}/{folder.name}" if path else folder.name

        fields = ['id', 'name', 'type', 'size', 'created_at', 'modified_at']
        for item in folder.get_items(limit=1000, fields=fields):
            current_path = f"{folder_path}/{item.name}"

            if item.type == 'file':
                if on_file:
                    on_file(current_path)
                ext = os.path.splitext(item.name)[1].lower() or 'no extension'

                if ext == '.pdf':
                    page_count = get_page_count_from_pdf(client, item.id)
                    if page_count is not None:
                        page_source = 'pdf_parsed'
                    else:
                        page_count = infer_page_count_from_bates(item.name)
                        page_source = 'bates_inferred' if page_count is not None else 'N/A'
                        if page_count is None:
                            page_count = 'N/A'
                else:
                    if ext in NON_PAGE_EXTENSIONS:
                        page_count = 'N/A'
                        page_source = 'N/A'
                    else:
                        page_count = infer_page_count_from_bates(item.name)
                        page_source = 'bates_inferred' if page_count is not None else 'N/A'
                        if page_count is None:
                            page_count = 'N/A'

                raw_size = getattr(item, 'size', None)
                size_kb = round(raw_size / 1024, 1) if raw_size else 'N/A'
                created = getattr(item, 'created_at', None)
                modified = getattr(item, 'modified_at', None)

                manifest.append({
                    'Name': item.name,
                    'Path': current_path,
                    'Folder': folder_path,
                    'Folder ID': folder_id,
                    'Folder URL': f'https://app.box.com/folder/{folder_id}',
                    'Extension': ext,
                    'Page Count': page_count,
                    'Page Count Source': page_source,
                    'Size (KB)': size_kb,
                    'Created': created[:10] if created else 'N/A',
                    'Modified': modified[:10] if modified else 'N/A',
                    'File ID': item.id,
                    'File URL': f'https://app.box.com/file/{item.id}',
                })
                time.sleep(REQUEST_DELAY)

            elif item.type == 'folder':
                manifest.extend(walk_box_folder(client, item.id, folder_path, on_file=on_file, skipped=skipped))
            else:
                skipped[item.type] = skipped.get(item.type, 0) + 1

    except Exception as e:
        print(f"  Error accessing folder {folder_id}: {e}", flush=True)

    return manifest


def annotate_duplicates(manifest):
    key_counts = collections.Counter(
        (r['Name'], r['Size (KB)']) for r in manifest
    )
    for row in manifest:
        row['Duplicate'] = 'Yes' if key_counts[(row['Name'], row['Size (KB)'])] > 1 else 'No'
    duplicates = [r for r in manifest if r['Duplicate'] == 'Yes']
    return manifest, duplicates


def build_and_write_summary(manifest, summary_file):
    folders = collections.defaultdict(lambda: {
        'File Count': 0,
        'Known Page Total': 0,
        'NA Count': 0,
        'Total Size KB': 0,
        'File Types': collections.Counter(),
    })

    for row in manifest:
        f = row['Folder']
        folders[f]['File Count'] += 1
        folders[f]['File Types'][row['Extension']] += 1
        if row['Page Count'] == 'N/A':
            folders[f]['NA Count'] += 1
        else:
            folders[f]['Known Page Total'] += int(row['Page Count'])
        if row['Size (KB)'] != 'N/A':
            folders[f]['Total Size KB'] += float(row['Size (KB)'])

    summary = []
    for folder_path, data in sorted(folders.items()):
        depth = folder_path.count('/')
        types_str = ', '.join(f"{ext}({n})" for ext, n in data['File Types'].most_common())
        total_kb = data['Total Size KB']
        size_display = (
            f"{round(total_kb / 1024, 1)} MB" if total_kb >= 1024
            else f"{round(total_kb, 1)} KB"
        )
        summary.append({
            'Folder': folder_path,
            'Depth': depth,
            'File Count': data['File Count'],
            'Known Page Total': data['Known Page Total'] if data['Known Page Total'] > 0 else 'N/A',
            'Files Missing Page Count': data['NA Count'],
            'Total Size': size_display,
            'File Types': types_str,
        })

    with open(summary_file, 'w', newline='', encoding='utf-8') as f:
        fieldnames = ['Folder', 'Depth', 'File Count', 'Known Page Total',
                      'Files Missing Page Count', 'Total Size', 'File Types']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(summary)


def main():
    parser = argparse.ArgumentParser(description='Box Folder Manifest Generator')
    parser.add_argument('--token', required=True, help='Box access token')
    parser.add_argument('--folder-id', required=True, help='Box folder ID to crawl')
    parser.add_argument('--output-dir', required=True, help='Directory for output CSVs')
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    auth = OAuth2(client_id=None, client_secret=None, access_token=args.token)
    client = Client(auth)

    try:
        me = client.user().get()
        print(f"Authenticated as: {me.name} ({me.login})", flush=True)
    except Exception as e:
        print(f"Authentication failed: {e}", flush=True)
        raise SystemExit(1)

    print(f"Crawling folder ID: {args.folder_id}", flush=True)

    file_count = [0]
    def on_file(path_str):
        file_count[0] += 1
        print(f"Processing file {file_count[0]}: {path_str}", flush=True)

    skipped = {}
    manifest = walk_box_folder(client, args.folder_id, on_file=on_file, skipped=skipped)

    if not manifest:
        print("No files found.", flush=True)
        raise SystemExit(1)

    case_root = manifest[0]['Path'].split('/')[0]
    slug = re.sub(r'[^\w]+', '_', case_root).strip('_').lower()

    output_file = os.path.join(args.output_dir, f'{slug}_manifest.csv')
    summary_file = os.path.join(args.output_dir, f'{slug}_summary.csv')
    dupes_file = os.path.join(args.output_dir, f'{slug}_duplicates.csv')

    manifest, duplicates = annotate_duplicates(manifest)

    fieldnames = ['Name', 'Path', 'Folder', 'Folder ID', 'Folder URL', 'Extension',
                  'Page Count', 'Page Count Source',
                  'Size (KB)', 'Created', 'Modified', 'File ID', 'File URL', 'Duplicate']

    with open(output_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(manifest)

    dup_rows_sorted = sorted(duplicates, key=lambda r: (r['Name'], r['Size (KB)']))
    with open(dupes_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(dup_rows_sorted)

    build_and_write_summary(manifest, summary_file)

    meta_file = os.path.join(args.output_dir, f'{slug}_meta.json')
    with open(meta_file, 'w', encoding='utf-8') as f:
        json.dump({'skipped': skipped, 'total_indexed': len(manifest)}, f)

    skipped_total = sum(skipped.values())
    if skipped:
        skipped_parts = ', '.join(f"{n} {t.replace('_', ' ')}{'s' if n != 1 else ''}" for t, n in sorted(skipped.items()))
        print(f"Done. {len(manifest)} files indexed, {skipped_total} items skipped ({skipped_parts}) → {output_file}", flush=True)
    else:
        print(f"Done. {len(manifest)} files → {output_file}", flush=True)


if __name__ == "__main__":
    main()
