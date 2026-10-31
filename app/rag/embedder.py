"""
app/rag/embedder.py
Compatibility wrapper delegating to src.retrieval.onnx_reranker.ONNXEmbedder (execution_plan.md).
"""
from src.retrieval.onnx_reranker import ONNXEmbedder

__all__ = ["ONNXEmbedder"]
