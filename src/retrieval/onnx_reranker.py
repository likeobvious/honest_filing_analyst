"""
src/retrieval/onnx_reranker.py
Quantized INT8 ONNX Embedder, Cross-Encoder Reranker, and Two-Tier Epistemic Abstention Gate.
Complies with RFC-PS5-2026-001 and execution_plan.md.
"""
import re
import os
import math
import logging
from pathlib import Path
from typing import List, Tuple, Optional, Dict

import numpy as np

from src.api.schemas import PassageResult

logger = logging.getLogger(__name__)

# Abstention threshold for bge-reranker-base raw cross-encoder logit
ABSTENTION_LOGIT_THRESHOLD = -1.50
TARGET_RERANK_TOP_K = 4


class ONNXEmbedder:
    """bge-small-en-v1.5 INT8 ONNX Embedder (~130 MB RAM)."""
    EMBEDDING_DIM = 384

    def __init__(self, model_dir: str = "models/bge-small-en-v1.5-onnx-int8", num_threads: int = 2):
        self.model_dir = Path(model_dir)
        self.num_threads = num_threads
        self._session = None
        self._tokenizer = None
        self._loaded = False
        self._stub_mode = False

    def load(self) -> None:
        """Load ONNX runtime session and tokenizer."""
        model_path = self.model_dir / "model.onnx"
        if not model_path.exists():
            logger.warning(
                f"ONNX model not found at {model_path}. "
                "Running ONNXEmbedder in stub mode (deterministic pseudo-embeddings for testing)."
            )
            self._stub_mode = True
            self._loaded = True
            return

        try:
            import onnxruntime as ort
            from transformers import AutoTokenizer

            sess_options = ort.SessionOptions()
            sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            sess_options.intra_op_num_threads = self.num_threads
            sess_options.inter_op_num_threads = 1

            self._session = ort.InferenceSession(
                str(model_path),
                sess_options=sess_options,
                providers=["CPUExecutionProvider"],
            )

            self._tokenizer = AutoTokenizer.from_pretrained(str(self.model_dir))
            self._loaded = True
            logger.info(f"ONNXEmbedder loaded: {model_path} (threads={self.num_threads})")
        except Exception as exc:
            logger.warning(f"Could not initialize ONNX runtime ({exc}). Falling back to stub mode.")
            self._stub_mode = True
            self._loaded = True

    def _generate_stub_vector(self, text: str) -> np.ndarray:
        import hashlib
        seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)
        rng = np.random.RandomState(seed)
        vec = rng.randn(self.EMBEDDING_DIM).astype(np.float32)
        norm = np.linalg.norm(vec)
        return (vec / (norm + 1e-9)).astype(np.float32)

    def embed(self, text: str) -> np.ndarray:
        """Compute L2-normalized 384-d embedding vector."""
        if not self._loaded:
            self.load()

        if self._stub_mode or self._session is None:
            return self._generate_stub_vector(text)

        inputs = self._tokenizer(
            text,
            max_length=512,
            truncation=True,
            padding=True,
            return_tensors="np"
        )
        ort_inputs = {
            "input_ids": inputs["input_ids"].astype(np.int64),
            "attention_mask": inputs["attention_mask"].astype(np.int64),
        }
        if "token_type_ids" in inputs:
            ort_inputs["token_type_ids"] = inputs["token_type_ids"].astype(np.int64)

        outputs = self._session.run(None, ort_inputs)
        # Mean pooling over attention mask
        token_embeddings = outputs[0]
        mask = inputs["attention_mask"][..., np.newaxis].astype(np.float32)
        pooled = (token_embeddings * mask).sum(axis=1) / (mask.sum(axis=1) + 1e-9)
        vec = pooled[0].astype(np.float32)
        norm = np.linalg.norm(vec)
        return (vec / (norm + 1e-9)).astype(np.float32)


