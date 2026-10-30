"""
app/rag/reranker.py
ONNX Cross-Encoder Reranker + Two-Tier Epistemic Abstention Gate.

Reranks the Top-12 RRF candidates using bge-reranker-base (INT8 ONNX),
then applies the abstention gate to determine whether to route to LLM or abstain.

Threshold: max_logit < -1.50 -> "Not in the documents" (0 tokens consumed).
"""
import logging
import numpy as np
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Set

from app.schemas import PassageResult

logger = logging.getLogger(__name__)

ABSTENTION_LOGIT_THRESHOLD = -1.50
RERANKER_MAX_SEQ_LEN = 384
RERANKER_TOP_N = 4


class ONNXCrossEncoderReranker:
    """
    INT8 ONNX bge-reranker-base cross-encoder for relevance reranking.
    """

    def __init__(self, model_dir: str = "models/bge-reranker-base-onnx-int8", num_threads: int = 2):
        self.model_dir = Path(model_dir)
        self.num_threads = num_threads
        self._session = None
        self._tokenizer = None
        self._loaded = False
        self._stub_mode = False

    def load(self) -> None:
        """Load the ONNX cross-encoder session and tokenizer from disk."""
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
            logger.warning(f"Could not load ONNX reranker ({exc}). Falling back to stub mode.")
            self._stub_mode = True
            self._loaded = True

    def _score_pairs_stub(self, query: str, passages: List[PassageResult]) -> np.ndarray:
        """Heuristic scoring for stub/test mode."""
        q_words = set(query.lower().split())
        scores = []
        for p in passages:
            p_words = set(p.clean_markdown_text.lower().split())
            overlap = len(q_words & p_words)
            # Produce scores in [-3.0, 3.0] range
            base_score = -2.0 + (overlap * 0.8)
            scores.append(min(base_score, 4.0))
        return np.array(scores, dtype=np.float32)

    def _score_pairs(self, query: str, passages: List[PassageResult]) -> np.ndarray:
        if not self._loaded:
            self.load()

        if self._stub_mode or self._session is None or self._tokenizer is None:
            return self._score_pairs_stub(query, passages)

        texts_a = [query] * len(passages)
        texts_b = [p.clean_markdown_text for p in passages]

        encoded = self._tokenizer(
            texts_a,
            texts_b,
            padding=True,
            truncation=True,
            max_length=RERANKER_MAX_SEQ_LEN,
            return_tensors="np",
        )

        ort_inputs = {
            "input_ids": encoded["input_ids"].astype(np.int64),
            "attention_mask": encoded["attention_mask"].astype(np.int64),
        }
        if "token_type_ids" in encoded:
            ort_inputs["token_type_ids"] = encoded["token_type_ids"].astype(np.int64)

        outputs = self._session.run(None, ort_inputs)
        logits = outputs[0].squeeze(-1)
        return logits.astype(np.float32)

    def rerank(
        self,
        passages: List[PassageResult],
        query: str,
        fiscal_years: Optional[List[int]] = None,
    ) -> Tuple[List[PassageResult], float, bool]:
        """
        Rerank passages and apply the epistemic abstention gate.

        Returns:
            (top_passages, max_logit, should_abstain)
        """
        if not passages:
            return [], -10.0, True

        logits = self._score_pairs(query, passages)

        for passage, score in zip(passages, logits):
            passage.reranker_score = float(score)

        max_logit = float(np.max(logits))

        # Tier 1 Abstention Gate: check threshold
        if max_logit < ABSTENTION_LOGIT_THRESHOLD:
            logger.info(
                f"Abstention gate triggered: max_logit={max_logit:.4f} < {ABSTENTION_LOGIT_THRESHOLD}"
            )
            return [], max_logit, True

        ranked = sorted(passages, key=lambda p: p.reranker_score, reverse=True)
        top_passages = self._apply_multi_year_coverage(ranked, fiscal_years)

        return top_passages, max_logit, False

    def _apply_multi_year_coverage(
        self,
        ranked: List[PassageResult],
        fiscal_years: Optional[List[int]],
    ) -> List[PassageResult]:
        """Guarantee balanced year coverage for comparison queries."""
        if not fiscal_years or len(fiscal_years) < 2:
            return ranked[:RERANKER_TOP_N]

        year_to_passages: Dict[int, List[PassageResult]] = {y: [] for y in fiscal_years}

        for p in ranked:
            for year in fiscal_years:
                if str(year) in p.pdf_filename or str(year) in p.clean_markdown_text:
                    year_to_passages[year].append(p)
                    break

        all_years_represented = all(len(year_to_passages[y]) >= 1 for y in fiscal_years)
        if not all_years_represented:
            return ranked[:RERANKER_TOP_N]

        selected: List[PassageResult] = []
        selected_chunk_ids: Set[str] = set()

        for year in fiscal_years:
            for p in year_to_passages[year][:2]:
                if p.chunk_id not in selected_chunk_ids:
                    selected.append(p)
                    selected_chunk_ids.add(p.chunk_id)
                if len(selected) >= RERANKER_TOP_N:
                    break
            if len(selected) >= RERANKER_TOP_N:
                break

        if len(selected) < RERANKER_TOP_N:
            for p in ranked:
                if p.chunk_id not in selected_chunk_ids:
                    selected.append(p)
                    selected_chunk_ids.add(p.chunk_id)
                if len(selected) >= RERANKER_TOP_N:
                    break

        return selected[:RERANKER_TOP_N]
