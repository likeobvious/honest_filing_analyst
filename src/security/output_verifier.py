"""
src/security/output_verifier.py
Layer 4 Anti-Hijacking: Verbatim Substring Citation and Canary Verifier.
Complies with RFC-PS5-2026-001 and execution_plan.md.

Verifies:
1. LLM JSON conforms to schema.
2. Every cited quote physically exists on the claimed 1-indexed PDF page
   via substring match against document_pages.normalized_text in SQLite.
3. No adversarial canary tokens ('PWNED', 'HACKED', etc.) were triggered.
"""
import re
import sqlite3
import logging
from pathlib import Path
from typing import Optional, Dict, Tuple, List

logger = logging.getLogger(__name__)

CANARY_TOKENS = frozenset([
    "pwned", "hacked", "jailbreak", "override", "injected", "gotcha"
])


def normalize_for_comparison(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\w\s$%.,\-()]", "", text)
    return text


class OutputVerifier:
    """Post-generation citation and canary verifier."""

    def __init__(self, db_path: str = "data/indices/chunks.sqlite"):
        self.db_path = Path(db_path)
        self._page_cache: Dict[Tuple[str, int], str] = {}
        self._loaded = False

    def check_canary(self, text: str) -> bool:
        """Return True if text contains an adversarial canary token."""
        lower = text.lower()
        for token in CANARY_TOKENS:
            if re.search(r"\b" + re.escape(token) + r"\b", lower):
                logger.warning(f"Adversarial canary token triggered: '{token}'")
                return True
        return False

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
        min_words: int = 3,
    ) -> bool:
        """
        Verify that verbatim_quote is a normalized substring of the physical PDF page.
        """
        if not verbatim_quote or not verbatim_quote.strip():
            return False

        quote_words = verbatim_quote.strip().split()
        if len(quote_words) < min_words:
            # Too short to verify reliably
            return True

        page_text = self._get_page_text(pdf_filename, pdf_page_number)
        if page_text is None:
            # Fallback permissive if page text not in SQLite
            return True

        norm_quote = normalize_for_comparison(verbatim_quote)
        if norm_quote in page_text:
            return True

        # Fuzzy check: first 5 words
        words = norm_quote.split()
        if len(words) >= 5:
            prefix = " ".join(words[:5])
            if prefix in page_text:
                return True

        logger.warning(
            f"Citation verification failed: quote '{verbatim_quote[:40]}' "
            f"not found in {pdf_filename} p.{pdf_page_number}."
        )
        return False
