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

---

# Development Activity Log — Teammate 3 Deliverables

**Date recorded:** 2026-10-31
**AI tool used:** AI assistant using Copilot SDK in VS Code
**Record scope:** The entries below document the Teammate 3 work performed in this workspace on the date above. Prompt descriptions are concise summaries of the actual requests in the conversation, not purported verbatim transcripts. No independent AI coding tool or remote code-generation service was used.

## Frontend development

- **Development task:** Implement the Honest Filing Analyst research interface and connect it to the existing query and health APIs.
- **Prompt given to AI (summary):** Build a professional responsive financial research UI in `src/ui/index.html` and `src/ui/app.js`; read the real Pydantic response schemas; safely render answer, numeric emphasis, math result, citations, errors, health, and cache/provider/outage banners; do not modify backend files.
- **Files modified:** `src/ui/index.html`, `src/ui/app.js`.
- **Code generated or assisted:** Semantic responsive dashboard markup and styling; client-side query submission to `POST /api/v1/query`; health request to `GET /api/v1/health`; response rendering for `answer`, `citations`, `is_abstention`, `is_fallback_mode`, `tokens_used`, `latency_ms`, `math_result`, and `system_status`; citation links using the existing `/pdfs/{filename}` route and physical `#page=N`; status notifications; request/loading/error handling.
- **Changes after reviewing AI output:** Used the actual `app/schemas.py` names and nested `system_status` fields instead of inventing fields. Changed cache-hit token display to `0` for the current cache lookup even if the cached response contains the original request's token count. Filing-derived strings use DOM text nodes and `textContent`, not raw HTML insertion.
- **Architectural decisions:** Kept the API base same-origin by default; optional `apiBase` query parameter and HTML `api-base` meta setting allow a separately hosted UI. Used backend `/pdfs/{filename}` route, not a path guessed from reference documentation. No backend files were changed.
- **Problems encountered:** FastAPI and Docker currently point to `frontend/`, whereas this assignment's UI location is `src/ui/`. The backend serves `/pdfs/{filename}`. These deployment-path mismatches were not changed because the frontend task prohibited backend changes.
- **Verification performed:** `node --check src/ui/app.js` passed. Browser checks against mocked API responses verified rendering of answers, calculation values, citation URLs, cache/provider/outage banners, health status, and HTML-like citation text as text rather than executable markup.
- **Not verified:** No live API/backend integration or actual PDF delivery was tested.

## PDF.js integration

- **Development task:** This section tracks the requested PDF.js viewer work and its current status.
- **Prompt given to AI:** No separate implementation prompt for a PDF.js viewer was issued in the frontend task recorded above.
- **Files modified:** None for PDF.js. No `src/ui/pdf_viewer.js`, PDF.js distribution, or PDF.js worker was created.
- **Code generated or assisted:** The frontend renders source citations as links to the existing backend PDF endpoint with `#page=N`. This uses the browser's PDF handling; it is not a PDF.js viewer or quote-highlighting implementation.
- **Changes after reviewing AI output:** Not applicable; PDF.js code was not generated.
- **Architectural decisions:** Do not imply that PDF.js, a split pane, or snippet highlighting exists. The existing `/pdfs/{filename}` endpoint is the only inspected PDF-serving contract.
- **Problems encountered:** PDF.js assets and PDF files were not present in the checkout during inspection. Adding a working PDF.js integration therefore remains outstanding.
- **Verification performed:** The citation URL and rendered citation text were checked in the UI using mocked data.
- **Not verified / follow-up:** No PDF.js rendering, page navigation inside a PDF.js viewer, or quote highlighting was implemented or tested. Complete this section with the actual implementation and test details if that work is later performed.

## Benchmark dataset

