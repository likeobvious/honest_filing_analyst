"""
app/rag/reranker.py
Compatibility wrapper delegating to src.retrieval.onnx_reranker (execution_plan.md).
"""
from src.retrieval.onnx_reranker import (
    ONNXCrossEncoderReranker, ABSTENTION_LOGIT_THRESHOLD, TARGET_RERANK_TOP_K
)

__all__ = ["ONNXCrossEncoderReranker", "ABSTENTION_LOGIT_THRESHOLD", "TARGET_RERANK_TOP_K"]
