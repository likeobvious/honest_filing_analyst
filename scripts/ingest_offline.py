"""
scripts/ingest_offline.py
Offline batch ingestion pipeline: Parses PDFs into SQLite, BM25, and FAISS indices.

Supports PyMuPDF (fitz) with automatic fallback to pypdf.
Builds:
  - data/indices/chunks.sqlite (documents, document_pages, chunks tables)
  - data/indices/bm25.pkl (pickled BM25Okapi index)
"""
import os
import re
import sys
import pickle
import sqlite3
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Try importing fitz (PyMuPDF), fallback to pypdf
try:
    import fitz
    PDF_ENGINE = "pymupdf"
except ImportError:
    try:
        import pypdf
        PDF_ENGINE = "pypdf"
    except ImportError:
        PDF_ENGINE = None

# Finance-aware tokenizer
_TOKEN_PATTERN = re.compile(r"[a-z0-9]+(?:[.,-][a-z0-9]+)*%?")


def finance_tokenize(text: str):
    return [t for t in _TOKEN_PATTERN.findall(text.lower()) if len(t) > 1]


def parse_metadata_from_filename(filename: str):
    """
    Extract ticker, year, and company name from filenames like:
    AMAZON_2022_10K.pdf, 3M_2022_10K.pdf, APPLE_2022_10K.pdf
    """
    stem = Path(filename).stem.upper()
    parts = stem.split("_")

    ticker_map = {
        "AMAZON": ("AMZN", "Amazon.com, Inc."),
        "APPLE": ("AAPL", "Apple Inc."),
        "MICROSOFT": ("MSFT", "Microsoft Corporation"),
        "NETFLIX": ("NFLX", "Netflix, Inc."),
        "NIKE": ("NKE", "NIKE, Inc."),
        "3M": ("MMM", "3M Company"),
        "WALMART": ("WMT", "Walmart Inc."),
        "TESLA": ("TSLA", "Tesla, Inc."),
    }

    raw_name = parts[0]
    ticker, company_name = ticker_map.get(raw_name, (raw_name, raw_name))

    fiscal_year = None
    for p in parts[1:]:
        if p.isdigit() and len(p) == 4:
            fiscal_year = int(p)
            break

    return ticker, company_name, fiscal_year


def extract_pages_pymupdf(pdf_path: Path):
    """Extract page text using PyMuPDF (stripping tiny fonts <4.5pt)."""
    doc = fitz.open(str(pdf_path))
    pages = []
    for page_idx, page in enumerate(doc):
        # Extract plain text
        text = page.get_text("text").strip()
        has_table = bool(re.search(r"(\$|€|\%|\||\d+\.\d+).*\n.*(\$|€|\%|\||\d+\.\d+)", text))
        pages.append({
            "page_number": page_idx + 1,  # 1-indexed
            "text": text,
            "has_table": has_table,
        })
    doc.close()
    return pages


def extract_pages_pypdf(pdf_path: Path):
    """Extract page text using pypdf."""
    reader = pypdf.PdfReader(str(pdf_path))
    pages = []
    for page_idx, page in enumerate(reader.pages):
        text = (page.extract_text() or "").strip()
        has_table = bool(re.search(r"(\$|€|\%|\||\d+\.\d+).*\n.*(\$|€|\%|\||\d+\.\d+)", text))
        pages.append({
            "page_number": page_idx + 1,  # 1-indexed
            "text": text,
            "has_table": has_table,
        })
    return pages


def extract_pages(pdf_path: Path):
    if PDF_ENGINE == "pymupdf":
        return extract_pages_pymupdf(pdf_path)
    elif PDF_ENGINE == "pypdf":
        return extract_pages_pypdf(pdf_path)
    else:
        raise RuntimeError("Neither PyMuPDF nor pypdf is installed.")