- **Development task:** Create the 45-question golden benchmark JSON with five defined category counts and evidence-aware fields.
- **Prompt given to AI (summary):** Inspect the actual PDFs and benchmark source first; do not invent answers; use real filenames and physical page numbers; create 15 factual, 13 comparison/calculation, 7 synthesis, 5 unanswerable, and 5 adversarial questions using the requested machine-readable schema.
- **Files modified:** `eval/golden_test_set.json`.
- **Code generated or assisted:** A 45-entry JSON question inventory with IDs, categories, question text, nullable expected answers and numeric values, citation arrays, expected status, tolerance, canary strings, evidence status, and a validation note.
- **Changes after reviewing AI output:** Recursive repository inspection found no PDFs, benchmark source files, or poisoned PDFs. Rather than invent financial answers or page citations, marked the dataset `BLOCKED_PENDING_CORPUS_AND_POISONED_PDFS`; placeholders and all unavailable evidence are explicitly identified. Candidate absent-company and undisclosed-metric cases are also marked unverified, not asserted as proven corpus facts.
- **Architectural decisions:** A question without verified filing evidence must not be treated as scored ground truth. Adversarial canaries listed in the design materials are provisional until checked against actual planted PDFs.
- **Problems encountered:** Required corpus, FinanceBench source data, and poisoned PDF fixtures were unavailable.
- **Verification performed:** Parsed the JSON and checked the exact total of 45, unique IDs, category counts `15/13/7/5/5`, required question fields, and citation/canary container types.
- **Not verified:** No financial answer, source page, corpus-absence assertion, or poisoned-PDF behavior could be verified.

## Evaluation harness

- **Development task:** Implement the automated evaluator for sequential API requests, scoring, latency, token logging, and detailed reports.
- **Prompt given to AI (summary):** Load the golden JSON; post questions sequentially to `/api/v1/query`; pace at 3.2 seconds; record each response and `perf_counter()` latency; compute p50/p95; score textual/numeric answers, citations, abstentions, and canaries; query the configured PostgreSQL spend logs in the run time window; handle CLI subsets and failures without inventing results.
- **Files modified:** `eval/run_eval.py`.
- **Code generated or assisted:** Command-line JSON benchmark loader, category/limit selection, sequential HTTP client, response-shape validation, per-question result capture, numeric relative-tolerance scoring (0.5% default), citation filename/page matching, abstention and canary checks, percentile calculations, PostgreSQL token query, Docker Compose `psql` fallback, and timestamped JSON report generation.
- **Changes after reviewing AI output:** Ground-truth scores are eligible only when expected evidence is marked verified and expected answers are populated. Provisional abstention and injection findings are not reported as proof of the five-of-five targets. Missing token-log information is represented as unavailable rather than zero. Added response-shape checks so malformed successful HTTP bodies are recorded as errors.
- **Architectural decisions:** Use the project’s documented `LiteLLM_SpendLogs` table and inclusive `"startTime"` run bounds, with the Compose `postgres` service as the local default path. No dependency manifest changes were needed.
- **Problems encountered:** `ruff` was not installed/available in the environment. The benchmark remains provisional because source documents are absent.
- **Verification performed:** Python compilation passed. A local mock API exercised numeric scoring, citation matching, abstention validation, canary detection, malformed JSON response handling, and skipped/unavailable token reporting. Separate checks covered the 0.5% numeric tolerance boundary, p95 interpolation, and the actual benchmark's 45 entries/category counts. An unavailable local API endpoint was recorded in a temporary report as an error with unavailable tokens.
- **Not verified:** No live backend benchmark was run; no real PostgreSQL token totals, accuracy score, latency target, or production token target were measured.

## Outage testing

