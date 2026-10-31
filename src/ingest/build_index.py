"""
src/ingest/build_index.py
Offline Batch Index Builder CLI (Complies with RFC-PS5-2026-001 & execution_plan.md).

Parses all 10-K PDFs in data/pdfs/ into:
  - data/indices/chunks.sqlite (documents, document_pages, chunks tables)
  - data/indices/bm25.pkl (pickled BM25Okapi inverted index)
  - data/indices/faiss.index (FAISS dense vector index)

Exits cleanly after building to release 100% parser RAM before the web server starts.
"""
import os
import re
import sys
import pickle
import sqlite3
import logging
import argparse
from pathlib import Path
from typing import List, Tuple, Optional

import numpy as np
try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

try:
    import faiss
except ImportError:
    faiss = None

try:
    from rank_bm25 import BM25Okapi
except ImportError:
    BM25Okapi = None

from src.ingest.pdf_sanitizer import PDFSanitizer
from src.ingest.table_chunker import TableChunker
from src.retrieval.hybrid_rrf import finance_tokenize
from src.retrieval.onnx_reranker import ONNXEmbedder

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def parse_metadata_from_filename(filename: str) -> Tuple[str, str, int]:
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

    year = 2022
    for p in parts:
        if p.isdigit() and len(p) == 4:
            year = int(p)
            break

    return ticker, company_name, year


def init_sqlite_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        DROP TABLE IF EXISTS chunks;
        DROP TABLE IF EXISTS document_pages;
        DROP TABLE IF EXISTS documents;

        CREATE TABLE documents (
            doc_id TEXT PRIMARY KEY,
            pdf_filename TEXT NOT NULL UNIQUE,
            company_name TEXT,
            ticker TEXT,
            fiscal_year INTEGER,
            total_pdf_pages INTEGER
        );

        CREATE TABLE document_pages (
            page_id TEXT PRIMARY KEY,
            doc_id TEXT NOT NULL REFERENCES documents(doc_id),
            pdf_page_number INTEGER NOT NULL,
            normalized_text TEXT NOT NULL,
            UNIQUE(doc_id, pdf_page_number)
        );

        CREATE TABLE chunks (
            chunk_id TEXT PRIMARY KEY,
            doc_id TEXT NOT NULL REFERENCES documents(doc_id),
            page_id TEXT NOT NULL REFERENCES document_pages(page_id),
            pdf_filename TEXT NOT NULL,
            pdf_page_number INTEGER NOT NULL,
            printed_page_label TEXT,
            section_item TEXT,
            clean_markdown_text TEXT NOT NULL,
            token_count INTEGER,
            has_financial_table BOOLEAN,
            faiss_vector_id INTEGER
        );

        CREATE INDEX idx_chunks_doc ON chunks(doc_id);
        CREATE INDEX idx_chunks_page ON chunks(pdf_filename, pdf_page_number);
    """)
    conn.commit()


def build_indices(pdf_dir: str = "data/pdfs", output_dir: str = "data/indices") -> None:
    pdf_path = Path(pdf_dir)
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    db_path = out_path / "chunks.sqlite"
    bm25_path = out_path / "bm25.pkl"
    faiss_path = out_path / "faiss.index"

    pdf_files = sorted(list(pdf_path.glob("*.pdf")))
    if not pdf_files:
        logger.warning(f"No PDF files found in {pdf_path}.")
        return

    logger.info(f"Starting index build for {len(pdf_files)} PDFs...")
    sanitizer = PDFSanitizer()
    chunker = TableChunker()

    conn = sqlite3.connect(str(db_path))
    init_sqlite_schema(conn)

    all_chunks_text: List[str] = []
    chunk_counter = 0

    for doc_idx, pf in enumerate(pdf_files, start=1):
        doc_id = f"doc_{doc_idx:03d}"
        ticker, company, year = parse_metadata_from_filename(pf.name)
        chunker.reset_doc_state()

        if fitz is None:
            logger.error("PyMuPDF (fitz) is required for index building.")
            sys.exit(1)

        doc = fitz.open(str(pf))
        total_pages = len(doc)
        logger.info(f"[{doc_idx}/{len(pdf_files)}] Parsing: {pf.name} ({ticker}, FY{year}, {total_pages} pages)")

        conn.execute(
            "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?)",
            (doc_id, pf.name, company, ticker, year, total_pages)
        )

        for page_num_0 in range(total_pages):
            page_num_1 = page_num_0 + 1
            page = doc[page_num_0]

            clean_text = sanitizer.sanitize_page_text(page)
            page_id = f"{doc_id}_p{page_num_1:04d}"

            # Store full normalized page text for citation verification
            conn.execute(
                "INSERT OR REPLACE INTO document_pages VALUES (?, ?, ?, ?)",
                (page_id, doc_id, page_num_1, clean_text)
            )

            # Chunk page
            extracted = chunker.extract_tables_and_text(page, page_num_1, clean_text)

            for ext in extracted:
                chunk_id = f"chk_{chunk_counter:06d}"
                header_prefix = f"[{ticker} | {pf.name} | Page {page_num_1}]\n"
                full_chunk_text = header_prefix + ext.chunk_text
                tokens = finance_tokenize(full_chunk_text)

                conn.execute("""
                    INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    chunk_id,
                    doc_id,
                    page_id,
                    pf.name,
                    page_num_1,
                    str(page_num_1),
                    ext.section_item,
                    full_chunk_text,
                    len(tokens),
                    ext.has_financial_table,
                    chunk_counter,
                ))

                all_chunks_text.append(full_chunk_text)
                chunk_counter += 1

        doc.close()
        conn.commit()

    conn.commit()
    conn.close()
    logger.info(f"SQLite chunks database populated: {chunk_counter} chunks across {len(pdf_files)} documents.")
    logger.info(f"Sanitizer audit: dropped {sanitizer.dropped_spans_count} adversarial spans.")

    # ── Build BM25 Index ──
    logger.info("Building BM25Okapi lexical index...")
    if BM25Okapi is not None and all_chunks_text:
        tokenized_corpus = [finance_tokenize(t) for t in all_chunks_text]
        bm25 = BM25Okapi(tokenized_corpus, k1=1.5, b=0.75)
        with open(bm25_path, "wb") as f:
            pickle.dump(bm25, f)
        logger.info(f"BM25 index saved: {bm25_path}")

    # ── Build FAISS Index ──
    if faiss is not None and all_chunks_text:
        logger.info("Building FAISS index with embedder...")
        embedder = ONNXEmbedder()
        embedder.load()

        dim = 384
        index = faiss.IndexFlatIP(dim)
        batch_size = 64
        for i in range(0, len(all_chunks_text), batch_size):
            batch = all_chunks_text[i: i + batch_size]
            vecs = np.array([embedder.embed(t) for t in batch], dtype=np.float32)
            index.add(vecs)

        faiss.write_index(index, str(faiss_path))
        logger.info(f"FAISS index saved: {faiss_path} ({index.ntotal} vectors)")

    logger.info(f"Index build complete! All artifacts saved to: {out_path.resolve()}")


def main():
    parser = argparse.ArgumentParser(description="Offline Index Builder CLI for PS5 Honest Filing Analyst.")
    parser.add_argument("--pdf-dir", default="data/pdfs", help="Directory containing source 10-K PDFs")
    parser.add_argument("--output-dir", default="data/indices", help="Directory to save index files")
    args = parser.parse_args()

    build_indices(args.pdf_dir, args.output_dir)


if __name__ == "__main__":
    main()
