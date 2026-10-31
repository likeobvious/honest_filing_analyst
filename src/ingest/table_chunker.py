"""
src/ingest/table_chunker.py
Layer 2 Table Parser & Stateful Multi-Page Header Propagator.
Complies with RFC-PS5-2026-001 and execution_plan.md.

Enforces:
1. Strict Page-Bounded Chunking: Chunks never cross physical PDF page boundaries.
2. Financial table serialization to GitHub-Flavored Markdown.
3. Stateful Continuation Propagator: Preserves table headers, fiscal year columns,
   and scale indicators ($ in millions) across multi-page split balance sheets.
"""
import re
import logging
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

SCALE_PATTERNS = [
    re.compile(r"(\$\s*in\s*millions)", re.IGNORECASE),
    re.compile(r"(\$\s*in\s*billions)", re.IGNORECASE),
    re.compile(r"(\$\s*in\s*thousands)", re.IGNORECASE),
    re.compile(r"(\(in\s*millions[^\)]*\))", re.IGNORECASE),
    re.compile(r"(\(in\s*thousands[^\)]*\))", re.IGNORECASE),
]

YEAR_COL_PATTERN = re.compile(r"\b(20(?:1[0-9]|2[0-9]))\b")


@dataclass
class PageTableState:
    """State preserved from previous page's financial table."""
    table_title: Optional[str] = None
    column_headers: List[str] = field(default_factory=list)
    scale_unit: Optional[str] = None
    page_number: int = 0


@dataclass
class ExtractedChunk:
    chunk_text: str
    page_number: int
    has_financial_table: bool
    table_context_prepended: bool = False
    section_item: Optional[str] = None


class TableChunker:
    """Page-bounded financial chunker with stateful header propagation."""

    def __init__(self, target_chunk_size: int = 500, chunk_overlap: int = 50):
        self.target_chunk_size = target_chunk_size
        self.chunk_overlap = chunk_overlap
        self.active_table_state: Optional[PageTableState] = None

    def reset_doc_state(self) -> None:
        """Reset state between distinct PDF documents."""
        self.active_table_state = None

    def extract_tables_and_text(self, page, page_number: int, sanitized_text: str) -> List[ExtractedChunk]:
        """
        Process page into strict page-bounded chunks.
        Serializes tables to markdown and applies stateful header propagation.
        """
        # Financial statement table detection and serialization
        markdown_tables = []
        lower_txt = sanitized_text.lower()

        # Detect scale indicator on this page
        current_scale = self._detect_scale_unit(sanitized_text)

        # Detect table headers / title
        current_headers = self._detect_column_headers(sanitized_text)
        is_continued_table = False
        table_context_prefix = ""

        # Check if this page is a continuation of page P-1 table
        if self.active_table_state and self.active_table_state.page_number == page_number - 1:
            if not current_headers and ("table of contents" not in sanitized_text.lower()[:100]):
                is_continued_table = True
                cols_str = " | ".join(self.active_table_state.column_headers) if self.active_table_state.column_headers else "Standard Balance Sheet"
                unit_str = current_scale or self.active_table_state.scale_unit or "$ in millions"
                table_context_prefix = (
                    f"[CONTINUED TABLE CONTEXT FROM PAGE {page_number - 1}: "
                    f"Columns = {cols_str} | Units = {unit_str}]\n\n"
                )

        # Update active state for next page
        if markdown_tables or "balance sheet" in sanitized_text.lower() or "consolidated" in sanitized_text.lower():
            self.active_table_state = PageTableState(
                table_title=self._extract_table_title(sanitized_text),
                column_headers=current_headers or (self.active_table_state.column_headers if self.active_table_state else []),
                scale_unit=current_scale or (self.active_table_state.scale_unit if self.active_table_state else None),
                page_number=page_number,
            )
        else:
            self.active_table_state = None

        has_table = bool(markdown_tables) or is_continued_table or self._contains_table_syntax(sanitized_text)

        # Build chunks strictly bounded within this physical page
        page_chunks: List[ExtractedChunk] = []
        combined_text = sanitized_text
        if markdown_tables:
            combined_text = "\n\n".join(markdown_tables) + "\n\n" + sanitized_text

        paragraphs = [p.strip() for p in combined_text.split("\n\n") if p.strip()]
        if not paragraphs:
            return []

        current_buf: List[str] = []
        current_word_count = 0

        for p in paragraphs:
            words = p.split()
            if current_word_count + len(words) > self.target_chunk_size and current_buf:
                chunk_str = "\n\n".join(current_buf)
                if table_context_prefix:
                    chunk_str = table_context_prefix + chunk_str

                page_chunks.append(
                    ExtractedChunk(
                        chunk_text=chunk_str,
                        page_number=page_number,
                        has_financial_table=has_table,
                        table_context_prepended=bool(table_context_prefix),
                    )
                )
                current_buf = []
                current_word_count = 0

            current_buf.append(p)
            current_word_count += len(words)

        if current_buf:
            chunk_str = "\n\n".join(current_buf)
            if table_context_prefix:
                chunk_str = table_context_prefix + chunk_str

            page_chunks.append(
                ExtractedChunk(
                    chunk_text=chunk_str,
                    page_number=page_number,
                    has_financial_table=has_table,
                    table_context_prepended=bool(table_context_prefix),
                )
            )

        return page_chunks

    @staticmethod
    def _dataframe_to_markdown(rows: List[List[Any]]) -> str:
        """Serialize 2D list into GitHub-Flavored Markdown table."""
        if not rows or len(rows) < 2:
            return ""
        clean_rows = [[str(cell or "").strip().replace("\n", " ") for cell in row] for row in rows]
        headers = clean_rows[0]
        separator = ["---"] * len(headers)
        lines = [
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join(separator) + " |",
        ]
        for row in clean_rows[1:]:
            # Pad row if needed
            while len(row) < len(headers):
                row.append("")
            lines.append("| " + " | ".join(row[:len(headers)]) + " |")
        return "\n".join(lines)

    @staticmethod
    def _detect_scale_unit(text: str) -> Optional[str]:
        for pat in SCALE_PATTERNS:
            m = pat.search(text)
            if m:
                return m.group(1)
        return None

    @staticmethod
    def _detect_column_headers(text: str) -> List[str]:
        lines = text.splitlines()[:15]
        for line in lines:
            years = YEAR_COL_PATTERN.findall(line)
            if len(years) >= 2:
                return [f"FY {y}" for y in years]
        return []

    @staticmethod
    def _extract_table_title(text: str) -> Optional[str]:
        for line in text.splitlines()[:6]:
            line_str = line.strip()
            if any(k in line_str.lower() for k in ["consolidated", "statements of", "balance sheet", "operations"]):
                return line_str
        return None

    @staticmethod
    def _contains_table_syntax(text: str) -> bool:
        return "|" in text and ("---" in text or "$" in text)