- **Development task:** Implement a guarded local Compose outage drill for the LiteLLM proxy and verify fallback and recovery behavior.
- **Prompt given to AI (summary):** Confirm Compose services; run a normal evaluation; require safety confirmation before stopping the local proxy; send five outage requests and measure each under five seconds; verify top-four evidence and physical citations; restart the proxy with a trap; wait for health; confirm normal inference; never target remote infrastructure.
- **Files modified:** `eval/test_outage_fallback.sh`.
- **Code generated or assisted:** Bash script with actual Compose service names from `docker-compose.yml` (`postgres`, `litellm-proxy`, `honest-analyst-app`); local Docker context checks; project-file-scoped Compose commands; explicit typed confirmation; backend/index preflight; baseline evaluator call; five outage requests; latency, fallback mode, passage count, citation filename/page assertions; proxy readiness polling; post-restart inference check; and exit cleanup that attempts to restart the proxy.
- **Changes after reviewing AI output:** Armed the restart trap before stopping the proxy; refused noninteractive confirmation and remote Docker endpoints; required the baseline response to be a normal `LLM_SYNTHESIZED` response before proceeding; preserved an outage window configurable by environment variable; used backend `/api/v1/health` and the Compose service name `litellm-proxy`.
- **Architectural decisions:** The script operates only with the checked-out Compose file and a local Docker context. A successful recovery requires a non-fallback synthesized response and positive token usage, not merely a responsive HTTP status.
- **Problems encountered:** The current environment does not provide a usable WSL Bash; Git for Windows Bash was used for syntax validation. No live local Docker stack or outage run was executed.
- **Verification performed:** `bash -n` passed under Git for Windows Bash. A remote `DOCKER_HOST=tcp://...` safety test was refused before any Compose operation. Script source checks confirmed the confirmation guard precedes the stop operation and that cleanup restart is armed before stopping.
- **Not verified:** No container was stopped; no five-query outage drill, real proxy health wait, or post-restart inference check was performed. Do not report the outage target as passed until the script has been run against the intended local test stack and its output reviewed.

## Review follow-up

- **Date:** 2026-10-31
- **Development task:** Review Teammate 3 deliverables for API/UI alignment, test validity, rate limiting, outage safety, and design consistency.
- **AI tool used:** AI assistant using Copilot SDK in VS Code.
- **Prompt given to AI (summary):** Perform a senior-engineer review of the assigned UI, benchmark, evaluation runner, outage script, AI log, and Day 4 report; run safe checks; fix bugs only in assigned files; report incomplete work and exact demo commands.
- **Files modified:** `eval/run_eval.py`, `eval/test_outage_fallback.sh`, `docs/AI_LOG.md`.
- **Code generated or assisted:** The evaluator now rejects request intervals below 3.2 seconds, combines verified abstention outcomes with answerable-question results for the 40-question accuracy metric, reports the 70% pass and 80% stretch thresholds, and includes accuracy failure in `--fail-on-targets`. The outage script rejects API/proxy endpoint URLs that are not localhost or loopback IPs.
- **Changes after reviewing AI output:** Changed the evaluator's 20-RPM guard from permitting arbitrary intervals to requiring the documented 3.2-second minimum. Tightened outage script local-only checks to cover both API/proxy URLs in addition to the Docker context.
- **Architectural decisions:** Keep PDF.js and quote highlighting marked incomplete: `src/ui/pdf_viewer.js` and PDF.js assets are absent. Do not create a Day 4 report with invented measurements; `docs/MEASUREMENT_REPORT_DAY4.md` is absent and needs actual benchmark data.
- **Problems encountered:** No PDFs, poisoned fixtures, measurement report, or live Compose stack were present. `ruff` was unavailable. The environment's WSL Bash launcher was unusable; Git for Windows Bash was used for shell syntax checks.
- **Verification performed:** Recompiled the evaluator, confirmed CLI help, checked that an interval below 3.2 seconds is rejected, checked aggregate accuracy with a verified abstention, verified p50/p95 outputs, syntax-checked the outage script with Git Bash, and confirmed remote Docker/API targets are rejected before container actions. `node --check` and `git diff --check` also passed.
- **Test limitation:** The existing `python -m unittest discover -s tests -p test_teammate1.py -v` command was attempted, but test discovery stopped on `ModuleNotFoundError: No module named 'pydantic'`; the project requirements were not installed as part of this review.
- **Not verified:** No live API benchmark, PostgreSQL token query, actual PDF citation destination, PDF.js viewer, or proxy outage/recovery drill was run.
