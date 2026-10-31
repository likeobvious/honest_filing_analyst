"""
app/rag/query_parser.py
Query Parser: Zero-token entity and fiscal-year extractor for financial queries.

Scans user queries against the corpus manifest (SQLite `documents` table) to extract:
- Company tickers (AAPL, MSFT, AMZN, etc.)
- Company names (Amazon, Apple, Microsoft, etc.)
- Fiscal years (4-digit years between 2000-2030)

If a query references a company completely absent from the corpus manifest,
the orchestrator short-circuits immediately to "Not in the documents" without
consuming any LLM tokens.
"""
import re
import sqlite3
import logging
from pathlib import Path
from typing import Optional, List, Dict, Set
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

FISCAL_YEAR_PATTERN = re.compile(
    r"\b(FY|fiscal year|year ended|year ending|for (?:the )?(?:year|period))[\s,]*"
    r"(20(?:0[0-9]|1[0-9]|2[0-9]|3[0-0]))\b",
    re.IGNORECASE
)
BARE_YEAR_PATTERN = re.compile(r"\b(20(?:0[0-9]|1[0-9]|2[0-9]|3[0]))\b")

COMMON_FINANCIAL_WORDS = frozenset([
    "WHAT", "WHEN", "WHERE", "WHICH", "WHO", "WHY", "HOW", "THE", "FOR",
    "AND", "CAN", "ARE", "WAS", "WERE", "WITH", "FROM", "THAT", "THIS",
    "TOTAL", "GROSS", "NET", "GAAP", "NON-GAAP", "SEC", "USD", "RATE"
])

KNOWN_EXTERNAL_COMPANIES: Dict[str, str] = {
    "tesla": "TSLA", "tsla": "TSLA",
    "nvidia": "NVDA", "nvda": "NVDA",
    "google": "GOOGL", "googl": "GOOGL", "alphabet": "GOOGL",
    "meta": "META", "facebook": "META",
    "oracle": "ORCL", "orcl": "ORCL",
    "intel": "INTC", "intc": "INTC",
    "jpmorgan": "JPM", "jpm": "JPM",
    "ford": "F", "general motors": "GM",
    "berkshire": "BRK",
}

QUANTITATIVE_KEYWORDS = frozenset([
    "margin", "change", "growth", "ratio", "compare", "comparison",
    "year-over-year", "yoy", "increase", "decrease", "percentage", "%",
    "how much", "what is the difference", "delta", "basis points", "bps",
    "revenue growth", "operating leverage", "return on", "cagr",
])


@dataclass
class ParsedQuery:
    original_question: str
    normalized_question: str
    tickers: List[str] = field(default_factory=list)
    company_names: List[str] = field(default_factory=list)
    fiscal_years: List[int] = field(default_factory=list)
    matched_doc_ids: List[str] = field(default_factory=list)
    matched_filenames: List[str] = field(default_factory=list)
    company_not_in_corpus: bool = False
    year_not_in_corpus: bool = False
    is_quantitative: bool = False
    is_multi_year: bool = False


