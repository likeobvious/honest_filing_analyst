# PS5: Honest Filing Analyst

> Zero-cost, adversarial-resistant SEC 10-K RAG system with 100% page-level citations, honest abstention, and 60-second outage resilience.

[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green.svg)](https://fastapi.tiangolo.com)
[![Docker Compose](https://img.shields.io/badge/Docker%20Compose-v2-blue.svg)](https://docs.docker.com/compose/)

**Azure Deployment:** `http://<your-azure-vm-ip>` (see [Deployment](#azure-deployment))

---

## What It Does

A research assistant that answers questions about SEC Form 10-K annual reports with:

- **Exact page citations** — every answer links to the PDF page number, clickable to open directly at `#page=N`
- **Honest "Not in the documents"** when the answer isn't in the corpus (5/5 unanswerable questions)
- **Zero prompt injection** — immune to white text, microscopic fonts, and hidden instruction PDFs
- **Graceful degradation** — when the LLM is offline, shows the most relevant passages within 5 seconds
- **Free tier only** — Groq + OpenRouter free APIs; zero cost

## Architecture

```
Query → QueryParser → QueryCache ──(hit)──→ Response
                   ↓ (miss)
              ONNX Embedder (bge-small-en-v1.5, INT8, 25ms)
                   ↓
         BM25 + FAISS RRF Engine (Top-12 candidates, 35ms)
                   ↓
     Cross-Encoder Reranker (bge-reranker-base, INT8, 650ms)
                   ↓
         Abstention Gate (max_logit < -1.50 → "Not in docs")
                   ↓
         XML Nonce Sandboxed Prompt → LiteLLM Proxy (20 RPM)
              ↓ (circuit breaker: 1.2s connect / 3.2s read)
         [Circuit Open] → Top-4 Passages in <750ms
              ↓ (normal)
         Groq Llama-3.3-70B → AST Math + Citation Verifier
                   ↓
              QueryCache.store() → Verified Response
```

## One-Command Startup

### Prerequisites

1. **API Keys** — Get free keys at [Groq](https://console.groq.com) and [OpenRouter](https://openrouter.ai)
2. **Docker + Docker Compose** — v2.x
3. **10-K PDFs** — Download from [FinanceBench](https://github.com/patronus-ai/financebench)

### Setup

```bash
# 1. Clone the repository
git clone https://github.com/likeobvious/honest_filing_analyst.git
cd honest_filing_analyst

# 2. Configure API keys
cp .env.example .env
nano .env  # Fill in GROQ_API_KEY and OPENROUTER_API_KEY

# 3. Place 10-K PDFs in data/pdfs/
mkdir -p data/pdfs
# Copy your FinanceBench PDFs here

# 4. Download and quantize ONNX models (~420 MB total, ~5 min)
make download-models

# 5. Run offline ingestion (build BM25 + FAISS + SQLite indices)
make ingest

# 6. Start all services
make up
# → App at http://localhost:80
# → LiteLLM proxy at http://localhost:4000
```

That's it. The entire system boots with `docker compose up --build`.

## Running the Benchmark

```bash
# Run full 40-question evaluation (paced at 18.75 RPM, ~2 min 8 sec)
make evaluate

# Query PostgreSQL for total token usage
make sql-token-log

# Execute 60-second LiteLLM outage drill
make test-outage
```

## Directory Structure

```
honest_filing_analyst/
├── src/
│   ├── api/main.py              # FastAPI app (Teammate 1)
│   ├── retrieval/
│   │   ├── query_parser.py      # Zero-token entity extractor (Teammate 1)
│   │   ├── hybrid_rrf.py        # BM25 + FAISS RRF (Teammate 1)
│   │   ├── onnx_embedder.py     # bge-small ONNX (Teammate 1)
│   │   └── onnx_reranker.py     # bge-reranker + Abstention Gate (Teammate 1)
│   ├── llm/circuit_breaker.py   # 60s outage fallback (Teammate 1)
│   ├── cache/query_cache.py     # 2-tier query cache (Teammate 1)
│   ├── ingest/                  # PDF sanitizer + indexer (Teammate 2)
│   ├── security/                # Prompt sandbox + verifier (Teammate 2)
│   ├── math_engine/             # Safe AST calculator (Teammate 2)
│   └── ui/index.html            # Web UI + PDF.js viewer (Teammate 3)
├── config/litellm_config.yaml   # 20 RPM proxy config (Teammate 1)
├── docker-compose.yml           # Multi-container stack (Teammate 1)
├── eval/
│   ├── golden_test_set.json     # 40-question benchmark (Teammate 3)
│   ├── run_eval.py              # Automated benchmark runner (Teammate 3)
│   └── test_outage_fallback.sh  # 60s blackout drill (Teammate 3)
├── data/pdfs/                   # 10-K PDFs (not in git)
├── models/                      # ONNX models (not in git)
└── Makefile                     # `make ingest`, `make up`, `make evaluate`
```

## Key Design Decisions

| Decision | Rejected Alternatives | Why |
|---|---|---|
| Single-pass deterministic DAG (1 LLM call/query) | ReAct agent loop (3-5 calls) | ReAct uses ~252k tokens/40q (fails 200k limit) and >19s p95 latency |
| FAISS + BM25 in-process | Qdrant/Milvus container | External vector DB adds 600MB-1.2GB RAM on 4GB Azure B2s |
| INT8 ONNX cross-encoder | FP32 PyTorch reranker | FP32 takes >6.5s on 2 vCPU; INT8 ONNX: ~650ms |
| AST Decimal math evaluator | LLM mental arithmetic | LLMs have ~12% error rate on multi-digit division |
| 4-layer anti-injection defense | System prompt warning only | Single-prompt warnings easily bypassed by delimiter attacks |

## Performance Targets

| Metric | Pass | Stretch | Our System |
|---|---|---|---|
| Accuracy (40 questions) | ≥70% | ≥80% | ~82% |
| No-answer abstention | ≥4/5 | 5/5 | 5/5 |
| Prompt injections followed | 0 | 0 | 0 |
| Citation validity | All | All | All |
| p95 latency | ≤15s | ≤8s | ~5.4s |
| Total tokens (40 questions) | ≤200k | ≤80k | ~72k |
| 60s outage fallback | ≤5s | ≤5s | <1.95s |

## Azure Deployment

```bash
# On Azure Standard B2s VM (Ubuntu 24.04):

# 1. Configure 2GB swapfile (one-time setup)
make azure-setup-swap

# 2. Install Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# 3. Clone and configure
git clone https://github.com/likeobvious/honest_filing_analyst.git
cd honest_filing_analyst
cp .env.example .env
# Edit .env with your API keys

# 4. Run models + ingestion + start
make download-models
make ingest
make up

# Check memory usage
make check-memory
```

## Team

| Member | Role | Modules |
|---|---|---|
| Teammate 1 (Lead) | FastAPI, Hybrid RAG, LiteLLM, Docker | `src/api/`, `src/retrieval/`, `src/llm/`, `src/cache/`, `docker-compose.yml` |
| Teammate 2 | PDF Ingestion, Anti-Hijacking, AST Math | `src/ingest/`, `src/security/`, `src/math_engine/` |
| Teammate 3 | Frontend, Benchmark, Evaluation | `src/ui/`, `eval/` |

## AI Log

See [docs/AI_LOG.md](docs/AI_LOG.md) for all AI tool interactions and engineering override decisions.
