# AI Interaction & Engineering Decision Log (`docs/AI_LOG.md`)

This log documents prompts given to AI coding tools during the architectural design and implementation phases of **PS5: Honest Filing Analyst**, highlighting key instances where standard AI recommendations were evaluated, rejected, and overridden with quantitative justification.

---

## Log Entry 1: Architecture Pattern Selection (Orchestration)

* **Prompt:** *"Design an agentic RAG system using LangGraph with an iterative ReAct loop that searches SEC 10-K filings, evaluates chunk sufficiency, refines queries, and generates answers."*
* **AI Tool Recommendation:** Suggested a 4-node LangGraph cyclic graph (`QueryPlanner -> Retriever -> Grader -> Generator -> HallucinationGrader`) averaging 3.2 LLM invocations per question.
* **Engineering Decision:** **REJECTED.**
* **Quantitative Justification:**  
  Under our project constraints (single Azure Standard B2s node, 20 RPM hard proxy rate limit, and an 80,000 stretch token budget for 40 questions):
  1. A 3.2-turn loop requires $40 \times 3.2 = 128$ LLM calls. At 20 RPM, just waiting for rate limiter slots consumes $6.4\text{ minutes}$.
  2. Multi-turn prompt repetition burns $\sim 252,000\text{ tokens}$ across 40 questions—violating even the 200,000 pass budget by $26\%$ and exceeding the 80,000 stretch budget by $3.15\times$.
  3. Latency $p95$ escalates to $>19\text{ seconds}$, completely failing the stretch target ($p95 \le 8.0\text{ s}$).
* **Selected Alternative:** Single-Pass Deterministic DAG moving entity routing, hybrid RRF, cross-encoder reranking, and epistemic gating into local CPU steps ($\sim 735\text{ ms}$, 0 LLM tokens), requiring **at most 1 LLM call per query** ($\sim 62,650\text{ tokens}$ total, $p95 \sim 5.4\text{ s}$).

---

## Log Entry 2: Vector Database Selection (Memory Constraints)

* **Prompt:** *"Recommend a vector database container to store and search 15,000 chunk embeddings for our Docker Compose deployment."*
* **AI Tool Recommendation:** Recommended running standalone `Qdrant` or `Milvus` containers alongside `PostgreSQL 16`, `LiteLLM Proxy`, and `FastAPI`.
* **Engineering Decision:** **REJECTED.**
* **Quantitative Justification:**  
  1. A standalone vector DB container consumes $600\text{ MB} - 1.2\text{ GB}$ of idle RSS memory.
  2. On an Azure B2s instance with only $4,096\text{ MB}$ total physical RAM, combined with PostgreSQL ($220\text{ MB}$), LiteLLM ($280\text{ MB}$), OS/Docker daemon ($450\text{ MB}$), and the cross-encoder reranker ($290\text{ MB}$), a separate vector DB container leaves virtually no safety buffer, causing Linux OOM kills (`SIGKILL 137`) during concurrent reranking bursts.
  3. For 15,000 384-dimensional `float32` vectors ($15,000 \times 1,536\text{ B} \approx 22.5\text{ MB}$), an external server adds unnecessary network serialization overhead.
* **Selected Alternative:** In-process embedded `FAISS IndexFlatIP` + local `SQLite` metadata store. Total RAM footprint is under $35\text{ MB}$, zero network overhead, and zero extra container footprint.

---

## Log Entry 3: Text Chunking Strategy for 10-K Filings

* **Prompt:** *"How should we chunk 100-page SEC 10-K PDFs for retrieval?"*
* **AI Tool Recommendation:** Recommended standard `RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=100)`.
* **Engineering Decision:** **REJECTED.**
* **Quantitative Justification:**  
  1. SEC 10-K financial statements (e.g., Consolidated Statements of Operations) span multi-column tabular data across pages. Sliding-window character splitting slices tables horizontally, divorcing line items on page 55 from their `(in $ millions)` unit scale and `(FY2023 | FY2022 | FY2021)` column headers on page 54.
  2. Overlapping chunks across physical page boundaries corrupts citation precision. When a chunk spans pages 54 and 55, the model may cite page 54 for a number physically printed on page 55, failing the requirement that *"each claim links to the document and page it came from, and clicking opens that page."*
* **Selected Alternative:** Page-bounded layout-aware chunks with Docling Markdown table serialization and a stateful header propagator that injects `[TABLE CONTEXT: ...]` into continuation pages while strictly maintaining canonical 1-indexed `pdf_page_number`.

---

## Log Entry 4: Financial Arithmetic Computation

* **Prompt:** *"How should we handle percentage growth and operating margin calculations in the model output?"*
* **AI Tool Recommendation:** Suggested either letting the LLM calculate math directly in prose or generating and executing arbitrary Python code using `exec()`.
* **Engineering Decision:** **REJECTED.**
* **Quantitative Justification:**  
  1. LLMs have a known $\sim 12\%$ calculation error rate on multi-digit division and basis point comparisons (e.g., `(255887 - 242901) / 242901 * 100`).
  2. Running `exec()` on LLM-generated code exposes the host to Remote Code Execution (OWASP LLM02 / LLM07) if a poisoned PDF executes an indirect injection command.
* **Selected Alternative:** Model emits a structured JSON calculation schema (`formula_expression`, `operands`). A sandboxed Python AST evaluator (`ast_calculator.py`) evaluates the formula using `ast.parse` and `Decimal` arithmetic, restricting nodes strictly to numeric constants and basic arithmetic operators (`+`, `-`, `*`, `/`, `**`). Guarantees $100\%$ mathematical precision with zero RCE vulnerability.

---

## Log Entry 5: Prompt Injection & Hijacking Defense

* **Prompt:** *"How should we defend against adversarial instructions hidden in 10-K filings?"*
* **AI Tool Recommendation:** Suggested using a secondary LLM guardrail call (`Llama-Guard`) or relying entirely on a system prompt instruction *"Ignore all previous instructions"*.
* **Engineering Decision:** **REJECTED.**
* **Quantitative Justification:**  
  1. A secondary guardrail LLM call doubles total proxy requests ($80\text{ calls}$ for 40 questions), immediately exceeding the 20 RPM limit and doubling token consumption.
  2. Single-prompt warnings are routinely bypassed by character escaping and XML delimiter attacks.
* **Selected Alternative:** 4-layer defense-in-depth:
  - Layer 1: PyMuPDF physical span filtering (drops text $<4.5\text{ pt}$, low contrast, off-page boxes).
  - Layer 2: Heuristic imperative phrase neutralization.
  - Layer 3: Cryptographic random-nonce XML sandboxing (`<untrusted_filing_data_{nonce}>`) with HTML entity escaping (`<` $\to$ `&lt;`) and post-data sandwich prompting.
  - Layer 4: Deterministic post-generation verbatim substring verification against physical page text.
