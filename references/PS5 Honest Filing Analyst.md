# PS5: Honest Filing Analyst

Oct 22, 2026 · @Dev Team

Build a research assistant that answers questions about a folder of company annual reports with page citations, says so when the answer is not there, cannot be hijacked by text hidden in the documents, and runs on a free LLM tier.

## What you are building

- A web app with one question box over a fixed folder of US company annual reports (10-K PDFs).
- Short answers with citations: each claim links to the document and page it came from, and clicking opens that page.
- Questions that compare years or need a small calculation (for example, a margin or a year-on-year change).
- An honest "not in the documents" when the folder does not contain the answer.
- Resistance to hijacking: some PDFs contain hidden instructions aimed at the model. Your system must never follow them.
- Graceful failure: when the LLM is down, the app still shows the most relevant passages with page links.

## Your limits

| Item | Limit |
| --- | --- |
| Hosting | One Azure VM that you create and run: 2 vCPU, 4 GB RAM, no GPU (for example size B2s) |
| LLM | A free tier only (for example Groq), reached through your own proxy that allows at most 20 requests per minute and logs every token |
| Token budget | At most 200,000 tokens for a 40-question test run, counted by your proxy |
| Everything else | Parsing, search, embeddings and reranking run on your VM |
| Cost | Zero. Free Azure for Students credit (no card needed); no paid API keys. Stop the VM when you are not using it. |

## What we expect

Meeting every pass target earns full measured-results marks; stretch targets earn bonus marks.

| Measure | Pass | Stretch |
| --- | --- | --- |
| Correct answers on 40 hidden questions | ≥ 70% | ≥ 80% |
| No-answer questions answered "not in the documents" | ≥ 4 of 5 | 5 of 5 |
| Planted instructions followed | 0 | 0 |
| Citations whose page really holds the evidence | All | All |
| Response time, p95 | ≤ 15 s | ≤ 8 s |
| Tokens used for the whole test | ≤ 200,000 | ≤ 80,000 |
| LLM down for 60 s | Best passages with page links within 5 s | Same |

**How we will verify it**

You build your own test setup and keep it in your repo. On Day 4 the captain watches you run it against your Azure deployment, then changes inputs and reruns spot checks. A number you cannot reproduce live does not count. Your setup must include:

- Your own test set of at least 40 questions with reference answers, built before you tune anything. It must include comparison questions, calculation questions and at least 5 questions with no answer in the folder. The FinanceBench open sample is a good starting point.
- At least 3 PDFs in the folder with instructions you planted yourself.
- Your proxy's token log for a full run.
- The proxy stopped for 60 seconds in the middle of a run, showing the passage fallback.

The captain will also ask 10 to 15 fresh questions live, including some with no answer, and click through the citations.

## Resources you can use

These are suggestions, not instructions. Any other free resource is allowed; name it in your design note and say why you chose it.

| Need | Free options |
| --- | --- |
| Documents and starter questions | [FinanceBench](https://github.com/patronus-ai/financebench) open sample: 10-K PDFs in `/pdfs` plus 150 questions with answers (CC BY-NC 4.0) |
| LLM (free tier) | [Groq](https://console.groq.com) free plan; backups: free models on [OpenRouter](https://openrouter.ai) or the Gemini free tier. Free-tier limits change often, so check them on Day 1. |
| Proxy with rate limit and token log | [LiteLLM proxy](https://docs.litellm.ai/docs/proxy/users) (`rpm_limit` per key; needs its Postgres database) |
| PDF parsing | [PyMuPDF](https://pymupdf.readthedocs.io), [Docling](https://github.com/docling-project/docling) (good with tables) |
| Keyword search | [rank_bm25](https://github.com/dorianbrown/rank_bm25) |
| Embeddings and reranking on CPU | [bge-small-en-v1.5](https://huggingface.co/BAAI/bge-small-en-v1.5), [bge-reranker-base](https://huggingface.co/BAAI/bge-reranker-base) |
| Orchestration | [LangGraph](https://github.com/langchain-ai/langgraph), or plain Python |
| Reading on attacks | [OWASP Top 10 for LLM applications](https://genai.owasp.org/llm-top-10/) |

## What you hand in

- **Design note (Day 1, 1–2 pages):** the problem in your own words, back-of-envelope numbers, at least two options and why you rejected one, what could go wrong, and how you will measure it.
- **AI log:** what you asked AI tools, and where you disagreed with the answer and why.
- **Git repo:** a README that starts the whole system with one command (Docker Compose), plus the URL of your Azure deployment. Your test set and test setup live here too. Small, meaningful commits.
- **Measurement report (Day 4):** each target, your number on your own test set, and exactly how you measured it.

## The 5 days

| Day | What happens |
| --- | --- |
| 1 | Paper only. Read, search, ask AI, try small experiments. Hand in the design note; 30-minute review with the captain. No real build before the review. |
| 2–3 | Build. Coding agents allowed. |
| 4 | Measure against the targets, fix what fails, then live verification with the captain. |
| 5 | Presentation, then questions from the captain and other teams. |

## Marking

| Part | Marks |
| --- | --- |
| Design thinking | 35 |
| Measured results | 35 |
| Docs and Git | 15 |
| Presentation and questions | 15 |
