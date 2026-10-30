"""
Model download and INT8 quantization script for bge-small-en-v1.5 and bge-reranker-base.

Run during Docker build stage or manually with:
    python scripts/download_models.py --output-dir models/

Downloads from HuggingFace Hub and applies ONNX INT8 dynamic quantization.
This reduces:
  - bge-small-en-v1.5: ~130 MB (vs ~440 MB FP32 PyTorch)
  - bge-reranker-base: ~290 MB (vs ~1.1 GB FP32 PyTorch)

Total model memory on Azure B2s: ~420 MB (fits comfortably in 4 GB RAM budget)
"""
import argparse
import logging
import os
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

EMBEDDER_MODEL_ID = "BAAI/bge-small-en-v1.5"
EMBEDDER_DIR_NAME = "bge-small-en-v1.5-onnx-int8"

RERANKER_MODEL_ID = "BAAI/bge-reranker-base"
RERANKER_DIR_NAME = "bge-reranker-base-onnx-int8"


def download_and_quantize_model(
    model_id: str,
    output_dir: Path,
    task: str,
) -> None:
    """
    Download a HuggingFace model, export to ONNX, and apply INT8 dynamic quantization.

    Args:
        model_id: HuggingFace model ID (e.g., "BAAI/bge-small-en-v1.5")
        output_dir: Directory to save the quantized ONNX model
        task: optimum export task ("feature-extraction" or "text-classification")
    """
    from optimum.onnxruntime import ORTModelForFeatureExtraction, ORTModelForSequenceClassification
    from optimum.onnxruntime.configuration import AutoQuantizationConfig
    from optimum.onnxruntime import ORTQuantizer
    from transformers import AutoTokenizer

    logger.info(f"Downloading {model_id} → {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Download and save tokenizer
    tokenizer = AutoTokenizer.from_pretrained(model_id)
    tokenizer.save_pretrained(str(output_dir))
    logger.info(f"Tokenizer saved to {output_dir}")

    # Export to ONNX (FP32 intermediate)
    fp32_dir = output_dir.parent / (output_dir.name + "-fp32-tmp")
    fp32_dir.mkdir(parents=True, exist_ok=True)

    if task == "feature-extraction":
        model = ORTModelForFeatureExtraction.from_pretrained(model_id, export=True)
        model.save_pretrained(str(fp32_dir))
    else:
        model = ORTModelForSequenceClassification.from_pretrained(model_id, export=True)
        model.save_pretrained(str(fp32_dir))

    logger.info(f"FP32 ONNX model saved to {fp32_dir}")

    # Apply INT8 dynamic quantization
    quantizer = ORTQuantizer.from_pretrained(str(fp32_dir))
    qconfig = AutoQuantizationConfig.avx512_vnni(is_static=False, per_channel=False)
    quantizer.quantize(
        save_dir=str(output_dir),
        quantization_config=qconfig,
    )

    # Copy tokenizer files to final dir (quantizer may overwrite)
    tokenizer.save_pretrained(str(output_dir))

    logger.info(f"INT8 quantized model saved to {output_dir}")

    # Cleanup FP32 intermediate
    import shutil
    shutil.rmtree(str(fp32_dir), ignore_errors=True)

    # Report size
    model_size = sum(f.stat().st_size for f in output_dir.glob("**/*") if f.is_file())
    logger.info(f"Model size: {model_size / 1024 / 1024:.1f} MB")


def main():
    parser = argparse.ArgumentParser(description="Download and quantize ONNX models.")
    parser.add_argument(
        "--output-dir",
        type=str,
        default="models",
        help="Base directory for model output (default: models/)",
    )
    parser.add_argument(
        "--skip-embedder",
        action="store_true",
        help="Skip downloading the embedder model",
    )
    parser.add_argument(
        "--skip-reranker",
        action="store_true",
        help="Skip downloading the reranker model",
    )
    args = parser.parse_args()

    base_dir = Path(args.output_dir)
    base_dir.mkdir(parents=True, exist_ok=True)

    if not args.skip_embedder:
        embedder_out = base_dir / EMBEDDER_DIR_NAME
        if (embedder_out / "model.onnx").exists():
            logger.info(f"Embedder already exists at {embedder_out}. Skipping.")
        else:
            download_and_quantize_model(
                EMBEDDER_MODEL_ID,
                embedder_out,
                task="feature-extraction",
            )

    if not args.skip_reranker:
        reranker_out = base_dir / RERANKER_DIR_NAME
        if (reranker_out / "model.onnx").exists():
            logger.info(f"Reranker already exists at {reranker_out}. Skipping.")
        else:
            download_and_quantize_model(
                RERANKER_MODEL_ID,
                reranker_out,
                task="text-classification",
            )

    logger.info("All models downloaded and quantized successfully.")


if __name__ == "__main__":
    main()
