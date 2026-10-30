"""
app/rag/embedder.py
ONNX Embedder: Query embedding using bge-small-en-v1.5 (INT8 quantized ONNX).

Produces 384-dimensional L2-normalized embeddings for FAISS dense search.
Runs on CPU using OnnxRuntime with ORT_ENABLE_ALL graph optimizations.
Target latency: <30ms per query on Azure B2s (2 vCPU).
"""
import logging
import numpy as np
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


class ONNXEmbedder:
    """
    CPU-optimized sentence embedder using bge-small-en-v1.5 in ONNX INT8 format.

    Supports graceful fallback/stubbing if model files are not yet downloaded (e.g. in test env).
    """

    EMBEDDING_DIM = 384
    MAX_SEQ_LEN = 512

    def __init__(self, model_dir: str = "models/bge-small-en-v1.5-onnx-int8", num_threads: int = 2):
        self.model_dir = Path(model_dir)
        self.num_threads = num_threads
        self._session = None
        self._tokenizer = None
        self._loaded = False
        self._stub_mode = False

    def load(self) -> None:
        """Load the ONNX session and HuggingFace tokenizer from disk."""
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
        """Generate a deterministic pseudo-random unit vector for testing without model weights."""
        import hashlib
        seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)
        rng = np.random.RandomState(seed)
        vec = rng.randn(self.EMBEDDING_DIM).astype(np.float32)
        norm = np.linalg.norm(vec)
        return (vec / (norm + 1e-9)).astype(np.float32)

    def embed(self, text: str) -> np.ndarray:
        """
        Embed a single query string.
        Returns L2-normalized embedding vector of shape (384,), dtype float32.
        """
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: List[str]) -> np.ndarray:
        """
        Embed a batch of strings.
        Returns L2-normalized embedding matrix of shape (N, 384), dtype float32.
        """
        if not self._loaded:
            self.load()

        if self._stub_mode or self._session is None or self._tokenizer is None:
            return np.array([self._generate_stub_vector(t) for t in texts], dtype=np.float32)

        prefixed = [f"query: {t}" if not t.startswith("query:") else t for t in texts]

        encoded = self._tokenizer(
            prefixed,
            padding=True,
            truncation=True,
            max_length=self.MAX_SEQ_LEN,
            return_tensors="np",
        )

        ort_inputs = {
            "input_ids": encoded["input_ids"].astype(np.int64),
            "attention_mask": encoded["attention_mask"].astype(np.int64),
        }
        if "token_type_ids" in encoded:
            ort_inputs["token_type_ids"] = encoded["token_type_ids"].astype(np.int64)

        outputs = self._session.run(None, ort_inputs)
        token_embeddings = outputs[0]  # shape: (batch, seq_len, 384)
        attention_mask = encoded["attention_mask"]

        mask_expanded = attention_mask[:, :, np.newaxis].astype(np.float32)
        sum_embeddings = np.sum(token_embeddings * mask_expanded, axis=1)
        sum_mask = np.clip(mask_expanded.sum(axis=1), a_min=1e-9, a_max=None)
        embeddings = sum_embeddings / sum_mask

        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms = np.clip(norms, a_min=1e-9, a_max=None)
        return (embeddings / norms).astype(np.float32)

    @property
    def is_loaded(self) -> bool:
        return self._loaded
