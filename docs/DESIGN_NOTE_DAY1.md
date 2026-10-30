# PS5: Honest Filing Analyst — Day 1 Design Note

**Document:** Day 1 Executive Deliverable (1–2 Page RFC)  
**Target Environment:** Azure Standard B2s (2 vCPU, 4 GB RAM, No GPU) · Zero-Cost Stack  
**Team Composition:** 3 Developers (Teammate 1: Lead/Systems, Teammate 2: Ingestion & Security, Teammate 3: UI & Test Benchmark Lead)

---

## 1. The Problem Statement: Deterministic Financial QA Under Hostile & Constrained Conditions

Financial analysts querying SEC Form 10-K filings require exact quantitative answers with zero tolerance for hallucinations, fabricated numbers, or prompt hijacking. **PS5: Honest Filing Analyst** delivers a zero-cost, single-node research assistant enforcing four non-negotiable guarantees on an Azure B2s VM (2 vCPU, 4 GB RAM, no GPU):

1. **100% Page-Level Grounding:** Binds every claim to an exact PDF filename and 1-indexed physical page number (`#page=N`) with one-click browser navigation.
2. **Honest Abstention:** Deterministically answers `"Not in the documents"` for out-of-corpus entities, missing years, or undisclosed metrics (targeting 5 of 5 on unanswerable queries at 0 LLM tokens).
3. **Zero Planted Instructions Executed:** Neutralizes indirect prompt injections (OWASP LLM01)—including white text, microscopic fonts (`< 4.5pt`), and metadata overrides—via visual sanitization and runtime XML sandboxing.
4. **Resilient Edge Deployment:** Runs the entire stack on a 4 GB RAM VM within a `20 RPM` proxy limit, consuming `≤ 80,000 tokens` (stretch) across 40 questions at `p95 ≤ 8.0 s`, and degrading to Top-4 evidence passages in `≤ 5.0 s` during 60-second proxy outages.

---

## 2. Back-of-the-Envelope Numbers: Memory, Compute, Latency & Token Budgets

Every architectural decision is anchored in quantitative capacity planning for an Azure `Standard_B2s` instance (`2 vCPU` Intel Xeon / AMD EPYC @ ~2.4 GHz, `4,096 MB` physical RAM, no GPU, 30 GB SSD).

### Corpus & Index Sizing
* **Corpus Volume:** ~50 10-K PDFs @ ~120 pages/PDF = ~6,000 pages (~125 MB on disk).
* **Chunk Count:** ~15,000 page-bounded chunks (~350 tokens/chunk). Every chunk inherits immutable `(doc_id, pdf_filename, pdf_page_number)` metadata.
* **Dense Vector Index:** `384` dims (`float32` = 1,536 B/vec) via `bge-small-en-v1.5` = **~35 MB** in FAISS + SQLite.
* **Lexical Index:** In-memory `rank_bm25` inverted token index = **~45 MB**.

### 4,096 MB RAM Allocation Budget (Preventing Linux OOM Kills)
| Component | Resident Memory (RSS) | Optimization Strategy |
| :--- | :--- | :--- |
| **Linux OS + Docker Overhead** | `450 MB` | Ubuntu 24.04 LTS minimal + 2 GB swapfile (`vm.swappiness=10`). |
| **PostgreSQL 16 (Token Audit DB)** | `220 MB` | Tuned `shared_buffers=64MB`, `max_connections=25`. |
| **LiteLLM Proxy Container** | `280 MB` | Single uvicorn worker (`--num_workers 1`), enforces 20 RPM limit. |
| **FastAPI Backend + ONNX Models + Indices** | `1,350 MB` | `bge-small` INT8 (~130 MB) + `bge-reranker` INT8 (~290 MB) + BM25/FAISS (~100 MB) + Python runtime (~350 MB). |
| **Free Headroom Buffer** | **`1,796 MB` (~43.8% Free)** | Zero OOM-killer invocations during peak cross-encoder reranking bursts. |