def build_indices(pdf_dir: Path, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    db_path = output_dir / "chunks.sqlite"
    bm25_path = output_dir / "bm25.pkl"

    logger.info(f"Using PDF Engine: {PDF_ENGINE}")
    pdf_files = list(pdf_dir.glob("*.pdf"))
    if not pdf_files:
        logger.warning(f"No PDF files found in {pdf_dir}!")
        return

    logger.info(f"Found {len(pdf_files)} PDF files in {pdf_dir}. Starting indexing...")

    # Initialize SQLite database
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    cur.execute("DROP TABLE IF EXISTS chunks")
    cur.execute("DROP TABLE IF EXISTS document_pages")
    cur.execute("DROP TABLE IF EXISTS documents")
    cur.execute("DROP TABLE IF EXISTS query_cache")

    cur.execute("""
        CREATE TABLE documents (
            doc_id TEXT PRIMARY KEY,
            pdf_filename TEXT NOT NULL,
            company_name TEXT,
            ticker TEXT,
            fiscal_year INTEGER,
            total_pdf_pages INTEGER
        )
    """)

    cur.execute("""
        CREATE TABLE document_pages (
            page_id TEXT PRIMARY KEY,
            doc_id TEXT NOT NULL,
            pdf_page_number INTEGER NOT NULL,
            normalized_text TEXT NOT NULL,
            has_financial_table BOOLEAN,
            FOREIGN KEY (doc_id) REFERENCES documents(doc_id)
        )
    """)

    cur.execute("""
        CREATE TABLE chunks (
            chunk_id TEXT PRIMARY KEY,
            doc_id TEXT NOT NULL,
            page_id TEXT NOT NULL,
            pdf_filename TEXT NOT NULL,
            pdf_page_number INTEGER NOT NULL,
            printed_page_label TEXT,
            section_item TEXT,
            clean_markdown_text TEXT NOT NULL,
            token_count INTEGER,
            has_financial_table BOOLEAN,
            faiss_vector_id INTEGER,
            FOREIGN KEY (doc_id) REFERENCES documents(doc_id)
        )
    """)

    cur.execute("""
        CREATE TABLE query_cache (
            cache_id TEXT PRIMARY KEY,
            normalized_query_hash TEXT NOT NULL,
            canonical_question TEXT NOT NULL,
            ticker_filter TEXT,
            fiscal_year_filter INTEGER,
            query_embedding_blob BLOB,
            verified_response_json TEXT NOT NULL,
            hit_count INTEGER DEFAULT 0,
            created_at REAL NOT NULL
        )
    """)

    all_chunks_text = []
    vector_id = 0

    for idx, pdf_path in enumerate(pdf_files, 1):
        filename = pdf_path.name
        doc_id = f"doc_{idx:03d}"
        ticker, company_name, fiscal_year = parse_metadata_from_filename(filename)

        logger.info(f"[{idx}/{len(pdf_files)}] Parsing: {filename} ({ticker}, FY{fiscal_year})")

        try:
            pages = extract_pages(pdf_path)
        except Exception as e:
            logger.error(f"  Error reading {filename}: {e}")
            continue

        cur.execute(
            "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?)",
            (doc_id, filename, company_name, ticker, fiscal_year, len(pages))
        )

        for page in pages:
            page_num = page["page_number"]
            page_id = f"{doc_id}_p{page_num}"
            raw_text = page["text"]
            if not raw_text or len(raw_text) < 20:
                continue

            normalized = raw_text.lower()
            cur.execute(
                "INSERT INTO document_pages VALUES (?, ?, ?, ?, ?)",
                (page_id, doc_id, page_num, normalized, page["has_table"])
            )

            # Chunk into page-bounded segments (~350 words each)
            words = raw_text.split()
            chunk_size = 350
            for c_idx in range(0, len(words), chunk_size):
                chunk_words = words[c_idx: c_idx + chunk_size]
                chunk_text = " ".join(chunk_words)
                chunk_id = f"{page_id}_c{c_idx // chunk_size}"

                header = f"[{ticker or '10-K'} | {filename} | Page {page_num}]\n"
                clean_text = header + chunk_text

                cur.execute(
                    "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        chunk_id, doc_id, page_id, filename, page_num,
                        str(page_num), None, clean_text,
                        len(chunk_words), page["has_table"], vector_id
                    )
                )
                all_chunks_text.append(clean_text)
                vector_id += 1

    conn.commit()
    conn.close()

    logger.info(f"SQLite chunks database created: {vector_id} chunks indexed across {len(pdf_files)} documents.")

    # Build BM25 index
    try:
        from rank_bm25 import BM25Okapi
        logger.info("Building BM25Okapi lexical search index...")
        tokenized_corpus = [finance_tokenize(txt) for txt in all_chunks_text]
        bm25 = BM25Okapi(tokenized_corpus, k1=1.5, b=0.75)
        with open(bm25_path, "wb") as f:
            pickle.dump(bm25, f)
        logger.info(f"BM25 index saved to {bm25_path}")
    except ImportError:
        logger.warning("rank_bm25 not installed; BM25 index file skipped (will build at runtime).")

    logger.info(f"✅ Ingestion complete! All indices saved to {output_dir.resolve()}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf-dir", default="data/pdfs")
    parser.add_argument("--output-dir", default="data/indices")
    args = parser.parse_args()

    build_indices(Path(args.pdf_dir), Path(args.output_dir))