class QueryParser:
    """
    Deterministic, zero-LLM-call entity extractor and corpus manifest checker.
    """

    def __init__(self, db_path: str = "data/indices/chunks.sqlite"):
        self.db_path = Path(db_path)
        self._ticker_to_docids: Dict[str, List[str]] = {}
        self._name_to_ticker: Dict[str, str] = {}
        self._year_to_docids: Dict[int, List[str]] = {}
        self._all_doc_ids: Set[str] = set()
        self._docid_to_filename: Dict[str, str] = {}
        self._loaded = False

    def load(self) -> None:
        """Load corpus manifest from SQLite into in-memory dictionaries."""
        if not self.db_path.exists():
            logger.warning(f"SQLite DB not found at {self.db_path}. QueryParser in empty mode.")
            self._loaded = True
            return

        conn = sqlite3.connect(str(self.db_path))
        try:
            tbl = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='documents'"
            ).fetchone()
            if not tbl:
                logger.warning("documents table not found in SQLite DB.")
                self._loaded = True
                return

            rows = conn.execute(
                "SELECT doc_id, pdf_filename, company_name, ticker, fiscal_year FROM documents"
            ).fetchall()
        finally:
            conn.close()

        for doc_id, pdf_filename, company_name, ticker, fiscal_year in rows:
            self._all_doc_ids.add(doc_id)
            self._docid_to_filename[doc_id] = pdf_filename

            if ticker:
                t = ticker.upper().strip()
                self._ticker_to_docids.setdefault(t, []).append(doc_id)

                if company_name:
                    for alias in self._build_name_aliases(company_name):
                        self._name_to_ticker[alias] = t

            if fiscal_year:
                try:
                    self._year_to_docids.setdefault(int(fiscal_year), []).append(doc_id)
                except ValueError:
                    pass

        logger.info(
            f"QueryParser loaded {len(rows)} documents, "
            f"{len(self._ticker_to_docids)} tickers, "
            f"{len(self._year_to_docids)} fiscal years."
        )
        self._loaded = True

    @staticmethod
    def _build_name_aliases(company_name: str) -> List[str]:
        name = company_name.strip()
        aliases = [name.lower()]
        for suffix in [", Inc.", " Inc.", " Corp.", " Corporation", " Ltd.", " LLC", ".com, Inc."]:
            if name.endswith(suffix):
                short = name[: -len(suffix)].strip()
                aliases.append(short.lower())
        first_word = name.split()[0].lower().rstrip(".,")
        if first_word not in aliases:
            aliases.append(first_word)
        return aliases

    def _normalize(self, text: str) -> str:
        text = text.lower().strip()
        text = re.sub(r"\s+", " ", text)
        SYNONYM_MAP = {
            "amazon": "amzn",
            "apple": "aapl",
            "microsoft": "msft",
            "alphabet": "googl",
            "google": "googl",
            "meta": "meta",
            "facebook": "meta",
            "tesla": "tsla",
            "nvidia": "nvda",
            "jpmorgan": "jpm",
            "jp morgan": "jpm",
            "3m": "mmm",
        }
        for name, ticker in SYNONYM_MAP.items():
            text = text.replace(name, ticker)
        return text

    def parse(
        self,
        question: str,
        ticker_hint: Optional[str] = None,
        fiscal_year_hint: Optional[int] = None
    ) -> ParsedQuery:
        """Parse natural-language financial question into structured entities."""
        if not self._loaded:
            self.load()

        normalized = self._normalize(question)
        result = ParsedQuery(
            original_question=question,
            normalized_question=normalized,
        )

        # ── 1. Extract Fiscal Years ──
        years: Set[int] = set()
        if fiscal_year_hint:
            years.add(fiscal_year_hint)

        for m in FISCAL_YEAR_PATTERN.finditer(question):
            years.add(int(m.group(2)))
        if not years:
            for m in BARE_YEAR_PATTERN.finditer(question):
                y = int(m.group(1))
                if 2000 <= y <= 2030:
                    years.add(y)

        result.fiscal_years = sorted(years)
        result.is_multi_year = len(result.fiscal_years) >= 2

        # ── 2. Extract Tickers / Company Names ──
        tickers: Set[str] = set()
        q_lower = question.lower()

        if ticker_hint:
            tickers.add(ticker_hint.upper())

        # Check uppercase ticker symbols in original question
        for token in re.findall(r"\b[A-Z]{2,5}\b", question):
            if token not in COMMON_FINANCIAL_WORDS:
                tickers.add(token)

        # Check known corpus company names / aliases
        for alias, ticker in self._name_to_ticker.items():
            if re.search(r"\b" + re.escape(alias) + r"\b", q_lower):
                tickers.add(ticker.upper())

        # Check corpus tickers (e.g. 'msft', 'amzn') in lowercase
        for t in self._ticker_to_docids:
            if re.search(r"\b" + re.escape(t.lower()) + r"\b", q_lower):
                tickers.add(t)

        # Check external companies known not to be in corpus
        for ext_name, ext_ticker in KNOWN_EXTERNAL_COMPANIES.items():
            if re.search(r"\b" + re.escape(ext_name) + r"\b", q_lower):
                tickers.add(ext_ticker.upper())

        result.tickers = sorted(tickers)

        # ── 3. Match Corpus Manifest ──
        matched_doc_ids: Set[str] = set()

        if self._all_doc_ids:
            if tickers:
                for t in tickers:
                    doc_ids = self._ticker_to_docids.get(t, [])
                    matched_doc_ids.update(doc_ids)

                if not matched_doc_ids:
                    # Requested company is known to be NOT in corpus
                    result.company_not_in_corpus = True
                    return result

                if result.fiscal_years:
                    year_filtered: Set[str] = set()
                    for y in result.fiscal_years:
                        year_doc_ids = set(self._year_to_docids.get(y, []))
                        year_filtered.update(matched_doc_ids & year_doc_ids)

                    if not year_filtered:
                        result.year_not_in_corpus = True
                    else:
                        matched_doc_ids = year_filtered
            else:
                if result.fiscal_years:
                    for y in result.fiscal_years:
                        matched_doc_ids.update(self._year_to_docids.get(y, []))
                else:
                    matched_doc_ids = set(self._all_doc_ids)

        result.matched_doc_ids = sorted(matched_doc_ids)
        result.matched_filenames = [
            self._docid_to_filename[d] for d in result.matched_doc_ids if d in self._docid_to_filename
        ]
        result.is_quantitative = any(kw in normalized for kw in QUANTITATIVE_KEYWORDS)

        return result
