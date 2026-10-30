"""
app/rag/hybrid_search.py
Hybrid Retrieval Engine: BM25 + FAISS Reciprocal Rank Fusion (RRF).

Combines lexical (BM25) and semantic (dense vector) search results using
Reciprocal Rank Fusion (k=60) to produce Top-12 candidate chunks for
downstream cross-encoder reranking.
"""
import pickle
import sqlite3
import logging
import re
from pathlib import Path
from typing import List, Optional, Dict, Tuple, Set
from dataclasses import dataclass

import numpy as np
try:
    import faiss
except ImportError:
    faiss = None

try:
    from rank_bm25 import BM25Okapi
except ImportError:
    BM25Okapi = None

from app.schemas import PassageResult

logger = logging.getLogger(__name__)

# Finance-aware tokenizer
_TOKEN_PATTERN = re.compile(
    r"[a-z0-9]+(?:[.,-][a-z0-9]+)*%?"
)


def finance_tokenize(text: str) -> List[str]:
    """Tokenize text preserving hyphenated terms and financial numeric strings."""
    tokens = _TOKEN_PATTERN.findall(text.lower())
    return [t for t in tokens if len(t) > 1]


@dataclass
class ChunkMeta:
    chunk_id: str
    doc_id: str
    pdf_filename: str
    pdf_page_number: int
    printed_page_label: Optional[str]
    section_item: Optional[str]
    clean_markdown_text: str
    token_count: int
    has_financial_table: bool
    faiss_vector_id: int


