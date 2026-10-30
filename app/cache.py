"""
app/cache.py
Two-Tier In-Chat Query Cache for zero-token repeated question handling.

Tier 1: Exact SHA-256 normalized query hash lookup (<2ms, 0 tokens).
Tier 2: Entity-scoped semantic cosine similarity lookup (<28ms, 0 tokens).

Safety: DEGRADED_PASSAGE_FALLBACK responses are never cached.
"""
import hashlib
import json
import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Optional, Tuple, Dict, List
import numpy as np

logger = logging.getLogger(__name__)

SEMANTIC_CACHE_THRESHOLD = 0.96

TICKER_SYNONYMS: Dict[str, str] = {
    "amazon": "amzn", "apple": "aapl", "microsoft": "msft",
    "alphabet": "googl", "google": "googl", "meta": "meta",
    "facebook": "meta", "tesla": "tsla", "nvidia": "nvda",
    "jpmorgan": "jpm", "jp morgan": "jpm", "3m": "mmm",
}


def normalize_query(question: str) -> str:
    text = question.lower().strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\w\s%-]", "", text)
    for synonym, canonical in TICKER_SYNONYMS.items():
        text = text.replace(synonym, canonical)
    years = sorted(set(re.findall(r"\b20\d{2}\b", text)))
    text = re.sub(r"\b20\d{2}\b", "", text).strip()
    if years:
        text = text + " " + " ".join(years)
    return text.strip()


def sha256_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class QueryCache:
    """
    Two-tier query result cache backed by SQLite.
    """

    def __init__(self, db_path: str = "data/indices/chunks.sqlite"):
        self.db_path = Path(db_path)
        self._lru: Dict[str, str] = {}
        self._semantic_entries: List[Tuple[str, str, Optional[str], Optional[int], np.ndarray]] = []
        self._loaded = False

    def load(self) -> None:
        if not self.db_path.exists():
            self._loaded = True
            return

        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS query_cache (
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
            conn.commit()

            rows = conn.execute("""
                SELECT cache_id, normalized_query_hash, ticker_filter,
                       fiscal_year_filter, query_embedding_blob, verified_response_json
                FROM query_cache
            """).fetchall()
        finally:
            conn.close()

        for cache_id, nhash, ticker, year, emb_blob, response_json in rows:
            self._lru[nhash] = response_json
            if emb_blob:
                try:
                    vec = np.frombuffer(emb_blob, dtype=np.float32)
                    self._semantic_entries.append((cache_id, nhash, ticker, year, vec))
                except Exception:
                    pass

        logger.info(f"QueryCache loaded {len(self._lru)} entries.")
        self._loaded = True

    def lookup(
        self,
        question: str,
        ticker_filter: Optional[str] = None,
        year_filter: Optional[int] = None,
        query_vector: Optional[np.ndarray] = None,
    ) -> Tuple[bool, Optional[str]]:
        if not self._loaded:
            self.load()

        normalized = normalize_query(question)
        nhash = sha256_hash(normalized)

        # Tier 1: Exact hash
        if nhash in self._lru:
            return True, self._lru[nhash]

        # Tier 2: Semantic cosine similarity
        if query_vector is not None and self._semantic_entries:
            best_score = -1.0
            best_response = None

            for _cid, _nhash, cached_ticker, cached_year, cached_vec in self._semantic_entries:
                if ticker_filter and cached_ticker and ticker_filter.upper() != cached_ticker.upper():
                    continue
                if year_filter and cached_year and year_filter != cached_year:
                    continue

                score = float(np.dot(query_vector, cached_vec))
                if score > best_score:
                    best_score = score
                    best_response = self._lru.get(_nhash)

            if best_score >= SEMANTIC_CACHE_THRESHOLD and best_response:
                return True, best_response

        return False, None

    def store(
        self,
        question: str,
        response_json: str,
        ticker_filter: Optional[str] = None,
        year_filter: Optional[int] = None,
        query_vector: Optional[np.ndarray] = None,
    ) -> None:
        if not self._loaded:
            return

        try:
            data = json.loads(response_json)
            if data.get("is_fallback_mode", False):
                return
        except Exception:
            return

        normalized = normalize_query(question)
        nhash = sha256_hash(normalized)

        import uuid
        cache_id = str(uuid.uuid4())
        now = time.time()
        emb_blob = query_vector.tobytes() if query_vector is not None else None

        if self.db_path.exists():
            try:
                conn = sqlite3.connect(str(self.db_path))
                conn.execute("""
                    INSERT OR REPLACE INTO query_cache
                    (cache_id, normalized_query_hash, canonical_question, ticker_filter,
                     fiscal_year_filter, query_embedding_blob, verified_response_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (cache_id, nhash, question, ticker_filter, year_filter, emb_blob, response_json, now))
                conn.commit()
                conn.close()
            except Exception as exc:
                logger.warning(f"Cache save failed: {exc}")

        self._lru[nhash] = response_json
        if query_vector is not None:
            self._semantic_entries.append(
                (cache_id, nhash, ticker_filter, year_filter, query_vector)
            )