### Latency Budget (Target: Stretch `p95 ≤ 8.0 s` / Outage `≤ 5.0 s`)
1. **Query Parsing & Entity Extraction:** `15 ms` (deterministic regex + SQLite manifest check; 0 tokens).
2. **Query Embedding (`bge-small-en-v1.5` ONNX):** `25 ms` (`ORT_ENABLE_ALL`, `intra_op_num_threads=2`).
3. **Hybrid Retrieval (BM25 Top-30 + FAISS Top-30 → RRF):** `35 ms` (Reciprocal Rank Fusion, $k=60$).
4. **Cross-Encoder Reranking (`bge-reranker-base` INT8 ONNX):** `650 ms` (Top-12 candidates truncated to 384 tokens).
5. **Pre-LLM Confidence Gate:** `10 ms` (`max_logit < -1.50` triggers immediate 0-token abstention).
6. **LiteLLM Proxy + Upstream LLM Call:** `1,800 – 3,200 ms` (Groq Llama-3.3-70B; circuit breaker trips after 1.2s connect / 3.2s read timeout).
7. **AST Math Verification & Grounding:** `20 ms` (evaluated by sandboxed `ast` + `Decimal`).
* **Total Median Latency:** `~2.6 s` | **p95 Latency:** `~5.4 s` (comfortably beats stretch `≤ 8.0 s`).
* **Outage Fallback Latency:** `~1.95 s` (1st failure) / `~0.75 s` (subsequent calls while circuit is OPEN).

### Token Budget & Rate Pacing (Stretch: ≤ 80,000 Tokens)
* Routed query: ~1,790 tokens (system prompt 280, question 60, top-4 chunks 1,240, JSON response 210).
* 35 answered queries × 1,790 tokens = ~62,650 tokens.
* 5 abstentions × 0 tokens = 0 tokens.
* Total across 40 questions = **~62,650 tokens** (well below the 80,000 stretch target).
* Pacing: 1 request every 3.2s = 18.75 RPM (never trips the 20 RPM limit).

---

## 3. Architectural Options Considered & Why Alternatives Were Rejected

| Subsystem | Option A (Rejected) | Option B (Rejected) | Option C (Selected Architecture) | Quantitative Reason for Rejection |
| :--- | :--- | :--- | :--- | :--- |
| **Reasoning & Orchestration** | **Multi-Agent ReAct Loop** (LangGraph autonomous agent, 3–5 LLM calls/query) | **Map-Reduce Summarization** over Top-15 pages | **Single-Pass Deterministic DAG** (Router → Hybrid RRF → ONNX Reranker → Gate → Single LLM Call → AST Math) | ReAct consumes ~252k tokens across 40 questions (exceeds 200k limit) and takes >19s p95 at 20 RPM. Option C uses 1 call/query (~62.6k tokens, p95 ~5.4s). |
| **Vector DB & Storage** | **Heavy Standalone Vector DB** (Milvus / Qdrant container) | **Lexical BM25 Only** | **In-Process BM25 + FAISS IndexFlatIP + SQLite** | External vector DB adds 600MB–1.2GB RAM on a 4GB VM, causing Linux OOM kills. In-process uses <100MB RAM. |
| **Arithmetic Execution** | **LLM Mental Math in Prose** | **LLM-Generated `exec()` Code** | **LLM Extracts Operands + Python AST Decimal Evaluation** | LLMs make ~12% errors on multi-digit division. `exec()` creates an RCE vulnerability. AST Decimal provides 100% precision with 0 RCE risk. |
| **Prompt Injection Defense** | **System Prompt Warning Only** | **Secondary LLM Guardrail Call** | **4-Layer Defense-in-Depth** (Visual strip → Heuristic neutralization → Nonce XML sandbox → Verifier) | Single warnings are bypassed by suffix attacks. Guardrail calls double RPM and token consumption. Layered defense costs 0 extra tokens. |

---

## 4. Failure Modes & Engineered Mitigations

1. **FM-1: Multi-Page Table Header Loss:** Statement of Operations splits across pages, losing column headers.  
   *Mitigation:* Stateful Page-Header Propagator detects continuation and prepends `[TABLE CONTEXT: ...]` to child page chunks.
2. **FM-2: Indirect Prompt Injection:** Hidden instructions in white text, 0.5pt font, or footnotes.  
   *Mitigation:* Layer 1 PyMuPDF visual filter strips spans `<4.5pt` or low-contrast. Layer 3 wraps passages in random hex nonce `<untrusted_filing_data_{nonce}>`.
3. **FM-3: 60-Second Proxy Outage:** Proxy container stopped mid-run.  
   *Mitigation:* Async circuit breaker with 1.2s connect / 3.2s read timeout fails fast and delivers Top-4 ranked passages with `#page=N` links in `<1.95s`.
4. **FM-4: Citation Page Off-by-One:** Printed page label differs from physical PDF page index.  
   *Mitigation:* Canonical 1-indexed `pdf_page_number = page.number + 1` stored in every chunk and verified against page content.