class HybridRRFSearchEngine:
    """
    Hybrid BM25 + FAISS search engine with Reciprocal Rank Fusion.
    """
    BM25_TOP_K = 30
    FAISS_TOP_K = 30
    RRF_K = 60
    DEFAULT_TOP_K = 12
    TABLE_BOOST = 1.15

    def __init__(self, index_dir: str = "data/indices"):
        self.index_dir = Path(index_dir)
        self._chunks: List[ChunkMeta] = []
        self._chunk_id_to_idx: Dict[str, int] = {}
        self._bm25 = None
        self._faiss_index = None
        self._loaded = False

    def load(self) -> None:
        """Load BM25 index, FAISS index, and chunk metadata from disk."""
        db_path = self.index_dir / "chunks.sqlite"
        bm25_path = self.index_dir / "bm25.pkl"
        faiss_path = self.index_dir / "faiss.index"

        if not db_path.exists():
            logger.warning(f"chunks.sqlite not found at {db_path}. Search will return empty results.")
            self._loaded = True
            return

        conn = sqlite3.connect(str(db_path))
        try:
            # Check if chunks table exists
            tbl_check = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='chunks'"
            ).fetchone()
            if not tbl_check:
                logger.warning("chunks table does not exist yet in chunks.sqlite.")
                self._loaded = True
                return

            rows = conn.execute("""
                SELECT chunk_id, doc_id, pdf_filename, pdf_page_number,
                       printed_page_label, section_item, clean_markdown_text,
                       token_count, has_financial_table, faiss_vector_id
                FROM chunks
                ORDER BY faiss_vector_id
            """).fetchall()
        finally:
            conn.close()

        self._chunks = [
            ChunkMeta(
                chunk_id=r[0],
                doc_id=r[1],
                pdf_filename=r[2],
                pdf_page_number=r[3],
                printed_page_label=r[4],
                section_item=r[5],
                clean_markdown_text=r[6],
                token_count=r[7],
                has_financial_table=bool(r[8]),
                faiss_vector_id=r[9] if r[9] is not None else idx,
            )
            for idx, r in enumerate(rows)
        ]
        self._chunk_id_to_idx = {c.chunk_id: i for i, c in enumerate(self._chunks)}
        logger.info(f"Loaded {len(self._chunks)} chunks from SQLite.")

        # Load BM25
        if bm25_path.exists():
            try:
                with open(bm25_path, "rb") as f:
                    self._bm25 = pickle.load(f)
                logger.info("BM25 index loaded from disk.")
            except Exception as e:
                logger.warning(f"Failed to load bm25.pkl ({e}). Rebuilding.")
                self._rebuild_bm25()
        elif self._chunks and BM25Okapi is not None:
            self._rebuild_bm25()

        # Load FAISS
        if faiss is not None and faiss_path.exists():
            try:
                self._faiss_index = faiss.read_index(str(faiss_path))
                logger.info(f"FAISS index loaded: {self._faiss_index.ntotal} vectors.")
            except Exception as e:
                logger.warning(f"Failed to load FAISS index ({e}).")

        self._loaded = True

    def _rebuild_bm25(self) -> None:
        if BM25Okapi is not None and self._chunks:
            tokenized = [finance_tokenize(c.clean_markdown_text) for c in self._chunks]
            self._bm25 = BM25Okapi(tokenized, k1=1.5, b=0.75)
            logger.info("BM25 index built in memory.")

    def search(
        self,
        query_vector: Optional[np.ndarray],
        query_text: str,
        top_k: int = DEFAULT_TOP_K,
        doc_id_filter: Optional[List[str]] = None,
        is_quantitative: bool = False,
    ) -> List[PassageResult]:
        """
        Perform hybrid BM25 + FAISS search with RRF fusion.
        """
        if not self._loaded:
            self.load()

        if not self._chunks:
            return []

        if doc_id_filter:
            filter_set: Set[str] = set(doc_id_filter)
            valid_indices = [i for i, c in enumerate(self._chunks) if c.doc_id in filter_set]
        else:
            valid_indices = list(range(len(self._chunks)))

        if not valid_indices:
            return []

        valid_set = set(valid_indices)

        # ── BM25 ──
        bm25_ranks: Dict[int, int] = {}
        if self._bm25 is not None:
            query_tokens = finance_tokenize(query_text)
            if query_tokens:
                scores = self._bm25.get_scores(query_tokens)
                filtered_scores = [(i, scores[i]) for i in valid_indices if scores[i] > 0]
                filtered_scores.sort(key=lambda x: x[1], reverse=True)
                for rank, (idx, _) in enumerate(filtered_scores[: self.BM25_TOP_K]):
                    bm25_ranks[idx] = rank + 1

        # ── FAISS ──
        faiss_ranks: Dict[int, int] = {}
        if self._faiss_index is not None and query_vector is not None:
            vec = np.ascontiguousarray([query_vector], dtype=np.float32)
            k_search = min(self.FAISS_TOP_K * 3, self._faiss_index.ntotal)
            if k_search > 0:
                _, faiss_indices = self._faiss_index.search(vec, k_search)
                rank = 0
                for vid in faiss_indices[0]:
                    if vid < 0 or vid >= len(self._chunks):
                        continue
                    if vid not in valid_set:
                        continue
                    if rank >= self.FAISS_TOP_K:
                        break
                    faiss_ranks[int(vid)] = rank + 1
                    rank += 1

        # ── RRF Fusion ──
        candidate_indices: Set[int] = set(bm25_ranks.keys()) | set(faiss_ranks.keys())
        if not candidate_indices:
            # Fallback: return first few valid chunks if no lexical or dense match
            candidate_indices = set(valid_indices[:top_k])

        rrf_scores: Dict[int, float] = {}
        for idx in candidate_indices:
            score = 0.0
            if idx in bm25_ranks:
                score += 1.0 / (self.RRF_K + bm25_ranks[idx])
            if idx in faiss_ranks:
                score += 1.0 / (self.RRF_K + faiss_ranks[idx])
            if not score:
                score = 0.001

            if is_quantitative and self._chunks[idx].has_financial_table:
                score *= self.TABLE_BOOST

            rrf_scores[idx] = score

        ranked = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)

        results: List[PassageResult] = []
        seen_chunk_ids: Set[str] = set()
        for idx, rrf_score in ranked[:top_k]:
            c = self._chunks[idx]
            if c.chunk_id in seen_chunk_ids:
                continue
            seen_chunk_ids.add(c.chunk_id)
            results.append(
                PassageResult(
                    chunk_id=c.chunk_id,
                    doc_id=c.doc_id,
                    pdf_filename=c.pdf_filename,
                    pdf_page_number=c.pdf_page_number,
                    printed_page_label=c.printed_page_label,
                    section_item=c.section_item,
                    clean_markdown_text=c.clean_markdown_text,
                    reranker_score=0.0,
                    bm25_rank=bm25_ranks.get(idx),
                    faiss_rank=faiss_ranks.get(idx),
                    rrf_score=rrf_score,
                    has_financial_table=c.has_financial_table,
                )
            )

        return results

    @property
    def total_chunks(self) -> int:
        return len(self._chunks)

    @property
    def total_documents(self) -> int:
        return len({c.doc_id for c in self._chunks})
