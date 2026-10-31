"""
scripts/download_sample_pdfs.py
Downloads sample SEC 10-K PDFs from the FinanceBench open dataset into data/pdfs/.

Usage:
    python scripts/download_sample_pdfs.py --limit 15
    python scripts/download_sample_pdfs.py --all
"""
import os
import sys
import json
import argparse
import urllib.request
from pathlib import Path

PDF_DIR = Path("data/pdfs")
FINANCEBENCH_API_URL = "https://api.github.com/repos/patronus-ai/financebench/contents/pdfs"

# Core curated 10-K PDFs commonly referenced in FinanceBench benchmark questions
CORE_SAMPLE_FILES = [
    "3M_2022_10K.pdf",
    "3M_2023_10K.pdf",
    "AMAZON_2022_10K.pdf",
    "AMAZON_2023_10K.pdf",
    "APPLE_2022_10K.pdf",
    "APPLE_2023_10K.pdf",
    "MICROSOFT_2022_10K.pdf",
    "MICROSOFT_2023_10K.pdf",
    "NETFLIX_2022_10K.pdf",
    "NETFLIX_2023_10K.pdf",
    "NIKE_2022_10K.pdf",
    "NIKE_2023_10K.pdf",
    "TESLA_2022_10K.pdf",
    "TESLA_2023_10K.pdf",
    "WALMART_2022_10K.pdf",
    "WALMART_2023_10K.pdf",
]


def download_file(url: str, dest_path: Path):
    """Download a file with progress indicator."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp, open(dest_path, "wb") as f:
        total_size = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        chunk_size = 64 * 1024
        while True:
            chunk = resp.read(chunk_size)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)
            if total_size > 0:
                percent = downloaded * 100 / total_size
                sys.stdout.write(f"\r  -> Downloading {dest_path.name}: {percent:.1f}% ({downloaded / 1024 / 1024:.1f} MB)")
                sys.stdout.flush()
    print()


def main():
    parser = argparse.ArgumentParser(description="Download FinanceBench 10-K PDFs.")
    parser.add_argument("--all", action="store_true", help="Download all available FinanceBench PDFs.")
    parser.add_argument("--limit", type=int, default=10, help="Number of PDFs to download if not --all.")
    args = parser.parse_args()

    PDF_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Fetching file list from FinanceBench repository...")
    req = urllib.request.Request(FINANCEBENCH_API_URL, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req) as resp:
            files_data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"Failed to fetch file list via GitHub API: {e}")
        print("Falling back to downloading core sample list directly from raw URLs...")
        files_data = [
            {
                "name": name,
                "download_url": f"https://raw.githubusercontent.com/patronus-ai/financebench/main/pdfs/{name}",
            }
            for name in CORE_SAMPLE_FILES
        ]

    # Filter 10-K PDF files
    pdf_files = [f for f in files_data if f["name"].endswith(".pdf") and "_10K" in f["name"]]

    if not args.all:
        # Prioritize core sample files
        core_names = set(CORE_SAMPLE_FILES)
        selected = [f for f in pdf_files if f["name"] in core_names]
        if len(selected) < args.limit:
            selected += [f for f in pdf_files if f["name"] not in core_names][: args.limit - len(selected)]
        else:
            selected = selected[: args.limit]
    else:
        selected = pdf_files

    print(f"Selected {len(selected)} PDF files for download into '{PDF_DIR}/':")
    for idx, item in enumerate(selected, 1):
        target = PDF_DIR / item["name"]
        if target.exists() and target.stat().st_size > 10000:
            print(f"[{idx}/{len(selected)}] Already downloaded: {item['name']} ({target.stat().st_size / 1024 / 1024:.1f} MB)")
            continue

        url = item.get("download_url") or f"https://raw.githubusercontent.com/patronus-ai/financebench/main/pdfs/{item['name']}"
        print(f"[{idx}/{len(selected)}] Fetching: {item['name']}...")
        try:
            download_file(url, target)
        except Exception as err:
            print(f"  Error downloading {item['name']}: {err}")

    print(f"\nDone! PDFs are ready in: {PDF_DIR.resolve()}")


if __name__ == "__main__":
    main()