class ONNXCrossEncoderReranker:
    """bge-reranker-base INT8 ONNX Cross-Encoder (~290 MB RAM) + Abstention Gate."""

    def __init__(self, model_dir: str = "models/bge-reranker-base-onnx-int8", num_threads: int = 2):
        self.model_dir = Path(model_dir)
        self.num_threads = num_threads
        self._session = None
        self._tokenizer = None
        self._loaded = False
        self._stub_mode = False

    def load(self) -> None:
        """Load cross-encoder ONNX session and tokenizer."""
        model_path = self.model_dir / "model.onnx"
        if not model_path.exists():
            logger.warning(
                f"Reranker ONNX model not found at {model_path}. "
                "Running in stub mode (heuristic keyword-based scoring for tests)."
            )
            self._stub_mode = True
            self._loaded = True
            return

        try:
            import onnxruntime as ort
            from transformers import AutoTokenizer

            sess_options = ort.SessionOptions()
            sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            sess_options.intra_op_num_threads = self.num_threads
            sess_options.inter_op_num_threads = 1

            self._session = ort.InferenceSession(
                str(model_path),
                sess_options=sess_options,
                providers=["CPUExecutionProvider"],
            )

            self._tokenizer = AutoTokenizer.from_pretrained(str(self.model_dir))
            self._loaded = True
            logger.info(f"ONNXCrossEncoderReranker loaded: {model_path}")
        except Exception as exc:
            logger.warning(f"Reranker ONNX load failed ({exc}). Falling back to stub mode.")
            self._stub_mode = True
            self._loaded = True

    def _score_stub(self, query: str, text: str) -> float:
        """Deterministic heuristic scoring when model weights are not present."""
        q_words = set(re.findall(r"[a-z0-9]+", query.lower()))
        t_words = set(re.findall(r"[a-z0-9]+", text.lower()))
        if not q_words:
            return -5.0
        overlap = len(q_words & t_words) / len(q_words)
        return -2.5 + (overlap * 4.0)

    def rerank(
        self,
        arg1=None,
        arg2=None,
        query: Optional[str] = None,
        candidates: Optional[List[PassageResult]] = None,
        top_k: int = TARGET_RERANK_TOP_K,
        is_multi_year: bool = False,
        target_years: Optional[List[int]] = None,
    ) -> Tuple[List[PassageResult], float, bool]:
        """
        Rerank candidates using cross-encoder.
        Accepts (query, candidates), (candidates, query), or keyword arguments.
        Returns:
            (top_passages, max_logit, should_abstain)
        """
        if query is None and candidates is None:
            if isinstance(arg1, str):
                query = arg1
                candidates = arg2
            else:
                candidates = arg1
                query = arg2
        elif query is None:
            query = arg1 if isinstance(arg1, str) else arg2
        elif candidates is None:
            candidates = arg1 if isinstance(arg1, list) else arg2

        if candidates is None:
            candidates = []
        if query is None:
            query = ""

        if not candidates:
            return [], -99.0, True

        if not self._loaded:
            self.load()

        scored_candidates: List[Tuple[PassageResult, float]] = []

        if self._stub_mode or self._session is None:
            for p in candidates:
                logit = self._score_stub(query, p.clean_markdown_text)
                scored_candidates.append((p, logit))
        else:
            pairs = [(query, p.clean_markdown_text[:512]) for p in candidates]
            inputs = self._tokenizer(
                pairs,
                max_length=512,
                truncation=True,
                padding=True,
                return_tensors="np"
            )
            ort_inputs = {
                "input_ids": inputs["input_ids"].astype(np.int64),
                "attention_mask": inputs["attention_mask"].astype(np.int64),
            }
            if "token_type_ids" in inputs:
                ort_inputs["token_type_ids"] = inputs["token_type_ids"].astype(np.int64)

            outputs = self._session.run(None, ort_inputs)
            logits = outputs[0].squeeze(-1).tolist()
            if isinstance(logits, float):
                logits = [logits]

            for p, logit in zip(candidates, logits):
                scored_candidates.append((p, float(logit)))

        scored_candidates.sort(key=lambda x: x[1], reverse=True)
        max_logit = scored_candidates[0][1] if scored_candidates else -99.0

        # Tier 1 Epistemic Abstention Gate
        should_abstain = max_logit < ABSTENTION_LOGIT_THRESHOLD
        if should_abstain:
            logger.info(
                f"Abstention Gate tripped: max_logit={max_logit:.3f} < {ABSTENTION_LOGIT_THRESHOLD}"
            )
            return [], max_logit, True

        # Multi-year balanced representation enforcement
        if is_multi_year and target_years and len(target_years) >= 2:
            top_passages = self._balance_multi_year(scored_candidates, target_years, top_k)
        else:
            top_passages = []
            for p, logit in scored_candidates[:top_k]:
                p_copy = p.model_copy()
                p_copy.reranker_score = round(logit, 4)
                top_passages.append(p_copy)

        return top_passages, max_logit, False

    def _balance_multi_year(
        self,
        scored: List[Tuple[PassageResult, float]],
        target_years: List[int],
        top_k: int = 4,
    ) -> List[PassageResult]:
        """Ensure Top-k includes representation across multiple comparison fiscal years."""
        slots_per_year = max(1, top_k // len(target_years))
        buckets: Dict[int, List[Tuple[PassageResult, float]]] = {y: [] for y in target_years}
        unassigned: List[Tuple[PassageResult, float]] = []

        for p, logit in scored:
            assigned = False
            for y in target_years:
                if str(y) in p.pdf_filename or str(y) in p.clean_markdown_text:
                    buckets[y].append((p, logit))
                    assigned = True
                    break
            if not assigned:
                unassigned.append((p, logit))

        selected: List[PassageResult] = []
        for y, items in buckets.items():
            for p, logit in items[:slots_per_year]:
                p_copy = p.model_copy()
                p_copy.reranker_score = round(logit, 4)
                selected.append(p_copy)

        remaining_needed = top_k - len(selected)
        if remaining_needed > 0:
            already_selected_ids = {p.chunk_id for p in selected}
            for p, logit in scored:
                if p.chunk_id not in already_selected_ids:
                    p_copy = p.model_copy()
                    p_copy.reranker_score = round(logit, 4)
                    selected.append(p_copy)
                    if len(selected) >= top_k:
                        break

        return selected[:top_k]

    def _apply_multi_year_coverage(
        self,
        passages: List[PassageResult],
        fiscal_years: List[int],
        top_k: int = 4,
    ) -> List[PassageResult]:
        """Convenience method enforcing balanced representation across comparison fiscal years."""
        scored = [(p, p.reranker_score) for p in passages]
        return self._balance_multi_year(scored, fiscal_years, top_k)
