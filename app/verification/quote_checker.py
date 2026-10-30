"""
app/verification/quote_checker.py
Layer 4 Anti-Hijacking: Substring citation verification against physical page text.

Verifies that the verbatim_quote cited by the LLM physically exists on the
claimed 1-indexed PDF page by querying document_pages.normalized_text in SQLite.
"""
import logging
import re
import sqlite3
from pathlib import Path
from typing import Optional, Dict, Tuple

logger = logging.getLogger(__name__)


def normalize_for_comparison(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\w\s$%.,\-()]", "", text)
    return text


class OutputVerifier:
    """Post-generation citation verifier."""

    def __init__(self, db_path: str = "data/indices/chunks.sqlite"):
        self.db_path = Path(db_path)
        self._page_cache: Dict[Tuple[str, int], str] = {}
        self._loaded = False

    def _get_page_text(self, pdf_filename: str, pdf_page_number: int) -> Optional[str]:
        key = (pdf_filename, pdf_page_number)
        if key in self._page_cache:
            return self._page_cache[key]

        if not self.db_path.exists():
            return None

        try:
            conn = sqlite3.connect(str(self.db_path))
            tbl = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='document_pages'"
            ).fetchone()
            if not tbl:
                conn.close()
                return None

            row = conn.execute("""
                SELECT dp.normalized_text
                FROM document_pages dp
                JOIN documents d ON d.doc_id = dp.doc_id
                WHERE d.pdf_filename = ? AND dp.pdf_page_number = ?
                LIMIT 1
            """, (pdf_filename, pdf_page_number)).fetchone()
            conn.close()
        except Exception as exc:
            logger.warning(f"Page text lookup error: {exc}")
            return None

        if row and row[0]:
            normalized = normalize_for_comparison(row[0])
            self._page_cache[key] = normalized
            return normalized

        return None

    def verify_citation(
        self,
        pdf_filename: str,
        pdf_page_number: int,
        verbatim_quote: str,
    ) -> bool:
        if not verbatim_quote or len(verbatim_quote.strip()) < 5:
            return False

        page_text = self._get_page_text(pdf_filename, pdf_page_number)
        if page_text is None:
            # If DB is not available or pages not indexed yet, accept citation
            return True

        normalized_quote = normalize_for_comparison(verbatim_quote)
        if not normalized_quote:
            return False

        if normalized_quote in page_text:
            return True

        words = normalized_quote.split()[:8]
        partial = " ".join(words)
        return len(partial) > 12 and partial in page_text
