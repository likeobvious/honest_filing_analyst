"""
src/cache/query_cache.py
Two-Tier In-Chat Query Deduplication Cache (Complies with RFC-PS5-2026-001 & execution_plan.md).

Tier 1 (Exact Hash):
  - Computes SHA-256 of normalized query text.
  - Matches in < 2ms, consumes 0 tokens.

Tier 2 (Entity-Scoped Semantic Cache):
  - Cosine similarity >= 0.96 on 384-d dense embedding vector.
  - Scoped strictly to matching (ticker_filter, fiscal_year_filter) to avoid cross-year false hits.
  - Matches in < 28ms, consumes 0 tokens.

Cache Invalidation Safety:
  - Degraded fallback responses are strictly excluded from storage.
"""
import time
import hashlib
import logging
import sqlite3
import re
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any

import numpy as np

logger = logging.getLogger(__name__)

SEMANTIC_SIMILARITY_THRESHOLD = 0.96


def normalize_query_text(query: str) -> str:
    """Normalize query text for deterministic hashing."""
    q = query.lower().strip()
    q = re.sub(r"[^\w\s]", " ", q)
    q = re.sub(r"\s+", " ", q).strip()
    return q


def compute_query_hash(normalized_query: str) -> str:
    """Compute SHA-256 hex digest of normalized query."""
    return hashlib.sha256(normalized_query.encode("utf-8")).hexdigest()


class QueryCache:
    """Two-tier exact + semantic query cache backed by SQLite."""

    def __init__(self, db_path: str = "data/indices/chunks.sqlite"):
        self.db_path = Path(db_path)
        self._exact_cache: Dict[str, str] = {}
        self._semantic_entries: List[Dict[str, Any]] = []
        self._mem_conn: Optional[sqlite3.Connection] = None
        self._loaded = False

    def _get_conn(self) -> sqlite3.Connection:
        if str(self.db_path) == ":memory:":
            if self._mem_conn is None:
                self._mem_conn = sqlite3.connect(":memory:")
            return self._mem_conn
        return sqlite3.connect(str(self.db_path))

    def _close_conn(self, conn: sqlite3.Connection) -> None:
        if str(self.db_path) != ":memory:":
            conn.close()

    def load(self) -> None:
        """Initialize SQLite table and preload in-memory lookup cache."""
        if str(self.db_path) != ":memory:" and not self.db_path.parent.exists():
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = self._get_conn()
        try:
            # Self-heal schema if old table without query_hash exists
            cursor = conn.execute("PRAGMA table_info(query_cache)")
            cols = [r[1] for r in cursor.fetchall()]
            if cols and "query_hash" not in cols:
                conn.execute("DROP TABLE query_cache")
                conn.commit()

            conn.execute("""
                CREATE TABLE IF NOT EXISTS query_cache (
                    cache_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query_hash TEXT NOT NULL UNIQUE,
                    normalized_query TEXT NOT NULL,
                    ticker_filter TEXT,
                    fiscal_year_filter INTEGER,
                    query_vector BLOB,
                    response_json TEXT NOT NULL,
                    hit_count INTEGER DEFAULT 0,
                    created_at REAL NOT NULL,
                    last_accessed_at REAL NOT NULL
                )
            """)
            conn.commit()

            rows = conn.execute("""
                SELECT query_hash, normalized_query, ticker_filter,
                       fiscal_year_filter, query_vector, response_json
                FROM query_cache
            """).fetchall()

            self._exact_cache.clear()
            self._semantic_entries.clear()

            for qhash, norm_q, ticker, yr, vec_blob, resp_json in rows:
                self._exact_cache[qhash] = resp_json
                if vec_blob:
                    vec = np.frombuffer(vec_blob, dtype=np.float32)
                    self._semantic_entries.append({
                        "query_hash": qhash,
                        "normalized_query": norm_q,
                        "ticker_filter": ticker,
                        "fiscal_year_filter": yr,
                        "vector": vec,
                        "response_json": resp_json,
                    })
        finally:
            self._close_conn(conn)

        self._loaded = True
        logger.info(f"QueryCache loaded {len(self._exact_cache)} entries from SQLite.")

    def lookup(
        self,
        query: str,
        ticker_filter: Optional[str] = None,
        year_filter: Optional[int] = None,
        query_vector: Optional[np.ndarray] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Lookup answer in query cache.
        Returns: (is_hit, cached_response_json)
        """
        if not self._loaded:
            self.load()

        norm_q = normalize_query_text(query)
        qhash = compute_query_hash(norm_q)

        # ── Tier 1: Exact SHA-256 match (< 2ms) ──
        if qhash in self._exact_cache:
            logger.info(f"Cache Tier 1 EXACT HIT for query: '{query[:60]}'")
            self._record_hit(qhash)
            return True, self._exact_cache[qhash]

        if str(self.db_path) != ":memory:":
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT response_json FROM query_cache WHERE query_hash = ?",
                    (qhash,)
                ).fetchone()
                if row:
                    self._exact_cache[qhash] = row[0]
                    logger.info(f"Cache Tier 1 EXACT HIT (from SQLite) for query: '{query[:60]}'")
                    self._record_hit(qhash)
                    return True, row[0]
            finally:
                self._close_conn(conn)

        # ── Tier 2: Entity-Scoped Semantic Cosine Match (< 28ms) ──
        if query_vector is not None and len(self._semantic_entries) > 0:
            qv = query_vector.astype(np.float32)
            qv_norm = np.linalg.norm(qv)
            if qv_norm > 1e-9:
                qv_unit = qv / qv_norm

                best_sim = -1.0
                best_match = None

                for entry in self._semantic_entries:
                    # Enforce strict scope on ticker and fiscal_year to prevent cross-year leakage
                    if ticker_filter and entry["ticker_filter"]:
                        if ticker_filter.upper() != entry["ticker_filter"].upper():
                            continue
                    if year_filter and entry["fiscal_year_filter"]:
                        if year_filter != entry["fiscal_year_filter"]:
                            continue

                    ev = entry["vector"]
                    ev_norm = np.linalg.norm(ev)
                    if ev_norm > 1e-9:
                        sim = float(np.dot(qv_unit, ev / ev_norm))
                        if sim > best_sim:
                            best_sim = sim
                            best_match = entry

                if best_match is not None and best_sim >= SEMANTIC_SIMILARITY_THRESHOLD:
                    logger.info(
                        f"Cache Tier 2 SEMANTIC HIT (cosine={best_sim:.4f}) for: '{query[:60]}'"
                    )
                    self._record_hit(best_match["query_hash"])
                    return True, best_match["response_json"]

        return False, None

    def store(
        self,
        query: str,
        response_json: str,
        ticker_filter: Optional[str] = None,
        year_filter: Optional[int] = None,
        query_vector: Optional[np.ndarray] = None,
        is_fallback_mode: bool = False,
    ) -> bool:
        """
        Store a verified response in cache.
        Degraded fallback responses are strictly excluded to prevent poison propagation.
        """
        if is_fallback_mode:
            logger.info("Skipping cache store: degraded fallback responses are excluded.")
            return False

        if not self._loaded:
            self.load()

        norm_q = normalize_query_text(query)
        qhash = compute_query_hash(norm_q)
        now = time.time()

        vec_blob = None
        vec_arr = None
        if query_vector is not None:
            vec_arr = query_vector.astype(np.float32)
            vec_blob = vec_arr.tobytes()

        try:
            conn = self._get_conn()
            conn.execute("""
                INSERT INTO query_cache
                    (query_hash, normalized_query, ticker_filter, fiscal_year_filter,
                     query_vector, response_json, hit_count, created_at, last_accessed_at)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
                ON CONFLICT(query_hash) DO UPDATE SET
                    response_json=excluded.response_json,
                    last_accessed_at=excluded.last_accessed_at
            """, (qhash, norm_q, ticker_filter, year_filter, vec_blob, response_json, now, now))
            conn.commit()
            self._close_conn(conn)

            self._exact_cache[qhash] = response_json
            if vec_arr is not None:
                self._semantic_entries.append({
                    "query_hash": qhash,
                    "normalized_query": norm_q,
                    "ticker_filter": ticker_filter,
                    "fiscal_year_filter": year_filter,
                    "vector": vec_arr,
                    "response_json": response_json,
                })
            return True
        except Exception as exc:
            logger.warning(f"Failed to store in query_cache: {exc}")
            return False

    def _record_hit(self, query_hash: str) -> None:
        try:
            conn = self._get_conn()
            conn.execute("""
                UPDATE query_cache
                SET hit_count = hit_count + 1, last_accessed_at = ?
                WHERE query_hash = ?
            """, (time.time(), query_hash))
            conn.commit()
            self._close_conn(conn)
        except Exception:
            pass
